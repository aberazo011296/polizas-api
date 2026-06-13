"""
Tests de autenticación por token (seguridad).

verificar_token se aplica como dependencia global; si settings.api_token
está vacío no exige nada (dev). Estos tests activan el token con monkeypatch.
"""
import pytest

from app.core.config import settings


class TestAuthDesactivada:
    def test_sin_token_configurado_permite(self, client):
        # api_token por defecto es None → la API no exige autenticación
        assert settings.api_token in (None, "")
        resp = client.get("/plantillas")
        assert resp.status_code == 200


class TestAuthActivada:
    @pytest.fixture(autouse=True)
    def activar_token(self, monkeypatch):
        monkeypatch.setattr(settings, "api_token", "secreto-fuerte")
        yield

    def test_sin_header_rechaza(self, client):
        resp = client.get("/plantillas")
        assert resp.status_code == 401

    def test_token_incorrecto_rechaza(self, client):
        resp = client.get("/plantillas", headers={"Authorization": "Bearer malo"})
        assert resp.status_code == 401

    def test_token_correcto_permite(self, client):
        resp = client.get(
            "/plantillas",
            headers={"Authorization": "Bearer secreto-fuerte"},
        )
        assert resp.status_code == 200

    def test_health_no_requiere_token(self, client):
        # Los endpoints de salud quedan fuera de la auth
        assert client.get("/health").status_code == 200
        assert client.get("/").status_code == 200

    def test_formato_header_sin_bearer_rechaza(self, client):
        resp = client.get("/plantillas", headers={"Authorization": "secreto-fuerte"})
        assert resp.status_code == 401
