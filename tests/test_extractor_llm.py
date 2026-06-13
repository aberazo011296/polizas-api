"""
Tests de extracción con IA (services/extractor_llm.py).

La API de Claude se mockea — no se hacen llamadas reales.
"""
import json

import pytest

from app.core.config import settings
from app.core.errors import PDFInvalidoError
from app.services import extractor_llm
from app.services.extractor_llm import (
    _normalizar,
    _palabras_presentes,
    _texto_completo,
    extraer_variables_llm,
    sugerir_variables,
)

TEXTO_POLIZA = (
    "El asegurado tiene la poliza numero 990664 con cobertura de muerte por "
    "cualquier causa y beneficios adicionales detallados a continuacion para "
    "el contrato vigente durante el periodo establecido en las condiciones "
    "generales del presente documento de seguro de desgravamen colectivo."
)


def _pdf_con_texto(texto: str = TEXTO_POLIZA) -> bytes:
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    # insert_textbox ajusta el texto al ancho para que no se recorte
    rect = fitz.Rect(50, 50, page.rect.width - 50, page.rect.height - 50)
    page.insert_textbox(rect, texto, fontsize=11)
    return doc.tobytes()


class FakeResp:
    def __init__(self, text):
        self.content = [type("C", (), {"text": text})()]


def _fake_anthropic(text):
    """Devuelve una factory que reemplaza Anthropic(api_key=...) por un cliente fake."""
    class FakeClient:
        def __init__(self, *a, **k):
            self.messages = type("M", (), {"create": lambda _s, **kw: FakeResp(text)})()
    return FakeClient


# ── Helpers puros ──────────────────────────────────────────────────────────

class TestHelpers:
    def test_normalizar_quita_espacios(self):
        assert _normalizar("Hola  Mundo") == "holamundo"

    def test_normalizar_une_guion_de_corte(self):
        assert _normalizar("to-\ntal") == "total"

    def test_palabras_presentes_true(self):
        norm = _normalizar("el asegurado tiene cobertura")
        assert _palabras_presentes("asegurado cobertura", norm) is True

    def test_palabras_presentes_false(self):
        norm = _normalizar("texto cualquiera")
        assert _palabras_presentes("inventado fantasma", norm) is False

    def test_texto_completo_pdf_invalido(self):
        with pytest.raises(PDFInvalidoError):
            _texto_completo(b"no soy un pdf")

    def test_texto_completo_sin_capa_texto(self):
        import fitz
        doc = fitz.open()
        doc.new_page()  # página en blanco → sin capa de texto
        with pytest.raises(PDFInvalidoError):
            _texto_completo(doc.tobytes())

    def test_texto_completo_ok(self):
        texto, n = _texto_completo(_pdf_con_texto())
        assert n == 1
        assert "990664" in texto


# ── extraer_variables_llm ────────────────────────────────────────────────────

class TestExtraerVariablesLLM:
    @pytest.fixture(autouse=True)
    def api_key(self, monkeypatch):
        monkeypatch.setattr(settings, "anthropic_api_key", "test-key")

    def test_sin_key_lanza_error(self, monkeypatch):
        monkeypatch.setattr(settings, "anthropic_api_key", None)
        with pytest.raises(RuntimeError):
            extraer_variables_llm(_pdf_con_texto(), [{"nombre": "x", "descripcion": ""}])

    def test_valor_presente_estado_ok(self, monkeypatch):
        respuesta = json.dumps({"numero_poliza": "990664"})
        monkeypatch.setattr(extractor_llm, "Anthropic", _fake_anthropic(respuesta))
        res = extraer_variables_llm(
            _pdf_con_texto(), [{"nombre": "numero_poliza", "descripcion": ""}]
        )
        var = res.variables[0]
        assert var.nombre == "numero_poliza"
        assert var.valor == "990664"
        assert var.estado == "ok"

    def test_valor_inventado_es_dudoso(self, monkeypatch):
        respuesta = json.dumps({"numero_poliza": "XYZ-INVENTADO-99999"})
        monkeypatch.setattr(extractor_llm, "Anthropic", _fake_anthropic(respuesta))
        res = extraer_variables_llm(
            _pdf_con_texto(), [{"nombre": "numero_poliza", "descripcion": ""}]
        )
        assert res.variables[0].estado == "dudoso"

    def test_valor_null_es_falta(self, monkeypatch):
        respuesta = json.dumps({"numero_poliza": None})
        monkeypatch.setattr(extractor_llm, "Anthropic", _fake_anthropic(respuesta))
        res = extraer_variables_llm(
            _pdf_con_texto(), [{"nombre": "numero_poliza", "descripcion": ""}]
        )
        assert res.variables[0].estado == "falta"
        assert len(res.advertencias) >= 1

    def test_respuesta_no_json_lanza_error(self, monkeypatch):
        monkeypatch.setattr(extractor_llm, "Anthropic", _fake_anthropic("esto no es json"))
        with pytest.raises(RuntimeError):
            extraer_variables_llm(
                _pdf_con_texto(), [{"nombre": "numero_poliza", "descripcion": ""}]
            )

    def test_tolera_json_en_bloque_markdown(self, monkeypatch):
        respuesta = "```json\n" + json.dumps({"numero_poliza": "990664"}) + "\n```"
        monkeypatch.setattr(extractor_llm, "Anthropic", _fake_anthropic(respuesta))
        res = extraer_variables_llm(
            _pdf_con_texto(), [{"nombre": "numero_poliza", "descripcion": ""}]
        )
        assert res.variables[0].valor == "990664"

    def test_campos_manuales(self, monkeypatch):
        respuesta = json.dumps({"numero_poliza": "990664"})
        monkeypatch.setattr(extractor_llm, "Anthropic", _fake_anthropic(respuesta))
        res = extraer_variables_llm(
            _pdf_con_texto(),
            [{"nombre": "numero_poliza", "descripcion": ""}],
            campos_manuales=[
                {"nombre": "fecha_actual", "valor_por_defecto": ""},
                {"nombre": "nombre_asegurado", "valor_por_defecto": "Juan"},
            ],
        )
        nombres = {v.nombre: v for v in res.variables}
        assert nombres["fecha_actual"].valor  # generado automáticamente
        assert nombres["nombre_asegurado"].valor == "Juan"


# ── sugerir_variables ────────────────────────────────────────────────────────

class TestSugerirVariables:
    @pytest.fixture(autouse=True)
    def api_key(self, monkeypatch):
        monkeypatch.setattr(settings, "anthropic_api_key", "test-key")

    def test_sin_key_lanza_error(self, monkeypatch):
        monkeypatch.setattr(settings, "anthropic_api_key", None)
        with pytest.raises(RuntimeError):
            sugerir_variables(_pdf_con_texto())

    def test_devuelve_lista_limpia(self, monkeypatch):
        respuesta = json.dumps([
            {"nombre": "Numero Poliza", "descripcion": "el número"},
            {"nombre": "fecha_inicio", "descripcion": "vigencia"},
        ])
        monkeypatch.setattr(extractor_llm, "Anthropic", _fake_anthropic(respuesta))
        out = sugerir_variables(_pdf_con_texto())
        nombres = [o["nombre"] for o in out]
        # "Numero Poliza" se normaliza a snake_case sin espacios
        assert "numero_poliza" in nombres
        assert "fecha_inicio" in nombres

    def test_respuesta_no_json_lanza_error(self, monkeypatch):
        monkeypatch.setattr(extractor_llm, "Anthropic", _fake_anthropic("nope"))
        with pytest.raises(RuntimeError):
            sugerir_variables(_pdf_con_texto())
