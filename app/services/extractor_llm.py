# -*- coding: utf-8 -*-
"""
Extracción automática de variables usando la API de Claude.

A diferencia del extractor por cajas (coordenadas), este servicio le pasa
TODO el texto del PDF al modelo junto con la lista de variables de la
plantilla, y el modelo localiza y copia cada sección literalmente.
Funciona aunque el PDF tenga otro layout, otro número de páginas o
encabezados distintos.

La misma limpieza de siempre se aplica después (listado_ → un párrafo por
ítem; resto → un solo párrafo), así el certificado sale idéntico.
"""
import json
import logging
import re

import fitz  # PyMuPDF
from anthropic import Anthropic

from app.core.config import settings
from app.core.errors import PDFInvalidoError
from app.models.plantilla import Caja, ResultadoExtraccion, Variable
from app.services.extractor import _limpiar_listado, _limpiar_texto

logger = logging.getLogger(__name__)

# Descripciones que ayudan al modelo a entender qué buscar.
# Si una variable no está aquí, se usa su nombre como pista.
DESCRIPCIONES = {
    "numero_poliza": "el número de la póliza (ej: 990664)",
    "fecha_inicio": "fecha de inicio de vigencia, en el formato que aparece (ej: 01/Feb/2026)",
    "fecha_fin": "fecha de fin de vigencia, en el formato que aparece",
    "cobertura_muerte_cualquier_causa": "el nombre de la cobertura de muerte por cualquier causa tal como aparece en el cuadro de coberturas",
    "cobertura_incapacidad": "el nombre de la cobertura de incapacidad total y permanente tal como aparece en el cuadro de coberturas",
    "suma_asegurada": "el monto máximo asegurado por persona, solo la cifra sin símbolo de moneda (ej: 200,000.00)",
    "mcc": "el párrafo completo que describe la cobertura/definición de muerte por cualquier causa",
    "exclusiones": "la lista completa de exclusiones de la cobertura de muerte (cada exclusión es un ítem)",
    "incapacidad_total_parte1": "la primera parte del texto que describe la cobertura de incapacidad total y permanente",
    "incapacidad_total_parte2": "la continuación del texto de incapacidad total y permanente (condiciones, plazos, exclusiones propias)",
    "listado_limites_edad_mcc": "los límites de edad para la cobertura de muerte (edad mínima de ingreso, máxima de ingreso, permanencia), una condición por línea",
    "doc_requeridos_mcc": "la lista de documentos requeridos en caso de siniestro por muerte",
    "doc_req_incapacidad": "la lista de documentos requeridos en caso de siniestro por incapacidad",
}

PROMPT_SISTEMA = """Eres un asistente que extrae secciones de pólizas de seguros.
Recibes el texto completo de una póliza y una lista de variables a extraer.

Reglas estrictas:
1. COPIA el texto LITERALMENTE del documento. No parafrasees, no resumas, no corrijas ortografía.
2. Une las líneas que el PDF corta por ancho de página (incluyendo palabras divididas con guión, ej: "to-\\ntal" → "total"), pero conserva un salto de línea (\\n) entre ítems distintos de una lista o entre condiciones distintas (ej: cada exclusión o cada límite de edad en su propia línea).
3. No incluyas el título/encabezado de la sección, solo su contenido.
4. Si la descripción de una variable pide un dato puntual (ej: "solo el nombre", "solo la
cifra", "solo la fecha"), devuelve exactamente ese dato, sin texto adicional alrededor.
5. Si una variable no existe en este documento, devuelve null para esa variable.
6. Responde ÚNICAMENTE con un objeto JSON {"nombre_variable": "texto" | null, ...}, sin explicaciones."""


def _texto_completo(pdf_bytes: bytes) -> tuple[str, int]:
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:
        raise PDFInvalidoError(f"No se pudo abrir el PDF: {e}") from e
    paginas = [p.get_text() for p in doc]
    n = len(doc)
    doc.close()
    texto = "\n".join(paginas)
    if sum(c.isalnum() for c in texto) < 200:
        raise PDFInvalidoError(
            "El PDF no tiene capa de texto (es un escaneo); "
            "la extracción automática requiere texto embebido"
        )
    return texto, n


def _normalizar(texto: str) -> str:
    """
    Elimina todo el espaciado y une guiones de corte para comparar.
    El PDF y el modelo pueden diferir en espacios (ej. "ingreso : 18"
    vs "ingreso: 18"), así que se comparan sin espacios.
    """
    texto = re.sub(r"-\s*\n\s*", "", texto)
    return re.sub(r"\s+", "", texto).lower()


def _palabras_presentes(linea: str, pdf_norm: str, umbral: float = 0.9) -> bool:
    """True si al menos `umbral` de las palabras de la línea aparecen en el PDF."""
    palabras = [p.lower() for p in re.findall(r"[\wáéíóúñü]+", linea) if len(p) > 2]
    if not palabras:
        return True
    presentes = sum(1 for p in palabras if p in pdf_norm)
    return presentes / len(palabras) >= umbral


def extraer_variables_llm(
    pdf_bytes: bytes,
    definiciones: list[dict],
    campos_manuales: list = None,
    coberturas_campos: list[dict] = None,
) -> ResultadoExtraccion:
    """
    Extrae las variables usando Claude sobre el texto completo del PDF.

    `definiciones` es una lista de {"nombre": ..., "descripcion": ...}.
    Si una definición no trae descripción, se usa el diccionario
    DESCRIPCIONES (plantillas antiguas) o el nombre como pista.

    `coberturas_campos` (opcional): sub-campos de UNA cobertura. Si se pasa,
    se pide al modelo una LISTA de coberturas (una por fila del cuadro de
    coberturas de la póliza) y se devuelve en ResultadoExtraccion.coberturas.

    Verifica que cada valor devuelto exista realmente en el documento;
    si no, lo marca como 'dudoso'.
    """
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY no configurada")

    texto_pdf, num_paginas = _texto_completo(pdf_bytes)

    lista_vars = "\n".join(
        "- {}: {}".format(
            d["nombre"],
            d.get("descripcion")
            or DESCRIPCIONES.get(d["nombre"], d["nombre"].replace("_", " ")),
        )
        for d in definiciones
    )

    coberturas_campos = coberturas_campos or []
    instruccion_coberturas = ""
    if coberturas_campos:
        campos_desc = "\n".join(
            "    - {}: {}".format(
                c["nombre"],
                c.get("descripcion") or c["nombre"].replace("_", " "),
            )
            for c in coberturas_campos
        )
        instruccion_coberturas = (
            "\n\nAdemás, la póliza tiene un CUADRO DE COBERTURAS con una o más "
            "coberturas (ej: muerte, incapacidad/discapacidad, anticipo de "
            "enfermedades graves, gastos funerarios…). Devuelve una entrada por "
            "CADA cobertura que realmente exista en este documento, en una clave "
            "\"coberturas\" cuyo valor es una lista de objetos. Cada objeto tiene "
            "estos sub-campos:\n" + campos_desc +
            "\nReglas para llenar los sub-campos de cada cobertura:\n"
            "- Si una cobertura tiene su propia sección TITULADA con su nombre "
            "(ej: 'LIMITES DE EDAD MUERTE…', 'Exclusiones de Incapacidad…'), usa "
            "esa.\n"
            "- Si una cobertura NO tiene su sección propia titulada, pero existe "
            "una sección GENÉRICA o COMPARTIDA sobre ese tema (ej: un bloque "
            "'LIMITES DE EDAD:' sin nombre de cobertura, o condiciones de edad "
            "generales que aparecen una sola vez), usa ESE bloque genérico como "
            "valor para esa cobertura. Es común que a una cobertura (típicamente "
            "la principal, muerte por cualquier causa) se le 'olvide' el título y "
            "sus datos queden en la sección genérica: en ese caso ASÍGNALE la "
            "sección genérica en lugar de dejarla vacía.\n"
            "- Solo usa null si de verdad no hay ningún dato (ni propio ni "
            "genérico) para ese sub-campo. No omitas la cobertura.\n"
            "- El valor de cada sub-campo es SIEMPRE un string (texto), nunca un "
            "array. Para los sub-campos que empiezan con 'listado_', pon cada "
            "ítem en su propia línea separándolos con saltos de línea (\\n) "
            "dentro del mismo string, no como lista JSON.\n"
            "No inventes coberturas que no estén en el cuadro."
        )

    client = Anthropic(api_key=settings.anthropic_api_key)
    respuesta = client.messages.create(
        model=settings.anthropic_model,
        max_tokens=8000,
        system=PROMPT_SISTEMA,
        messages=[{
            "role": "user",
            "content": (
                f"Variables a extraer:\n{lista_vars}{instruccion_coberturas}\n\n"
                f"Texto de la póliza:\n<<<\n{texto_pdf}\n>>>"
            ),
        }],
    )

    bruto = respuesta.content[0].text.strip()
    # Tolerar que el modelo envuelva el JSON en ```json ... ```
    bruto = re.sub(r"^```(?:json)?\s*|\s*```$", "", bruto)
    try:
        valores = json.loads(bruto)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Respuesta de IA no es JSON válido: {e}") from e

    pdf_norm = _normalizar(texto_pdf)
    variables: list[Variable] = []
    advertencias: list[str] = []

    for d in definiciones:
        nombre_var = d["nombre"]
        crudo = valores.get(nombre_var)
        if not crudo or not str(crudo).strip():
            variables.append(Variable(
                nombre=nombre_var,
                valor=None,
                origen="extraido_ia",
                estado="falta",
                nota="No se encontró esta sección en el PDF",
            ))
            advertencias.append(f"Campo '{nombre_var}' no encontrado en este PDF")
            continue

        crudo = str(crudo)
        if nombre_var.startswith("listado_"):
            limpio = _limpiar_listado(crudo)
        else:
            limpio = _limpiar_texto(crudo)

        # Verificación anti-invención: cada línea debe existir contigua en
        # el PDF, o (para tablas, donde las columnas se intercalan al
        # extraer el texto) al menos el 90% de sus palabras deben aparecer.
        lineas = [l for l in crudo.split("\n") if l.strip()]
        faltan = [
            l for l in lineas
            if _normalizar(l) not in pdf_norm and not _palabras_presentes(l, pdf_norm)
        ]
        if faltan:
            estado, nota = "dudoso", "Verificar: el texto no coincide literalmente con el PDF"
            advertencias.append(f"Campo '{nombre_var}' requiere revisión manual")
        else:
            estado, nota = "ok", None

        variables.append(Variable(
            nombre=nombre_var,
            valor=limpio or None,
            origen="extraido_ia",
            estado=estado,
            nota=nota,
        ))

    # Campos manuales: misma lógica que el extractor por cajas
    from datetime import date
    CAMPOS_AUTOMATICOS = {
        "fecha_actual": lambda: date.today().strftime("%d/%m/%Y"),
    }
    for campo in (campos_manuales or []):
        nombre = campo.get("nombre") if isinstance(campo, dict) else campo.nombre
        valor = campo.get("valor_por_defecto", "") if isinstance(campo, dict) else campo.valor_por_defecto
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

    # Coberturas: lista de objetos, una por fila del cuadro de coberturas.
    # Cada sub-campo se limpia con la misma lógica que las variables planas
    # (listado_ → un párrafo por ítem; resto → un solo párrafo).
    coberturas: list[dict] = []
    if coberturas_campos:
        crudas = valores.get("coberturas")
        if isinstance(crudas, list):
            nombres_campos = [c["nombre"] for c in coberturas_campos]
            for item in crudas:
                if not isinstance(item, dict):
                    continue
                limpio_item: dict = {}
                for campo in nombres_campos:
                    valor_campo = item.get(campo)
                    # El modelo a veces devuelve los listado_ como array JSON
                    # (["a", "b"]) en vez de string; unirlos con saltos de línea
                    # para no terminar con el repr de lista "['a', 'b']".
                    if isinstance(valor_campo, list):
                        valor_campo = "\n".join(
                            str(x).strip() for x in valor_campo
                            if x is not None and str(x).strip()
                        )
                    if valor_campo is None or not str(valor_campo).strip():
                        limpio_item[campo] = ""
                        continue
                    valor_campo = str(valor_campo)
                    if campo.startswith("listado_"):
                        limpio_item[campo] = _limpiar_listado(valor_campo) or ""
                    else:
                        limpio_item[campo] = _limpiar_texto(valor_campo) or ""
                # Solo conservar coberturas con al menos un sub-campo con valor
                if any(v for v in limpio_item.values()):
                    coberturas.append(limpio_item)
        else:
            advertencias.append("No se encontró el cuadro de coberturas en este PDF")

    return ResultadoExtraccion(
        plantilla_id="",
        variables=variables,
        paginas_procesadas=num_paginas,
        advertencias=advertencias,
        coberturas=coberturas,
    )


PROMPT_SUGERENCIA = """Eres un asistente que analiza pólizas de seguros para configurar \
un sistema de generación de certificados individuales.

Recibes el texto completo de una póliza. Tu tarea: proponer cómo extraer sus datos,
separando DOS grupos:

A) "variables": datos a nivel de la PÓLIZA (uno solo por póliza). Ej: número de \
póliza, vigencia (inicio/fin), contratante, beneficiario/acreedor, número de endoso. \
NO incluyas aquí nada que se repita por cobertura.

B) "coberturas_campos": los SUB-CAMPOS de UNA cobertura, cuando la póliza tiene un \
CUADRO DE COBERTURAS con una o más coberturas (ej: Muerte por cualquier causa, \
Incapacidad/Discapacidad, Anticipo de Enfermedades Graves, Gastos funerarios…). \
En vez de crear variables planas por cada cobertura (descripcion_muerte, \
descripcion_incapacidad…), define UNA sola vez los sub-campos que tiene CUALQUIER \
cobertura, y el sistema los repetirá por cada cobertura del cuadro (2, 3 o N). \
Sub-campos típicos: nombre, suma_asegurada, descripcion, listado_exclusiones, \
listado_limites_edad, listado_docs_siniestro. Si la póliza NO tiene un cuadro de \
coberturas repetible, devuelve "coberturas_campos": [].

Reglas de nombres y descripciones (aplican a ambos grupos):
1. Nombres en snake_case, cortos y descriptivos (ej: numero_poliza, fecha_inicio).
2. La descripción dice QUÉ es el dato, cómo reconocerlo y QUÉ devolver exactamente. \
Para datos puntuales empieza con "solo": "solo la cifra del monto sin símbolo (ej: \
200,000.00)", "solo la fecha". Para sub-campos de cobertura, descríbelos de forma \
GENÉRICA (válida para cualquier cobertura), no atada al nombre de una en particular: \
bien: "el párrafo que define esta cobertura"; mal: "la definición de muerte".
3. Prefijo "listado_" cuando cada ítem debe quedar en su propio párrafo (exclusiones, \
límites de edad, documentos requeridos).
4. No incluyas datos del asegurado individual (nombre, cédula, fecha de nacimiento) — \
esos se llenan a mano.
5. En "variables", entre 5 y 15 datos de póliza. En "coberturas_campos", entre 3 y 7 \
sub-campos. Omite lo que no aparezca en este documento.
6. Responde ÚNICAMENTE con un objeto JSON con esta forma exacta:
{"variables": [{"nombre": "...", "descripcion": "..."}, ...], \
"coberturas_campos": [{"nombre": "...", "descripcion": "..."}, ...]}"""


def _limpiar_defs(crudas) -> list[dict]:
    """Normaliza una lista de {nombre, descripcion} sugerida por la IA."""
    limpias = []
    for s in crudas or []:
        if not isinstance(s, dict):
            continue
        nombre = re.sub(r"[^a-z0-9_]", "",
                        str(s.get("nombre", "")).strip().lower().replace(" ", "_"))
        if nombre:
            limpias.append({"nombre": nombre,
                            "descripcion": str(s.get("descripcion", "")).strip()})
    return limpias


def sugerir_variables(pdf_bytes: bytes) -> dict:
    """
    Analiza un PDF de ejemplo y propone:
      - variables: datos a nivel póliza {nombre, descripcion}
      - coberturas_campos: sub-campos de UNA cobertura (si hay cuadro repetible)
    La persona luego edita ambas listas. Devuelve un dict con ambas claves.

    Tolera que la IA devuelva el formato antiguo (un array plano de variables):
    en ese caso coberturas_campos queda vacío.
    """
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY no configurada")

    texto_pdf, _ = _texto_completo(pdf_bytes)

    client = Anthropic(api_key=settings.anthropic_api_key)
    respuesta = client.messages.create(
        model=settings.anthropic_model,
        max_tokens=4000,
        system=PROMPT_SUGERENCIA,
        messages=[{
            "role": "user",
            "content": f"Texto de la póliza:\n<<<\n{texto_pdf}\n>>>",
        }],
    )

    bruto = respuesta.content[0].text.strip()
    bruto = re.sub(r"^```(?:json)?\s*|\s*```$", "", bruto)
    try:
        data = json.loads(bruto)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Respuesta de IA no es JSON válido: {e}") from e

    # Formato nuevo: dict con variables + coberturas_campos.
    # Formato antiguo (compatibilidad): array plano = solo variables.
    if isinstance(data, list):
        return {"variables": _limpiar_defs(data), "coberturas_campos": []}
    return {
        "variables": _limpiar_defs(data.get("variables")),
        "coberturas_campos": _limpiar_defs(data.get("coberturas_campos")),
    }
