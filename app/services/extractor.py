# -*- coding: utf-8 -*-
"""
Servicio de extraccion de campos desde PDFs escaneados.

El PDF de Generali Ecuador es un PDF de imagenes (scan), no tiene capa de texto.
Usamos PyMuPDF para rasterizar paginas y Tesseract OCR para extraer texto
de las regiones (cajas) definidas en la plantilla.
"""
import io
import logging
from pathlib import Path

import cv2
import numpy as np
import fitz  # PyMuPDF
import pytesseract
from PIL import Image

from app.core.config import settings
from app.core.errors import PDFInvalidoError
from app.models.plantilla import Caja, ResultadoExtraccion, Variable

logger = logging.getLogger(__name__)

# DPI para rasterizar paginas - 400 DPI da mejor calidad para Tesseract en textos pequeños
RENDER_DPI = 400

# Factor de escala relativo al tamanio de renderizado del frontend (PDF.js).
# PDF.js a scale=1.0 renderiza 1 punto PDF = 1 CSS pixel.
# Los PDFs miden en puntos (72 puntos por pulgada), por lo que el canvas
# del frontend equivale a 72 DPI.
FRONTEND_DPI = 72
SCALE_FACTOR = RENDER_DPI / FRONTEND_DPI  # ~5.556


def _preprocesar_imagen(img: Image.Image) -> Image.Image:
    """
    Mejora la imagen antes de OCR usando OpenCV:
    - Convierte a escala de grises
    - Aplica denoising para reducir ruido del scan
    - Umbral adaptativo (mejor que fijo para PDFs con iluminación irregular)
    - Escala x2 para dar más resolución a Tesseract
    """
    # PIL → numpy para OpenCV
    arr = np.array(img.convert("RGB"))
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)

    # Reducir ruido preservando bordes de letras
    denoised = cv2.fastNlMeansDenoising(gray, h=10, templateWindowSize=7, searchWindowSize=21)

    # Umbral adaptativo: ajusta el umbral por zonas de 31x31 píxeles
    # Mucho mejor que umbral fijo para scans con sombras o iluminación no uniforme
    binarizado = cv2.adaptiveThreshold(
        denoised,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        blockSize=31,
        C=10,
    )

    # Escalar x2 para Tesseract (más resolución = mejor lectura)
    h, w = binarizado.shape
    escalado = cv2.resize(binarizado, (w * 2, h * 2), interpolation=cv2.INTER_CUBIC)

    return Image.fromarray(escalado)


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
    """Aplica la ruta de Tesseract si esta configurada."""
    if settings.tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd


def _pagina_tiene_texto(doc: fitz.Document, pagina_idx: int) -> bool:
    """
    Detecta si una pagina del PDF tiene capa de texto embebido.
    Un PDF generado digitalmente tiene texto; un scan puro no.
    Umbral: mas de 50 caracteres imprimibles en la pagina.
    """
    page = doc[pagina_idx]
    texto = page.get_text().strip()
    chars_utiles = sum(1 for c in texto if c.isalnum())
    return chars_utiles > 50


def _extraer_texto_directo(doc: fitz.Document, pagina_idx: int, caja: Caja) -> str:
    """
    Extrae texto directamente de la capa de texto del PDF (sin OCR).
    Las coordenadas del frontend estan en puntos PDF (72 DPI = 1pt/px),
    por lo que se usan directamente como rect de PyMuPDF.

    Filtra por el centro vertical de cada linea de texto (no por
    interseccion de bounding box), para evitar arrastrar lineas
    vecinas (ej. un titulo justo encima de la caja) que apenas
    tocan el borde del rectangulo seleccionado.
    """
    page = doc[pagina_idx]
    rect = fitz.Rect(caja.x, caja.y, caja.x + caja.ancho, caja.y + caja.alto)

    lineas = []
    for block in page.get_text("dict", clip=rect)["blocks"]:
        for line in block.get("lines", []):
            bbox = line["bbox"]
            centro_y = (bbox[1] + bbox[3]) / 2
            if rect.y0 <= centro_y <= rect.y1:
                texto_linea = "".join(span["text"] for span in line["spans"])
                lineas.append(texto_linea)

    return "\n".join(lineas).strip()


def _rasterizar_pagina(doc: fitz.Document, pagina_idx: int) -> Image.Image:
    """Convierte una pagina del PDF en imagen PIL."""
    page = doc[pagina_idx]
    matriz = fitz.Matrix(RENDER_DPI / 72, RENDER_DPI / 72)
    pix = page.get_pixmap(matrix=matriz)
    return Image.open(io.BytesIO(pix.tobytes("png")))


def _extraer_texto_ocr(imagen: Image.Image, caja: Caja) -> str:
    """
    Extrae texto via OCR desde una imagen rasterizada.
    Solo se usa cuando el PDF NO tiene capa de texto.
    """
    x = int(caja.x * SCALE_FACTOR)
    y = int(caja.y * SCALE_FACTOR)
    w = int(caja.ancho * SCALE_FACTOR)
    h = int(caja.alto * SCALE_FACTOR)

    img_w, img_h = imagen.size
    x2 = min(x + w, img_w)
    y2 = min(y + h, img_h)

    if x >= img_w or y >= img_h or w <= 0 or h <= 0:
        logger.warning("Caja '%s' fuera de limites de imagen", caja.nombre)
        return ""

    recorte = imagen.crop((x, y, x2, y2))
    recorte = _preprocesar_imagen(recorte)

    # psm 7 = una linea | psm 6 = bloque uniforme de texto
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
    Procesa un PDF y extrae el valor de cada caja.
    Estrategia:
      1. Si la pagina tiene texto embebido -> extraccion directa (perfecta, sin OCR).
      2. Si es un scan (imagen) -> OCR con preprocesado avanzado.
    """
    _configurar_tesseract()

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:
        raise PDFInvalidoError(f"No se pudo abrir el PDF: {e}") from e

    num_paginas = len(doc)
    variables: list[Variable] = []
    advertencias: list[str] = []

    # Cache: para cada pagina guardamos si tiene texto y la imagen rasterizada (si es scan)
    cache_tiene_texto: dict[int, bool] = {}
    cache_imagenes: dict[int, Image.Image] = {}

    for caja in cajas:
        if caja.pagina >= num_paginas:
            advertencias.append(
                f"Caja '{caja.nombre}': pagina {caja.pagina} no existe "
                f"(el PDF tiene {num_paginas} paginas)"
            )
            variables.append(Variable(
                nombre=caja.nombre,
                valor=None,
                origen="extraido",
                estado="falta",
                nota="Pagina no encontrada en este PDF",
            ))
            continue

        # Detectar si la pagina tiene texto embebido (solo se comprueba una vez por pagina)
        if caja.pagina not in cache_tiene_texto:
            cache_tiene_texto[caja.pagina] = _pagina_tiene_texto(doc, caja.pagina)

        tiene_texto = cache_tiene_texto[caja.pagina]

        if tiene_texto:
            # Extraccion directa desde la capa de texto del PDF
            texto = _extraer_texto_directo(doc, caja.pagina, caja)
            origen = "extraido_directo"
            logger.debug("Caja '%s': extraccion directa (%d chars)", caja.nombre, len(texto))
        else:
            # Fallback a OCR para PDFs escaneados
            if caja.pagina not in cache_imagenes:
                try:
                    cache_imagenes[caja.pagina] = _rasterizar_pagina(doc, caja.pagina)
                except Exception as e:
                    logger.error("Error rasterizando pagina %d: %s", caja.pagina, e)
                    advertencias.append(f"No se pudo procesar la pagina {caja.pagina}")
                    continue
            texto = _extraer_texto_ocr(cache_imagenes[caja.pagina], caja)
            origen = "extraido"
            logger.debug("Caja '%s': OCR (%d chars)", caja.nombre, len(texto))

        if caja.nombre.startswith("listado_"):
            texto_limpio = _limpiar_listado(texto)
        else:
            texto_limpio = _limpiar_texto(texto)

        if texto_limpio:
            estado = "ok"
        else:
            estado = "falta"
            advertencias.append(f"Campo '{caja.nombre}' vacío tras OCR")

        variables.append(Variable(
            nombre=caja.nombre,
            valor=texto_limpio or None,
            origen=origen,
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


def _limpiar_listado(texto: str) -> str:
    """
    Limpieza especial para variables que empiezan con 'listado_'.
    Une líneas que son continuación del mismo ítem y solo mantiene
    \\n entre ítems nuevos (a), b), 1., -, •, etc.).
    Resultado: cada ítem en su propio párrafo, sin saltos internos.
    """
    import re

    # 1. Primero aplicar limpieza base
    texto = _corregir_caracteres_espaciados(texto)
    texto = re.sub(r"-\s*\n\s*", "", texto)  # unir palabras con guión explícito
    texto = texto.replace("\r", "")

    lineas = texto.split("\n")
    lineas = ["".join(c for c in l if c.isprintable()) for l in lineas]
    lineas = [" ".join(l.split()) for l in lineas]
    lineas = [l for l in lineas if l]  # eliminar vacías

    # 2. Patrón de inicio de ítem: a), b), 1., -, •, *, letra), o líneas
    #    tipo "Edad mínima/máxima..." (clausulas de límites de edad)
    patron_item = re.compile(
        r'^([a-zA-Z]\)|[0-9]+[\.\)]|[-•\*–]|[Ee]dad)\s'
    )

    # 3. Agrupar líneas: si la línea es inicio de ítem → nuevo ítem,
    #    si no → continúa el ítem anterior
    items = []
    item_actual = []

    for linea in lineas:
        if patron_item.match(linea):
            if item_actual:
                items.append(" ".join(item_actual))
            item_actual = [linea]
        else:
            item_actual.append(linea)

    if item_actual:
        items.append(" ".join(item_actual))

    return "\n".join(items)


def _limpiar_texto(texto: str) -> str:
    """
    Limpia artefactos del OCR y une todo el contenido en un solo párrafo.
    - Une palabras cortadas con guión explícito al final de línea
    - Une todas las líneas en un único párrafo continuo (igual que en el
      Word de referencia: párrafos largos se escriben sin saltos internos,
      el ajuste de línea es solo visual)
    - Normaliza espacios
    - Elimina caracteres no imprimibles

    Para campos donde cada línea debe ser su propio párrafo (listas con
    viñetas, ítems, etc.) usar el prefijo `listado_` en el nombre del campo.
    """
    import re

    # 0. Corregir caracteres espaciados por OCR ("9 9 0 6 6 4" → "990664")
    texto = _corregir_caracteres_espaciados(texto)

    texto = texto.replace("\r", "")

    # 1. Unir palabras cortadas con guión explícito al final de línea
    texto = re.sub(r"-\s*\n\s*", "", texto)

    # 2. Limpiar cada línea individualmente
    lineas = texto.split("\n")
    lineas = ["".join(c for c in l if c.isprintable()) for l in lineas]
    lineas = [" ".join(l.split()) for l in lineas]
    lineas = [l for l in lineas if l]

    # 3. Unir todas las líneas en un único párrafo continuo
    texto = " ".join(lineas)
    texto = " ".join(texto.split())

    return texto.strip()
