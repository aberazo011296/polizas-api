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

# DPI para rasterizar páginas — 300 DPI es el óptimo para Tesseract OCR
RENDER_DPI = 300

# Factor de escala relativo al tamaño de renderizado del frontend (PDF.js).
# PDF.js a scale=1.0 renderiza 1 punto PDF = 1 CSS pixel.
# Los PDFs miden en puntos (72 puntos por pulgada), por lo que el canvas
# del frontend equivale a 72 DPI.
# Al rasterizar con PyMuPDF a RENDER_DPI, escalamos las coordenadas.
FRONTEND_DPI = 72
SCALE_FACTOR = RENDER_DPI / FRONTEND_DPI  # ≈ 2.778


def _preprocesar_imagen(img: Image.Image) -> Image.Image:
    """
    Mejora la imagen antes de OCR:
    - Escala al doble para dar más resolución a Tesseract
    - Convierte a escala de grises
    - Binariza con umbral adaptativo
    """
    # Escalar x2
    w, h = img.size
    img = img.resize((w * 2, h * 2), Image.LANCZOS)
    # Convertir a gris y binarizar
    img = img.convert("L")
    # Umbral simple: pixeles < 180 → negro, resto → blanco
    img = img.point(lambda p: 0 if p < 180 else 255, "1")
    return img.convert("L")


def _corregir_caracteres_espaciados(texto: str) -> str:
    """
    Corrige el caso en que el OCR separa cada carácter con espacio.
    Ej: "9 9 0 6 6 4" → "990664", "0 1 / F e b / 2 0 2 6" → "01/Feb/2026"
    Detecta cuando la mayoría de tokens son de 1-2 caracteres y los une.
    """
    lineas_corregidas = []
    for linea in texto.split("\n"):
        tokens = linea.split()
        if len(tokens) >= 3:
            tokens_cortos = sum(1 for t in tokens if len(t) <= 2)
            if tokens_cortos / len(tokens) >= 0.7:
                linea = "".join(tokens)
        lineas_corregidas.append(linea)
    return "\n".join(lineas_corregidas)


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

    # Mejorar imagen antes de OCR: escalar al doble y binarizar
    recorte = _preprocesar_imagen(recorte)

    # psm 7 = una línea | psm 6 = bloque uniforme de texto (varias líneas)
    altura_recorte = y2 - y
    psm = 7 if altura_recorte < 60 else 6

    texto = pytesseract.image_to_string(
        recorte,
        lang="spa",
        config=f"--psm {psm} --oem 3",
    ).strip()

    return texto


def extraer_variables(
    pdf_bytes: bytes,
    cajas: list[Caja],
    campos_manuales: list = None,
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

    # Agregar campos manuales con su valor por defecto
    from datetime import date
    CAMPOS_AUTOMATICOS = {
        "fecha_actual": lambda: date.today().strftime("%d/%m/%Y"),
    }

    for campo in (campos_manuales or []):
        nombre = campo.get("nombre") if isinstance(campo, dict) else campo.nombre
        valor = campo.get("valor_por_defecto", "") if isinstance(campo, dict) else campo.valor_por_defecto

        # Campos con valor automático
        if nombre in CAMPOS_AUTOMATICOS:
            valor = CAMPOS_AUTOMATICOS[nombre]()
            nota = "Generado automáticamente"
        else:
            nota = "Campo de ingreso manual"

        variables.append(Variable(
            nombre=nombre,
            valor=valor or None,
            origen="manual",
            estado="ok" if valor else "falta",
            nota=nota,
        ))

    return ResultadoExtraccion(
        plantilla_id="",  # Se llena en el router
        variables=variables,
        paginas_procesadas=num_paginas,
        advertencias=advertencias,
    )


def _limpiar_texto(texto: str) -> str:
    """
    Limpia artefactos del OCR preservando la estructura del texto.
    - Une palabras cortadas con guión al final de línea (ej: "ca-\ndáver" → "cadáver")
    - Preserva saltos de línea para mantener listas, ítems y párrafos
    - Normaliza espacios dentro de cada línea
    - Elimina caracteres no imprimibles
    """
    import re

    # 0. Corregir caracteres espaciados por OCR ("9 9 0 6 6 4" → "990664")
    texto = _corregir_caracteres_espaciados(texto)

    # 1. Unir palabras cortadas con guión al final de línea
    texto = re.sub(r"-\s*\n\s*", "", texto)

    # 2. Limpiar cada línea individualmente (espacios, no imprimibles)
    lineas = texto.replace("\r", "").split("\n")
    lineas = ["".join(c for c in linea if c.isprintable()) for linea in lineas]
    lineas = [" ".join(linea.split()) for linea in lineas]  # normalizar espacios

    # 3. Colapsar más de 2 saltos de línea consecutivos vacíos
    texto = "\n".join(lineas)
    texto = re.sub(r"\n{3,}", "\n\n", texto)

    return texto.strip()
