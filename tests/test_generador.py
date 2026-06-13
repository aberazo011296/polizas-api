"""
Tests de generación de certificados (services/generador.py).
"""
import io

import pytest
from docx import Document

from app.core.config import settings
from app.core.errors import PlantillaNoEncontradaError
from app.core.paths import ruta_template_docx
from app.services.generador import generar_certificado


def _crear_template_docx(aseguradora: str, tipo: str, parrafos: list[str]) -> None:
    """Escribe un template .docx con marcadores Jinja en templates_dir."""
    ruta = ruta_template_docx(aseguradora, tipo)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    doc = Document()
    for p in parrafos:
        doc.add_paragraph(p)
    doc.save(str(ruta))


def _texto(docx_bytes: bytes) -> str:
    doc = Document(io.BytesIO(docx_bytes))
    return "\n".join(p.text for p in doc.paragraphs)


@pytest.fixture
def template_simple():
    aseg, tipo = "genaseg", "gentipo"
    _crear_template_docx(aseg, tipo, ["Asegurado: {{nombre_asegurado}}",
                                      "Póliza: {{numero_poliza}}"])
    yield aseg, tipo
    ruta = ruta_template_docx(aseg, tipo)
    if ruta.exists():
        ruta.unlink()


class TestGenerarCertificado:
    def test_rellena_variables(self, template_simple):
        aseg, tipo = template_simple
        docx_bytes, usadas, faltantes = generar_certificado(
            aseg, tipo, {"nombre_asegurado": "Juan Perez", "numero_poliza": "990664"}
        )
        texto = _texto(docx_bytes)
        assert "Juan Perez" in texto
        assert "990664" in texto
        assert set(usadas) == {"nombre_asegurado", "numero_poliza"}
        assert faltantes == []

    def test_variable_faltante(self, template_simple):
        aseg, tipo = template_simple
        docx_bytes, usadas, faltantes = generar_certificado(
            aseg, tipo, {"nombre_asegurado": "Ana"}
        )
        assert "numero_poliza" in faltantes
        assert "nombre_asegurado" in usadas

    def test_template_no_existe(self):
        with pytest.raises(PlantillaNoEncontradaError):
            generar_certificado("noexiste", "notipo", {})

    def test_salto_de_linea_genera_parrafos(self):
        aseg, tipo = "braseg", "brtipo"
        _crear_template_docx(aseg, tipo, ["Coberturas: {{listado}}"])
        try:
            docx_bytes, _, _ = generar_certificado(
                aseg, tipo, {"listado": "a) Muerte\nb) Invalidez"}
            )
            doc = Document(io.BytesIO(docx_bytes))
            textos = [p.text for p in doc.paragraphs]
            # El \n debe haber generado párrafos separados
            assert any("Muerte" in t for t in textos)
            assert any("Invalidez" in t for t in textos)
        finally:
            ruta = ruta_template_docx(aseg, tipo)
            if ruta.exists():
                ruta.unlink()
