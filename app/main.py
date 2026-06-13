"""
polizas-api — Backend de generación de certificados de seguros.
POC Semana 1-3: Extracción OCR + generación .docx.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.security import verificar_token
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

# CORS — orígenes permitidos según el entorno (config: CORS_ORIGINS).
# El WebView del host usa un origin conocido; nada de wildcard.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Autenticación global: el host (WebView) inyecta `Authorization: Bearer`.
# Si API_TOKEN no está configurado, verificar_token no exige nada (dev).
# Los endpoints de salud quedan fuera para health-checks de infraestructura.
_auth = [Depends(verificar_token)]
app.include_router(polizas.router, dependencies=_auth)
app.include_router(plantillas.router, dependencies=_auth)
app.include_router(certificados.router, dependencies=_auth)


@app.get("/", tags=["health"])
def root():
    return {"status": "ok", "version": "0.1.0", "env": settings.app_env}


@app.get("/health", tags=["health"])
def health():
    return {"status": "ok"}
