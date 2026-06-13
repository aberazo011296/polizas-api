"""
Servicio de generación de certificados individuales.

Usa docxtpl para rellenar marcadores {{nombre_variable}} en una
plantilla .docx y devuelve el documento como bytes.
"""
import io
import logging
from pathlib import Path

import io as _io
import zipfile as _zipfile
from copy import deepcopy
from lxml import etree as _etree
from docxtpl import DocxTemplate
from jinja2.sandbox import SandboxedEnvironment

# Parser endurecido para XML de .docx subidos por el usuario (anti-XXE).
_PARSER_SEGURO = _etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)

from app.core.config import settings
from app.core.errors import ErrorGeneracionDocumento, PlantillaNoEncontradaError
from app.core.paths import ruta_template_docx

logger = logging.getLogger(__name__)


_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _br_a_parrafos(docx_bytes: bytes) -> bytes:
    """
    Post-procesa el DOCX: convierte <w:br/> (saltos de línea suaves)
    en párrafos reales <w:p>, preservando el formato del párrafo original.
    Así, \\n en una variable genera párrafos separados en Word.
    """
    buf_in = _io.BytesIO(docx_bytes)
    buf_out = _io.BytesIO()

    with _zipfile.ZipFile(buf_in, "r") as zin, \
         _zipfile.ZipFile(buf_out, "w", _zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "word/document.xml":
                try:
                    data = _procesar_parrafos(data)
                except Exception as e:
                    logger.warning("Error en _br_a_parrafos: %s", e)
            zout.writestr(item, data)

    return buf_out.getvalue()


def _procesar_parrafos(xml_bytes: bytes) -> bytes:
    root = _etree.fromstring(xml_bytes, parser=_PARSER_SEGURO)
    body = root.find(f".//{{{_W}}}body")
    if body is None:
        return xml_bytes

    # Iterar sobre una copia para poder modificar body durante el bucle
    for para in list(body):
        if para.tag != f"{{{_W}}}p":
            continue
        if not _tiene_br(para):
            continue
        _dividir_parrafo(body, para)

    return _etree.tostring(root, xml_declaration=True,
                           encoding="UTF-8", standalone=True)


def _tiene_br(para) -> bool:
    """Devuelve True si el párrafo contiene algún <w:br/>."""
    for run in para.iter(f"{{{_W}}}r"):
        if run.find(f"{{{_W}}}br") is not None:
            return True
    return False


def _dividir_parrafo(body, para):
    """
    Divide un párrafo en múltiples párrafos en cada punto <w:br/>.
    Copia las propiedades del párrafo original (pPr) a cada nuevo párrafo.
    """
    ppr = para.find(f"{{{_W}}}pPr")
    idx = list(body).index(para)

    # Construir lista de segmentos: cada segmento = lista de elementos <w:r>/<w:ins>/etc.
    # Un nuevo segmento se inicia en cada <w:br/>
    segmentos = [[]]

    for child in para:
        tag = child.tag
        if tag == f"{{{_W}}}pPr":
            continue  # se copia aparte a cada párrafo nuevo

        if tag == f"{{{_W}}}r":
            rpr = child.find(f"{{{_W}}}rPr")
            partes_run = [[]]  # sub-runs separados por <w:br/>

            for rchild in child:
                if rchild.tag == f"{{{_W}}}rPr":
                    continue
                if rchild.tag == f"{{{_W}}}br":
                    partes_run.append([])   # nuevo segmento de run
                else:
                    partes_run[-1].append(deepcopy(rchild))

            for i, parte in enumerate(partes_run):
                # Agregar run con esta parte al segmento actual
                run_nuevo = _etree.Element(f"{{{_W}}}r")
                if rpr is not None:
                    run_nuevo.append(deepcopy(rpr))
                for elem in parte:
                    run_nuevo.append(elem)
                segmentos[-1].append(run_nuevo)

                # Si no es la última parte, abrir nuevo segmento (= nuevo párrafo)
                if i < len(partes_run) - 1:
                    segmentos.append([])
        else:
            # Otros elementos (bookmarks, etc.) van al segmento actual
            segmentos[-1].append(deepcopy(child))

    if len(segmentos) <= 1:
        return  # nada que dividir

    # Crear un nuevo <w:p> por cada segmento
    nuevos_paras = []
    for seg in segmentos:
        p_nuevo = _etree.Element(f"{{{_W}}}p")
        if ppr is not None:
            p_nuevo.append(deepcopy(ppr))
        for elem in seg:
            p_nuevo.append(elem)
        nuevos_paras.append(p_nuevo)

    # Reemplazar el párrafo original con los nuevos
    body.remove(para)
    for i, p in enumerate(nuevos_paras):
        body.insert(idx + i, p)


def _ruta_template_docx(aseguradora: str, tipo_poliza: str) -> Path:
    """
    Construye la ruta al archivo .docx de salida para una combinación
    aseguradora + tipo_poliza, sanitizada y contenida en templates_dir.

    Convención de nombre: {aseguradora}_{tipo_poliza}.docx
    Ejemplo: generali_desgravamen.docx
    """
    return ruta_template_docx(aseguradora, tipo_poliza)


def generar_certificado(
    aseguradora: str,
    tipo_poliza: str,
    variables: dict[str, str],
) -> tuple[bytes, list[str], list[str]]:
    """
    Genera un certificado rellenando el template .docx.

    Args:
        aseguradora: Identificador de la aseguradora.
        tipo_poliza: Tipo de póliza.
        variables: Mapa {nombre_variable: valor}.

    Returns:
        Tupla de (bytes_docx, variables_usadas, variables_faltantes)
    """
    ruta = _ruta_template_docx(aseguradora, tipo_poliza)

    if not ruta.exists():
        raise PlantillaNoEncontradaError(
            f"No se encontró el template de salida: {ruta.name}. "
            f"Coloca el archivo en {settings.templates_dir}/"
        )

    try:
        tpl = DocxTemplate(str(ruta))
    except Exception as e:
        raise ErrorGeneracionDocumento(f"Error abriendo template: {e}") from e

    # Los templates .docx los sube el usuario: renderizar en un entorno Jinja
    # con sandbox para neutralizar SSTI (acceso a __class__/__subclasses__, etc.).
    jinja_env = SandboxedEnvironment()

    # Identificar qué variables espera el template
    # (puede fallar si el XML interno del doc tiene sintaxis Jinja2 inválida)
    try:
        variables_template = set(tpl.get_undeclared_template_variables(jinja_env))
    except Exception:
        variables_template = set(variables.keys())

    variables_usadas = []
    variables_faltantes = []
    contexto = {}

    for nombre in variables_template:
        if nombre in variables and variables[nombre]:
            contexto[nombre] = variables[nombre]
            variables_usadas.append(nombre)
        else:
            contexto[nombre] = ""
            variables_faltantes.append(nombre)
            logger.warning("Variable faltante en generación: %s", nombre)

    # También pasar variables extra que no están en el template (no daña)
    for nombre, valor in variables.items():
        if nombre not in contexto:
            contexto[nombre] = valor

    try:
        tpl.render(contexto, jinja_env=jinja_env)
        buffer = io.BytesIO()
        tpl.save(buffer)
        buffer.seek(0)
        docx_bytes = _br_a_parrafos(buffer.read())
        return docx_bytes, variables_usadas, variables_faltantes
    except Exception as e:
        raise ErrorGeneracionDocumento(f"Error al renderizar template: {e}") from e
