"""
Router: /polizas

Endpoints para subir PDFs y extraer campos usando una plantilla.
"""
import logging
from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from app.core.config import settings
from app.core.errors import PDFDemasiadoGrandeError, PDFInvalidoError, PlantillaNoEncontradaError
from app.models.plantilla import ResultadoExtraccion
from app.services.extractor import extraer_variables
from app.storage.local import obtener_plantilla

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/polizas", tags=["pólizas"])


@router.post(
    "/upload",
    summary="Subir PDF y extraer variables según plantilla",
    response_model=ResultadoExtraccion,
    status_code=status.HTTP_200_OK,
)
async def upload_poliza(
    archivo: UploadFile = File(..., description="PDF de la póliza"),
    plantilla_id: str = Form(..., description="ID de la plantilla a usar"),
):
    """
    Recibe un PDF de póliza y extrae los valores de los campos
    definidos en la plantilla indicada, usando OCR.

    - Si el PDF tiene un campo sin texto visible, el estado del campo
      será `falta`.
    - Los campos dudosos se marcan como `dudoso` para revisión manual.
    """
    # Validar tipo de archivo
    if archivo.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Solo se aceptan archivos PDF",
        )

    # Leer contenido
    pdf_bytes = await archivo.read()

    # Validar tamaño
    if len(pdf_bytes) > settings.max_pdf_size_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"El PDF excede el tamaño máximo de {settings.max_pdf_size_bytes // (1024*1024)} MB",
        )

    if len(pdf_bytes) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El archivo PDF está vacío",
        )

    # Obtener plantilla
    try:
        plantilla_dict = obtener_plantilla(plantilla_id)
    except PlantillaNoEncontradaError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Plantilla '{plantilla_id}' no encontrada",
        )

    # Extraer
    from app.models.plantilla import Caja
    cajas = [Caja(**c) for c in plantilla_dict.get("cajas", [])]
    campos_manuales = plantilla_dict.get("campos_manuales", [])

    # Qué extraer: definiciones nuevas (nombre + descripción) o, en
    # plantillas antiguas, los nombres de las cajas.
    definiciones = plantilla_dict.get("variables") or [
        {"nombre": c.nombre, "descripcion": ""} for c in cajas
    ]

    try:
        resultado = None
        if settings.anthropic_api_key:
            # Extracción automática con IA: tolera cambios de layout entre PDFs
            try:
                from app.services.extractor_llm import extraer_variables_llm
                resultado = extraer_variables_llm(pdf_bytes, definiciones, campos_manuales)
            except Exception as e:
                logger.warning("Extracción IA falló: %s", e)
                resultado = None
        if resultado is None:
            if not cajas:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="La extracción con IA no está disponible y esta "
                           "plantilla no tiene cajas de respaldo. Revisa la "
                           "ANTHROPIC_API_KEY del servidor.",
                )
            resultado = extraer_variables(pdf_bytes, cajas, campos_manuales)
    except PDFInvalidoError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )
    except PDFDemasiadoGrandeError as e:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=str(e),
        )

    resultado.plantilla_id = plantilla_id
    logger.info(
        "PDF procesado: %d variables, %d advertencias",
        len(resultado.variables),
        len(resultado.advertencias),
    )
    return resultado


@router.post(
    "/upload/sin-plantilla",
    summary="Subir PDF y obtener info básica (sin extracción de campos)",
    status_code=status.HTTP_200_OK,
)
async def upload_poliza_sin_plantilla(
    archivo: UploadFile = File(..., description="PDF de la póliza"),
):
    """
    Útil para crear una nueva plantilla: sube el PDF modelo y obtén
    el número de páginas y dimensiones para dibujar las cajas.
    """
    import fitz

    if archivo.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Solo se aceptan archivos PDF",
        )

    pdf_bytes = await archivo.read()

    if len(pdf_bytes) == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="PDF vacío")

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        paginas = []
        for i, page in enumerate(doc):
            rect = page.rect
            paginas.append({
                "pagina": i,
                "ancho": rect.width,
                "alto": rect.height,
            })
        doc.close()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"PDF inválido: {e}",
        )

    return {
        "nombre_archivo": archivo.filename,
        "num_paginas": len(paginas),
        "paginas": paginas,
    }
