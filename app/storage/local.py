"""
Almacenamiento local en JSON + filesystem.

Backend por defecto (STORAGE_BACKEND=local). Para MongoDB + GridFS, ver
app/storage/mongo.py — misma interfaz, ver docs/adr/0001-persistencia-mongodb.md.
"""
import json
import logging
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.errors import PlantillaNoEncontradaError
from app.core.paths import ruta_template_docx

logger = logging.getLogger(__name__)

ARCHIVO_PLANTILLAS = settings.data_dir / "plantillas.json"


def _leer_plantillas() -> dict[str, Any]:
    """Lee el archivo JSON de plantillas. Devuelve dict vacío si no existe."""
    if not ARCHIVO_PLANTILLAS.exists():
        return {}
    try:
        with open(ARCHIVO_PLANTILLAS, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Error leyendo plantillas.json: %s", e)
        return {}


def _escribir_plantillas(datos: dict[str, Any]) -> None:
    """Escribe el diccionario al archivo JSON."""
    ARCHIVO_PLANTILLAS.parent.mkdir(parents=True, exist_ok=True)
    with open(ARCHIVO_PLANTILLAS, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=2, default=str)


def guardar_plantilla(plantilla_dict: dict[str, Any]) -> None:
    """Persiste una plantilla. El ID debe estar en plantilla_dict['id']."""
    datos = _leer_plantillas()
    datos[plantilla_dict["id"]] = plantilla_dict
    _escribir_plantillas(datos)
    logger.info("Plantilla guardada: %s", plantilla_dict["id"])


def obtener_plantilla(plantilla_id: str) -> dict[str, Any]:
    """Retorna una plantilla por ID o lanza PlantillaNoEncontradaError."""
    datos = _leer_plantillas()
    if plantilla_id not in datos:
        raise PlantillaNoEncontradaError(f"Plantilla no encontrada: {plantilla_id}")
    return datos[plantilla_id]


def listar_plantillas() -> list[dict[str, Any]]:
    """Retorna todas las plantillas ordenadas por fecha de creación (desc)."""
    datos = _leer_plantillas()
    plantillas = list(datos.values())
    plantillas.sort(key=lambda p: p.get("creado_en", ""), reverse=True)
    return plantillas


def actualizar_plantilla(plantilla_id: str, datos_nuevos: dict[str, Any]) -> dict[str, Any]:
    """Actualiza campos de una plantilla existente. Preserva id y creado_en."""
    datos = _leer_plantillas()
    if plantilla_id not in datos:
        raise PlantillaNoEncontradaError(f"Plantilla no encontrada: {plantilla_id}")
    plantilla = datos[plantilla_id]
    plantilla.update(datos_nuevos)
    plantilla["id"] = plantilla_id  # garantizar que el id no cambie
    datos[plantilla_id] = plantilla
    _escribir_plantillas(datos)
    logger.info("Plantilla actualizada: %s", plantilla_id)
    return plantilla


def eliminar_plantilla(plantilla_id: str) -> None:
    """Elimina una plantilla. Lanza PlantillaNoEncontradaError si no existe."""
    datos = _leer_plantillas()
    if plantilla_id not in datos:
        raise PlantillaNoEncontradaError(f"Plantilla no encontrada: {plantilla_id}")
    del datos[plantilla_id]
    _escribir_plantillas(datos)
    logger.info("Plantilla eliminada: %s", plantilla_id)


# --- Archivos .docx (filesystem) --------------------------------------------

def guardar_archivo_template(aseguradora: str, tipo_poliza: str, contenido: bytes) -> None:
    """Guarda el template .docx de salida para (aseguradora, tipo_poliza)."""
    ruta = ruta_template_docx(aseguradora, tipo_poliza)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_bytes(contenido)


def obtener_archivo_template(aseguradora: str, tipo_poliza: str) -> bytes | None:
    """Retorna el template .docx de salida, o None si no existe."""
    ruta = ruta_template_docx(aseguradora, tipo_poliza)
    if not ruta.exists():
        return None
    return ruta.read_bytes()


def guardar_doc_referencia(plantilla_id: str, contenido: bytes) -> None:
    """Guarda el Word de referencia usado para construir el template."""
    referencias_dir = settings.data_dir / "referencias"
    referencias_dir.mkdir(parents=True, exist_ok=True)
    (referencias_dir / f"{plantilla_id}.docx").write_bytes(contenido)


def obtener_doc_referencia(plantilla_id: str) -> bytes | None:
    """Retorna el Word de referencia de una plantilla, o None si no existe."""
    ruta = settings.data_dir / "referencias" / f"{plantilla_id}.docx"
    if not ruta.exists():
        return None
    return ruta.read_bytes()
