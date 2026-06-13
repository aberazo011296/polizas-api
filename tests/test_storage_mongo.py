"""
Tests del almacenamiento en MongoDB (storage/mongo.py + storage/auditoria.py)
usando mongomock — no requiere un Mongo real.
"""
import uuid

import mongomock
import pytest
from mongomock.gridfs import enable_gridfs_integration

from app.core.config import settings
from app.core.errors import PlantillaNoEncontradaError
from app.storage import auditoria, db as storage_db
from app.storage import mongo as mongo_storage

enable_gridfs_integration()


@pytest.fixture(autouse=True)
def mongo_mock(monkeypatch):
    """Reemplaza get_db() por una base mongomock en memoria, fresca por test."""
    cliente = mongomock.MongoClient()
    fake_get_db = lambda: cliente["test"]
    monkeypatch.setattr(storage_db, "get_db", fake_get_db)
    monkeypatch.setattr(mongo_storage, "get_db", fake_get_db)
    monkeypatch.setattr(auditoria, "get_db", fake_get_db)
    yield


@pytest.fixture
def plantilla_temp():
    pid = str(uuid.uuid4())
    mongo_storage.guardar_plantilla({
        "id": pid,
        "nombre": "Storage Mongo Test",
        "aseguradora": "x",
        "tipo_poliza": "y",
        "creado_en": "2026-01-01T00:00:00",
    })
    yield pid


class TestPlantillasMongo:
    def test_guardar_y_obtener(self, plantilla_temp):
        p = mongo_storage.obtener_plantilla(plantilla_temp)
        assert p["nombre"] == "Storage Mongo Test"
        assert "_id" not in p

    def test_obtener_no_existe(self):
        with pytest.raises(PlantillaNoEncontradaError):
            mongo_storage.obtener_plantilla("no-existe-" + str(uuid.uuid4()))

    def test_listar_incluye_creada(self, plantilla_temp):
        ids = [p["id"] for p in mongo_storage.listar_plantillas()]
        assert plantilla_temp in ids

    def test_actualizar_preserva_id(self, plantilla_temp):
        actualizada = mongo_storage.actualizar_plantilla(plantilla_temp, {"nombre": "Nuevo"})
        assert actualizada["nombre"] == "Nuevo"
        assert actualizada["id"] == plantilla_temp

    def test_actualizar_no_existe(self):
        with pytest.raises(PlantillaNoEncontradaError):
            mongo_storage.actualizar_plantilla("no-existe", {"nombre": "x"})

    def test_eliminar_funciona(self, plantilla_temp):
        mongo_storage.eliminar_plantilla(plantilla_temp)
        with pytest.raises(PlantillaNoEncontradaError):
            mongo_storage.obtener_plantilla(plantilla_temp)

    def test_eliminar_no_existe(self):
        with pytest.raises(PlantillaNoEncontradaError):
            mongo_storage.eliminar_plantilla("no-existe")


class TestArchivosGridFS:
    def test_template_guardar_y_obtener(self):
        mongo_storage.guardar_archivo_template("generali", "desgravamen", b"contenido-v1")
        assert mongo_storage.obtener_archivo_template("generali", "desgravamen") == b"contenido-v1"

    def test_template_reemplazo_no_duplica(self):
        mongo_storage.guardar_archivo_template("generali", "vida", b"v1")
        mongo_storage.guardar_archivo_template("generali", "vida", b"v2")
        assert mongo_storage.obtener_archivo_template("generali", "vida") == b"v2"

    def test_template_no_existe(self):
        assert mongo_storage.obtener_archivo_template("nadie", "nada") is None

    def test_doc_referencia_guardar_y_obtener(self):
        pid = str(uuid.uuid4())
        mongo_storage.guardar_doc_referencia(pid, b"word-referencia")
        assert mongo_storage.obtener_doc_referencia(pid) == b"word-referencia"

    def test_doc_referencia_no_existe(self):
        assert mongo_storage.obtener_doc_referencia("no-existe") is None


class TestAuditoria:
    def test_registrar_no_op_si_backend_local(self, monkeypatch):
        monkeypatch.setattr(settings, "storage_backend", "local")
        auditoria.registrar("plantilla_creada", plantilla_id="x")
        assert auditoria.historial("x") == []

    def test_registrar_y_consultar_historial(self, monkeypatch):
        monkeypatch.setattr(settings, "storage_backend", "mongo")
        pid = str(uuid.uuid4())
        auditoria.registrar(
            "plantilla_creada",
            plantilla_id=pid,
            aseguradora="generali",
            tipo_poliza="desgravamen",
            detalle={"num_variables": 3},
        )
        import time
        time.sleep(0.001)
        auditoria.registrar("certificado_generado", plantilla_id=pid)

        eventos = auditoria.historial(pid)
        assert len(eventos) == 2
        # más reciente primero
        assert eventos[0]["evento"] == "certificado_generado"
        assert eventos[1]["detalle"]["num_variables"] == 3

    def test_historial_vacio_para_otra_plantilla(self, monkeypatch):
        monkeypatch.setattr(settings, "storage_backend", "mongo")
        assert auditoria.historial("otra-plantilla-" + str(uuid.uuid4())) == []
