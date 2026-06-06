"""
Tests de integración de los endpoints.

Para correr: pytest tests/ -v
"""
import io
import json
import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


class TestHealth:
    def test_root(self):
        resp = client.get("/")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_health(self):
        resp = client.get("/health")
        assert resp.status_code == 200


class TestPlantillas:
    def test_listar_vacio(self):
        resp = client.get("/plantillas")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_crear_plantilla(self):
        body = {
            "nombre": "Test Generali Desgravamen",
            "aseguradora": "generali",
            "tipo_poliza": "desgravamen",
            "cajas": [
                {"nombre": "numero_poliza", "pagina": 0, "x": 100, "y": 50, "ancho": 200, "alto": 30},
                {"nombre": "contratante", "pagina": 1, "x": 50, "y": 150, "ancho": 400, "alto": 25},
            ]
        }
        resp = client.post("/plantillas", json=body)
        assert resp.status_code == 201
        data = resp.json()
        assert data["id"]
        assert data["nombre"] == body["nombre"]
        assert len(data["cajas"]) == 2
        return data["id"]

    def test_crear_plantilla_variables_duplicadas(self):
        body = {
            "nombre": "Test duplicados",
            "aseguradora": "generali",
            "tipo_poliza": "desgravamen",
            "cajas": [
                {"nombre": "numero_poliza", "pagina": 0, "x": 100, "y": 50, "ancho": 200, "alto": 30},
                {"nombre": "numero_poliza", "pagina": 0, "x": 300, "y": 50, "ancho": 200, "alto": 30},
            ]
        }
        resp = client.post("/plantillas", json=body)
        assert resp.status_code == 400

    def test_obtener_plantilla_no_existe(self):
        resp = client.get("/plantillas/id-inexistente")
        assert resp.status_code == 404

    def test_eliminar_plantilla_no_existe(self):
        resp = client.delete("/plantillas/id-inexistente")
        assert resp.status_code == 404


class TestPolizas:
    def test_upload_sin_plantilla_pdf_invalido(self):
        resp = client.post(
            "/polizas/upload/sin-plantilla",
            files={"archivo": ("test.pdf", b"not a pdf", "application/pdf")},
        )
        assert resp.status_code == 422

    def test_upload_pdf_minimo(self):
        """Sube un PDF válido de una página."""
        import fitz
        doc = fitz.open()
        doc.new_page()
        pdf_bytes = doc.tobytes()

        resp = client.post(
            "/polizas/upload/sin-plantilla",
            files={"archivo": ("poliza.pdf", pdf_bytes, "application/pdf")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["num_paginas"] == 1
