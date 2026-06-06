"""
Tests del servicio de extracción.

Para correr: pytest tests/ -v
"""
import pytest
from unittest.mock import patch, MagicMock
from PIL import Image

from app.models.plantilla import Caja, ResultadoExtraccion
from app.services.extractor import _limpiar_texto, extraer_variables
from app.core.errors import PDFInvalidoError


class TestLimpiarTexto:
    def test_elimina_saltos_de_linea(self):
        assert _limpiar_texto("hola\nmundo") == "hola mundo"

    def test_colapsa_espacios(self):
        assert _limpiar_texto("  hola   mundo  ") == "hola mundo"

    def test_texto_vacio(self):
        assert _limpiar_texto("") == ""

    def test_elimina_no_imprimibles(self):
        # El \x00 no es imprimible, se elimina sin agregar espacio
        assert _limpiar_texto("hola\x00mundo") == "holamundo"
        # Pero un salto de línea sí agrega espacio
        assert _limpiar_texto("hola\nmundo") == "hola mundo"

    def test_numero_poliza_tipico(self):
        # Simulación de output OCR para número de póliza
        assert _limpiar_texto("990664\n") == "990664"


class TestExtraerVariables:
    def test_pdf_invalido_lanza_error(self):
        with pytest.raises(PDFInvalidoError):
            extraer_variables(b"not a pdf", [])

    def test_sin_cajas_devuelve_vacio(self, pdf_bytes_fixture):
        resultado = extraer_variables(pdf_bytes_fixture, [])
        assert resultado.variables == []
        assert resultado.paginas_procesadas > 0

    def test_caja_pagina_inexistente(self, pdf_bytes_fixture):
        cajas = [Caja(
            nombre="test",
            pagina=999,
            x=0, y=0, ancho=100, alto=30
        )]
        resultado = extraer_variables(pdf_bytes_fixture, cajas)
        assert len(resultado.variables) == 1
        assert resultado.variables[0].estado == "falta"
        assert len(resultado.advertencias) > 0


@pytest.fixture
def pdf_bytes_fixture():
    """
    Crea un PDF mínimo de una página en blanco para tests.
    Requiere PyMuPDF.
    """
    import fitz
    doc = fitz.open()
    doc.new_page()
    return doc.tobytes()
