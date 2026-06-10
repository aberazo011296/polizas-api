"""
Router: /plantillas

CRUD de plantillas de extracción.
"""
import io
import json
import logging
import re
import zipfile

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse

from app.core.config import settings
from app.core.errors import PlantillaNoEncontradaError
from app.models.plantilla import Plantilla, PlantillaCrear, PlantillaResumen
from app.services.template_builder import construir_template
from app.storage.local import (
    actualizar_plantilla,
    eliminar_plantilla,
    guardar_plantilla,
    listar_plantillas,
    obtener_plantilla,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/plantillas", tags=["plantillas"])


def _limpiar_template_docx(contenido: bytes) -> bytes:
    """Elimina marcadores Jinja2 vacíos {{}} del XML del documento."""
    try:
        buf_in = io.BytesIO(contenido)
        buf_out = io.BytesIO()
        with zipfile.ZipFile(buf_in, 'r') as zin, \
             zipfile.ZipFile(buf_out, 'w', zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == 'word/document.xml':
                    xml = data.decode('utf-8')
                    xml = re.sub(r'\{\{\s*\}\}', '', xml)
                    data = xml.encode('utf-8')
                zout.writestr(item, data)
        return buf_out.getvalue()
    except Exception:
        return contenido


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


@router.get(
    "/{plantilla_id}/template",
    summary="Descargar template .docx de una plantilla",
)
def descargar_template(plantilla_id: str):
    try:
        plantilla = obtener_plantilla(plantilla_id)
    except PlantillaNoEncontradaError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Plantilla '{plantilla_id}' no encontrada")

    nombre_archivo = f"{plantilla['aseguradora'].lower()}_{plantilla['tipo_poliza'].lower()}.docx"
    ruta = settings.templates_dir / nombre_archivo

    if not ruta.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No hay template subido para esta plantilla")

    return FileResponse(path=str(ruta), filename=nombre_archivo,
                        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


@router.post(
    "/{plantilla_id}/template/build",
    summary="Construir template a partir de documento real con reemplazos",
    status_code=status.HTTP_200_OK,
)
async def construir_template_desde_doc(
    plantilla_id: str,
    archivo: UploadFile = File(...),
    reemplazos: str = Form(...),  # JSON: {"variable": "texto_original"}
):
    """
    Recibe el documento Word original y un mapa de reemplazos.
    Genera el template .docx con {{variable}} preservando el formato.
    """
    try:
        plantilla = obtener_plantilla(plantilla_id)
    except PlantillaNoEncontradaError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Plantilla '{plantilla_id}' no encontrada")

    try:
        mapa = json.loads(reemplazos)
    except json.JSONDecodeError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="El campo 'reemplazos' debe ser JSON válido")

    # Detectar variables de bloque (alto > 40px en el canvas = párrafos multi-línea)
    try:
        p = obtener_plantilla(plantilla_id)
        variables_bloque = {c["nombre"] for c in p.get("cajas", []) if c.get("alto", 0) > 40}
    except Exception:
        variables_bloque = set()

    contenido = await archivo.read()
    template_bytes = construir_template(contenido, mapa, variables_bloque)
    template_bytes = _limpiar_template_docx(template_bytes)

    nombre_archivo = f"{plantilla['aseguradora'].lower()}_{plantilla['tipo_poliza'].lower()}.docx"
    settings.templates_dir.mkdir(parents=True, exist_ok=True)
    (settings.templates_dir / nombre_archivo).write_bytes(template_bytes)

    logger.info("Template construido con reemplazos: %s → %s", list(mapa.keys()), nombre_archivo)
    return {"archivo": nombre_archivo, "variables": list(mapa.keys())}


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
    contenido = _limpiar_template_docx(contenido)
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
