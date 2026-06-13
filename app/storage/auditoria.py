"""
Auditoría de negocio: colección `auditoria` en MongoDB.

Registra eventos discretos (quién/qué/cuándo, sin PII de asegurados) para
trazabilidad LOPDP — ver docs/adr/0001-persistencia-mongodb.md.

Distinto de los logs operacionales (`logging` a stdout en app/main.py, que
van a CloudWatch): esto es de bajo volumen, retención larga, sin TTL.

`registrar()` es best-effort: si Mongo no está disponible, o si
STORAGE_BACKEND="local" (dev sin Mongo), no hace nada y no lanza — un
problema de auditoría nunca debe bloquear la operación de negocio.
"""
import logging
from datetime import datetime, timezone
from typing import Any

from pymongo.errors import PyMongoError

from app.core.config import settings
from app.storage.db import get_db

logger = logging.getLogger(__name__)

COLECCION_AUDITORIA = "auditoria"

_indices_creados = False


def _coleccion():
    global _indices_creados
    coleccion = get_db()[COLECCION_AUDITORIA]
    if not _indices_creados:
        coleccion.create_index([("plantilla_id", 1), ("timestamp", -1)])
        coleccion.create_index([("evento", 1), ("timestamp", -1)])
        _indices_creados = True
    return coleccion


def registrar(
    evento: str,
    *,
    plantilla_id: str | None = None,
    aseguradora: str | None = None,
    tipo_poliza: str | None = None,
    usuario: str | None = None,
    detalle: dict[str, Any] | None = None,
) -> None:
    """
    Registra un evento de auditoría (ej. "plantilla_creada",
    "certificado_generado"). Llamar solo tras una operación exitosa.
    """
    if settings.storage_backend != "mongo":
        return
    documento = {
        "timestamp": datetime.now(timezone.utc),
        "evento": evento,
        "plantilla_id": plantilla_id,
        "aseguradora": aseguradora,
        "tipo_poliza": tipo_poliza,
        "usuario": usuario,
        "detalle": detalle or {},
    }
    try:
        _coleccion().insert_one(documento)
    except PyMongoError as e:
        logger.error("No se pudo registrar evento de auditoría '%s': %s", evento, e)


def historial(plantilla_id: str, limite: int = 50) -> list[dict[str, Any]]:
    """Eventos de auditoría de una plantilla, más recientes primero."""
    if settings.storage_backend != "mongo":
        return []
    cursor = (
        _coleccion()
        .find({"plantilla_id": plantilla_id})
        .sort("timestamp", -1)
        .limit(limite)
    )
    eventos = []
    for doc in cursor:
        doc["_id"] = str(doc["_id"])
        eventos.append(doc)
    return eventos
