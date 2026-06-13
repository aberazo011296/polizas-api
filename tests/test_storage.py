"""
Tests del almacenamiento local (storage/local.py).
"""
import uuid

import pytest

from app.core.errors import PlantillaNoEncontradaError
from app.storage.local import (
    actualizar_plantilla,
    eliminar_plantilla,
    guardar_plantilla,
    listar_plantillas,
    obtener_plantilla,
)


@pytest.fixture
def plantilla_temp():
    """Guarda una plantilla directa en storage y la limpia al final."""
    pid = str(uuid.uuid4())
    guardar_plantilla({
        "id": pid,
        "nombre": "Storage Test",
        "aseguradora": "x",
        "tipo_poliza": "y",
        "creado_en": "2026-01-01T00:00:00",
    })
    yield pid
    try:
        eliminar_plantilla(pid)
    except PlantillaNoEncontradaError:
        pass


class TestStorage:
    def test_guardar_y_obtener(self, plantilla_temp):
        p = obtener_plantilla(plantilla_temp)
        assert p["nombre"] == "Storage Test"

    def test_obtener_no_existe(self):
        with pytest.raises(PlantillaNoEncontradaError):
            obtener_plantilla("no-existe-" + str(uuid.uuid4()))

    def test_listar_incluye_creada(self, plantilla_temp):
        ids = [p["id"] for p in listar_plantillas()]
        assert plantilla_temp in ids

    def test_actualizar_preserva_id(self, plantilla_temp):
        actualizada = actualizar_plantilla(plantilla_temp, {"nombre": "Nuevo"})
        assert actualizada["nombre"] == "Nuevo"
        assert actualizada["id"] == plantilla_temp

    def test_actualizar_no_existe(self):
        with pytest.raises(PlantillaNoEncontradaError):
            actualizar_plantilla("no-existe", {"nombre": "x"})

    def test_eliminar_no_existe(self):
        with pytest.raises(PlantillaNoEncontradaError):
            eliminar_plantilla("no-existe")

    def test_eliminar_funciona(self):
        pid = str(uuid.uuid4())
        guardar_plantilla({"id": pid, "nombre": "Borrar", "creado_en": "2026-01-01"})
        eliminar_plantilla(pid)
        with pytest.raises(PlantillaNoEncontradaError):
            obtener_plantilla(pid)
