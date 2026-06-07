"""
Servicio de extracción de campos desde PDFs escaneados.

El PDF de Generali Ecuador es un PDF de imágenes (scan), no tiene capa de texto.
Usamos PyMuPDF para rasterizar páginas y Tesseract OCR para extraer texto
de las regiones (cajas) definidas en la plantilla.
"""
import io
import logging
from pathlib import Path

import fitz  # PyMuPDF
import pytesseract
from PIL import Image

from app.core.config import settings
from app.core.errors import PDFInvalidoError
from app.models.plantilla import Caja, ResultadoExtraccion, Variable

logger = logging.getLogger(__name__)

# DPI para rasterizar páginas — 200 dpi es buen balance velocidad/precisión
RENDER_DPI = 200

# Factor de escala relativo al tamaño de renderizado del frontend (PDF.js).
# PDF.js a scale=1.0 renderiza 1 punto PDF = 1 CSS pixel.
# Los PDFs miden en puntos (72 puntos por pulgada), por lo que el canvas
# del frontend equivale a 72 DPI.
# Al rasterizar con PyMuPDF a RENDER_DPI, escalamos las coordenadas.
FRONTEND_DPI = 72
SCALE_FACTOR = RENDER_DPI / FRONTEND_DPI  # ≈ 2.778


def _configurar_tesseract():
    """Aplica la ruta de Tesseract si está configurada."""
    if settings.tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd


def _rasterizar_pagina(doc: fitz.Document, pagina_idx: int) -> Image.Image:
    """Convierte una página del PDF en imagen PIL."""
    page = doc[pagina_idx]
    matriz = fitz.Matrix(RENDER_DPI / 72, RENDER_DPI / 72)
    pix = page.get_pixmap(matrix=matriz)
    return Image.open(io.BytesIO(pix.tobytes("png")))


def _extraer_texto_de_caja(imagen: Image.Image, caja: Caja) -> str:
    """
    Recorta la región de la imagen correspondiente a la caja
    y ejecuta OCR sobre ese recorte.
    """
    # Las coordenadas vienen en pixels del canvas frontend (96 DPI).
    # Escalamos al DPI de renderizado.
    x = int(caja.x * SCALE_FACTOR)
    y = int(caja.y * SCALE_FACTOR)
    w = int(caja.ancho * SCALE_FACTOR)
    h = int(caja.alto * SCALE_FACTOR)

    # Validar que la caja esté dentro de la imagen
    img_w, img_h = imagen.size
    x2 = min(x + w, img_w)
    y2 = min(y + h, img_h)

    if x >= img_w or y >= img_h or w <= 0 or h <= 0:
        logger.warning("Caja '%s' fuera de límites de imagen", caja.nombre)
        return ""

    recorte = imagen.crop((x, y, x2, y2))

    # psm 7 = una línea | psm 6 = bloque uniforme de texto (varias líneas)
    altura_recorte = y2 - y
    psm = 7 if altura_recorte < 60 else 6

    texto = pytesseract.image_to_string(
        recorte,
        lang="spa",
        config=f"--psm {psm}",
    ).strip()

    return texto


def extraer_variables(
    pdf_bytes: bytes,
    cajas: list[Caja],
) -> ResultadoExtraccion:
    """
    Procesa un PDF y extrae el valor de cada caja usando OCR.

    Args:
        pdf_bytes: Contenido binario del PDF.
        cajas: Lista de cajas definidas en la plantilla.

    Returns:
        ResultadoExtraccion con las variables extraídas y su estado.
    """
    _configurar_tesseract()

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:
        raise PDFInvalidoError(f"No se pudo abrir el PDF: {e}") from e

    num_paginas = len(doc)
    variables: list[Variable] = []
    advertencias: list[str] = []

    # Caché de páginas renderizadas (evitar re-rasterizar la misma página)
    cache_paginas: dict[int, Image.Image] = {}

    for caja in cajas:
        if caja.pagina >= num_paginas:
            advertencias.append(
                f"Caja '{caja.nombre}': página {caja.pagina} no existe "
                f"(el PDF tiene {num_paginas} páginas)"
            )
            variables.append(Variable(
                nombre=caja.nombre,
                valor=None,
                origen="extraido",
                estado="falta",
                nota="Página no encontrada en este PDF",
            ))
            continue

        if caja.pagina not in cache_paginas:
            try:
                cache_paginas[caja.pagina] = _rasterizar_pagina(doc, caja.pagina)
            except Exception as e:
                logger.error("Error rasterizando página %d: %s", caja.pagina, e)
                advertencias.append(f"No se pudo procesar la página {caja.pagina}")
                continue

        imagen_pagina = cache_paginas[caja.pagina]

        texto = _extraer_texto_de_caja(imagen_pagina, caja)
        texto_limpio = _limpiar_texto(texto)

        if texto_limpio:
            estado = "ok"
        else:
            estado = "falta"
            advertencias.append(f"Campo '{caja.nombre}' vacío tras OCR")

        variables.append(Variable(
            nombre=caja.nombre,
            valor=texto_limpio or None,
            origen="extraido",
            estado=estado,
        ))

    doc.close()

    return ResultadoExtraccion(
        plantilla_id="",  # Se llena en el router
        variables=variables,
        paginas_procesadas=num_paginas,
        advertencias=advertencias,
    )


def _limpiar_texto(texto: str) -> str:
    """
    Limpia artefactos comunes del OCR en documentos de pólizas ecuatorianas.
    - Une palabras cortadas con guión al final de línea (ej: "Coope-\nrativa" → "Cooperativa")
    - Elimina saltos de línea internos
    - Normaliza espacios múltiples
    - Elimina caracteres no imprimibles
    """
    import re
    # Unir palabras cortadas con guión: "palabra-\n" + "continuación" → "palabracontinuación"
    texto = re.sub(r"-\s*\n\s*", "", texto)
    # Reemplazar saltos de línea restantes por espacio
    texto = texto.replace("\n", " ").replace("\r", " ")
    # Eliminar caracteres no imprimibles excepto espacio
    texto = "".join(c for c in texto if c.isprintable())
    # Colapsar espacios múltiples
    texto = " ".join(texto.split())
    return texto.strip()
