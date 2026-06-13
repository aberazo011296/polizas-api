from pydantic import BaseModel, Field


class CertificadoRequest(BaseModel):
    """Solicitud de generación de certificado."""
    plantilla_id: str
    variables: dict[str, str] = Field(
        ...,
        description="Mapa de nombre_variable -> valor a rellenar en el template"
    )
    coberturas: list[dict] = Field(
        default=[],
        description="Lista de coberturas (cada una con sus sub-campos) para el "
                    "loop {% for c in coberturas %} del template. Vacía si la "
                    "plantilla no usa coberturas dinámicas.",
    )


class CertificadoResponse(BaseModel):
    """Respuesta exitosa de generación."""
    archivo: str
    tamaño_bytes: int
    variables_usadas: list[str]
    variables_faltantes: list[str]
