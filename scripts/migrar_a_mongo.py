"""
Migración one-shot: data/plantillas.json + templates/*.docx +
data/referencias/*.docx -> MongoDB (colección `plantillas` + GridFS).

Uso (con Mongo levantado, ver docker-compose.yml):

    venv/bin/python -m scripts.migrar_a_mongo

Lee MONGO_URI/MONGO_DB_NAME del .env (igual que la app) sin importar el
valor de STORAGE_BACKEND, para poder preparar Mongo antes de cambiar el
backend en producción. Es idempotente: se puede correr varias veces.
"""
import json
import logging
from datetime import datetime
from pathlib import Path

from app.core.config import settings
from app.core.paths import ruta_template_docx
from app.storage.mongo import guardar_archivo_template, guardar_doc_referencia, guardar_plantilla

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

ARCHIVO_PLANTILLAS = settings.data_dir / "plantillas.json"
REFERENCIAS_DIR = settings.data_dir / "referencias"


def _parsear_fecha(valor):
    """plantillas.json guarda creado_en como string ISO; Mongo prefiere datetime."""
    if isinstance(valor, str):
        try:
            return datetime.fromisoformat(valor)
        except ValueError:
            return valor
    return valor


def migrar():
    if not ARCHIVO_PLANTILLAS.exists():
        logger.warning("No existe %s, nada que migrar.", ARCHIVO_PLANTILLAS)
        return

    with open(ARCHIVO_PLANTILLAS, "r", encoding="utf-8") as f:
        plantillas = json.load(f)

    logger.info("Migrando %d plantillas a Mongo (%s/%s)...",
                 len(plantillas), settings.mongo_uri, settings.mongo_db_name)

    for plantilla_id, datos in plantillas.items():
        datos = dict(datos)
        datos["id"] = plantilla_id
        if "creado_en" in datos:
            datos["creado_en"] = _parsear_fecha(datos["creado_en"])

        guardar_plantilla(datos)
        logger.info("  plantilla %s (%s) -> colección 'plantillas'", plantilla_id, datos.get("nombre"))

        aseguradora = datos.get("aseguradora")
        tipo_poliza = datos.get("tipo_poliza")
        if aseguradora and tipo_poliza:
            ruta_template = ruta_template_docx(aseguradora, tipo_poliza)
            if ruta_template.exists():
                guardar_archivo_template(aseguradora, tipo_poliza, ruta_template.read_bytes())
                logger.info("    template %s -> GridFS", ruta_template.name)

        ruta_referencia = REFERENCIAS_DIR / f"{plantilla_id}.docx"
        if ruta_referencia.exists():
            guardar_doc_referencia(plantilla_id, ruta_referencia.read_bytes())
            logger.info("    referencia %s -> GridFS", ruta_referencia.name)

    logger.info("Migración completa.")


if __name__ == "__main__":
    migrar()
