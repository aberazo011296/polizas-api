"""
Construcción segura de rutas de archivos derivadas de datos del usuario.

`aseguradora` y `tipo_poliza` son texto libre de la plantilla; se usan para
nombrar el template .docx de salida. Sin sanitizar permiten path traversal
(`../../`) hacia escrituras/lecturas fuera de `templates_dir`.
"""
import re
from pathlib import Path

from app.core.config import settings

_SLUG_INVALIDO = re.compile(r"[^a-z0-9_-]+")


def slug(texto: str) -> str:
    """Reduce un texto a un slug seguro para nombres de archivo: [a-z0-9_-]."""
    limpio = _SLUG_INVALIDO.sub("-", texto.lower()).strip("-_")
    return limpio


def ruta_template_docx(aseguradora: str, tipo_poliza: str) -> Path:
    """
    Ruta al template .docx de salida para (aseguradora, tipo_poliza),
    garantizada dentro de `templates_dir`.

    Lanza ValueError si los valores no producen un nombre válido o si la
    ruta resuelta escapa del directorio de templates.
    """
    a, t = slug(aseguradora), slug(tipo_poliza)
    if not a or not t:
        raise ValueError("aseguradora/tipo_poliza inválidos para nombre de archivo")

    base = settings.templates_dir.resolve()
    ruta = (base / f"{a}_{t}.docx").resolve()
    if not ruta.is_relative_to(base):
        raise ValueError("Ruta de template fuera del directorio permitido")
    return ruta
