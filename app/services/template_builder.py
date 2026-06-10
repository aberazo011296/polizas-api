"""
Construye un template .docx a partir de un documento real.

Recibe el documento con datos reales (ej: "990664") y un mapa
{nombre_variable: texto_a_reemplazar} y produce un .docx con
marcadores {{nombre_variable}} en lugar del texto original,
preservando todo el formato del documento.
"""
import io
import logging
import re
import zipfile
from copy import deepcopy

from lxml import etree

logger = logging.getLogger(__name__)

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def construir_template(docx_bytes: bytes, reemplazos: dict[str, str],
                       variables_bloque: set[str] | None = None) -> bytes:
    """
    Recibe el DOCX original y un dict {variable: texto_original}.
    Devuelve el DOCX con {{variable}} en lugar de cada texto original.
    """
    variables_bloque = variables_bloque or set()
    # Ordenar por longitud descendente para evitar reemplazos parciales
    items = sorted(reemplazos.items(), key=lambda x: len(x[1]), reverse=True)
    items = [(var, texto.strip()) for var, texto in items if texto.strip()]

    buf_in = io.BytesIO(docx_bytes)
    buf_out = io.BytesIO()

    with zipfile.ZipFile(buf_in, "r") as zin, \
         zipfile.ZipFile(buf_out, "w", zipfile.ZIP_DEFLATED) as zout:

        for item in zin.infolist():
            data = zin.read(item.filename)

            if item.filename in ("word/document.xml", "word/header1.xml",
                                  "word/footer1.xml"):
                try:
                    data = _reemplazar_en_xml(data, items)
                except Exception as e:
                    logger.warning("Error procesando %s: %s", item.filename, e)

            zout.writestr(item, data)

    return buf_out.getvalue()


def _reemplazar_en_xml(xml_bytes: bytes, items: list[tuple[str, str]]) -> bytes:
    """
    Reemplaza texto en el XML del documento fusionando runs adyacentes
    para manejar el caso en que Word divide el texto entre varios runs.
    """
    root = etree.fromstring(xml_bytes)

    # Procesar cada párrafo
    for para in root.iter(f"{{{W}}}p"):
        _reemplazar_en_parrafo(para, items)

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _reemplazar_en_parrafo(para, items: list[tuple[str, str]]):
    """
    Fusiona el texto de todos los runs del párrafo, aplica los reemplazos,
    y redistribuye el texto resultante en los runs.
    """
    runs = para.findall(f".//{{{W}}}r")
    if not runs:
        return

    # Construir texto completo del párrafo
    textos = []
    for run in runs:
        t = run.find(f"{{{W}}}t")
        textos.append(t.text or "" if t is not None else "")

    texto_completo = "".join(textos)
    texto_nuevo = texto_completo

    for variable, original in items:
        if original and original in texto_nuevo:
            texto_nuevo = texto_nuevo.replace(original, f"{{{{{variable}}}}}")

    if texto_nuevo == texto_completo:
        return  # Sin cambios, no tocar nada

    # Poner todo el texto nuevo en el primer run y vaciar los demás
    primer_run = runs[0]
    t_elem = primer_run.find(f"{{{W}}}t")
    if t_elem is None:
        t_elem = etree.SubElement(primer_run, f"{{{W}}}t")

    t_elem.text = texto_nuevo
    # Preservar espacios si el texto empieza/termina con espacio
    if texto_nuevo and (texto_nuevo[0] == " " or texto_nuevo[-1] == " "):
        t_elem.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")

    # Vaciar el resto de los runs
    for run in runs[1:]:
        t = run.find(f"{{{W}}}t")
        if t is not None:
            t.text = ""
