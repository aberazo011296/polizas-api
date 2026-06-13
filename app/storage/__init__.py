"""
Selector de backend de persistencia según settings.storage_backend.

- "local" (default): plantillas.json + filesystem (app/storage/local.py).
- "mongo": colección `plantillas` + GridFS (app/storage/mongo.py).

Los routers importan desde aquí (`from app.storage import ...`), nunca
directamente de local.py/mongo.py, para que el backend sea intercambiable
por configuración. Ver docs/adr/0001-persistencia-mongodb.md.
"""
from app.core.config import settings

if settings.storage_backend == "mongo":
    from app.storage.mongo import (
        actualizar_plantilla,
        eliminar_plantilla,
        guardar_archivo_template,
        guardar_doc_referencia,
        guardar_plantilla,
        listar_plantillas,
        obtener_archivo_template,
        obtener_doc_referencia,
        obtener_plantilla,
    )
else:
    from app.storage.local import (
        actualizar_plantilla,
        eliminar_plantilla,
        guardar_archivo_template,
        guardar_doc_referencia,
        guardar_plantilla,
        listar_plantillas,
        obtener_archivo_template,
        obtener_doc_referencia,
        obtener_plantilla,
    )

__all__ = [
    "actualizar_plantilla",
    "eliminar_plantilla",
    "guardar_archivo_template",
    "guardar_doc_referencia",
    "guardar_plantilla",
    "listar_plantillas",
    "obtener_archivo_template",
    "obtener_doc_referencia",
    "obtener_plantilla",
]
