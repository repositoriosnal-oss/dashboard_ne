"""
Pruebas del motor de distribución por bolsa de cargo y períodos de vigencia.
Ejecutar:
    python pruebas/test_rrhh_dist.py
"""
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import rrhh_dist as dist

MES = date(2026, 8, 1)  # Agosto 2026: 31 días


def periodo_test(pid=1, alm="CENTRO6", f_ini=date(2026, 1, 1), f_fin=None):
    return {
        "id": pid,
        "almacen": alm,
        "nombre": "Politica 2026",
        "fecha_inicio": f_ini,
        "fecha_fin": f_fin,
        "activo": True
    }


def cargos_test(pid=1, **cargos):
    return [{"periodo_id": pid, "cargo": c, "pct": p} for c, p in cargos.items()]


def asig(cid, cargo="Vendedor 2", desde=date(2026, 8, 1), hasta=None, almacen="CENTRO6", **kw):
    base = {
        "colaborador_id": cid, "cedula": str(1000 + cid), "nombre": f"Persona {cid}",
        "almacen": almacen, "cargo": cargo, "desde": desde, "hasta": hasta,
        "activo": True, "fecha_inactividad": None, "en_prueba": False,
        "prueba_inicio": None, "prueba_fin": None
    }
    base.update(kw)
    return base


def test_reparto_tres_vendedores_mes_completo():
    """3 vendedores en el cargo: el 45% se reparte exactamente en 15% cada uno."""
    per = [periodo_test()]
    cargos = cargos_test(1, **{"Gerente Punto": 25.0, "Vendedor 2": 45.0, "Cajera": 15.0, "Bodega 1": 15.0})
    asigs = [
        asig(1, "Gerente Punto"),
        asig(2, "Vendedor 2"),
        asig(3, "Vendedor 2"),
        asig(4, "Vendedor 2"),
        asig(5, "Cajera"),
        asig(6, "Bodega 1")
    ]
    r = dist.calcular_reparto_mes("CENTRO6", MES, per, cargos, asigs)
    vendedores = [p for p in r["personas"] if p["cargo"] == "Vendedor 2"]
    assert len(vendedores) == 3
    for v in vendedores:
        assert abs(v["pct_efectivo"] - 15.0) < 1e-4
    assert abs(r["totales"]["repartido"] - 100.0) < 1e-4
    assert r["totales"]["vacante"] == 0.0


def test_reparto_dos_vendedores_mes_completo():
    """2 vendedores en el cargo: el 45% se reparte en 22.5% cada uno."""
    per = [periodo_test()]
    cargos = cargos_test(1, **{"Vendedor 2": 45.0})
    asigs = [asig(1, "Vendedor 2"), asig(2, "Vendedor 2")]
    r = dist.calcular_reparto_mes("CENTRO6", MES, per, cargos, asigs)
    vendedores = [p for p in r["personas"] if p["cargo"] == "Vendedor 2"]
    assert len(vendedores) == 2
    for v in vendedores:
        assert abs(v["pct_efectivo"] - 22.5) < 1e-4
    assert abs(r["totales"]["repartido"] - 45.0) < 1e-4


def test_reparto_un_vendedor():
    """1 solo vendedor: recibe el 45% completo."""
    per = [periodo_test()]
    cargos = cargos_test(1, **{"Vendedor 2": 45.0})
    asigs = [asig(1, "Vendedor 2")]
    r = dist.calcular_reparto_mes("CENTRO6", MES, per, cargos, asigs)
    assert abs(r["personas"][0]["pct_efectivo"] - 45.0) < 1e-4


def test_prorrateo_dias_distintos():
    """Vendedor 1: 30 días; Vendedor 2: 15 días (ingresó a mitad de mes). Total días = 45."""
    per = [periodo_test()]
    cargos = cargos_test(1, **{"Vendedor 2": 30.0})
    asigs = [
        asig(1, "Vendedor 2", desde=date(2026, 8, 1), hasta=date(2026, 8, 30)),   # 30 días
        asig(2, "Vendedor 2", desde=date(2026, 8, 16), hasta=date(2026, 8, 30)),  # 15 días
    ]
    r = dist.calcular_reparto_mes("CENTRO6", MES, per, cargos, asigs)
    p1 = next(p for p in r["personas"] if p["colaborador_id"] == 1)
    p2 = next(p for p in r["personas"] if p["colaborador_id"] == 2)
    assert abs(p1["pct_efectivo"] - 20.0) < 1e-4
    assert abs(p2["pct_efectivo"] - 10.0) < 1e-4
    assert abs(p1["pct_efectivo"] + p2["pct_efectivo"] - 30.0) < 1e-4


def test_cargo_vacante_queda_libre():
    """Si no hay nadie en Cajera (15%), queda como vacante / libre."""
    per = [periodo_test()]
    cargos = cargos_test(1, **{"Gerente Punto": 30.0, "Cajera": 15.0})
    asigs = [asig(1, "Gerente Punto")]
    r = dist.calcular_reparto_mes("CENTRO6", MES, per, cargos, asigs)
    assert abs(r["totales"]["repartido"] - 30.0) < 1e-4
    assert abs(r["totales"]["vacante"] - 15.0) < 1e-4
    assert any(a["tipo"] == "CARGO_VACANTE" for a in r["alertas"])


def test_periodo_de_prueba_no_se_paga():
    """Colaborador en prueba: su parte se aparta en pct_en_prueba."""
    per = [periodo_test()]
    cargos = cargos_test(1, **{"Vendedor 2": 40.0})
    asigs = [
        asig(1, "Vendedor 2"),
        asig(2, "Vendedor 2", en_prueba=True, prueba_inicio=date(2026, 8, 1), prueba_fin=date(2026, 8, 31))
    ]
    r = dist.calcular_reparto_mes("CENTRO6", MES, per, cargos, asigs)
    p1 = next(p for p in r["personas"] if p["colaborador_id"] == 1)
    p2 = next(p for p in r["personas"] if p["colaborador_id"] == 2)
    assert abs(p1["pct_efectivo"] - 20.0) < 1e-4
    assert abs(p2["pct_efectivo"] - 0.0) < 1e-4
    assert abs(p2["pct_en_prueba"] - 20.0) < 1e-4
    assert abs(r["totales"]["repartido"] - 20.0) < 1e-4
    assert abs(r["totales"]["en_prueba"] - 20.0) < 1e-4


def test_resolucion_periodo_abierto():
    """Un período con fecha_fin=None rige para cualquier fecha posterior."""
    p1 = periodo_test(1, "CENTRO6", date(2025, 1, 1), date(2025, 12, 31))
    p2 = periodo_test(2, "CENTRO6", date(2026, 1, 1), None)
    periodos = [p1, p2]
    res2025 = dist.resolver_periodo(periodos, date(2025, 6, 1))
    assert res2025["id"] == 1
    res2026 = dist.resolver_periodo(periodos, date(2026, 8, 1))
    assert res2026["id"] == 2
    res2027 = dist.resolver_periodo(periodos, date(2027, 2, 1))
    assert res2027["id"] == 2


def test_monto_dinero_exacto_al_peso():
    """El algoritmo del resto mayor reparte al peso exacto sin perder un centavo."""
    pcts = {"carlos": 22.0, "ana": 26.0, "juan": 26.0, "luis": 26.0}
    bono_total = 1_000_003
    montos = dist.monto_por_porcentaje(bono_total, pcts, decimales=0)
    assert sum(montos.values()) == Decimal(bono_total)


if __name__ == "__main__":
    fallos = 0
    for nombre, fn in sorted(globals().items()):
        if nombre.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"OK    {nombre}")
            except AssertionError as e:
                fallos += 1
                print(f"FALLA {nombre}: {e}")
    sys.exit(1 if fallos else 0)