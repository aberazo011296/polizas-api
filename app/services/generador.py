"""
Servicio de generación de certificados individuales.

Usa docxtpl para rellenar marcadores {{nombre_variable}} en una
plantilla .docx y devuelve el documento como bytes.
"""
import io
import logging
from pathlib import Path

from docxtpl import DocxTemplate

from app.core.config import settings
from app.core.errors import ErrorGeneracionDocumento, PlantillaNoEncontradaError

logger = logging.getLogger(__name__)


def _ruta_template_docx(aseguradora: str, tipo_poliza: str) -> Path:
    """
    Construye la ruta al archivo .docx de salida para una combinación
    aseguradora + tipo_poliza.

    Convención de nombre: {aseguradora}_{tipo_poliza}.docx
    Ejemplo: generali_desgravamen.docx
    """
    nombre = f"{aseguradora.lower()}_{tipo_poliza.lower()}.docx"
    return settings.templates_dir / nombre


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

    # Identificar qué variables espera el template
    # (puede fallar si el XML interno del doc tiene sintaxis Jinja2 inválida)
    try:
        variables_template = set(tpl.undeclared_template_variables)
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
        tpl.render(contexto)
        buffer = io.BytesIO()
        tpl.save(buffer)
        buffer.seek(0)
        return buffer.read(), variables_usadas, variables_faltantes
    except Exception as e:
        raise ErrorGeneracionDocumento(f"Error al renderizar template: {e}") from e
