"""
Almacenamiento en MongoDB: colección `plantillas` + archivos .docx en GridFS.

Misma interfaz que app/storage/local.py (ver docs/adr/0001-persistencia-mongodb.md):
los routers no saben (ni les importa) qué backend está activo — el selector
en app/storage/__init__.py elige este módulo o local.py según
settings.storage_backend.
"""
import logging
from typing import Any

import gridfs

from app.core.errors import PlantillaNoEncontradaError
from app.storage.db import get_db

logger = logging.getLogger(__name__)

COLECCION_PLANTILLAS = "plantillas"


def _coleccion():
    return get_db()[COLECCION_PLANTILLAS]


def _fs() -> gridfs.GridFS:
    return gridfs.GridFS(get_db())


def _sin_id_interno(documento: dict[str, Any]) -> dict[str, Any]:
    documento = dict(documento)
    documento.pop("_id", None)
    return documento


# --- Plantillas ------------------------------------------------------------

def guardar_plantilla(plantilla_dict: dict[str, Any]) -> None:
    """Persiste una plantilla. El ID debe estar en plantilla_dict['id']."""
    documento = dict(plantilla_dict)
    documento["_id"] = documento["id"]
    _coleccion().replace_one({"_id": documento["_id"]}, documento, upsert=True)
    logger.info("Plantilla guardada: %s", documento["_id"])


def obtener_plantilla(plantilla_id: str) -> dict[str, Any]:
    """Retorna una plantilla por ID o lanza PlantillaNoEncontradaError."""
    documento = _coleccion().find_one({"_id": plantilla_id})
    if documento is None:
        raise PlantillaNoEncontradaError(f"Plantilla no encontrada: {plantilla_id}")
    return _sin_id_interno(documento)


def listar_plantillas() -> list[dict[str, Any]]:
    """Retorna todas las plantillas ordenadas por fecha de creación (desc)."""
    documentos = _coleccion().find().sort("creado_en", -1)
    return [_sin_id_interno(d) for d in documentos]


def actualizar_plantilla(plantilla_id: str, datos_nuevos: dict[str, Any]) -> dict[str, Any]:
    """Actualiza campos de una plantilla existente. Preserva id y creado_en."""
    documento = _coleccion().find_one({"_id": plantilla_id})
    if documento is None:
        raise PlantillaNoEncontradaError(f"Plantilla no encontrada: {plantilla_id}")
    documento.update(datos_nuevos)
    documento["_id"] = plantilla_id
    documento["id"] = plantilla_id
    _coleccion().replace_one({"_id": plantilla_id}, documento)
    logger.info("Plantilla actualizada: %s", plantilla_id)
    return _sin_id_interno(documento)


def eliminar_plantilla(plantilla_id: str) -> None:
    """Elimina una plantilla. Lanza PlantillaNoEncontradaError si no existe."""
    resultado = _coleccion().delete_one({"_id": plantilla_id})
    if resultado.deleted_count == 0:
        raise PlantillaNoEncontradaError(f"Plantilla no encontrada: {plantilla_id}")
    logger.info("Plantilla eliminada: %s", plantilla_id)


# --- Archivos .docx (GridFS) -------------------------------------------------
#
# GridFS permite varias versiones con el mismo "filename"; aquí se quiere
# que cada nombre lógico tenga un único contenido vigente, así que antes de
# guardar se eliminan las versiones previas.

def _reemplazar_archivo(nombre: str, contenido: bytes) -> None:
    fs = _fs()
    for previo in fs.find({"filename": nombre}):
        fs.delete(previo._id)
    fs.put(contenido, filename=nombre)


def _leer_archivo(nombre: str) -> bytes | None:
    fs = _fs()
    archivo = fs.find_one({"filename": nombre})
    if archivo is None:
        return None
    return archivo.read()


def guardar_archivo_template(aseguradora: str, tipo_poliza: str, contenido: bytes) -> None:
    """Guarda el template .docx de salida para (aseguradora, tipo_poliza)."""
    nombre = f"template_{aseguradora}_{tipo_poliza}.docx"
    _reemplazar_archivo(nombre, contenido)
    logger.info("Template guardado en GridFS: %s", nombre)


def obtener_archivo_template(aseguradora: str, tipo_poliza: str) -> bytes | None:
    """Retorna el template .docx de salida, o None si no existe."""
    nombre = f"template_{aseguradora}_{tipo_poliza}.docx"
    return _leer_archivo(nombre)


def guardar_doc_referencia(plantilla_id: str, contenido: bytes) -> None:
    """Guarda el Word de referencia usado para construir el template."""
    nombre = f"referencia_{plantilla_id}.docx"
    _reemplazar_archivo(nombre, contenido)
    logger.info("Documento de referencia guardado en GridFS: %s", nombre)


def obtener_doc_referencia(plantilla_id: str) -> bytes | None:
    """Retorna el Word de referencia de una plantilla, o None si no existe."""
    nombre = f"referencia_{plantilla_id}.docx"
    return _leer_archivo(nombre)
