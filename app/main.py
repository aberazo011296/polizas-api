"""
polizas-api — Backend de generación de certificados de seguros.
POC Semana 1-3: Extracción OCR + generación .docx.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.routers import certificados, plantillas, polizas

# Logging básico — en producción usar structlog o similar
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Iniciando polizas-api (env=%s)", settings.app_env)
    logger.info("Directorio de uploads: %s", settings.uploads_dir)
    logger.info("Directorio de templates: %s", settings.templates_dir)
    yield
    logger.info("Cerrando polizas-api")


app = FastAPI(
    title="Pólizas API",
    description=(
        "API para convertir PDFs de pólizas de seguros en certificados individuales. "
        "POC — Generali Ecuador / Desgravamen."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — en POC permitir todo; en producción restringir a dominio del frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.app_env == "development" else [],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(polizas.router)
app.include_router(plantillas.router)
app.include_router(certificados.router)


@app.get("/", tags=["health"])
def root():
    return {"status": "ok", "version": "0.1.0", "env": settings.app_env}


@app.get("/health", tags=["health"])
def health():
    return {"status": "ok"}
