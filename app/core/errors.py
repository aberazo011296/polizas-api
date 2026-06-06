class PolizaError(Exception):
    """Base error del dominio."""
    pass


class PDFInvalidoError(PolizaError):
    """PDF no válido o corrupto."""
    pass


class PDFDemasiadoGrandeError(PolizaError):
    """PDF excede el tamaño máximo permitido."""
    pass


class PlantillaNoEncontradaError(PolizaError):
    """Plantilla no existe."""
    pass


class PlantillaDuplicadaError(PolizaError):
    """Ya existe una plantilla con ese nombre."""
    pass


class ErrorGeneracionDocumento(PolizaError):
    """Error al generar el documento de salida."""
    pass
