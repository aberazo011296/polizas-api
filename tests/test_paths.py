"""
Tests de sanitización de rutas (seguridad — path traversal).
"""
import pytest

from app.core.config import settings
from app.core.paths import slug, ruta_template_docx


class TestSlug:
    def test_normal(self):
        assert slug("Generali") == "generali"

    def test_espacios_y_mayusculas(self):
        assert slug("Seguros Del Sur") == "seguros-del-sur"

    def test_neutraliza_traversal(self):
        # Los separadores y puntos se reducen a guiones — no quedan ../ ni /
        s = slug("../../etc/passwd")
        assert "/" not in s and ".." not in s

    def test_caracteres_invalidos(self):
        assert slug("a@b#c!") == "a-b-c"

    def test_solo_invalidos_queda_vacio(self):
        assert slug("///") == ""


class TestRutaTemplateDocx:
    def test_nombre_normal(self):
        ruta = ruta_template_docx("Generali", "Desgravamen")
        assert ruta.name == "generali_desgravamen.docx"

    def test_queda_dentro_de_templates_dir(self):
        base = settings.templates_dir.resolve()
        ruta = ruta_template_docx("generali", "desgravamen")
        assert ruta.resolve().is_relative_to(base)

    def test_traversal_no_escapa(self):
        base = settings.templates_dir.resolve()
        ruta = ruta_template_docx("../../etc/passwd", "desgravamen")
        # El nombre se sanitiza y la ruta sigue dentro del directorio permitido
        assert ruta.resolve().is_relative_to(base)
        assert "etc-passwd" in ruta.name

    def test_vacio_se_rechaza(self):
        with pytest.raises(ValueError):
            ruta_template_docx("", "desgravamen")

    def test_solo_invalidos_se_rechaza(self):
        with pytest.raises(ValueError):
            ruta_template_docx("///", "\\\\")
