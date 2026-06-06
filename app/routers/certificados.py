"""
Router: /certificados

Generación de certificados individuales en formato .docx.
"""
import logging
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import Response

from app.core.errors import (
    ErrorGeneracionDocumento,
    PlantillaNoEncontradaError,
)
from app.models.certificado import CertificadoRequest
from app.services.generador import generar_certificado
from app.storage.local import obtener_plantilla

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/certificados", tags=["certificados"])


@router.post(
    "/generar",
    summary="Generar certificado individual en .docx",
    responses={
        200: {
            "description": "Archivo .docx del certificado",
            "content": {
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {}
            },
        }
    },
)
def generar(body: CertificadoRequest):
    """
    Recibe las variables extraídas/corregidas y genera el certificado
    individual rellenando el template .docx de la aseguradora.

    El archivo se descarga directamente como .docx.
    """
    # Obtener plantilla para saber aseguradora y tipo
    try:
        plantilla = obtener_plantilla(body.plantilla_id)
    except PlantillaNoEncontradaError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Plantilla '{body.plantilla_id}' no encontrada",
        )

    aseguradora = plantilla["aseguradora"]
    tipo_poliza = plantilla["tipo_poliza"]

    try:
        docx_bytes, vars_usadas, vars_faltantes = generar_certificado(
            aseguradora=aseguradora,
            tipo_poliza=tipo_poliza,
            variables=body.variables,
        )
    except PlantillaNoEncontradaError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )
    except ErrorGeneracionDocumento as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )

    if vars_faltantes:
        logger.warning("Campos faltantes en certificado: %s", vars_faltantes)

    nombre_archivo = f"certificado_{aseguradora}_{tipo_poliza}.docx"

    return Response(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition": f'attachment; filename="{nombre_archivo}"',
            "X-Variables-Usadas": ",".join(vars_usadas),
            "X-Variables-Faltantes": ",".join(vars_faltantes),
        },
    )
