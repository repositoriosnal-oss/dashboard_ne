"""
Pruebas del motor de distribución (src/rrhh_dist.py). No necesitan base de datos ni streamlit.

Ejecutar:  pytest pruebas/test_rrhh_dist.py     o     python pruebas/test_rrhh_dist.py
"""
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import rrhh_dist as dist  # noqa: E402

MES = date(2026, 3, 1)  # marzo: 31 días


# ------------------------------------------------------------------ ayudas
def periodo(cargos, inicio=date(2026, 1, 1), fin=None, regla=dist.REGLA_POOL, diluye=True, almacen="Q6", id=1):
    return {"id": id, "almacen": almacen, "inicio": inicio, "fin": fin, "regla": regla, "prueba_diluye": diluye,
            "cargos": [{"cargo": c, "pct": p, "cupos": k} for c, p, k in cargos]}


def asig(cid, cargo="Vendedor 3", desde=date(2026, 1, 1), hasta=None, almacen="Q6", **kw):
    base = {"colaborador_id": cid, "cedula": str(1000 + cid), "nombre": f"Persona {cid}", "almacen": almacen,
            "cargo": cargo, "desde": desde, "hasta": hasta, "activo": True, "fecha_inactividad": None,
            "en_prueba": False, "prueba_inicio": None, "prueba_fin": None}
    base.update(kw)
    return base


def cerca(a, b, tol=1e-9):
    return abs(a - b) <= tol


def tipos(r):
    return {a["tipo"] for a in r["alertas"]}


def invariante(r):
    t = r["totales"]
    assert cerca(t["pagado"] + t["prueba"] + t["libre"], t["asignado"]), t
    assert cerca(t["asignado"] + t["sin_definir"], 100.0), t
    assert t["pagado"] + t["prueba"] <= 100.0 + 1e-9, t


# Almacén tipo: Gerente 40 %, Vendedor 3 30 %, Cajera 30 %  (bolsas por cargo)
P_COMPLETO = [("Gerente Punto", 40, None), ("Vendedor 3", 30, None), ("Cajera", 30, None)]


def reparto(asigs, per=None, mes=MES):
    return dist.calcular_reparto_mes("Q6", mes, [per or periodo(P_COMPLETO)], asigs)


def persona(r, cid):
    return next(p for p in r["personas"] if p["colaborador_id"] == cid)


# ------------------------------------------------------------------ regla POOL: bolsa por cargo
def test_ejemplo_del_negocio_tres_vendedores_10_cada_uno():
    asigs = [asig(1, "Gerente Punto"), asig(2, "Cajera"), asig(3), asig(4), asig(5)]
    r = reparto(asigs)
    for cid in (3, 4, 5):
        assert cerca(persona(r, cid)["pct_efectivo"], 10.0)
    assert cerca(r["totales"]["pagado"], 100.0) and r["alertas"] == []
    invariante(r)


def test_ejemplo_del_negocio_dos_vendedores_15_cada_uno():
    asigs = [asig(1, "Gerente Punto"), asig(2, "Cajera"), asig(3), asig(4)]
    r = reparto(asigs)
    assert cerca(persona(r, 3)["pct_efectivo"], 15.0) and cerca(persona(r, 4)["pct_efectivo"], 15.0)
    assert cerca(r["totales"]["pagado"], 100.0)
    invariante(r)


def test_salida_a_mitad_de_mes_la_bolsa_se_redistribuye_por_dia():
    """B sale el 10 (10 días): del 1 al 10 son 2 personas (15 c/u), del 11 al 31 queda solo A (30)."""
    asigs = [asig(1, "Gerente Punto"), asig(2, "Cajera"), asig(3), asig(4, hasta=date(2026, 3, 10))]
    r = reparto(asigs)
    assert cerca(persona(r, 4)["pct_efectivo"], 15 * 10 / 31)
    assert cerca(persona(r, 3)["pct_efectivo"], (15 * 10 + 30 * 21) / 31)
    assert cerca(r["totales"]["pagado"], 100.0)
    invariante(r)


def test_reemplazo_con_cruce_nunca_excede_la_bolsa():
    """Vendedores: 2 personas (15 % c/u), 3 durante el cruce del 8 al 10 (10 % c/u). El total sigue en 100."""
    asigs = [asig(1, "Gerente Punto"), asig(2, "Cajera"), asig(3),
             asig(4, hasta=date(2026, 3, 10)), asig(5, desde=date(2026, 3, 8))]
    r = reparto(asigs)
    assert cerca(r["totales"]["pagado"], 100.0)
    c = next(x for x in r["cargos"] if x["cargo"] == "Vendedor 3")
    assert cerca(c["pct_persona_min"], 10.0) and cerca(c["pct_persona_max"], 15.0)   # 3 en el cruce, 2 el resto
    invariante(r)


def test_cargo_sin_personas_libera_la_bolsa():
    asigs = [asig(1, "Gerente Punto"), asig(2, "Cajera")]
    r = reparto(asigs)
    assert cerca(r["totales"]["libre"], 30.0) and cerca(r["totales"]["pagado"], 70.0)
    assert "VACANTE" in tipos(r)
    invariante(r)


def test_prueba_diluye_su_parte_queda_aparte():
    asigs = [asig(1, "Gerente Punto"), asig(2, "Cajera"), asig(3),
             asig(4, prueba_inicio=date(2026, 3, 1), prueba_fin=date(2026, 3, 31), en_prueba=True)]
    r = reparto(asigs, periodo(P_COMPLETO, diluye=True))
    assert cerca(persona(r, 3)["pct_efectivo"], 15.0)
    assert cerca(persona(r, 4)["pct_efectivo"], 0.0) and cerca(persona(r, 4)["pct_en_prueba"], 15.0)
    assert cerca(r["totales"]["prueba"], 15.0)
    invariante(r)


def test_prueba_no_diluye_la_bolsa_va_a_los_demas():
    asigs = [asig(1, "Gerente Punto"), asig(2, "Cajera"), asig(3),
             asig(4, prueba_inicio=date(2026, 3, 1), prueba_fin=date(2026, 3, 31), en_prueba=True)]
    r = reparto(asigs, periodo(P_COMPLETO, diluye=False))
    assert cerca(persona(r, 3)["pct_efectivo"], 30.0)
    assert cerca(r["totales"]["prueba"], 0.0) and cerca(r["totales"]["pagado"], 100.0)
    invariante(r)


def test_todos_en_prueba_y_no_diluye_libera_la_bolsa():
    asigs = [asig(1, "Gerente Punto"), asig(2, "Cajera"),
             asig(3, prueba_inicio=date(2026, 3, 1), prueba_fin=date(2026, 3, 31), en_prueba=True)]
    r = reparto(asigs, periodo(P_COMPLETO, diluye=False))
    assert cerca(r["totales"]["libre"], 30.0)
    invariante(r)


def test_prueba_a_mitad_de_mes_mezcla_dias():
    asigs = [asig(1, "Gerente Punto"), asig(2, "Cajera"), asig(3),
             asig(4, prueba_inicio=date(2026, 3, 1), prueba_fin=date(2026, 3, 15), en_prueba=True)]
    r = reparto(asigs)
    assert cerca(persona(r, 4)["pct_en_prueba"], 15 * 15 / 31)
    assert cerca(persona(r, 4)["pct_efectivo"], 15 * 16 / 31)
    invariante(r)


# ------------------------------------------------------------------ periodos por almacén
def test_periodos_distintos_por_mes_y_por_almacen():
    q6_mar = periodo([("Gerente Punto", 22, None), ("Vendedor 3", 78, None)], inicio=date(2026, 1, 1),
                     fin=date(2026, 3, 31), id=1)
    q6_abr = periodo([("Gerente Punto", 25, None), ("Vendedor 3", 75, None)], inicio=date(2026, 4, 1), id=2)
    n128 = periodo([("Gerente Punto", 19, None), ("Vendedor 3", 81, None)], almacen="N128", id=3)
    asigs = [asig(1, "Gerente Punto"), asig(2), asig(3, "Gerente Punto", almacen="N128"), asig(4, almacen="N128")]
    marzo = dist.calcular_reparto_mes("Q6", date(2026, 3, 1), [q6_mar, q6_abr, n128], asigs)
    abril = dist.calcular_reparto_mes("Q6", date(2026, 4, 1), [q6_mar, q6_abr, n128], asigs)
    norte = dist.calcular_reparto_mes("N128", date(2026, 3, 1), [q6_mar, q6_abr, n128], asigs)
    assert cerca(persona(marzo, 1)["pct_efectivo"], 22.0)
    assert cerca(persona(abril, 1)["pct_efectivo"], 25.0)
    assert cerca(persona(norte, 3)["pct_efectivo"], 19.0)


def test_mes_sin_periodo_queda_libre_y_avisa():
    solo_feb = periodo(P_COMPLETO, inicio=date(2026, 2, 1), fin=date(2026, 2, 28))
    r = reparto([asig(1, "Gerente Punto")], solo_feb)
    assert "SIN_PERIODO" in tipos(r)
    assert cerca(r["totales"]["sin_definir"], 100.0) and cerca(r["totales"]["pagado"], 0.0)
    assert r["personas"][0]["pct_efectivo"] is None
    invariante(r)


def test_mes_sin_periodo_y_sin_personas_solo_informa():
    r = reparto([], periodo(P_COMPLETO, inicio=date(2026, 6, 1)))
    assert [a["nivel"] for a in r["alertas"] if a["tipo"] == "SIN_PERIODO"] == ["info"]


def test_periodo_incompleto_avisa_y_el_resto_queda_sin_definir():
    r = reparto([asig(1, "Gerente Punto")], periodo([("Gerente Punto", 60, None)]))
    assert "PLANTILLA_INCOMPLETA" in tipos(r)
    assert cerca(r["totales"]["pagado"], 60.0) and cerca(r["totales"]["sin_definir"], 40.0)
    invariante(r)


def test_cargo_sin_bolsa_en_el_periodo_es_error():
    r = reparto([asig(1, "Gerente Punto"), asig(2, "Conductor")])
    assert "SIN_PCT" in tipos(r)
    assert persona(r, 2)["pct_efectivo"] is None


def test_validar_fechas_solape_inclusivo_y_meses_completos():
    existente = periodo(P_COMPLETO, inicio=date(2026, 1, 1), fin=date(2026, 3, 31))
    assert dist.validar_fechas_periodo([existente], date(2026, 4, 1), None) == []
    assert any("solapa" in e for e in dist.validar_fechas_periodo([existente], date(2026, 3, 1), date(2026, 3, 31)))
    assert any("día 1" in e for e in dist.validar_fechas_periodo([existente], date(2026, 4, 15), None))
    assert any("último día" in e for e in dist.validar_fechas_periodo([existente], date(2026, 4, 1), date(2026, 4, 20)))
    assert dist.validar_fechas_periodo([existente], date(2026, 1, 1), date(2026, 3, 31), excluir_id=1) == []


def test_crear_periodo_cierra_el_abierto_anterior():
    abierto = periodo(P_COMPLETO, inicio=date(2026, 1, 1), fin=None)
    plan = dist.plan_alta_periodo([abierto], date(2026, 4, 1), None)
    assert plan["errores"] == []
    previo, nuevo_fin = plan["cerrar"]
    assert previo is abierto and nuevo_fin == date(2026, 3, 31)


def test_crear_periodo_cerrado_dentro_de_uno_abierto_avisa_del_hueco():
    abierto = periodo(P_COMPLETO, inicio=date(2026, 1, 1), fin=None)
    plan = dist.plan_alta_periodo([abierto], date(2026, 4, 1), date(2026, 5, 31))
    assert plan["errores"] == [] and plan["aviso"] and "sin porcentajes" in plan["aviso"]


def test_no_se_puede_abrir_un_periodo_antes_de_otro_posterior():
    posterior = periodo(P_COMPLETO, inicio=date(2026, 6, 1), fin=None)
    plan = dist.plan_alta_periodo([posterior], date(2026, 1, 1), None)
    assert plan["errores"] and plan["cerrar"] is None


def test_huecos_detecta_meses_sin_periodo():
    per = [periodo(P_COMPLETO, inicio=date(2026, 1, 1), fin=date(2026, 2, 28), id=1),
           periodo(P_COMPLETO, inicio=date(2026, 5, 1), fin=None, id=2)]
    assert dist.huecos(per, date(2026, 1, 1), date(2026, 6, 1)) == [(date(2026, 3, 1), date(2026, 4, 1))]
    assert dist.huecos(per, date(2025, 11, 1), date(2026, 2, 1)) == [(date(2025, 11, 1), date(2025, 12, 1))]


# ------------------------------------------------------------------ validación de bolsas
def test_validar_cargos_nunca_pasa_de_100():
    errs, total = dist.validar_cargos([{"cargo": "Gerente Punto", "pct": 60}, {"cargo": "Cajera", "pct": 41}],
                                      dist.REGLA_POOL)
    assert total == 101.0 and any("100" in e for e in errs)


def test_validar_cargos_acepta_exactamente_100_con_decimales():
    errs, total = dist.validar_cargos([{"cargo": "Gerente Punto", "pct": 33.333}, {"cargo": "Cajera", "pct": 33.333},
                                       {"cargo": "Vendedor 3", "pct": 33.334}], dist.REGLA_POOL)
    assert errs == [] and total == 100.0


def test_validar_cargos_detecta_repetidos_invalidos_y_cupos():
    errs, _ = dist.validar_cargos([{"cargo": "Cajera", "pct": 10}, {"cargo": "Cajera", "pct": 5}], dist.REGLA_POOL)
    assert any("repetido" in e for e in errs)
    errs, _ = dist.validar_cargos([{"cargo": "Ejecutivo Comercial", "pct": 10}], dist.REGLA_POOL)
    assert any("no es un cargo" in e for e in errs)
    errs, _ = dist.validar_cargos([{"cargo": "Cajera", "pct": 10, "cupos": None}], dist.REGLA_CUPO)
    assert any("cupos" in e for e in errs)
    errs, _ = dist.validar_cargos([{"cargo": "Cajera", "pct": 0}], dist.REGLA_POOL)
    assert errs


# ------------------------------------------------------------------ regla POR_CUPO (modelo anterior)
def legacy(*puestos, desde=date(2026, 1, 1)):
    return [{"almacen": "Q6", "cargo": c, "pct": p, "cupos": k, "vigente_desde": desde, "vigente_hasta": None}
            for c, p, k in puestos]


def test_importar_modelo_anterior_conserva_los_resultados():
    """Plantilla anterior: Gerente 40 (1 cupo), Vendedor 2 20 por persona (3 cupos) = 100 %."""
    pers = dist.plantilla_a_periodos(legacy(("Gerente Punto", 40, 1), ("Vendedor 2", 20, 3)))
    assert len(pers) == 1 and pers[0]["regla"] == dist.REGLA_CUPO and pers[0]["fin"] is None
    assert {c["cargo"]: c["pct"] for c in pers[0]["cargos"]} == {"Gerente Punto": 40.0, "Vendedor 2": 60.0}
    asigs = [asig(1, "Gerente Punto"), asig(2, "Vendedor 2"), asig(3, "Vendedor 2")]   # falta 1 vendedor
    r = dist.calcular_reparto_mes("Q6", MES, pers, asigs)
    assert cerca(persona(r, 2)["pct_efectivo"], 20.0)           # NO sube a 30: es por cupo
    assert cerca(r["totales"]["libre"], 20.0) and cerca(r["totales"]["pagado"], 80.0)
    assert "VACANTE" in tipos(r)
    invariante(r)


def test_por_cupo_reemplazo_con_cruce_reparte_el_cupo():
    pers = dist.plantilla_a_periodos(legacy(("Gerente Punto", 40, 1), ("Vendedor 2", 20, 3)))
    asigs = [asig(1, "Gerente Punto"), asig(2, "Vendedor 2"), asig(3, "Vendedor 2"),
             asig(4, "Vendedor 2", hasta=date(2026, 3, 10)), asig(5, "Vendedor 2", desde=date(2026, 3, 8))]
    r = dist.calcular_reparto_mes("Q6", MES, pers, asigs)
    assert cerca(r["totales"]["pagado"], 100.0)
    assert "SOBRECUPO" in tipos(r)
    invariante(r)


def test_importar_varias_versiones_cierra_cada_una_el_mes_anterior():
    v1 = legacy(("Gerente Punto", 100, 1), desde=date(2026, 1, 1))
    v2 = legacy(("Gerente Punto", 60, 1), ("Vendedor 2", 40, 1), desde=date(2026, 3, 1))
    pers = sorted(dist.plantilla_a_periodos(v1 + v2), key=lambda p: p["inicio"])
    assert pers[0]["fin"] == date(2026, 2, 28) and pers[1]["inicio"] == date(2026, 3, 1) and pers[1]["fin"] is None
    assert dist.validar_fechas_periodo(pers[:1], pers[1]["inicio"], pers[1]["fin"]) == []


# ------------------------------------------------------------------ datos y otras reglas
def test_traslado_entre_almacenes_suma_un_mes():
    a_q6 = asig(9, hasta=date(2026, 3, 15), almacen="Q6")
    a_otro = asig(9, desde=date(2026, 3, 16), almacen="Q7")
    pers = [periodo(P_COMPLETO), periodo(P_COMPLETO, almacen="Q7", id=2)]
    r6 = dist.calcular_reparto_mes("Q6", MES, pers, [a_q6, a_otro])
    r7 = dist.calcular_reparto_mes("Q7", MES, pers, [a_q6, a_otro])
    assert cerca(r6["personas"][0]["fraccion"] + r7["personas"][0]["fraccion"], 1.0)
    assert r6["personas"][0]["dias"] == 15 and r7["personas"][0]["dias"] == 16


def test_ultimo_dia_inclusivo_detecta_solape():
    asigs = [asig(1, "Gerente Punto"), asig(4, hasta=date(2026, 3, 31)), asig(4, desde=date(2026, 3, 31))]
    assert "ASIGNACION_SOLAPADA" in tipos(reparto(asigs))


def test_inactivo_sin_fecha_se_excluye_e_inactivacion_recorta():
    r = reparto([asig(1, "Gerente Punto"), asig(2, activo=False)])
    assert all(p["colaborador_id"] != 2 for p in r["personas"]) and "DATOS" in tipos(r)
    r = reparto([asig(1, "Gerente Punto", activo=False, fecha_inactividad=date(2026, 3, 10))])
    assert r["personas"][0]["dias"] == 10


def test_prueba_marcada_sin_fechas_avisa():
    assert "PRUEBA_SIN_FECHAS" in tipos(reparto([asig(1, "Gerente Punto", en_prueba=True)]))


def test_modo_individual_no_usa_periodos():
    r = dist.calcular_reparto_mes("EJECOM", MES, [], [asig(1, "Ejecutivo Comercial", almacen="EJECOM")],
                                  dist.MODO_INDIVIDUAL)
    assert r["totales"]["asignado"] == 0.0 and r["personas"][0]["pct_efectivo"] is None


def test_armar_periodos_une_filas_de_la_base():
    per = [{"id": 7, "almacen": "Q6", "fecha_inicio": date(2026, 1, 1), "fecha_fin": None,
            "regla_reparto": "POOL", "prueba_diluye": True}]
    car = [{"id": 1, "periodo_id": 7, "cargo": "Cajera", "pct": 30, "cupos": None},
           {"id": 2, "periodo_id": 7, "cargo": "Gerente Punto", "pct": 70, "cupos": float("nan")}]
    p = dist.armar_periodos(per, car)[0]
    assert [c["cargo"] for c in p["cargos"]] == ["Gerente Punto", "Cajera"] and p["cargos"][0]["cupos"] is None


def test_monto_por_porcentaje_suma_exacta():
    montos = dist.monto_por_porcentaje(1_000_003, {"a": Decimal("33.333"), "b": Decimal("33.333"),
                                                   "c": Decimal("33.334")})
    assert sum(montos.values()) == Decimal(1_000_003)
    assert sum(dist.monto_por_porcentaje(1_000_000, {"a": 40, "b": 40}).values()) == Decimal(800_000)


def test_invariante_en_un_mes_complejo():
    asigs = [asig(1, "Gerente Punto", hasta=date(2026, 3, 9)), asig(2, "Gerente Punto", desde=date(2026, 3, 12)),
             asig(3, "Cajera"), asig(4), asig(5, hasta=date(2026, 3, 20)), asig(6, desde=date(2026, 3, 18)),
             asig(7, desde=date(2026, 3, 5), prueba_inicio=date(2026, 3, 5), prueba_fin=date(2026, 4, 30))]
    for diluye in (True, False):
        invariante(reparto(asigs, periodo(P_COMPLETO, diluye=diluye)))


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