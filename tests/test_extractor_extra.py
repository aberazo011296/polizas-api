"""
Tests adicionales del extractor por capa de texto / limpieza
(services/extractor.py) — sin depender de Tesseract.
"""
import fitz
import pytest

from app.models.plantilla import Caja
from app.services.extractor import (
    _corregir_caracteres_espaciados,
    _limpiar_listado,
    _limpiar_texto,
    extraer_variables,
)


def _pdf_con_caja_de_texto(texto: str, rect_caja):
    """PDF de una página con `texto` ubicado para que una caja lo capture."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_textbox(fitz.Rect(50, 50, 540, 750), texto, fontsize=11)
    return doc.tobytes()


class TestCorregirCaracteresEspaciados:
    def test_une_caracteres_espaciados(self):
        assert _corregir_caracteres_espaciados("9 9 0 6 6 4") == "990664"

    def test_respeta_texto_normal(self):
        assert _corregir_caracteres_espaciados("hola mundo grande") == "hola mundo grande"


class TestLimpiarListado:
    def test_separa_items_por_marcador(self):
        entrada = "a) Primera exclusion\nb) Segunda exclusion\nc) Tercera"
        salida = _limpiar_listado(entrada)
        assert salida.count("\n") == 2  # tres ítems → dos saltos

    def test_une_continuacion_de_item(self):
        entrada = "a) Una exclusion que sigue\nen la linea siguiente\nb) Otra"
        salida = _limpiar_listado(entrada)
        lineas = salida.split("\n")
        assert len(lineas) == 2
        assert "linea siguiente" in lineas[0]


class TestLimpiarTexto:
    def test_une_todo_en_un_parrafo(self):
        entrada = "Esta es una linea\ny esta otra\ny una tercera"
        salida = _limpiar_texto(entrada)
        assert "\n" not in salida

    def test_une_guion_de_corte(self):
        assert "total" in _limpiar_texto("to-\ntal")


class TestExtraccionDirecta:
    def test_extrae_texto_de_la_capa(self):
        # La página necesita >50 caracteres para detectarse como capa de texto
        texto = ("CERTIFICADO DE SEGURO DE DESGRAVAMEN POLIZA NUMERO 990664 "
                 "emitido para el contratante con vigencia segun condiciones.")
        pdf = _pdf_con_caja_de_texto(texto, None)
        cajas = [Caja(nombre="numero_poliza", pagina=0, x=40, y=40, ancho=520, alto=300)]
        res = extraer_variables(pdf, cajas)
        var = res.variables[0]
        assert var.origen == "extraido_directo"
        assert "990664" in (var.valor or "")
        assert var.estado == "ok"

    def test_caja_pagina_inexistente(self):
        pdf = _pdf_con_caja_de_texto("texto cualquiera aqui", None)
        cajas = [Caja(nombre="x", pagina=5, x=0, y=0, ancho=100, alto=100)]
        res = extraer_variables(pdf, cajas)
        assert res.variables[0].estado == "falta"
        assert len(res.advertencias) >= 1

    def test_campos_manuales_y_automaticos(self):
        pdf = _pdf_con_caja_de_texto("contenido de prueba largo para la pagina", None)
        cajas = [Caja(nombre="dato", pagina=0, x=40, y=40, ancho=520, alto=200)]
        res = extraer_variables(pdf, cajas, campos_manuales=[
            {"nombre": "fecha_actual", "valor_por_defecto": ""},
            {"nombre": "agente", "valor_por_defecto": "Maria"},
        ])
        nombres = {v.nombre: v for v in res.variables}
        assert nombres["fecha_actual"].valor  # automático
        assert nombres["agente"].valor == "Maria"
