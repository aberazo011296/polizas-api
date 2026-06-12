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
) -> ResultadoExtraccion:
    """
    Extrae las variables usando Claude sobre el texto completo del PDF.

    `definiciones` es una lista de {"nombre": ..., "descripcion": ...}.
    Si una definición no trae descripción, se usa el diccionario
    DESCRIPCIONES (plantillas antiguas) o el nombre como pista.

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

    client = Anthropic(api_key=settings.anthropic_api_key)
    respuesta = client.messages.create(
        model=settings.anthropic_model,
        max_tokens=8000,
        system=PROMPT_SISTEMA,
        messages=[{
            "role": "user",
            "content": (
                f"Variables a extraer:\n{lista_vars}\n\n"
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

    return ResultadoExtraccion(
        plantilla_id="",
        variables=variables,
        paginas_procesadas=num_paginas,
        advertencias=advertencias,
    )


PROMPT_SUGERENCIA = """Eres un asistente que analiza pólizas de seguros para configurar \
un sistema de generación de certificados individuales.

Recibes el texto completo de una póliza. Tu tarea: proponer la lista de variables \
(datos y secciones) que habría que extraer de pólizas como esta para llenar un \
certificado individual.

Reglas:
1. Nombres en snake_case, cortos y descriptivos (ej: numero_poliza, fecha_inicio, exclusiones).
2. UNA variable = UN solo dato puntual o UNA sola sección de texto. Nunca agrupes varios \
datos en una variable (mal: "coberturas" con toda la tabla; bien: una variable por el \
nombre de cada cobertura y otra por su monto).
2b. Cuando algo existe por cada cobertura (descripción, exclusiones, límites de edad, \
documentos), usa un sufijo descriptivo derivado del nombre de la cobertura, nunca números: \
bien: descripcion_muerte, exclusiones_discapacidad, docs_siniestro_muerte; \
mal: descripcion_cobertura_1, exclusiones_cobertura_2.
3. La descripción debe decir QUÉ es el dato, cómo reconocerlo en el documento, y QUÉ \
devolver exactamente. Para datos puntuales empieza la descripción con "solo": \
"solo el nombre de la cobertura tal como aparece (ej: Muerte por cualquier causa)", \
"solo la cifra del monto sin símbolo de moneda (ej: 200,000.00)", "solo la fecha".
4. Si una sección es una lista de condiciones cortas donde cada línea debe quedar en su \
propio párrafo (ej: límites de edad), antepone "listado_" al nombre.
5. Incluye los datos típicos de un certificado: identificación de la póliza, vigencia, \
contratante/beneficiario, el nombre de cada cobertura por separado, el monto asegurado, \
descripción de cada cobertura, límites de edad, exclusiones y documentos requeridos en \
caso de siniestro. Omite lo que no aparezca en este documento.
5b. OBLIGATORIO: por CADA cobertura que aparezca bajo "DEFINICIONES" (o sección \
equivalente), crea una variable descripcion_<cobertura> cuyo valor sea el párrafo \
completo que define esa cobertura (el texto que va entre el título de la cobertura y \
sus "Exclusiones"). Ejemplo: descripcion_muerte = "La Compañía indemnizará al \
Contratante hasta el valor asegurado...". Nunca omitas estas definiciones aunque la \
lista de variables quede larga.
6. Entre 8 y 30 variables; la cobertura completa de las secciones (definiciones, \
exclusiones, límites) tiene prioridad sobre quedarse corto. No incluyas datos del \
asegurado individual (nombre, cédula) — esos se llenan a mano.
7. Responde ÚNICAMENTE con un array JSON: [{"nombre": "...", "descripcion": "..."}, ...]"""


def sugerir_variables(pdf_bytes: bytes) -> list[dict]:
    """
    Analiza un PDF de ejemplo y propone variables {nombre, descripcion}
    para crear una plantilla. La persona luego edita la lista.
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
        sugeridas = json.loads(bruto)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Respuesta de IA no es JSON válido: {e}") from e

    limpias = []
    for s in sugeridas:
        nombre = re.sub(r"[^a-z0-9_]", "", str(s.get("nombre", "")).strip().lower().replace(" ", "_"))
        if nombre:
            limpias.append({"nombre": nombre, "descripcion": str(s.get("descripcion", "")).strip()})
    return limpias
