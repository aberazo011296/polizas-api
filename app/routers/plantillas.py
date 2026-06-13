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
from app.core.paths import ruta_template_docx
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


def _validar_tamano_docx(contenido: bytes) -> None:
    """Rechaza .docx que excedan el tamaño máximo configurado."""
    if len(contenido) > settings.max_docx_size_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"El .docx excede el tamaño máximo de "
                   f"{settings.max_docx_size_bytes // (1024 * 1024)} MB",
        )


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
            num_variables=len(p.get("variables") or p.get("cajas") or []),
            creado_en=p["creado_en"],
        )
        for p in plantillas
    ]


def _validar_definicion(body: PlantillaCrear):
    """La plantilla debe definir qué extraer, sin nombres repetidos."""
    nombres = ([c.nombre for c in body.cajas]
               + [v.nombre for v in body.variables]
               + [m.nombre for m in body.campos_manuales])
    if len(nombres) != len(set(nombres)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Hay nombres de variable duplicados",
        )
    if not body.cajas and not body.variables:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La plantilla debe tener al menos una variable",
        )


@router.post(
    "",
    summary="Crear nueva plantilla",
    response_model=Plantilla,
    status_code=status.HTTP_201_CREATED,
)
def crear(body: PlantillaCrear):
    """
    Guarda una nueva plantilla. Las variables (nombre + descripción)
    definen qué extrae la IA; las cajas con coordenadas son opcionales
    (modo antiguo por posición).
    """
    _validar_definicion(body)

    plantilla = Plantilla(**body.model_dump())
    guardar_plantilla(plantilla.model_dump())
    logger.info("Nueva plantilla creada: %s (%s)", plantilla.nombre, plantilla.id)
    return plantilla


@router.post(
    "/sugerir-variables",
    summary="Sugerir variables a partir de un PDF de ejemplo (IA)",
    status_code=status.HTTP_200_OK,
)
async def sugerir_variables_endpoint(
    archivo: UploadFile = File(..., description="PDF de póliza de ejemplo"),
):
    """
    Lee un PDF de ejemplo con IA y propone la lista de variables
    (nombre + descripción) para crear una plantilla. La persona luego
    edita, renombra o elimina lo que no necesite.
    """
    if archivo.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Solo se aceptan archivos PDF",
        )
    if not settings.anthropic_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="La sugerencia con IA requiere ANTHROPIC_API_KEY en el servidor",
        )

    pdf_bytes = await archivo.read()
    if len(pdf_bytes) > settings.max_pdf_size_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"El PDF excede el tamaño máximo de "
                   f"{settings.max_pdf_size_bytes // (1024 * 1024)} MB",
        )
    from app.core.errors import PDFInvalidoError
    from app.services.extractor_llm import sugerir_variables
    try:
        variables = sugerir_variables(pdf_bytes)
    except PDFInvalidoError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    except Exception as e:
        logger.error("Error sugiriendo variables: %s", e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"No se pudieron sugerir variables: {e}",
        )

    return {"variables": variables}


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
    _validar_definicion(body)
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

    ruta = ruta_template_docx(plantilla["aseguradora"], plantilla["tipo_poliza"])
    nombre_archivo = ruta.name

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
    _validar_tamano_docx(contenido)
    template_bytes = construir_template(contenido, mapa, variables_bloque)
    template_bytes = _limpiar_template_docx(template_bytes)

    ruta = ruta_template_docx(plantilla["aseguradora"], plantilla["tipo_poliza"])
    nombre_archivo = ruta.name
    settings.templates_dir.mkdir(parents=True, exist_ok=True)
    ruta.write_bytes(template_bytes)

    # Persistir el mapeo y el Word de referencia para poder reeditar el
    # template sin volver a subir el archivo ni reescribir los textos.
    referencias_dir = settings.data_dir / "referencias"
    referencias_dir.mkdir(parents=True, exist_ok=True)
    (referencias_dir / f"{plantilla_id}.docx").write_bytes(contenido)
    actualizar_plantilla(plantilla_id, {
        "reemplazos_template": mapa,
        "doc_referencia": archivo.filename or "documento.docx",
    })

    logger.info("Template construido con reemplazos: %s → %s", list(mapa.keys()), nombre_archivo)
    return {"archivo": nombre_archivo, "variables": list(mapa.keys())}


@router.get(
    "/{plantilla_id}/template/doc-referencia",
    summary="Descargar el Word de referencia con el que se construyó el template",
)
def descargar_doc_referencia(plantilla_id: str):
    try:
        plantilla = obtener_plantilla(plantilla_id)
    except PlantillaNoEncontradaError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Plantilla '{plantilla_id}' no encontrada")

    ruta = settings.data_dir / "referencias" / f"{plantilla_id}.docx"
    if not ruta.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No hay documento de referencia guardado")

    return FileResponse(path=str(ruta),
                        filename=plantilla.get("doc_referencia", "documento.docx"),
                        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


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

    ruta = ruta_template_docx(plantilla["aseguradora"], plantilla["tipo_poliza"])
    nombre_archivo = ruta.name
    settings.templates_dir.mkdir(parents=True, exist_ok=True)

    contenido = await archivo.read()
    _validar_tamano_docx(contenido)
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
