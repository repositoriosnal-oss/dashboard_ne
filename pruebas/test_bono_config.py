"""Pruebas de src/bono_config.py (sin Streamlit ni base de datos)."""
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from src import bono_config as bc

# Copias de las constantes estándar de src/conexion_db.py
ESCALA_RENT_DEFAULT = [
    (0.17, False, None, False, 1.10), (0.14, False, 0.17, True, 1.00), (0.13, False, 0.14, True, 0.80),
    (0.115, False, 0.13, True, 0.60), (0.10, False, 0.115, True, 0.50), (0.10, True, None, False, 0.00),
    (0.095, True, 0.10, False, -0.05), (0.09, True, 0.095, False, -0.10), (0.085, True, 0.09, False, -0.15),
    (0.08, True, 0.085, False, -0.20), (0.075, True, 0.08, False, -0.25), (0.07, True, 0.075, False, -0.30),
    (0.065, True, 0.07, False, -0.35), (0.06, True, 0.065, False, -0.40), (0.055, True, 0.06, False, -0.45),
    (0.05, True, 0.055, False, -0.50), (0.045, True, 0.05, False, -0.55), (0.04, True, 0.045, False, -0.60),
    (0.035, True, 0.04, False, -0.65), (0.03, True, 0.035, False, -0.70), (0.025, True, 0.03, False, -0.75),
    (0.02, True, 0.025, False, -0.80), (0.015, True, 0.02, False, -0.85), (0.01, True, 0.015, False, -0.90),
    (0.005, True, 0.01, False, -0.95), (0.0, True, 0.005, False, -1.00), (None, False, 0.0, False, -1.00),
]
METAS_2025 = [(0, True, 1_380_000_000, False, 0.0), (1_380_000_000, True, 1_955_000_000, False, 0.33),
              (1_955_000_000, True, 2_300_000_000, False, 0.67), (2_300_000_000, True, None, False, 1.0)]
METAS_2026 = [(0, True, 2_190_000_000, False, 0.0), (2_190_000_000, True, 2_580_000_000, False, 0.67),
              (2_580_000_000, True, None, False, 1.0)]
METAS_EJECOM = [(0, True, 430_000_000, False, 0.0), (430_000_000, True, None, False, 1.0)]


# ---------------- tipos nativos ----------------
def test_entero_acepta_numpy_y_devuelve_int_nativo():
    v = bc.entero(np.int64(2))
    assert v == 2 and type(v) is int


def test_entero_desde_dataframe_como_en_la_vista():
    df = pd.DataFrame({"id": [1, 2, 3], "nombre": list("abc")})
    pid = df[df["nombre"] == "b"]["id"].iloc[0]
    assert type(pid).__module__ == "numpy"        # el origen del error de psycopg2
    assert type(bc.entero(pid)) is int


def test_entero_vacio_falla():
    with pytest.raises(ValueError):
        bc.entero(None)


def test_py_convierte_numpy_nan_y_nat():
    assert type(bc.py(np.float64(1.5))) is float
    assert type(bc.py(np.bool_(True))) is bool
    assert bc.py(float("nan")) is None and bc.py(pd.NaT) is None


def test_fecha_normaliza_distintos_tipos():
    assert bc.fecha(pd.Timestamp("2026-03-05")) == date(2026, 3, 5)
    assert bc.fecha(datetime(2026, 3, 5, 10)) == date(2026, 3, 5)
    assert bc.fecha("2026-03-05") == date(2026, 3, 5)
    assert bc.fecha(np.datetime64("2026-03-05")) == date(2026, 3, 5)
    assert bc.fecha(None) is None and bc.fecha(pd.NaT) is None


# ---------------- editor -> tuplas ----------------
def test_tuplas_metas_omite_filas_sin_minimo_y_usa_valores_por_defecto():
    df = pd.DataFrame({"umbral_min": [0.0, np.nan, 430e6], "umbral_max": [430e6, np.nan, np.nan],
                       "pct_aplica": [0.0, np.nan, 1.0],
                       "min_inclusivo": [True, None, None], "max_inclusivo": [False, None, None]})
    t = bc.tuplas_metas(df)
    assert t == [(0.0, True, 430e6, False, 0.0), (430e6, True, None, False, 1.0)]
    assert all(type(x) in (float, bool, type(None)) for fila in t for x in fila)


def test_tuplas_escala_omite_filas_vacias():
    df = pd.DataFrame({"cond_min": [0.1, np.nan], "cond_max": [np.nan, np.nan], "pct_bono": [1.0, np.nan],
                       "min_inclusivo": [True, None], "max_inclusivo": [False, None]})
    assert bc.tuplas_escala(df) == [(0.1, True, None, False, 1.0)]


# ---------------- evaluación ----------------
def test_evaluar_primera_fila_que_coincide():
    assert bc.evaluar(500e6, METAS_EJECOM, 0.0) == 1.0
    assert bc.evaluar(429_999_999, METAS_EJECOM, 0.0) == 0.0
    assert bc.evaluar(430e6, METAS_EJECOM, 0.0) == 1.0
    # la fila general (>= 10 %) va después de las específicas: gana el orden
    assert bc.evaluar(0.12, ESCALA_RENT_DEFAULT, -1.0) == 0.60
    assert bc.evaluar(0.10, ESCALA_RENT_DEFAULT, -1.0) == 0.0
    assert bc.evaluar(0.20, ESCALA_RENT_DEFAULT, -1.0) == 1.10
    assert bc.evaluar(-0.02, ESCALA_RENT_DEFAULT, -1.0) == -1.0


# ---------------- validación ----------------
@pytest.mark.parametrize("metas", [METAS_2025, METAS_2026, METAS_EJECOM])
def test_los_tramos_estandar_no_dan_errores_ni_avisos(metas):
    assert bc.validar_metas(metas) == ([], [])


def test_la_escala_estandar_no_da_errores_ni_avisos():
    # tiene una fila general al final: un traslape resuelto por orden NO es un error
    assert bc.validar_escala(ESCALA_RENT_DEFAULT) == ([], [])


def test_tabla_vacia_es_error():
    assert bc.validar_metas([])[0] and bc.validar_escala([])[0]


def test_intervalo_vacio_es_error():
    err, _ = bc.validar_metas([(500.0, True, 100.0, False, 1.0)])
    assert any("vacío" in e for e in err)
    err, _ = bc.validar_metas([(100.0, True, 100.0, False, 1.0)])   # [100, 100) no contiene nada
    assert any("vacío" in e for e in err)
    assert bc.validar_metas([(100.0, True, 100.0, True, 1.0)])[0] == []   # [100, 100] sí


def test_porcentaje_fuera_de_rango_o_faltante_es_error():
    assert any("fuera de rango" in e for e in bc.validar_metas([(0.0, True, None, False, 1.5)])[0])
    assert any("falta" in e for e in bc.validar_metas([(0.0, True, None, False, None)])[0])
    assert any("fuera de rango" in e for e in bc.validar_escala([(0.0, True, None, False, 3.0)])[0])


def test_hueco_entre_tramos_da_aviso_no_error():
    metas = [(0.0, True, 100.0, False, 0.0), (200.0, True, None, False, 1.0)]
    err, av = bc.validar_metas(metas)
    assert err == [] and any("sin fila" in a for a in av)


def test_fila_que_nunca_se_aplica_da_aviso():
    metas = [(0.0, True, None, False, 0.0), (500.0, True, None, False, 1.0)]
    err, av = bc.validar_metas(metas)
    assert err == [] and any("Fila 2" in a and "nunca" in a for a in av)


def test_tramos_sin_cubrir_valores_altos_dan_aviso():
    err, av = bc.validar_metas([(0.0, True, 100.0, False, 0.0), (100.0, True, 200.0, False, 1.0)])
    assert err == [] and av
