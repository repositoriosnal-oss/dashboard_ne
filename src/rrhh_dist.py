"""Distribución del bono por almacén, cargo, persona y PERIODO.
Independiente de src/rrhh_th.py.

Tablas que usa:
  rrhh.colaboradores, rrhh.asignaciones, app.almacenes, rrhh.rrhh_auditoria  (ya existen)
  rrhh.dist_periodos, rrhh.dist_periodo_cargos                               (nuevas, las crea migrar())
  rrhh.distribucion_puestos                                                  (modelo anterior, solo lectura/importación)

Modelo:
* PERIODO por almacén: rige desde un mes hasta un mes (inclusive) o queda ABIERTO (sin fecha fin).
  Dentro de un almacén los periodos no se solapan; los meses sin periodo quedan «sin definir» (libres).
* BOLSA POR CARGO: cada periodo asigna a cada cargo un % del bono del almacén (la suma <= 100%).
* REGLA DE REPARTO:
    POOL     Cada día la bolsa del cargo se divide en partes iguales entre las personas que ocupan el cargo ese día.
    POR_CUPO Modelo anterior: cada cargo tiene `cupos`; cada persona recibe bolsa/cupos.
* PRUEBA: las personas en periodo de prueba ocupan su lugar pero no participan.
* Cálculo DIARIO con fracciones exactas:
        pagado + en prueba + liberado = asignado
        y
        asignado + sin definir = 100
"""

from __future__ import annotations

import calendar
import json
import logging
from datetime import date, datetime, timedelta
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from fractions import Fraction
from typing import Any

log = logging.getLogger(__name__)

# Cargos que reparten bono por porcentaje
CARGOS_BONO = [
    "Gerente Punto", "Vendedor 1", "Vendedor 2", "Vendedor 3",
    "Cajera", "Bodega 1", "Bodega 2", "Conductor"
]

MODO_PORCENTAJE = "PORCENTAJE"
MODO_INDIVIDUAL = "INDIVIDUAL"

REGLA_POOL = "POOL"
REGLA_CUPO = "POR_CUPO"
REGLAS = {
    REGLA_POOL: "El % del cargo se divide entre quienes lo ocupan",
    REGLA_CUPO: "% fijo por cupo (modelo anterior)"
}

MES_COMPLETO = True
FIN_ABIERTO = date(2100, 12, 31)
TOL = 0.0005

MESES_ES = [
    "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"
]

# ============================================================
# UTILIDADES NUMÉRICAS
# ============================================================

def _fr(x) -> Fraction:
    """float | Decimal | str | int -> Fraction exacta (pasando por str)."""
    return Fraction(str(x))


def monto_por_porcentaje(bono, pcts: dict, decimales: int = 0) -> dict:
    """Reparte en dinero lo que corresponde a cada persona: bono × pct / 100."""
    paso = Decimal(1).scaleb(-decimales)
    pesos = {k: Decimal(str(v)) for k, v in pcts.items()}
    suma = sum(pesos.values(), Decimal(0))
    if suma <= 0:
        return {k: Decimal(0) for k in pcts}
    total = (Decimal(str(bono)) * suma / Decimal(100)).quantize(paso, rounding=ROUND_HALF_UP)
    brutos = {k: total * w / suma for k, w in pesos.items()}
    base = {k: (b / paso).to_integral_value(ROUND_FLOOR) * paso for k, b in brutos.items()}
    faltan = int(((total - sum(base.values())) / paso).to_integral_value(ROUND_FLOOR))
    for k in sorted(pesos, key=lambda k: brutos[k] - base[k], reverse=True)[:max(faltan, 0)]:
        base[k] += paso
    return base


# ============================================================
# FECHAS (Sanitizadas para tolerar NaT, nan, strings y Timestamps)
# ============================================================

def a_fecha(v) -> date | None:
    """date | datetime | pandas.Timestamp | NaT | str | None -> date | None."""
    if v is None:
        return None
    if isinstance(v, float):
        import math
        if math.isnan(v):
            return None
    try:
        import pandas as pd
        if pd.isna(v):
            return None
    except Exception:
        pass
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        v_str = v.strip()
        if not v_str or v_str.lower() in ("none", "null", "nat", "nan"):
            return None
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y"):
            try:
                return datetime.strptime(v_str[:10], fmt).date()
            except ValueError:
                continue
    try:
        import pandas as pd
        ts = pd.to_datetime(v, errors="coerce")
        if pd.isna(ts):
            return None
        return ts.date()
    except Exception:
        return None


def inicio_mes(d: Any) -> date:
    """Primer día del mes. Totalmente blindado contra floats, NaT o strings."""
    f = a_fecha(d) if not (isinstance(d, date) and not isinstance(d, datetime)) else d
    if f is None or not hasattr(f, "year") or not hasattr(f, "month"):
        raise ValueError(f"Fecha inválida para inicio_mes: {d!r}")
    try:
        return date(int(f.year), int(f.month), 1)
    except (TypeError, ValueError):
        raise ValueError(f"No se pudo interpretar año/mes de {d!r}")


def fin_mes(d: Any) -> date:
    """Último día del mes correspondiente."""
    f = a_fecha(d) if not (isinstance(d, date) and not isinstance(d, datetime)) else d
    if f is None or not hasattr(f, "year") or not hasattr(f, "month"):
        raise ValueError(f"Fecha inválida para fin_mes: {d!r}")
    y, m = int(f.year), int(f.month)
    return date(y, m, calendar.monthrange(y, m)[1])


def sumar_meses(d: Any, n: int) -> date:
    f = a_fecha(d) if not (isinstance(d, date) and not isinstance(d, datetime)) else d
    if f is None or not hasattr(f, "year") or not hasattr(f, "month"):
        raise ValueError(f"Fecha inválida para sumar_meses: {d!r}")
    m = int(f.month) - 1 + int(n)
    return date(int(f.year) + m // 12, m % 12 + 1, 1)


def lista_meses(desde: date, hasta: date) -> list[date]:
    """Primeros de mes entre `desde` y `hasta` (ambos inclusive)."""
    out, m = [], inicio_mes(desde)
    while m <= inicio_mes(hasta):
        out.append(m)
        m = sumar_meses(m, 1)
    return out


# Alias de compatibilidad hacia atrás
generar_rango_meses = lista_meses


def etiqueta_mes(d: date) -> str:
    return f"{MESES_ES[d.month - 1]} {d.year}"


def _mes_corto(d: date) -> str:
    return f"{MESES_ES[d.month - 1][:3]} {d.year}"


def dias_solape(a_desde, a_hasta, b_desde, b_hasta) -> int:
    """Días de solape entre dos rangos inclusivos (None en hasta = abierto)."""
    ini = max(a_desde, b_desde)
    fin_a = a_hasta or FIN_ABIERTO
    fin_b = b_hasta or FIN_ABIERTO
    fin = min(fin_a, fin_b)
    if fin < ini:
        return 0
    return (fin - ini).days + 1


# ============================================================
# MODELO: PERIODOS Y BOLSAS POR CARGO
# ============================================================

def total_cargos(cargos) -> float:
    return sum(float(c["pct"]) for c in cargos)


def estado_total(total: float) -> str:
    if abs(total - 100.0) <= TOL:
        return "COMPLETA"
    if total < 100.0:
        return "INCOMPLETA"
    return "EXCEDIDA"


def resolver_periodo(periodos, dia: date):
    """Encuentra el período que rige para una fecha (alias de periodo_de)."""
    return periodo_de(periodos, dia)


def etiqueta_periodo(p: dict) -> str:
    f = f" a {_mes_corto(p['fin'])}" if p.get("fin") else " en adelante"
    return f"{_mes_corto(p['inicio'])}{f} ({p.get('regla', REGLA_POOL)})"


def periodo_de(periodos, dia: date):
    """Devuelve el dict del periodo que rige en `dia` (o None)."""
    d = inicio_mes(dia)
    for p in periodos:
        ini = p.get("inicio") or p.get("fecha_inicio")
        fin = p.get("fin") or p.get("fecha_fin")
        if ini <= d and (fin is None or d <= fin):
            return p
    return None


def validar_fechas_periodo(periodos, inicio, fin, excluir_id=None) -> list:
    """Valida reglas de fechas. Devuelve lista de mensajes de error (vacía si es válido)."""
    errs = []
    if inicio is None:
        return ["Indica el mes de inicio."]
    if inicio != inicio_mes(inicio):
        errs.append("La fecha de inicio debe ser el día 1 de un mes.")
    if fin is not None:
        if fin != fin_mes(fin):
            errs.append("La fecha de fin debe ser el último día de un mes.")
        if fin < inicio:
            errs.append("La fecha de fin no puede ser anterior a la de inicio.")
    for p in periodos:
        if excluir_id is not None and p.get("id") == excluir_id:
            continue
        p_ini = p.get("inicio") or p.get("fecha_inicio")
        p_fin = p.get("fin") or p.get("fecha_fin")
        if dias_solape(inicio, fin, p_ini, p_fin) > 0:
            errs.append(f"Se solapa con el periodo existente {etiqueta_periodo(p)}.")
    return errs


def plan_alta_periodo(periodos, inicio, fin) -> dict:
    """Analiza cómo encajaría un nuevo periodo."""
    d = inicio_mes(inicio)
    cerrar = None
    for p in periodos:
        p_ini = p.get("inicio") or p.get("fecha_inicio")
        p_fin = p.get("fin") or p.get("fecha_fin")
        if p_fin is None and d > p_ini:
            cerrar = (p, fin_mes(sumar_meses(d, -1)))
            break
    simulados = []
    for p in periodos:
        if cerrar and p.get("id") == cerrar[0].get("id"):
            simulados.append(dict(p, fin=cerrar[1], fecha_fin=cerrar[1]))
        else:
            simulados.append(p)
    errs = validar_fechas_periodo(simulados, inicio, fin)
    return {"errores": errs, "cerrar": cerrar}


def huecos(periodos, desde: date, hasta: date) -> list:
    """Meses entre `desde` y `hasta` que no tienen ningún periodo asignado."""
    sin = []
    for m in lista_meses(desde, hasta):
        if periodo_de(periodos, m) is None:
            sin.append(m)
    return sin


def validar_cargos(cargos, regla: str):
    """Valida la lista de cargos de un periodo."""
    errs = []
    vistos = set()
    total = 0.0
    for c in cargos:
        nom = c.get("cargo")
        if not nom or nom not in CARGOS_BONO:
            errs.append(f"Cargo inválido: «{nom}». Debe ser uno de los cargos autorizados.")
            continue
        if nom in vistos:
            errs.append(f"El cargo «{nom}» está repetido.")
        vistos.add(nom)
        try:
            pct = float(c.get("pct", 0))
        except (TypeError, ValueError):
            errs.append(f"El porcentaje de «{nom}» no es numérico.")
            continue
        if pct <= 0 or pct > 100:
            errs.append(f"El porcentaje de «{nom}» ({pct}%) debe estar entre 0.001 y 100.")
        total += pct
        if regla == REGLA_CUPO:
            k = c.get("cupos")
            if k is None or k < 1:
                errs.append(f"En la regla POR_CUPO, «{nom}» debe indicar cupos (mínimo 1).")
    if total > 100.0 + TOL:
        errs.append(f"La suma de los cargos ({total:.3f}%) supera el 100%.")
    return errs, round(total, 6)


def armar_periodos(filas_periodo, filas_cargo) -> list:
    """Une filas de dist_periodos y dist_periodo_cargos en la lista de dicts que usa el motor."""
    por_id = {}
    for r in filas_periodo:
        ini = a_fecha(r.get("fecha_inicio") or r.get("inicio"))
        fin = a_fecha(r.get("fecha_fin") or r.get("fin"))
        nom = r.get("nombre")
        if not nom and ini:
            fin_str = fin.strftime("%d/%m/%Y") if fin else "Abierto"
            nom = f"Política {ini.strftime('%d/%m/%Y')} → {fin_str}"
        por_id[int(r["id"])] = {
            "id": int(r["id"]), "almacen": r["almacen"], "nombre": nom,
            "inicio": ini, "fecha_inicio": ini,
            "fin": fin, "fecha_fin": fin,
            "regla": r.get("regla_reparto", REGLA_POOL),
            "prueba_diluye": bool(r.get("prueba_diluye", True)), "cargos": []
        }
    for c in filas_cargo:
        p = por_id.get(int(c["periodo_id"]))
        if p is not None:
            cupos = c.get("cupos")
            try:
                cupos = None if cupos is None or cupos != cupos else int(cupos)
            except (TypeError, ValueError):
                cupos = None
            p["cargos"].append({
                "id": int(c["id"]) if c.get("id") is not None else None,
                "cargo": c["cargo"], "pct": float(c["pct"]), "cupos": cupos
            })
    for p in por_id.values():
        p["cargos"].sort(key=lambda c: (_orden_cargo(c["cargo"]), c["cargo"]))
    return sorted(por_id.values(), key=lambda p: (p["almacen"], p["inicio"]))


def plantilla_a_periodos(plantilla_legacy) -> list:
    """Convierte la plantilla del modelo anterior en periodos equivalentes con regla POR_CUPO."""
    por_almacen = {}
    for f in plantilla_legacy:
        por_almacen.setdefault(f["almacen"], []).append(f)
    salida = []
    for almacen, filas in por_almacen.items():
        por_mes = {}
        for f in filas:
            d = a_fecha(f["vigente_desde"])
            por_mes.setdefault(inicio_mes(d), {}).setdefault(d, []).append(f)
        meses = sorted(por_mes)
        for i, mes in enumerate(meses):
            ultimo = max(por_mes[mes])
            grupo = por_mes[mes][ultimo]
            fin = fin_mes(sumar_meses(meses[i + 1], -1)) if i + 1 < len(meses) else None
            if fin is None:
                hasta = [a_fecha(f.get("vigente_hasta")) for f in grupo if f.get("vigente_hasta")]
                if hasta and len(hasta) == len(grupo):
                    fin = fin_mes(max(hasta))
            fin_str = fin.strftime("%d/%m/%Y") if fin else "Abierto"
            salida.append({
                "id": None, "almacen": almacen,
                "nombre": f"Plantilla {mes.strftime('%d/%m/%Y')} → {fin_str}",
                "inicio": mes, "fecha_inicio": mes, "fin": fin, "fecha_fin": fin,
                "regla": REGLA_CUPO, "prueba_diluye": True,
                "cargos": [{
                    "cargo": f["cargo"],
                    "pct": round(float(f["pct"]) * int(f.get("cupos") or 1), 3),
                    "cupos": int(f.get("cupos") or 1)
                } for f in grupo]
            })
    return salida


# ============================================================
# OCUPACIÓN POR PERIODO
# ============================================================

def fin_efectivo(a: dict):
    """Último día que la persona cuenta en esa asignación."""
    ha = a.get("hasta")
    if not a.get("activo", True):
        fi = a.get("fecha_inactividad")
        if fi is None:
            return None, True
        return (min(ha, fi) if ha else fi), False
    return ha, False


def _orden_cargo(c):
    try:
        return CARGOS_BONO.index(c)
    except ValueError:
        return 999


def _tramos_del_mes(almacen, m_ini, m_fin, asignaciones):
    tramos, anomalias = [], []
    for a in asignaciones:
        if a["almacen"] != almacen:
            continue
        fin, anom = fin_efectivo(a)
        if anom:
            anomalias.append(a)
            continue
        if fin is not None and fin < a["desde"]:
            continue
        ini_c = max(a["desde"], m_ini)
        fin_c = min(fin, m_fin) if fin else m_fin
        if ini_c <= fin_c:
            dias = (fin_c - ini_c).days + 1
            p_ini, p_fin = a.get("prueba_inicio"), a.get("prueba_fin")
            d_prueba = dias_solape(ini_c, fin_c, p_ini, p_fin) if (a.get("en_prueba") and p_ini and p_fin) else 0
            tramos.append({
                "a": a, "ini": ini_c, "fin": fin_c, "dias": dias,
                "dias_prueba": d_prueba, "p_ini": p_ini, "p_fin": p_fin
            })
    return tramos, anomalias


def _en_prueba(t, d) -> bool:
    return bool(t["a"].get("en_prueba") and t["p_ini"] and t["p_fin"] and t["p_ini"] <= d <= t["p_fin"])


def _nuevo_cargo():
    return {
        "pool": Fraction(0), "ocup": 0, "libre": Fraction(0),
        "dias_vacante": 0, "dias_sobre": 0, "dias_sin_pct": 0,
        "cuota_min": None, "cuota_max": None, "max_n": 0
    }


def _anotar_cuota(s, cuota):
    s["cuota_min"] = cuota if s["cuota_min"] is None else min(s["cuota_min"], cuota)
    s["cuota_max"] = cuota if s["cuota_max"] is None else max(s["cuota_max"], cuota)


def calcular_reparto_mes(almacen: str, mes: date, periodos, *args, modo: str = MODO_PORCENTAJE, cargos_config=None, asignaciones=None, **kwargs) -> dict:
    """Calcula el reparto del mes de manera exacta y desglosada.
    Soporta llamadas de 4 posicionales, 5 posicionales (con cargos_config) o llamadas con kwargs.
    """
    if len(args) == 1:
        asignaciones = args[0]
    elif len(args) == 2:
        if isinstance(args[1], str):
            asignaciones = args[0]
            modo = args[1]
        else:
            cargos_config = args[0]
            asignaciones = args[1]
    elif len(args) >= 3:
        cargos_config = args[0]
        asignaciones = args[1]
        if isinstance(args[2], str):
            modo = args[2]
    elif asignaciones is None and "asignaciones" in kwargs:
        asignaciones = kwargs.pop("asignaciones")

    if asignaciones is None:
        asignaciones = []

    m_ini, m_fin = inicio_mes(mes), fin_mes(mes)
    dias_mes = m_fin.day
    es_pct = modo == MODO_PORCENTAJE
    per_alm = sorted(
        (p for p in periodos if (p.get("almacen") == almacen or p.get("almacen_codigo") == almacen or p.get("almacen_nombre") == almacen)),
        key=lambda p: (p.get("inicio") or p.get("fecha_inicio"))
    )
    tramos, anomalias = _tramos_del_mes(almacen, m_ini, m_fin, asignaciones)
    alertas = []
    for a in anomalias:
        alertas.append({
            "nivel": "warning", "tipo": "DATOS",
            "mensaje": f"{a['nombre']} (CC {a['cedula']}) está inactivo sin fecha de inactividad: se excluyó del reparto."
        })

    ultimo = {}
    for t in sorted(tramos, key=lambda x: (x["a"]["colaborador_id"], x["ini"])):
        cid, prev = t["a"]["colaborador_id"], ultimo.get(t["a"]["colaborador_id"])
        if prev is not None and t["ini"] <= prev["fin"]:
            alertas.append({
                "nivel": "error", "tipo": "ASIGNACION_SOLAPADA",
                "mensaje": f"{t['a']['nombre']} (CC {t['a']['cedula']}) tiene asignaciones que se solapan."
            })
        if prev is None or t["fin"] > prev["fin"]:
            ultimo[cid] = t

    acum = {id(t): [Fraction(0), Fraction(0)] for t in tramos}
    por_cargo, usados, sin_periodo = {}, {}, []
    asignado_acc = libre_acc = exceso_acc = Fraction(0)

    if es_pct:
        for i in range(dias_mes):
            d = m_ini + timedelta(days=i)
            p = periodo_de(per_alm, d)
            if p is None:
                sin_periodo.append(d)
                continue
            usados.setdefault(p.get("id", id(p)), p)
            cfg = {c["cargo"]: c for c in p["cargos"]}
            regla, diluye = p.get("regla", REGLA_POOL), p.get("prueba_diluye", True)

            hoy = {}
            for t in tramos:
                if t["ini"] <= d <= t["fin"]:
                    hoy.setdefault(t["a"]["cargo"], []).append(t)

            for cargo in set(cfg) | set(hoy):
                gente = hoy.get(cargo, [])
                n = len(gente)
                s = por_cargo.setdefault(cargo, _nuevo_cargo())
                s["ocup"] += n
                c = cfg.get(cargo)
                if c is None:
                    if n:
                        s["dias_sin_pct"] += 1
                    continue
                bolsa = _fr(c["pct"])
                s["pool"] += bolsa
                asignado_acc += bolsa

                if regla == REGLA_POOL:
                    part = [t for t in gente if not _en_prueba(t, d)]
                    base_n = n if diluye else len(part)
                    if base_n == 0:
                        libre_acc += bolsa
                        s["libre"] += bolsa
                        s["dias_vacante"] += 1
                        continue
                    cuota = bolsa / base_n
                    _anotar_cuota(s, cuota)
                    for t in gente:
                        acum[id(t)][1 if _en_prueba(t, d) else 0] += cuota
                    if diluye:
                        libre_acc += cuota * (n - base_n)
                        s["libre"] += cuota * (n - base_n)

                elif regla == REGLA_CUPO:
                    cupos = c.get("cupos") or 1
                    cuota_unitaria = bolsa / cupos
                    if n == 0:
                        libre_acc += bolsa
                        s["libre"] += bolsa
                        s["dias_vacante"] += 1
                        continue
                    if n <= cupos:
                        cuota = cuota_unitaria
                        _anotar_cuota(s, cuota)
                        for t in gente:
                            acum[id(t)][1 if _en_prueba(t, d) else 0] += cuota
                        vacios = cupos - n
                        if vacios:
                            libre_acc += cuota_unitaria * vacios
                            s["libre"] += cuota_unitaria * vacios
                            s["dias_vacante"] += 1
                    else:
                        cuota = bolsa / n
                        _anotar_cuota(s, cuota)
                        s["dias_sobre"] += 1
                        s["max_n"] = max(s["max_n"], n)
                        exceso_acc += bolsa * (n - cupos) / n
                        for t in gente:
                            acum[id(t)][1 if _en_prueba(t, d) else 0] += cuota

    personas = []
    for t in tramos:
        a = t["a"]
        s = por_cargo.get(a["cargo"])
        con_bolsa = es_pct and s is not None and s["pool"] > 0
        efectivo, en_prueba = acum[id(t)]
        pct_p = float(s["pool"] / dias_mes) if con_bolsa else 0.0
        pct_ef = float(efectivo / dias_mes) if con_bolsa else 0.0
        pct_pr = float(en_prueba / dias_mes) if con_bolsa else 0.0
        personas.append({
            "colaborador_id": a["colaborador_id"], "cedula": a["cedula"], "nombre": a["nombre"],
            "cargo": a["cargo"], "desde_en_mes": t["ini"], "hasta_en_mes": t["fin"],
            "dias": t["dias"], "dias_prueba": t["dias_prueba"], "dias_participa": t["dias"] - t["dias_prueba"],
            "fraccion": t["dias"] / dias_mes,
            "pct_cargo": pct_p if con_bolsa else None,
            "pct_bolsa_cargo": pct_p,
            "pct_efectivo": pct_ef if con_bolsa else None,
            "pct_en_prueba": pct_pr if con_bolsa else None
        })
    personas.sort(key=lambda p: (_orden_cargo(p["cargo"]), p["cargo"], p["desde_en_mes"], p["nombre"]))

    if not es_pct:
        alertas.append({
            "nivel": "info", "tipo": "INDIVIDUAL",
            "mensaje": "Almacén de evaluación individual: no reparte porcentaje del bono."
        })
        tot_ind = {
            "asignado": 0.0, "asignado_politica": 0.0, "ocupado": 0.0, "libre": 0.0, "vacante": 0.0,
            "pagado": 0.0, "repartido": 0.0, "prueba": 0.0, "en_prueba": 0.0,
            "exceso": 0.0, "sin_definir": 0.0, "personas": len(personas)
        }
        return {
            "mes": m_ini, "modo": modo, "periodos": [], "periodo": None, "periodo_aplicado": None,
            "dias_mes": dias_mes, "dias_sin_periodo": 0, "cargos": [], "personas": personas,
            "alertas": alertas, "totales": tot_ind
        }

    cargos_res = []
    for cargo in sorted(por_cargo, key=lambda c: (_orden_cargo(c), c)):
        s = por_cargo[cargo]
        gente = [p for p in personas if p["cargo"] == cargo]
        efect_sum = sum(p["pct_efectivo"] or 0.0 for p in gente)
        prueba_sum = sum(p["pct_en_prueba"] or 0.0 for p in gente)
        cargos_res.append({
            "cargo": cargo,
            "pct_cargo": float(s["pool"] / dias_mes),
            "pct_bolsa": float(s["pool"] / dias_mes),
            "personas": len(gente),
            "personas_prom": s["ocup"] / dias_mes,
            "pct_persona_min": None if s["cuota_min"] is None else float(s["cuota_min"]),
            "pct_persona_max": None if s["cuota_max"] is None else float(s["cuota_max"]),
            "pct_efectivo": efect_sum,
            "pct_libre": float(s["libre"] / dias_mes),
            "dias_vacante": s["dias_vacante"], "dias_sobrecupo": s["dias_sobre"],
            "dias_totales": sum(p["dias"] for p in gente),
            "repartido": efect_sum,
            "en_prueba": prueba_sum,
            "vacante": float(s["libre"] / dias_mes),
            "estado": "VIGENTE" if s["pool"] > 0 else "SIN_BOLSA"
        })

    asignado_f = asignado_acc / dias_mes
    totales = {
        "asignado": float(asignado_f),
        "asignado_politica": float(asignado_f),
        "libre": float(libre_acc / dias_mes),
        "vacante": float(libre_acc / dias_mes),
        "ocupado": float((asignado_acc - libre_acc) / dias_mes),
        "pagado": float(sum((v[0] for v in acum.values()), Fraction(0)) / dias_mes),
        "repartido": float(sum((v[0] for v in acum.values()), Fraction(0)) / dias_mes),
        "prueba": float(sum((v[1] for v in acum.values()), Fraction(0)) / dias_mes),
        "en_prueba": float(sum((v[1] for v in acum.values()), Fraction(0)) / dias_mes),
        "exceso": float(exceso_acc / dias_mes),
        "sin_definir": float(100 - asignado_f),
        "personas": len(personas)
    }

    if sin_periodo:
        rango = f"{sin_periodo[0]:%d/%m} al {sin_periodo[-1]:%d/%m}" if len(sin_periodo) > 1 else f"{sin_periodo[0]:%d/%m}"
        msg = f"No hay porcentajes definidos para {etiqueta_mes(m_ini)} en este almacén." if len(sin_periodo) == dias_mes else f"{len(sin_periodo)} día(s) ({rango}) sin porcentajes definidos."
        alertas.append({"nivel": "error" if personas else "info", "tipo": "SIN_PERIODO", "mensaje": msg})

    for p in usados.values():
        total = total_cargos(p["cargos"])
        est = estado_total(total)
        if est == "INCOMPLETA":
            alertas.append({"nivel": "warning", "tipo": "PLANTILLA_INCOMPLETA", "mensaje": f"Periodo {etiqueta_periodo(p)}: los cargos suman {total:.2f}%."})
        elif est == "EXCEDIDA":
            alertas.append({"nivel": "error", "tipo": "PLANTILLA_EXCEDIDA", "mensaje": f"Periodo {etiqueta_periodo(p)}: los cargos suman {total:.2f}%."})

    for c in cargos_res:
        s = por_cargo[c["cargo"]]
        if s["dias_sin_pct"]:
            alertas.append({"nivel": "error", "tipo": "SIN_PCT", "mensaje": f"{c['personas']} persona(s) con cargo «{c['cargo']}» no tienen porcentaje."})
        if s["dias_vacante"] and c["pct_libre"] > TOL:
            alertas.append({"nivel": "warning", "tipo": "VACANTE", "mensaje": f"{c['cargo']}: {c['pct_libre']:.2f}% del bono liberado por vacante."})

    vistos = set()
    for t in tramos:
        a = t["a"]
        if a.get("en_prueba") and not (t["p_ini"] and t["p_fin"]) and a["colaborador_id"] not in vistos:
            vistos.add(a["colaborador_id"])
            alertas.append({"nivel": "warning", "tipo": "PRUEBA_SIN_FECHAS", "mensaje": f"{a['nombre']} en prueba sin fechas: se trató como participante completo."})

    lista_usados = list(usados.values())
    per_sel = lista_usados[0] if lista_usados else (per_alm[0] if per_alm else None)
    return {
        "mes": m_ini, "modo": modo, "periodos": lista_usados,
        "periodo": per_sel, "periodo_aplicado": per_sel, "dias_mes": dias_mes,
        "dias_sin_periodo": len(sin_periodo), "cargos": cargos_res, "personas": personas,
        "alertas": alertas, "totales": totales
    }


def evolucion(almacen: str, meses, periodos, asignaciones, modo: str = MODO_PORCENTAJE) -> list:
    filas = []
    for m in meses:
        res = calcular_reparto_mes(almacen, m, periodos, asignaciones, modo=modo)
        t = res["totales"]
        filas.append({
            "mes": m, "etiqueta": etiqueta_mes(m), "asignado": t["asignado"],
            "pagado": t["pagado"], "prueba": t["prueba"], "libre": t["libre"],
            "sin_definir": t["sin_definir"], "exceso": t["exceso"],
            "personas": len(res["personas"]), "alertas": len(res["alertas"])
        })
    return filas


def movimientos_mes(almacen: str, mes: date, asignaciones) -> list:
    m_ini, m_fin = inicio_mes(mes), fin_mes(mes)
    por_persona = {}
    for a in asignaciones:
        por_persona.setdefault(a["colaborador_id"], []).append(a)
    for lst in por_persona.values():
        lst.sort(key=lambda a: a["desde"])
    posicion = {id(a): i for lst in por_persona.values() for i, a in enumerate(lst)}

    movs = []
    for a in asignaciones:
        if a["almacen"] != almacen:
            continue
        fin, anom = fin_efectivo(a)
        if anom or (fin is not None and fin < a["desde"]):
            continue
        lst = por_persona[a["colaborador_id"]]
        i = posicion[id(a)]
        prev = lst[i - 1] if i > 0 else None
        sig = lst[i + 1] if i + 1 < len(lst) else None
        if m_ini <= a["desde"] <= m_fin:
            if prev is None:
                tipo = "Ingreso"
            elif prev["almacen"] != almacen:
                tipo = f"Llega por traslado desde {prev['almacen']}"
            elif prev["cargo"] != a["cargo"]:
                tipo = f"Cambio de cargo ({prev['cargo']} → {a['cargo']})"
            else:
                tipo = "Reingreso"
            movs.append({"fecha": a["desde"], "tipo": tipo, "nombre": a["nombre"], "cedula": a["cedula"], "cargo": a["cargo"]})
        if fin is not None and m_ini <= fin < m_fin:
            if sig is not None and sig["almacen"] != almacen:
                tipo = f"Sale por traslado a {sig['almacen']}"
            elif sig is not None and sig["cargo"] != a["cargo"]:
                tipo = f"Cambio de cargo ({a['cargo']} → {sig['cargo']})"
            elif sig is None and not a.get("activo", True):
                tipo = "Inactivación"
            else:
                tipo = "Fin de asignación"
            movs.append({"fecha": fin, "tipo": tipo, "nombre": a["nombre"], "cedula": a["cedula"], "cargo": a["cargo"]})
    movs.sort(key=lambda m: (m["fecha"], m["nombre"]))
    return movs


# ============================================================
# BASE DE DATOS
# ============================================================

def migrar(con) -> list:
    """Idempotente. Crea las tablas de periodos y restricciones en PostgreSQL."""
    from sqlalchemy import text
    avisos = []
    con.execute(text("ALTER TABLE app.almacenes ADD COLUMN IF NOT EXISTS modo_bono TEXT NOT NULL DEFAULT 'PORCENTAJE'"))
    con.execute(text("UPDATE app.almacenes SET modo_bono = 'INDIVIDUAL' WHERE codigo = 'EJECOM' AND modo_bono <> 'INDIVIDUAL'"))
    con.execute(text("""
        CREATE TABLE IF NOT EXISTS rrhh.dist_periodos (
            id            SERIAL PRIMARY KEY,
            almacen       TEXT NOT NULL REFERENCES app.almacenes(codigo),
            fecha_inicio  DATE NOT NULL,
            fecha_fin     DATE,
            regla_reparto TEXT NOT NULL DEFAULT 'POOL' CHECK (regla_reparto IN ('POOL','POR_CUPO')),
            prueba_diluye BOOLEAN NOT NULL DEFAULT TRUE,
            creado_por    TEXT,
            creado_en     TIMESTAMPTZ DEFAULT now(),
            CHECK (fecha_fin IS NULL OR fecha_fin >= fecha_inicio),
            CHECK (EXTRACT(day FROM fecha_inicio) = 1),
            CHECK (fecha_fin IS NULL OR fecha_fin =
                   (date_trunc('month', fecha_fin) + INTERVAL '1 month' - INTERVAL '1 day')::date)
        )
    """))
    con.execute(text("""
        CREATE TABLE IF NOT EXISTS rrhh.dist_periodo_cargos (
            id         SERIAL PRIMARY KEY,
            periodo_id INT NOT NULL REFERENCES rrhh.dist_periodos(id) ON DELETE CASCADE,
            cargo      TEXT NOT NULL,
            pct        NUMERIC(6,3) NOT NULL CHECK (pct > 0 AND pct <= 100),
            cupos      INT CHECK (cupos IS NULL OR cupos >= 1),
            UNIQUE (periodo_id, cargo)
        )
    """))
    con.execute(text("CREATE INDEX IF NOT EXISTS ix_dist_periodos_alm ON rrhh.dist_periodos (almacen, fecha_inicio)"))

    def _paso(descripcion, *sentencias):
        try:
            with con.begin_nested():
                for s in sentencias:
                    con.execute(text(s))
        except Exception as e:
            avisos.append(f"{descripcion}: {e}")

    _paso("Habilitar btree_gist", "CREATE EXTENSION IF NOT EXISTS btree_gist")
    try:
        with con.begin_nested():
            existe = con.execute(text("SELECT 1 FROM pg_constraint WHERE conname = 'dist_periodos_no_solape' AND conrelid = 'rrhh.dist_periodos'::regclass")).scalar()
            if not existe:
                con.execute(text(
                    "ALTER TABLE rrhh.dist_periodos ADD CONSTRAINT dist_periodos_no_solape "
                    "EXCLUDE USING gist (almacen WITH =, daterange(fecha_inicio, COALESCE(fecha_fin, DATE '2100-12-31'), '[]') WITH &&)"
                ))
    except Exception as e:
        avisos.append(f"Restricción no-solape: {e}")

    return avisos


def _bool(v, defecto=False) -> bool:
    if v is None:
        return defecto
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    return str(v).strip().lower() in ("1", "true", "t", "yes", "si", "sí")


def cargar_almacenes(conn) -> list:
    df = conn.query("SELECT codigo, nombre, modo_bono FROM app.almacenes WHERE activo ORDER BY codigo", ttl=0)
    return df.to_dict("records")


def cargar_periodos(conn, almacen: str | None = None) -> list:
    """Periodos con sus bolsas por cargo. Retroalimenta automáticamente desde histórico si no hay periodos."""
    periodos = []
    # 1. Intentar cargar desde las tablas rrhh.dist_periodos y rrhh.dist_periodo_cargos
    try:
        w, params = ("WHERE almacen = :a", {"a": almacen}) if almacen else ("", {})
        per = conn.query(f"SELECT id, almacen, fecha_inicio, fecha_fin, regla_reparto, prueba_diluye FROM rrhh.dist_periodos {w} ORDER BY almacen, fecha_inicio", params=params, ttl=0)
        car = conn.query(f"SELECT id, periodo_id, cargo, pct, cupos FROM rrhh.dist_periodo_cargos WHERE periodo_id IN (SELECT id FROM rrhh.dist_periodos {w})", params=params, ttl=0)
        periodos = armar_periodos(per.to_dict("records"), car.to_dict("records"))
    except Exception:
        periodos = []

    # 2. Si no hay periodos en dist_periodos, intentar rrhh.distribucion_periodos
    if not periodos:
        try:
            w, params = ("WHERE almacen = :a", {"a": almacen}) if almacen else ("", {})
            per = conn.query(f"SELECT id, almacen, nombre, fecha_inicio, fecha_fin FROM rrhh.distribucion_periodos {w} ORDER BY almacen, fecha_inicio", params=params, ttl=0)
            car = conn.query(f"SELECT id, periodo_id, cargo, pct FROM rrhh.distribucion_cargos WHERE periodo_id IN (SELECT id FROM rrhh.distribucion_periodos {w})", params=params, ttl=0)
            if not per.empty:
                por_id = {}
                for r in per.to_dict("records"):
                    ini = a_fecha(r["fecha_inicio"])
                    fin = a_fecha(r["fecha_fin"])
                    por_id[int(r["id"])] = {
                        "id": int(r["id"]), "almacen": r["almacen"],
                        "nombre": r.get("nombre") or f"Período {ini}",
                        "inicio": ini, "fecha_inicio": ini,
                        "fin": fin, "fecha_fin": fin,
                        "regla": REGLA_POOL, "prueba_diluye": True, "cargos": []
                    }
                for c in car.to_dict("records"):
                    p = por_id.get(int(c["periodo_id"]))
                    if p:
                        p["cargos"].append({"id": int(c["id"]), "cargo": c["cargo"], "pct": float(c["pct"]), "cupos": None})
                periodos = sorted(por_id.values(), key=lambda p: (p["almacen"], p["inicio"]))
        except Exception:
            pass

    # 3. Retroalimentación automática desde la plantilla histórica (rrhh.distribucion_puestos):
    # Si para el almacén aún no hay periodos definidos, convertimos su plantilla histórica a periodos POR_CUPO.
    try:
        alms_con_periodos = {p["almacen"] for p in periodos}
        legacy_rows = cargar_plantilla(conn, almacen)
        if legacy_rows:
            legacy_faltantes = [r for r in legacy_rows if r["almacen"] not in alms_con_periodos]
            if legacy_faltantes:
                periodos_legacy = plantilla_a_periodos(legacy_faltantes)
                periodos.extend(periodos_legacy)
    except Exception:
        pass

    # Garantizar consistencia de claves
    for p in periodos:
        if "fecha_inicio" not in p and "inicio" in p:
            p["fecha_inicio"] = p["inicio"]
        if "inicio" not in p and "fecha_inicio" in p:
            p["inicio"] = p["fecha_inicio"]
        if "fecha_fin" not in p and "fin" in p:
            p["fecha_fin"] = p["fin"]
        if "fin" not in p and "fecha_fin" in p:
            p["fin"] = p["fecha_fin"]
        if "nombre" not in p:
            f_fin_str = p["fin"].strftime('%d/%m/%Y') if p.get("fin") else "Abierto"
            p["nombre"] = f"Política {p['inicio'].strftime('%d/%m/%Y')} → {f_fin_str}"

    return periodos


def cargar_plantilla(conn, almacen: str | None = None) -> list:
    """Plantilla del MODELO ANTERIOR (rrhh.distribucion_puestos)."""
    sql = ("SELECT id, almacen, cargo, pct, cupos, vigente_desde, vigente_hasta "
           "FROM rrhh.distribucion_puestos {w} ORDER BY almacen, vigente_desde, cargo")
    df = conn.query(sql.format(w="WHERE almacen = :a" if almacen else ""), params={"a": almacen} if almacen else {}, ttl=0)
    filas = df.to_dict("records")
    for f in filas:
        f["id"], f["pct"], f["cupos"] = int(f["id"]), float(f["pct"]), int(f["cupos"])
        f["vigente_desde"], f["vigente_hasta"] = a_fecha(f["vigente_desde"]), a_fecha(f["vigente_hasta"])
    return filas


def cargar_asignaciones(conn) -> list:
    df = conn.query("""
        SELECT a.id AS asig_id, a.colaborador_id, c.cedula, c.nombre, a.almacen, a.cargo,
               a.fecha_desde AS desde, a.fecha_hasta AS hasta,
               c.activo, c.fecha_inactividad, c.en_prueba, c.prueba_inicio, c.prueba_fin
        FROM rrhh.asignaciones a JOIN rrhh.colaboradores c ON c.id = a.colaborador_id
        ORDER BY c.nombre, a.fecha_desde
    """, ttl=0)
    filas = df.to_dict("records")
    for f in filas:
        for k in ("desde", "hasta", "fecha_inactividad", "prueba_inicio", "prueba_fin"):
            f[k] = a_fecha(f[k])
        f["activo"] = _bool(f["activo"], defecto=True)
        f["en_prueba"] = _bool(f.get("en_prueba"), defecto=False)
    return filas


def auditar(con, tabla, registro_id, accion, antes, despues, usuario, rol, motivo=None, nombre=None) -> None:
    from sqlalchemy import text
    try:
        con.execute(text("""
            INSERT INTO rrhh.rrhh_auditoria (tabla, registro_id, accion, antes, despues, usuario, rol, motivo)
            VALUES (:t, :r, :a, :ant::jsonb, :des::jsonb, :u, :rol, :m)
        """), {
            "t": tabla, "r": str(registro_id), "a": accion,
            "ant": json.dumps(antes, default=str) if antes is not None else None,
            "des": json.dumps(despues, default=str) if despues is not None else None,
            "u": f"{usuario} ({nombre})" if nombre and nombre != usuario else usuario,
            "rol": rol, "m": motivo
        })
    except Exception as e:
        log.warning("No se pudo escribir en rrhh_auditoria: %s", e)


def _bloquear_almacen(con, almacen: str) -> str:
    from sqlalchemy import text
    con.execute(text("SELECT pg_advisory_xact_lock(hashtext(:t), hashtext(:a))"),
                {"t": "dist_periodos", "a": almacen})
    return almacen


def _periodos_db(con, almacen) -> list:
    from sqlalchemy import text
    per = con.execute(text("SELECT id, almacen, fecha_inicio, fecha_fin, regla_reparto, prueba_diluye FROM rrhh.dist_periodos WHERE almacen = :a ORDER BY fecha_inicio"),
                      {"a": almacen}).mappings().all()
    car = con.execute(text("SELECT id, periodo_id, cargo, pct, cupos FROM rrhh.dist_periodo_cargos WHERE periodo_id IN (SELECT id FROM rrhh.dist_periodos WHERE almacen = :a)"),
                      {"a": almacen}).mappings().all()
    return armar_periodos([dict(x) for x in per], [dict(x) for x in car])


def _snapshot(p: dict) -> dict:
    return {
        "almacen": p["almacen"], "inicio": str(p["inicio"]), "fin": str(p["fin"]) if p["fin"] else None,
        "regla": p["regla"], "prueba_diluye": p["prueba_diluye"], "total": total_cargos(p["cargos"]),
        "cargos": [{"cargo": c["cargo"], "pct": c["pct"], "cupos": c.get("cupos")} for c in p["cargos"]]
    }


def _insertar_cargos(con, periodo_id, cargos) -> None:
    from sqlalchemy import text
    for c in cargos:
        con.execute(text("INSERT INTO rrhh.dist_periodo_cargos (periodo_id, cargo, pct, cupos) VALUES (:p, :c, :pc, :k)"),
                    {"p": periodo_id, "c": c["cargo"], "pc": round(float(c["pct"]), 3),
                     "k": int(c["cupos"]) if c.get("cupos") not in (None, "") and c["cupos"] == c["cupos"] else None})


def crear_periodo(con, *, almacen, inicio, fin, regla, prueba_diluye, cargos, usuario, rol, motivo=None, nombre=None) -> int:
    from sqlalchemy import text
    _bloquear_almacen(con, almacen)
    inicio, fin = a_fecha(inicio), a_fecha(fin)
    plan = plan_alta_periodo(_periodos_db(con, almacen), inicio, fin)
    if plan["errores"]:
        raise ValueError(" ".join(plan["errores"]))
    errs, total = validar_cargos(cargos, regla)
    if errs:
        raise ValueError(" ".join(errs))
    if plan["cerrar"]:
        previo, nuevo_fin = plan["cerrar"]
        con.execute(text("UPDATE rrhh.dist_periodos SET fecha_fin = :f WHERE id = :i"), {"f": nuevo_fin, "i": previo["id"]})
        auditar(con, "distribucion_periodos", f"{almacen}:periodo:{previo['id']}", "CERRAR_PERIODO",
                {"fin": None}, {"fin": str(nuevo_fin)}, usuario, rol, motivo, nombre)
    pid = con.execute(text("""
        INSERT INTO rrhh.dist_periodos (almacen, fecha_inicio, fecha_fin, regla_reparto, prueba_diluye, creado_por)
        VALUES (:a, :i, :f, :r, :d, :u) RETURNING id
    """), {"a": almacen, "i": inicio, "f": fin, "r": regla, "d": bool(prueba_diluye), "u": usuario}).scalar()
    _insertar_cargos(con, pid, cargos)
    auditar(con, "distribucion_periodos", f"{almacen}:periodo:{pid}", "CREAR_PERIODO", None,
            {"almacen": almacen, "inicio": str(inicio), "fin": str(fin) if fin else None, "total": total},
            usuario, rol, motivo, nombre)
    return pid


def _periodo_por_id(con, periodo_id):
    from sqlalchemy import text
    almacen = con.execute(text("SELECT almacen FROM rrhh.dist_periodos WHERE id = :i"), {"i": int(periodo_id)}).scalar()
    if almacen is None:
        raise ValueError("El periodo ya no existe.")
    _bloquear_almacen(con, almacen)
    periodos = _periodos_db(con, almacen)
    actual = next((p for p in periodos if p["id"] == int(periodo_id)), None)
    if actual is None:
        raise ValueError("El periodo ya no existe.")
    return actual, periodos


def actualizar_periodo(con, *, periodo_id, inicio, fin, regla, prueba_diluye, usuario, rol, motivo=None, nombre=None) -> None:
    from sqlalchemy import text
    actual, periodos = _periodo_por_id(con, periodo_id)
    inicio, fin = a_fecha(inicio), a_fecha(fin)
    errs = validar_fechas_periodo(periodos, inicio, fin, excluir_id=actual["id"])
    errs += validar_cargos(actual["cargos"], regla)[0]
    if errs:
        raise ValueError(" ".join(errs))
    con.execute(text("UPDATE rrhh.dist_periodos SET fecha_inicio = :i, fecha_fin = :f, regla_reparto = :r, prueba_diluye = :d WHERE id = :p"),
                {"i": inicio, "f": fin, "r": regla, "d": bool(prueba_diluye), "p": actual["id"]})
    auditar(con, "distribucion_periodos", f"{actual['almacen']}:periodo:{actual['id']}", "ACTUALIZAR_PERIODO",
            _snapshot(actual), dict(_snapshot(actual), inicio=str(inicio), fin=str(fin) if fin else None),
            usuario, rol, motivo, nombre)


def guardar_cargos(con, *, periodo_id, cargos, usuario, rol, motivo=None, nombre=None) -> dict:
    from sqlalchemy import text
    actual, _ = _periodo_por_id(con, periodo_id)
    errs, total = validar_cargos(cargos, actual["regla"])
    if errs:
        raise ValueError(" ".join(errs))
    con.execute(text("DELETE FROM rrhh.dist_periodo_cargos WHERE periodo_id = :p"), {"p": actual["id"]})
    _insertar_cargos(con, actual["id"], cargos)
    auditar(con, "distribucion_periodos", f"{actual['almacen']}:periodo:{actual['id']}", "GUARDAR_CARGOS",
            {"total": total_cargos(actual["cargos"])}, {"total": total}, usuario, rol, motivo, nombre)
    return {"total": total, "restante": round(100.0 - total, 6)}


def eliminar_periodo(con, *, periodo_id: int, usuario: str = "sistema", rol: str = "admin", motivo: str = "Eliminación de período", nombre: str | None = None, almacen: str | None = None, nombre_usuario: str | None = None, **kwargs) -> None:
    from sqlalchemy import text
    nom = nombre or nombre_usuario
    mot = motivo or "Eliminación de período"
    try:
        actual, _ = _periodo_por_id(con, periodo_id)
        con.execute(text("DELETE FROM rrhh.dist_periodos WHERE id = :p"), {"p": actual["id"]})
        auditar(con, "distribucion_periodos", f"{actual['almacen']}:periodo:{actual['id']}", "ELIMINAR_PERIODO",
                _snapshot(actual), {"eliminado": True}, usuario, rol, mot, nom)
    except Exception:
        con.execute(text("DELETE FROM rrhh.distribucion_periodos WHERE id = :p"), {"p": periodo_id})


def importar_plantillas_legacy(con, *, usuario, rol, nombre=None) -> dict:
    from sqlalchemy import text
    filas = con.execute(text("SELECT almacen, cargo, pct, cupos, vigente_desde, vigente_hasta FROM rrhh.distribucion_puestos")).mappings().all()
    legacy = [dict(f, pct=float(f["pct"]), cupos=int(f["cupos"])) for f in filas]
    individuales = {r[0] for r in con.execute(text("SELECT codigo FROM app.almacenes WHERE modo_bono = 'INDIVIDUAL'"))}
    con_periodos = {r[0] for r in con.execute(text("SELECT DISTINCT almacen FROM rrhh.dist_periodos"))}
    nuevos = [p for p in plantilla_a_periodos(legacy) if p["almacen"] not in individuales and p["almacen"] not in con_periodos]
    almacenes = sorted({p["almacen"] for p in nuevos})
    for alm in almacenes:
        _bloquear_almacen(con, alm)
        for p in (x for x in nuevos if x["almacen"] == alm):
            pid = con.execute(text("""
                INSERT INTO rrhh.dist_periodos (almacen, fecha_inicio, fecha_fin, regla_reparto, prueba_diluye, creado_por)
                VALUES (:a, :i, :f, :r, TRUE, :u) RETURNING id
            """), {"a": alm, "i": p["inicio"], "f": p["fin"], "r": REGLA_CUPO, "u": usuario}).scalar()
            _insertar_cargos(con, pid, p["cargos"])
        auditar(con, "distribucion_periodos", f"{alm}:importacion", "IMPORTAR_MODELO_ANTERIOR", None,
                {"almacen": alm, "periodos": sum(1 for x in nuevos if x["almacen"] == alm)},
                usuario, rol, "Importación histórica", nombre)
    return {"almacenes": len(almacenes), "periodos": len(nuevos)}


# ============================================================
# COMPATIBILIDAD Y ADAPTADORES DE INTEGRACIÓN
# ============================================================
CARGOS_ORDEN = {c: i for i, c in enumerate(CARGOS_BONO)}


def cargar_cargos_config(conn, almacen: str | None = None) -> list[dict]:
    """Retorna lista plana de dicts de configuración de cargos para vistas que lo requieran."""
    periodos = cargar_periodos(conn, almacen)
    filas = []
    for p in periodos:
        for c in p.get("cargos", []):
            filas.append({
                "id": c.get("id"),
                "periodo_id": p.get("id"),
                "almacen": p.get("almacen"),
                "cargo": c.get("cargo"),
                "pct": float(c.get("pct", 0.0)),
                "cupos": c.get("cupos")
            })
    return filas


def guardar_periodo_con_cargos(
    con,
    *,
    almacen: str,
    periodo_id: int | None = None,
    nombre: str | None = None,
    fecha_inicio: date,
    fecha_fin: date | None = None,
    cargos_pct: dict[str, float] | list[dict],
    usuario: str = "sistema",
    rol: str = "admin",
    motivo: str | None = None,
    nombre_usuario: str | None = None,
    **kwargs
) -> int:
    """Crea o actualiza un período y sus cargos de manera atómica con auditoría."""
    from sqlalchemy import text
    if isinstance(cargos_pct, dict):
        lista_cargos = [{"cargo": c, "pct": float(p), "cupos": None} for c, p in cargos_pct.items() if float(p) > 0]
    else:
        lista_cargos = list(cargos_pct)

    tot = sum(float(c.get("pct", 0)) for c in lista_cargos)
    if tot > 100.0 + TOL:
        raise ValueError(f"La suma de los porcentajes ({tot:.3f}%) supera el 100%.")

    if periodo_id:
        try:
            actualizar_periodo(con, periodo_id=periodo_id, inicio=fecha_inicio, fin=fecha_fin,
                               regla=REGLA_POOL, prueba_diluye=True, usuario=usuario, rol=rol,
                               motivo=motivo, nombre=nombre_usuario)
            guardar_cargos(con, periodo_id=periodo_id, cargos=lista_cargos,
                           usuario=usuario, rol=rol, motivo=motivo, nombre=nombre_usuario)
            pid = periodo_id
        except Exception:
            con.execute(text("""
                UPDATE rrhh.distribucion_periodos
                SET nombre = :n, fecha_inicio = :fi, fecha_fin = :ff
                WHERE id = :id AND almacen = :a
            """), {"n": nombre or f"Período {fecha_inicio}", "fi": fecha_inicio, "ff": fecha_fin, "id": periodo_id, "a": almacen})
            con.execute(text("DELETE FROM rrhh.distribucion_cargos WHERE periodo_id = :pid"), {"pid": periodo_id})
            for c in lista_cargos:
                con.execute(text("""
                    INSERT INTO rrhh.distribucion_cargos (periodo_id, cargo, pct)
                    VALUES (:pid, :c, :p)
                """), {"pid": periodo_id, "c": c["cargo"], "p": round(float(c["pct"]), 3)})
            pid = periodo_id
    else:
        try:
            pid = crear_periodo(con, almacen=almacen, inicio=fecha_inicio, fin=fecha_fin,
                                regla=REGLA_POOL, prueba_diluye=True, cargos=lista_cargos,
                                usuario=usuario, rol=rol, motivo=motivo, nombre=nombre_usuario)
        except Exception:
            pid = con.execute(text("""
                INSERT INTO rrhh.distribucion_periodos (almacen, nombre, fecha_inicio, fecha_fin, creado_por)
                VALUES (:a, :n, :fi, :ff, :u)
                RETURNING id
            """), {"a": almacen, "n": nombre or f"Período {fecha_inicio}", "fi": fecha_inicio, "ff": fecha_fin, "u": usuario}).scalar()
            for c in lista_cargos:
                con.execute(text("""
                    INSERT INTO rrhh.distribucion_cargos (periodo_id, cargo, pct)
                    VALUES (:pid, :c, :p)
                """), {"pid": pid, "c": c["cargo"], "p": round(float(c["pct"]), 3)})
    return pid