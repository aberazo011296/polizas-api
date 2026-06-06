"""
Almacenamiento local en JSON para la POC.

En el MVP esto se reemplaza por MongoDB — la interfaz (save/get/list/delete)
no cambia, solo la implementación.
"""
import json
import logging
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.errors import PlantillaNoEncontradaError

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


def eliminar_plantilla(plantilla_id: str) -> None:
    """Elimina una plantilla. Lanza PlantillaNoEncontradaError si no existe."""
    datos = _leer_plantillas()
    if plantilla_id not in datos:
        raise PlantillaNoEncontradaError(f"Plantilla no encontrada: {plantilla_id}")
    del datos[plantilla_id]
    _escribir_plantillas(datos)
    logger.info("Plantilla eliminada: %s", plantilla_id)
