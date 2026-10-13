"""Ejecutar:  python pruebas/test_rrhh_nov.py"""
import os, sys
from datetime import date
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import rrhh_nov as nov

d = date.fromisoformat
CONFIG = [{"tipo_falta_id": i, "pct_descuento": p, "vigente_desde": d("2025-01-01"), "vigente_hasta": None}
          for i, p in ((1, 15.0), (2, 30.0), (3, 45.0))]

def test_dominical_y_reparto():
    assert nov.primer_domingo_despues(d("2026-01-16")) == d("2026-01-18")
    assert nov.dias_por_mes(d("2026-01-29"), d("2026-02-03")) == {d("2026-01-01"): 3, d("2026-02-01"): 3}
    dm = nov.dias_por_mes(d("2026-01-29"), d("2026-02-03"), incluye_dominical=True)
    assert dm[d("2026-02-01")] == 4                      # dominical 8-feb cae en febrero
    assert nov.pct_por_dias(3) == 10.0

def test_persona_mes_faltas_mas_dias():
    ns = [{"tipo": "FALTA", "tipo_falta_id": 1, "grado": "Falta Tipo 1",
           "fecha_inicio": d("2026-01-10"), "fecha_fin": d("2026-01-10"), "incluye_dominical": False},
          {"tipo": "FALTA", "tipo_falta_id": 2, "grado": "Falta Tipo 2",
           "fecha_inicio": d("2026-01-20"), "fecha_fin": d("2026-01-20"), "incluye_dominical": False},
          {"tipo": "MEMORANDO", "tipo_falta_id": None, "grado": None,
           "fecha_inicio": d("2026-01-15"), "fecha_fin": d("2026-01-16"), "incluye_dominical": True}]
    r = nov.calcular_persona_mes(ns, CONFIG, d("2026-01-01"))
    assert r["pct_faltas"] == 45.0 and r["dias_total"] == 3 and r["pct_dias"] == 10.0
    assert r["total"] == 55.0 and not r["tope_aplicado"]

def test_tope_y_vacaciones_cruzadas():
    ns = [{"tipo": "FALTA", "tipo_falta_id": 3, "grado": "T3", "fecha_inicio": d("2026-01-05"),
           "fecha_fin": d("2026-01-05"), "incluye_dominical": False},
          {"tipo": "INCAPACIDAD", "tipo_falta_id": None, "grado": None,
           "fecha_inicio": d("2026-01-01"), "fecha_fin": d("2026-01-31"), "incluye_dominical": False}]
    r = nov.calcular_persona_mes(ns, CONFIG, d("2026-01-01"))
    assert r["tope_aplicado"] and r["total"] == 100.0
    vac = [{"tipo": "VACACIONES", "tipo_falta_id": None, "grado": None,
            "fecha_inicio": d("2026-01-25"), "fecha_fin": d("2026-02-10"), "incluye_dominical": False}]
    assert nov.calcular_persona_mes(vac, CONFIG, d("2026-01-01"))["dias_total"] == 7
    assert nov.calcular_persona_mes(vac, CONFIG, d("2026-02-01"))["dias_total"] == 10

def test_validaciones_y_vigencias():
    assert nov.validar_novedad("FALTA", None, d("2026-01-05"), d("2026-01-05"), False, d("2026-01-01"))[0]
    assert nov.validar_novedad("FALTA", "T1", d("2026-02-05"), d("2026-02-05"), False, d("2026-01-01"))[0]
    assert nov.validar_novedad("MEMORANDO", None, d("2026-01-05"), d("2026-01-04"), False)[0]
    assert not nov.validar_novedad("PERMISO", None, d("2026-01-05"), d("2026-01-08"), True, d("2026-01-01"))[0]
    cfg = CONFIG + [{"tipo_falta_id": 1, "pct_descuento": 20.0,
                     "vigente_desde": d("2026-06-01"), "vigente_hasta": None}]
    assert nov.pct_falta_vigente(cfg, 1, d("2026-05-01")) == 15.0
    assert nov.pct_falta_vigente(cfg, 1, d("2026-06-01")) == 20.0

if __name__ == "__main__":
    fs = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    fallos = 0
    for n, f in fs:
        try:
            f(); print(f"OK    {n}")
        except Exception as e:
            fallos += 1; print(f"FALLA {n}: {type(e).__name__}: {e}")
    print(f"\n{len(fs) - fallos}/{len(fs)} pruebas pasaron")
    sys.exit(1 if fallos else 0)