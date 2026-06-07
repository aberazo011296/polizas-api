"""
Router: /plantillas

CRUD de plantillas de extracción.
"""
import logging
from fastapi import APIRouter, File, HTTPException, UploadFile, status

from app.core.config import settings
from app.core.errors import PlantillaNoEncontradaError
from app.models.plantilla import Plantilla, PlantillaCrear, PlantillaResumen
from app.storage.local import (
    actualizar_plantilla,
    eliminar_plantilla,
    guardar_plantilla,
    listar_plantillas,
    obtener_plantilla,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/plantillas", tags=["plantillas"])


@router.get(
    "",
    summary="Listar todas las plantillas",
    response_model=list[PlantillaResumen],
)
def listar():
    """Retorna todas las plantillas ordenadas por fecha de creación."""
    plantillas = listar_plantillas()
    return [
        PlantillaResumen(
            id=p["id"],
            nombre=p["nombre"],
            aseguradora=p["aseguradora"],
            tipo_poliza=p["tipo_poliza"],
            num_variables=len(p["cajas"]),
            creado_en=p["creado_en"],
        )
        for p in plantillas
    ]


@router.post(
    "",
    summary="Crear nueva plantilla",
    response_model=Plantilla,
    status_code=status.HTTP_201_CREATED,
)
def crear(body: PlantillaCrear):
    """
    Guarda una nueva plantilla con sus cajas de extracción.

    Las coordenadas de las cajas deben estar en pixels del canvas
    frontend (PDF.js a escala 1.0, ~96 DPI).
    """
    # Validar que no haya nombres de variable duplicados
    nombres = [c.nombre for c in body.cajas]
    if len(nombres) != len(set(nombres)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Hay nombres de variable duplicados en las cajas",
        )

    plantilla = Plantilla(**body.model_dump())
    guardar_plantilla(plantilla.model_dump())
    logger.info("Nueva plantilla creada: %s (%s)", plantilla.nombre, plantilla.id)
    return plantilla


@router.get(
    "/{plantilla_id}",
    summary="Obtener plantilla por ID",
    response_model=Plantilla,
)
def obtener(plantilla_id: str):
    try:
        return obtener_plantilla(plantilla_id)
    except PlantillaNoEncontradaError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Plantilla '{plantilla_id}' no encontrada",
        )


@router.put(
    "/{plantilla_id}",
    summary="Actualizar plantilla existente",
    response_model=Plantilla,
)
def actualizar(plantilla_id: str, body: PlantillaCrear):
    nombres = [c.nombre for c in body.cajas]
    if len(nombres) != len(set(nombres)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Hay nombres de variable duplicados en las cajas",
        )
    try:
        datos = body.model_dump()
        plantilla = actualizar_plantilla(plantilla_id, datos)
        return plantilla
    except PlantillaNoEncontradaError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Plantilla '{plantilla_id}' no encontrada",
        )


@router.post(
    "/{plantilla_id}/template",
    summary="Subir template .docx para una plantilla",
    status_code=status.HTTP_200_OK,
)
async def subir_template(plantilla_id: str, archivo: UploadFile = File(...)):
    """
    Guarda el archivo .docx que se usará como template de certificado.
    El nombre del archivo se construye como {aseguradora}_{tipo_poliza}.docx.
    """
    try:
        plantilla = obtener_plantilla(plantilla_id)
    except PlantillaNoEncontradaError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Plantilla '{plantilla_id}' no encontrada",
        )

    if not archivo.filename.endswith(".docx"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El archivo debe ser un .docx",
        )

    nombre_archivo = f"{plantilla['aseguradora'].lower()}_{plantilla['tipo_poliza'].lower()}.docx"
    settings.templates_dir.mkdir(parents=True, exist_ok=True)
    ruta = settings.templates_dir / nombre_archivo

    contenido = await archivo.read()
    ruta.write_bytes(contenido)

    logger.info("Template subido: %s", nombre_archivo)
    return {"archivo": nombre_archivo}


@router.delete(
    "/{plantilla_id}",
    summary="Eliminar plantilla",
    status_code=status.HTTP_204_NO_CONTENT,
)
def eliminar(plantilla_id: str):
    try:
        eliminar_plantilla(plantilla_id)
    except PlantillaNoEncontradaError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Plantilla '{plantilla_id}' no encontrada",
        )
