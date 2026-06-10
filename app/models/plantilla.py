from pydantic import BaseModel, Field
from typing import Literal
from datetime import datetime
import uuid


class Caja(BaseModel):
    """Región rectangular sobre una página del PDF que contiene un campo."""
    nombre: str = Field(..., description="Nombre de la variable, ej: 'numero_poliza'")
    pagina: int = Field(..., ge=0, description="Índice de página (0-based)")
    x: float = Field(..., description="Coordenada X (pixels desde esquina superior izquierda)")
    y: float = Field(..., description="Coordenada Y (pixels desde esquina superior izquierda)")
    ancho: float = Field(..., gt=0)
    alto: float = Field(..., gt=0)


class CampoManual(BaseModel):
    """Campo de entrada manual — no se extrae del PDF, el usuario lo completa."""
    nombre: str = Field(..., description="Nombre de la variable, ej: 'nombre_asegurado'")
    valor_por_defecto: str = Field(default="", description="Valor pre-rellenado al procesar")


class PlantillaCrear(BaseModel):
    """Payload para crear una nueva plantilla."""
    nombre: str = Field(..., min_length=1, max_length=100)
    aseguradora: str = Field(..., min_length=1, max_length=50)
    tipo_poliza: str = Field(..., min_length=1, max_length=50)
    cajas: list[Caja] = Field(..., min_length=1)
    campos_manuales: list[CampoManual] = Field(default=[])


class Plantilla(PlantillaCrear):
    """Plantilla persistida."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    creado_en: datetime = Field(default_factory=datetime.utcnow)


class PlantillaResumen(BaseModel):
    """Vista resumida para listados."""
    id: str
    nombre: str
    aseguradora: str
    tipo_poliza: str
    num_variables: int
    creado_en: datetime


class Variable(BaseModel):
    """Dato extraído o ingresado manualmente para rellenar un certificado."""
    nombre: str
    valor: str | None = None
    origen: Literal["extraido", "manual"] = "manual"
    estado: Literal["ok", "falta", "dudoso"] = "falta"
    nota: str | None = None


class ResultadoExtraccion(BaseModel):
    """Resultado de procesar un PDF contra una plantilla."""
    plantilla_id: str
    variables: list[Variable]
    paginas_procesadas: int
    advertencias: list[str] = []
