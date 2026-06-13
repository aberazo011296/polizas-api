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

# Parser endurecido para XML proveniente de .docx subidos por el usuario:
# sin entidades externas, sin DTD, sin red (defensa en profundidad anti-XXE).
_PARSER_SEGURO = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)

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

    Los textos con saltos de línea (\\n) se tratan como bloques que
    abarcan varios párrafos consecutivos: el primero se convierte en
    {{variable}} y los demás se eliminan (al generar el certificado,
    \\n en el valor vuelve a crear los párrafos necesarios).
    """
    root = etree.fromstring(xml_bytes, parser=_PARSER_SEGURO)

    items_simples = [(v, t) for v, t in items if "\n" not in t]
    items_multilinea = [(v, t) for v, t in items if "\n" in t]

    for variable, texto in items_multilinea:
        _reemplazar_bloque_multilinea(root, variable, texto)

    # Procesar cada párrafo (coincidencia exacta dentro del párrafo)
    for para in root.iter(f"{{{W}}}p"):
        _reemplazar_en_parrafo(para, items_simples)

    # Segunda pasada: lo que no se reemplazó exacto se busca con
    # comparación normalizada (sin espacios) a nivel de párrafos,
    # tolerando diferencias de espaciado entre el PDF y el Word.
    pendientes = _variables_sin_reemplazar(root, items)
    for variable, texto in pendientes:
        _reemplazar_bloque_multilinea(root, variable, texto)

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _variables_sin_reemplazar(root, items):
    """Items cuyo {{variable}} aún no aparece en el documento."""
    texto_doc = "".join(t.text or "" for t in root.iter(f"{{{W}}}t"))
    return [(v, t) for v, t in items if f"{{{{{v}}}}}" not in texto_doc]


def _texto_de_parrafo(para) -> str:
    """Texto plano completo de un párrafo (todos sus runs concatenados)."""
    return "".join(
        (t.text or "")
        for t in para.findall(f".//{{{W}}}t")
    )


def _normalizar(texto: str) -> str:
    """
    Elimina todo el espaciado para comparar textos sin depender del
    espaciado exacto (el PDF y el Word difieren en cosas como
    "ingreso :" vs "ingreso:").
    """
    return re.sub(r"\s+", "", texto).lower()


def _reemplazar_bloque_multilinea(root, variable: str, texto: str):
    """
    Busca una secuencia de párrafos consecutivos cuyo texto combinado
    coincida (normalizado, en orden) con las líneas de `texto`. Cada
    línea puede abarcar uno o varios párrafos. Reemplaza el primero por
    {{variable}} y elimina los siguientes.
    """
    lineas = [_normalizar(l) for l in texto.split("\n") if l.strip()]
    lineas = [l for l in lineas if l]
    if not lineas:
        return

    for body in root.iter():
        paras = [h for h in body if h.tag == f"{{{W}}}p"]
        if not paras:
            continue
        textos = [_normalizar(_texto_de_parrafo(p)) for p in paras]

        for inicio in range(len(paras)):
            consumidos = _match_bloque(textos, inicio, lineas)
            if consumidos:
                _poner_texto_en_parrafo(paras[inicio], f"{{{{{variable}}}}}")
                for p in paras[inicio + 1:inicio + consumidos]:
                    body.remove(p)
                return  # solo el primer match


def _match_bloque(textos: list[str], inicio: int, lineas: list[str]) -> int:
    """
    Intenta consumir las `lineas` empezando en textos[inicio], donde cada
    línea puede ocupar uno o varios párrafos consecutivos (concatenados).
    Devuelve cuántos párrafos consume el bloque completo, o 0 si no calza.
    """
    i = inicio
    for linea in lineas:
        acumulado = ""
        while i < len(textos) and len(acumulado) < len(linea):
            if textos[i] == "":  # párrafos vacíos intermedios se saltan
                i += 1
                continue
            acumulado += textos[i]
            i += 1
        if acumulado != linea:
            return 0
    return i - inicio


def _poner_texto_en_parrafo(para, texto: str):
    """Pone `texto` en el primer run del párrafo y vacía los demás."""
    runs = para.findall(f".//{{{W}}}r")
    if not runs:
        return
    t_elem = runs[0].find(f"{{{W}}}t")
    if t_elem is None:
        t_elem = etree.SubElement(runs[0], f"{{{W}}}t")
    t_elem.text = texto
    for run in runs[1:]:
        t = run.find(f"{{{W}}}t")
        if t is not None:
            t.text = ""


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
