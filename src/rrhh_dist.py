"""
Distribución del bono por almacén, cargo, persona y PERIODO.

Tablas que usa:
  rrhh.colaboradores, rrhh.asignaciones, app.almacenes, rrhh.rrhh_auditoria  (ya existen)
  rrhh.dist_periodos, rrhh.dist_periodo_cargos                               (nuevas, las crea migrar())
  rrhh.distribucion_puestos                                                  (modelo anterior, solo lectura/importación)

Modelo
------
* PERIODO por almacén: rige desde un mes hasta un mes (inclusive) o queda ABIERTO (sin fecha fin). Dentro
  de un almacén los periodos no se solapan; los meses sin periodo quedan «sin definir» (libres) y se avisan.
  Cada almacén tiene sus propios periodos y porcentajes.
* BOLSA POR CARGO: cada periodo asigna a cada cargo un % del bono del almacén (la bolsa del cargo). La suma
  de las bolsas es ≤ 100 y no se puede exceder. La cantidad de personas NO se configura: sale de las
  asignaciones del sistema (módulo Colaboradores).
* REGLA DE REPARTO (por periodo):
    POOL     Cada día la bolsa del cargo se divide en partes iguales entre las personas que ocupan el cargo ese
             día (bolsa 30 %: 3 personas → 10 % c/u; 2 personas → 15 % c/u). Si nadie ocupa el cargo, la bolsa
             de ese día queda LIBERADA.
    POR_CUPO Modelo anterior: cada cargo tiene `cupos`; cada persona recibe bolsa/cupos y, si faltan personas,
             lo de los cupos vacíos queda liberado (si sobran, el cupo se reparte entre las presentes).
* PRUEBA: las personas en periodo de prueba ocupan su lugar pero no participan (POOL: si `prueba_diluye`,
  su parte queda aparte como «en prueba»; si no, no cuentan y la bolsa se reparte entre los demás).
* El cálculo es DIARIO, con fracciones exactas, y se cumple siempre:
        pagado + en prueba + liberado = asignado        y        asignado + sin definir = 100
* Almacenes con modo_bono = 'INDIVIDUAL' (EJECOM) no usan periodos: su meta es individual.

Convención de fechas: fecha_hasta y fecha_inactividad son el ÚLTIMO DÍA trabajado (inclusivo). Los periodos
también son inclusivos y se definen por meses completos (día 1 hasta último día de un mes).
"""

from __future__ import annotations

import calendar
import json
import logging
from datetime import date, datetime, timedelta
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from fractions import Fraction

log = logging.getLogger(__name__)

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
    """float | Decimal | str | int -> Fraction exacta (pasando por str, sin ruido binario)."""
    return Fraction(str(x))


def monto_por_porcentaje(bono, pcts: dict, decimales: int = 0) -> dict:
    """
    Reparte en dinero lo que le corresponde a cada persona: bono × pct / 100.
    Asegura que la suma de lo repartido coincida exactamente con el total.
    """
    paso = Decimal(1).scaleb(-decimales)
    pesos = {k: Decimal(str(v)) for k, v in pcts.items()}
    suma = sum(pesos.values(), Decimal(0))
    b = Decimal(str(bono))
    total_esperado = (b * suma / Decimal(100)).quantize(paso, rounding=ROUND_HALF_UP)

    crudos = {k: (b * p / Decimal(100)) for k, p in pesos.items()}
    base = {k: c.quantize(paso, rounding=ROUND_FLOOR) for k, c in crudos.items()}
    falta = int((total_esperado - sum(base.values(), Decimal(0))) / paso)

    restos = sorted(crudos, key=lambda k: crudos[k] - base[k], reverse=True)
    for k in restos[:falta]:
        base[k] += paso
    return base


# ============================================================
# FECHAS Y CONVERSIONES
# ============================================================

def a_fecha(v):
    """Convierte de forma segura datetime, Timestamp, str, NaT, None a date."""
    if v is None:
        return None
    if isinstance(v, float):
        import math
        if math.isnan(v) or math.isinf(v):
            return None
    try:
        import pandas as pd
        if pd.isna(v):
            return None
    except Exception:
        pass
    if isinstance(v, datetime):
        return date(int(v.year), int(v.month), int(v.day))
    if isinstance(v, date):
        return date(int(v.year), int(v.month), int(v.day))
    try:
        import pandas as pd
        ts = pd.to_datetime(v, errors="coerce")
        if pd.isna(ts) or ts is pd.NaT:
            return None
        return date(int(ts.year), int(ts.month), int(ts.day))
    except Exception:
        pass
    try:
        s = str(v).strip().split(" ")[0].split("T")[0]
        partes = [int(p) for p in s.split("-")]
        if len(partes) == 3:
            return date(partes[0], partes[1], partes[2])
    except Exception:
        pass
    return None


def inicio_mes(d) -> date | None:
    if d is None:
        return None
    d = a_fecha(d)
    if d is None:
        return None
    return date(int(d.year), int(d.month), 1)


def fin_mes(d) -> date | None:
    if d is None:
        return None
    d = a_fecha(d)
    if d is None:
        return None
    y, m = int(d.year), int(d.month)
    return date(y, m, calendar.monthrange(y, m)[1])


def sumar_meses(d, n: int) -> date | None:
    if d is None:
        return None
    d = a_fecha(d)
    if d is None:
        return None
    y, m = int(d.year), int(d.month)
    total_m = (y * 12 + (m - 1)) + int(n)
    return date(total_m // 12, (total_m % 12) + 1, 1)


def lista_meses(desde: date, hasta: date) -> list:
    """Primeros de mes entre `desde` y `hasta` (ambos inclusive)."""
    out, m = [], inicio_mes(desde)
    limite = inicio_mes(hasta)
    while m <= limite:
        out.append(m)
        m = sumar_meses(m, 1)
    return out


def etiqueta_mes(d: date) -> str:
    return f"{MESES_ES[d.month - 1]} {d.year}"


def _mes_corto(d: date) -> str:
    return f"{MESES_ES[d.month - 1][:3]}. {d.year}"


def dias_solape(i1, f1, i2, f2) -> int:
    """Días de solape entre dos intervalos inclusivos."""
    ini = max(i1, i2)
    fin1 = f1 if f1 is not None else FIN_ABIERTO
    fin2 = f2 if f2 is not None else FIN_ABIERTO
    fin = min(fin1, fin2)
    return max(0, (fin - ini).days + 1)


# ============================================================
# PERIODOS Y VALIDACIONES
# ============================================================

def etiqueta_periodo(p: dict) -> str:
    ini = _mes_corto(p["inicio"])
    fin = _mes_corto(p["fin"]) if p.get("fin") else "en adelante"
    return f"{ini} → {fin}"


def periodo_de(periodos, d: date):
    """Encuentra el periodo aplicable para la fecha d."""
    for p in periodos:
        ini = p.get("inicio") or p.get("fecha_inicio")
        fin = p.get("fin") or p.get("fecha_fin")
        if ini is not None and ini <= d and (fin is None or d <= fin):
            return p
    return None


def total_cargos(cargos) -> float:
    return round(sum(float(c.get("pct", 0.0)) for c in (cargos or [])), 3)


def validar_fechas_periodo(periodos, inicio, fin, excluir_id=None) -> list:
    """Valida que el periodo sea coherente y no se solape con otros del mismo almacén."""
    errs = []
    if inicio is None:
        return ["Debes indicar el mes de inicio del periodo."]
    if MES_COMPLETO:
        if inicio != inicio_mes(inicio):
            errs.append(f"El inicio debe ser el día 1 del mes ({inicio_mes(inicio)}), no {inicio}.")
        if fin is not None and fin != fin_mes(fin):
            errs.append(f"El fin debe ser el último día del mes ({fin_mes(fin)}), no {fin}.")
    if fin is not None and fin < inicio:
        errs.append(f"La fecha de fin ({fin}) no puede ser anterior al inicio ({inicio}).")
    for p in periodos:
        if excluir_id and p.get("id") == excluir_id:
            continue
        p_ini = p.get("inicio") or p.get("fecha_inicio")
        p_fin = p.get("fin") or p.get("fecha_fin")
        if dias_solape(inicio, fin, p_ini, p_fin) > 0:
            errs.append(f"Se solapa con el periodo existente {etiqueta_periodo(p)}.")
    return errs


def plan_alta_periodo(periodos, inicio, fin) -> dict:
    """
    Si el nuevo período choca con uno ABIERTO que empezó antes, propone cerrarlo
    el día anterior al inicio del nuevo.
    """
    cerrar, aviso = None, None
    solapados = (
        [p for p in periodos if dias_solape(inicio, fin, p.get("inicio") or p.get("fecha_inicio"), p.get("fin") or p.get("fecha_fin")) > 0]
        if inicio is not None else []
    )
    if (len(solapados) == 1 and
            (solapados[0].get("fin") or solapados[0].get("fecha_fin")) is None and
            (solapados[0].get("inicio") or solapados[0].get("fecha_inicio")) < inicio):
        previo = solapados[0]
        cerrar = (previo, inicio - timedelta(days=1))
        errores = validar_fechas_periodo([p for p in periodos if p is not previo], inicio, fin)
        if fin is not None and not errores:
            aviso = (f"El periodo abierto {etiqueta_periodo(previo)} se cerrará el {cerrar[1]}, y después de "
                     f"{_mes_corto(fin)} el almacén quedará sin porcentajes definidos hasta que crees otro periodo.")
    else:
        errores = validar_fechas_periodo(periodos, inicio, fin)
    return {"errores": errores, "cerrar": cerrar if not errores else None, "aviso": aviso}


def huecos(periodos, desde: date, hasta: date) -> list:
    """Rangos de meses [(mes_ini, mes_fin)] entre `desde` y `hasta` sin periodo definido."""
    out, abierto = [], None
    for m in lista_meses(desde, hasta):
        cubierto = periodo_de(periodos, m) is not None and periodo_de(periodos, fin_mes(m)) is not None
        if not cubierto:
            abierto = (abierto[0], m) if abierto else (m, m)
        elif abierto:
            out.append(abierto)
            abierto = None
    if abierto:
        out.append(abierto)
    return out


def validar_cargos(cargos, regla: str):
    """(errores, total) de la lista de bolsas por cargo [{cargo, pct, cupos}]. Nunca permite pasar de 100."""
    errs, vistos, validos = [], set(), []
    if regla not in REGLAS:
        errs.append(f"Regla de reparto desconocida: {regla}.")
    for c in cargos:
        cargo = c.get("cargo")
        if not cargo:
            continue
        if cargo in vistos:
            errs.append(f"El cargo «{cargo}» está repetido.")
        vistos.add(cargo)
        try:
            pct = float(c.get("pct", 0.0))
        except (TypeError, ValueError):
            pct = -1.0
        if pct <= 0 or pct > 100:
            errs.append(f"El porcentaje de «{cargo}» debe estar entre 0.001 y 100.")
        else:
            validos.append(pct)
        if regla == REGLA_CUPO:
            cupos = c.get("cupos")
            if cupos in (None, "", 0):
                errs.append(f"En regla {regla}, «{cargo}» requiere indicar la cantidad de cupos (≥ 1).")
            else:
                try:
                    if int(cupos) < 1:
                        errs.append(f"Los cupos de «{cargo}» deben ser al menos 1.")
                except (TypeError, ValueError):
                    errs.append(f"Los cupos de «{cargo}» no son un número válido.")
    total = round(sum(validos), 3)
    if total > 100.0 + TOL:
        errs.append(f"La suma de las bolsas ({total:.3f} %) supera el 100 % (exceso: {total - 100:.3f} %).")
    return errs, total


# ============================================================
# CONSTRUCCIÓN Y ADAPTACIÓN DE PERIODOS
# ============================================================

def plantilla_a_periodos(plantilla: list) -> list:
    """Convierte las filas de rrhh.distribucion_puestos a periodos del nuevo modelo."""
    agrup = {}
    for f in plantilla:
        alm = f["almacen"]
        ini = a_fecha(f.get("vigente_desde")) or date(2025, 1, 1)
        fin = a_fecha(f.get("vigente_hasta"))
        k = (alm, ini, fin)
        agrup.setdefault(k, []).append({
            "cargo": f["cargo"], "pct": float(f["pct"]),
            "cupos": int(f["cupos"]) if f.get("cupos") not in (None, "") and f["cupos"] == f["cupos"] else None
        })
    periodos = []
    for i, ((alm, ini, fin), cargos) in enumerate(sorted(agrup.items()), start=1):
        periodos.append({
            "id": i, "almacen": alm, "inicio": ini, "fin": fin,
            "regla": REGLA_CUPO, "prueba_diluye": True, "cargos": cargos
        })
    return periodos


def armar_periodos(filas_periodos: list, filas_cargos: list) -> list:
    """Une las cabeceras de dist_periodos con sus filas de dist_periodo_cargos."""
    por_pid = {}
    for c in filas_cargos:
        pid = c["periodo_id"]
        por_pid.setdefault(pid, []).append({
            "id": c.get("id"), "cargo": c["cargo"], "pct": float(c["pct"]),
            "cupos": int(c["cupos"]) if c.get("cupos") is not None else None
        })
    out = []
    for p in filas_periodos:
        cargos = por_pid.get(p["id"], [])
        cargos.sort(key=lambda x: (_orden_cargo(x["cargo"]), x["cargo"]))
        out.append({
            "id": p["id"], "almacen": p["almacen"],
            "inicio": a_fecha(p["fecha_inicio"]),
            "fin": a_fecha(p.get("fecha_fin")),
            "regla": p.get("regla_reparto", REGLA_POOL),
            "prueba_diluye": bool(p.get("prueba_diluye", True)),
            "cargos": cargos
        })
    out.sort(key=lambda x: (x["almacen"], x["inicio"]))
    return out


def _orden_cargo(c: str) -> int:
    try:
        return CARGOS_BONO.index(c)
    except ValueError:
        return 999


# ============================================================
# CÁLCULO DE ASIGNACIONES EN EL MES
# ============================================================

def _tramos_del_mes(almacen: str, m_ini: date, m_fin: date, asignaciones: list):
    """Filtra y recorta las asignaciones activas de colaboradores en el mes."""
    tramos, anomalias = [], []
    for a in asignaciones:
        if a["almacen"] != almacen:
            continue
        if not a.get("activo", True):
            f_inact = a_fecha(a.get("fecha_inactividad"))
            if f_inact is None:
                anomalias.append(a)
                continue
            if f_inact < m_ini:
                continue
        else:
            f_inact = None

        a_ini = a_fecha(a.get("desde")) or m_ini
        a_fin = a_fecha(a.get("hasta"))
        if f_inact is not None:
            a_fin = min(a_fin, f_inact) if a_fin else f_inact

        if dias_solape(a_ini, a_fin, m_ini, m_fin) == 0:
            continue

        ini = max(m_ini, a_ini)
        fin = min(m_fin, a_fin) if a_fin else m_fin
        p_ini, p_fin = a_fecha(a.get("prueba_inicio")), a_fecha(a.get("prueba_fin"))
        dias_tot = (fin - ini).days + 1

        if a.get("en_prueba") and p_ini and p_fin:
            dias_p = dias_solape(ini, fin, p_ini, p_fin)
        elif a.get("en_prueba") and not p_ini and not p_fin:
            dias_p = 0
            anomalias.append(dict(a, _aviso_prueba=True))
        else:
            dias_p = 0

        tramos.append({
            "a": a, "ini": ini, "fin": fin,
            "dias": dias_tot, "dias_prueba": dias_p,
            "dias_participa": dias_tot - dias_p
        })
    return tramos, anomalias


def _dia_cargo_pool(bolsa: Fraction, gente: list, d: date, diluye: bool):
    """Regla POOL para un día dado."""
    n = len(gente)
    if n == 0:
        return {}, Fraction(0), bolsa, Fraction(0)
    cuota = bolsa / n
    asigs, prueba_dia, exceso = {}, Fraction(0), Fraction(0)
    for t in gente:
        en_p = t["a"].get("en_prueba") and t["a"].get("prueba_inicio") and t["a"].get("prueba_fin") and (t["a"]["prueba_inicio"] <= d <= t["a"]["prueba_fin"])
        if en_p:
            if diluye:
                asigs[id(t)] = (Fraction(0), cuota)
                prueba_dia += cuota
            else:
                asigs[id(t)] = (Fraction(0), Fraction(0))
        else:
            asigs[id(t)] = (cuota, Fraction(0))
    if not diluye:
        participantes = [t for t in gente if id(t) in asigs and asigs[id(t)][0] == cuota]
        k = len(participantes)
        if k == 0:
            return {id(t): (Fraction(0), Fraction(0)) for t in gente}, Fraction(0), bolsa, Fraction(0)
        cuota_part = bolsa / k
        for t in participantes:
            asigs[id(t)] = (cuota_part, Fraction(0))
        return asigs, Fraction(0), Fraction(0), Fraction(0)
    pagado_dia = sum((v[0] for v in asigs.values()), Fraction(0))
    libre_dia = bolsa - pagado_dia - prueba_dia
    return asigs, prueba_dia, libre_dia, exceso


def _dia_cargo_cupo(bolsa: Fraction, cupos: int, gente: list, d: date):
    """Regla POR_CUPO (modelo anterior) para un día."""
    n = len(gente)
    if cupos <= 0:
        return {}, Fraction(0), bolsa, Fraction(0)
    if n == 0:
        return {}, Fraction(0), bolsa, Fraction(0)
    cuota = bolsa / cupos
    asigs = {}
    if n <= cupos:
        for t in gente:
            asigs[id(t)] = (cuota, Fraction(0))
        pagado = cuota * n
        return asigs, Fraction(0), bolsa - pagado, Fraction(0)
    cuota_red = bolsa / n
    for t in gente:
        asigs[id(t)] = (cuota_red, Fraction(0))
    return asigs, Fraction(0), Fraction(0), Fraction(0)


def _nuevo_cargo():
    return {"pool": Fraction(0), "ocup": 0, "libre": Fraction(0), "dias_vacante": 0, "dias_sobre": 0,
            "max_n": 0, "dias_sin_pct": 0, "cuota_min": None, "cuota_max": None}


def _anotar_cuota(s, cuota):
    s["cuota_min"] = cuota if s["cuota_min"] is None else min(s["cuota_min"], cuota)
    s["cuota_max"] = cuota if s["cuota_max"] is None else max(s["cuota_max"], cuota)


# ============================================================
# MOTOR PRINCIPAL DE REPARTO
# ============================================================

def calcular_reparto_mes(
    almacen: str,
    mes: date,
    periodos,
    *args,
    modo: str = MODO_PORCENTAJE,
    cargos_config=None,
    asignaciones=None,
    **kwargs
) -> dict:
    """
    Calcula el reparto porcentual diario exacto para un mes en un almacén.
    Soporta firmas posicionales históricas y por parámetros nombrados.
    """
    if asignaciones is None:
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
            modo = args[2]
        else:
            asignaciones = []

    if not isinstance(asignaciones, list):
        try:
            asignaciones = list(asignaciones)
        except Exception:
            asignaciones = []

    m_ini, m_fin = inicio_mes(mes), fin_mes(mes)
    dias_mes = m_fin.day
    es_pct = modo == MODO_PORCENTAJE
    per_alm = sorted((p for p in periodos if p["almacen"] == almacen), key=lambda p: p["inicio"])
    tramos, anomalias = _tramos_del_mes(almacen, m_ini, m_fin, asignaciones)
    alertas = []

    for a in anomalias:
        if a.get("_aviso_prueba"):
            continue
        alertas.append({
            "nivel": "warning", "tipo": "DATOS",
            "mensaje": f"{a['nombre']} (CC {a['cedula']}) está inactivo sin fecha de inactividad: se excluyó."
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
                s["max_n"] = max(s["max_n"], n)
                asignado_acc += bolsa

                if regla == REGLA_POOL:
                    asigs, prueba_dia, libre_dia, exc_dia = _dia_cargo_pool(bolsa, gente, d, diluye)
                else:
                    k = c.get("cupos") or 1
                    asigs, prueba_dia, libre_dia, exc_dia = _dia_cargo_cupo(bolsa, k, gente, d)
                    if n < k:
                        s["dias_vacante"] += 1
                    elif n > k:
                        s["dias_sobre"] += 1

                libre_acc += libre_dia
                exceso_acc += exc_dia
                s["libre"] += libre_dia
                for tid, (pag, pru) in asigs.items():
                    acum[tid][0] += pag
                    acum[tid][1] += pru
                    if pag > 0:
                        _anotar_cuota(s, pag)

    personas = []
    for t in tramos:
        a = t["a"]
        s = por_cargo.get(a["cargo"])
        con_bolsa = es_pct and s is not None and s["pool"] > 0
        efectivo, en_prueba = acum[id(t)]
        personas.append({
            "colaborador_id": a["colaborador_id"], "cedula": a["cedula"], "nombre": a["nombre"],
            "cargo": a["cargo"], "desde_en_mes": t["ini"], "hasta_en_mes": t["fin"],
            "dias": t["dias"], "dias_prueba": t["dias_prueba"], "dias_participa": t["dias"] - t["dias_prueba"],
            "fraccion": t["dias"] / dias_mes,
            "pct_cargo": float(s["pool"] / dias_mes) if con_bolsa else None,
            "pct_bolsa_cargo": float(s["pool"] / dias_mes) if con_bolsa else 0.0,
            "pct_efectivo": float(efectivo / dias_mes) if con_bolsa else None,
            "pct_en_prueba": float(en_prueba / dias_mes) if con_bolsa else None
        })
    personas.sort(key=lambda p: (_orden_cargo(p["cargo"]), p["cargo"], p["desde_en_mes"], p["nombre"]))
    
    if not es_pct:
        alertas.append({
            "nivel": "info", "tipo": "INDIVIDUAL",
            "mensaje": "Almacén de evaluación individual: no reparte porcentaje del bono."
        })

        return {
            "mes": m_ini, "modo": modo, "periodos": [], "periodo": None,
            "periodo_aplicado": None, "dias_mes": dias_mes, "dias_sin_periodo": 0,
            "cargos": [], "personas": personas, "alertas": alertas,
            "totales": {
                "asignado": 0.0, "asignado_politica": 0.0,
                "ocupado": 0.0, "libre": 0.0, "vacante": 0.0,
                "pagado": 0.0, "repartido": 0.0,
                "prueba": 0.0, "en_prueba": 0.0,
                "exceso": 0.0, "sin_definir": 0.0,
                "personas": len(personas),
            },
        }

    cargos_res = []
    for cargo in sorted(por_cargo, key=lambda c: (_orden_cargo(c), c)):
        s = por_cargo[cargo]
        gente = [p for p in personas if p["cargo"] == cargo]
        cargos_res.append({
            "cargo": cargo,
            "pct_cargo": float(s["pool"] / dias_mes),
            "personas": len(gente),
            "personas_prom": s["ocup"] / dias_mes,
            "pct_persona_min": None if s["cuota_min"] is None else float(s["cuota_min"]),
            "pct_persona_max": None if s["cuota_max"] is None else float(s["cuota_max"]),
            "pct_efectivo": sum(p["pct_efectivo"] or 0.0 for p in gente),
            "pct_libre": float(s["libre"] / dias_mes),
            "dias_vacante": s["dias_vacante"], "dias_sobrecupo": s["dias_sobre"]
        })

    asignado_f = asignado_acc / dias_mes
    totales = {
        "asignado": float(asignado_f), "libre": float(libre_acc / dias_mes),
        "ocupado": float((asignado_acc - libre_acc) / dias_mes),
        "pagado": float(sum((v[0] for v in acum.values()), Fraction(0)) / dias_mes),
        "prueba": float(sum((v[1] for v in acum.values()), Fraction(0)) / dias_mes),
        "exceso": float(exceso_acc / dias_mes), "sin_definir": float(100 - asignado_f)
    }

    if sin_periodo:
        rango = f"{sin_periodo[0]:%d/%m} al {sin_periodo[-1]:%d/%m}" if len(sin_periodo) > 1 else f"{sin_periodo[0]:%d/%m}"
        msg = (f"No hay porcentajes definidos para {etiqueta_mes(m_ini)} en este almacén."
               if len(sin_periodo) == dias_mes else f"{len(sin_periodo)} día(s) del mes ({rango}) sin porcentajes.")
        alertas.append({
            "nivel": "error" if personas else "info", "tipo": "SIN_PERIODO",
            "mensaje": msg + (f" Hay {len(personas)} persona(s) en el periodo." if personas else "")
        })

    for p in usados.values():
        errs, total = validar_cargos(p["cargos"], p.get("regla", REGLA_POOL))
        if total < 100.0 - TOL:
            alertas.append({
                "nivel": "info", "tipo": "PERIODO_INCOMPLETO",
                "mensaje": f"El periodo {etiqueta_periodo(p)} suma {total:.1f} % (queda {100 - total:.1f} % sin definir)."
            })

    for cargo, s in por_cargo.items():
        if s["pool"] == 0 and s["ocup"] > 0:
            alertas.append({
                "nivel": "error", "tipo": "SIN_PCT",
                "mensaje": f"Hay personas en «{cargo}» pero el periodo no le asigna porcentaje."
            })
        elif s["libre"] > 0:
            alertas.append({
                "nivel": "info", "tipo": "VACANTE",
                "mensaje": f"«{cargo}» tuvo cupos vacíos en el mes: {float(s['libre']/dias_mes):.2f} % liberado."
            })
        if s["dias_sobre"] > 0:
            alertas.append({
                "nivel": "warning", "tipo": "SOBRECUPO",
                "mensaje": f"«{cargo}» tuvo más personas que cupos en {s['dias_sobre']} día(s)."
            })

    for a in anomalias:
        if a.get("_aviso_prueba"):
            alertas.append({
                "nivel": "warning", "tipo": "PRUEBA_SIN_FECHAS",
                "mensaje": f"{a['nombre']} (CC {a['cedula']}) está marcado en prueba pero sin fechas."
            })

    lista_usados = list(usados.values())
    per_unico = lista_usados[0] if lista_usados else None

        # ---- Alias de compatibilidad: las vistas y bono_reparticion usan estos nombres ----
    for c in cargos_res:
        c.setdefault("pct_bolsa", c.get("pct_cargo") or c.get("pct_total") or 0.0)
        c.setdefault("repartido", c.get("pct_efectivo") or 0.0)
        c.setdefault("en_prueba", 0.0)
        c.setdefault("vacante", c.get("pct_libre") or 0.0)
        c.setdefault("dias_totales",
                     sum(p["dias"] for p in personas if p["cargo"] == c["cargo"]))
        c.setdefault("estado", "VIGENTE" if (c.get("pct_bolsa") or 0) > 0 else "SIN_BOLSA")
    for p in personas:
        p.setdefault("pct_bolsa_cargo", p.get("pct_cargo") or p.get("pct_plantilla") or 0.0)
    totales.setdefault("asignado_politica", totales.get("asignado", 0.0))
    totales.setdefault("repartido", totales.get("pagado", totales.get("ocupado", 0.0)))
    totales.setdefault("en_prueba", totales.get("prueba", 0.0))
    totales.setdefault("vacante", totales.get("libre", 0.0))
    totales.setdefault("personas", len(personas))

    return {
        "mes": m_ini, "modo": modo, "periodos": lista_usados,
        "periodo": per_unico, "periodo_aplicado": per_unico,
        "dias_mes": dias_mes, "dias_sin_periodo": len(sin_periodo),
        "cargos": cargos_res, "personas": personas,
        "alertas": alertas, "totales": totales
    }


def evolucion(almacen: str, meses, periodos, asignaciones, modo: str = MODO_PORCENTAJE) -> list:
    filas = []
    for m in meses:
        r = calcular_reparto_mes(almacen, m, periodos, asignaciones, modo=modo)
        t = r["totales"]
        filas.append({
            "mes": m, "etiqueta": etiqueta_mes(m), "modo": modo,
            "periodo": etiqueta_periodo(r["periodo"]) if r["periodo"] else "—",
            "asignado": t["asignado"], "ocupado": t["ocupado"], "libre": t["libre"],
            "pagado": t["pagado"], "prueba": t["prueba"], "sin_definir": t["sin_definir"],
            "personas": len(r["personas"]),
            "alertas_error": sum(1 for a in r["alertas"] if a["nivel"] == "error"),
            "alertas_warn": sum(1 for a in r["alertas"] if a["nivel"] == "warning")
        })
    return filas


# ============================================================
# PERSISTENCIA Y MIGRACIONES
# ============================================================

def migrar(con) -> list:
    """Idempotente. Crea las tablas de periodos y ajusta app.almacenes."""
    from sqlalchemy import text
    avisos = []
    try:
        con.execute(text("ALTER TABLE app.almacenes ADD COLUMN IF NOT EXISTS modo_bono TEXT NOT NULL DEFAULT 'PORCENTAJE'"))
        con.execute(text("UPDATE app.almacenes SET modo_bono = 'INDIVIDUAL' WHERE codigo = 'EJECOM' AND modo_bono <> 'INDIVIDUAL'"))
    except Exception:
        pass
    try:
        con.execute(text("ALTER TABLE app.almacenes ADD COLUMN IF NOT EXISTS nombre TEXT"))
        con.execute(text("UPDATE app.almacenes SET nombre = COALESCE(nombre_serie, nombre_whs, codigo) WHERE nombre IS NULL"))
    except Exception:
        pass

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
    return avisos


def _bool(v, defecto=False) -> bool:
    if v is None:
        return defecto
    try:
        import pandas as pd
        if pd.isna(v):
            return defecto
    except (ImportError, TypeError, ValueError):
        pass
    return bool(v)


def cargar_almacenes(conn) -> list:
    """Carga almacenes activos con su código, nombre canónico y modalidad de bono."""
    try:
        df = conn.query("""
            SELECT codigo,
                   COALESCE(nombre_serie, nombre_whs, codigo) AS nombre,
                   COALESCE(modo_bono, 'PORCENTAJE') AS modo_bono
            FROM app.almacenes
            WHERE activo = TRUE OR activo IS NULL
            ORDER BY codigo
        """, ttl=0)
    except Exception:
        try:
            df = conn.query("""
                SELECT codigo,
                       COALESCE(nombre_serie, nombre_whs, codigo) AS nombre
                FROM app.almacenes
                WHERE activo = TRUE OR activo IS NULL
                ORDER BY codigo
            """, ttl=0)
            df["modo_bono"] = df["codigo"].apply(
                lambda c: "INDIVIDUAL" if str(c).strip().upper() == "EJECOM" else "PORCENTAJE"
            )
        except Exception:
            try:
                df = conn.query("SELECT * FROM app.almacenes", ttl=0)
                if not df.empty:
                    if "codigo" not in df.columns and "id" in df.columns:
                        df["codigo"] = df["id"].astype(str)
                    if "nombre" not in df.columns:
                        for c_cand in ("nombre_serie", "nombre_whs", "descripcion", "almacen"):
                            if c_cand in df.columns:
                                df["nombre"] = df[c_cand]
                                break
                        if "nombre" not in df.columns:
                            df["nombre"] = df["codigo"]
                    if "modo_bono" not in df.columns:
                        df["modo_bono"] = df["codigo"].apply(
                            lambda c: "INDIVIDUAL" if str(c).strip().upper() == "EJECOM" else "PORCENTAJE"
                        )
                    if "activo" in df.columns:
                        df = df[df["activo"].fillna(True).astype(bool)]
                else:
                    return []
            except Exception as ex:
                log.error("Error al cargar almacenes: %s", ex)
                return []

    filas = df.to_dict("records")
    for f in filas:
        if not f.get("nombre"):
            f["nombre"] = str(f.get("codigo", ""))
        if not f.get("modo_bono"):
            f["modo_bono"] = "INDIVIDUAL" if str(f.get("codigo", "")).strip().upper() == "EJECOM" else "PORCENTAJE"
    return filas


def cargar_periodos(conn, almacen: str | None = None) -> list:
    """
    Periodos con sus bolsas por cargo (todos los almacenes, o uno).
    Si rrhh.dist_periodos no contiene datos, recurre automáticamente a
    rrhh.distribucion_puestos para retroalimentar la liquidación.
    """
    w, params = ("WHERE almacen = :a", {"a": almacen}) if almacen else ("", {})
    try:
        per = conn.query(f"SELECT id, almacen, fecha_inicio, fecha_fin, regla_reparto, prueba_diluye "
                         f"FROM rrhh.dist_periodos {w} ORDER BY almacen, fecha_inicio", params=params, ttl=0)
        car = conn.query(f"SELECT id, periodo_id, cargo, pct, cupos FROM rrhh.dist_periodo_cargos "
                         f"WHERE periodo_id IN (SELECT id FROM rrhh.dist_periodos {w})", params=params, ttl=0)
        res = armar_periodos(per.to_dict("records"), car.to_dict("records"))
        if res:
            return res
    except Exception:
        pass

    try:
        plantilla = cargar_plantilla(conn, almacen=almacen)
        if plantilla:
            return plantilla_a_periodos(plantilla)
    except Exception:
        pass
    return []


def cargar_plantilla(conn, almacen: str | None = None) -> list:
    """Plantilla del MODELO ANTERIOR (rrhh.distribucion_puestos)."""
    sql = ("SELECT id, almacen, cargo, pct, cupos, vigente_desde, vigente_hasta "
           "FROM rrhh.distribucion_puestos {w} ORDER BY almacen, vigente_desde, cargo")
    if almacen:
        df = conn.query(sql.format(w="WHERE almacen = :a"), params={"a": almacen}, ttl=0)
    else:
        df = conn.query(sql.format(w=""), ttl=0)
    filas = df.to_dict("records")
    for f in filas:
        f["id"], f["pct"] = int(f["id"]), float(f["pct"])
        f["cupos"] = int(f["cupos"]) if f.get("cupos") not in (None, "") and f["cupos"] == f["cupos"] else None
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
    pay = dict(despues or {})
    pay["motivo"] = motivo
    con.execute(text("""
        INSERT INTO rrhh.rrhh_auditoria
            (tabla, registro_id, accion, antes, despues, usuario, rol, nombre_usuario)
        VALUES (:t, :r, :a, CAST(:b AS jsonb), CAST(:d AS jsonb), :u, :rol, :nom)
    """), {
        "t": tabla, "r": str(registro_id), "a": accion,
        "b": json.dumps(antes, default=str) if antes is not None else None,
        "d": json.dumps(pay, default=str) if pay is not None else None,
        "u": usuario, "rol": rol, "nom": nombre
    })


def _bloquear_almacen(con, almacen: str) -> None:
    from sqlalchemy import text
    con.execute(text("SELECT id FROM rrhh.dist_periodos WHERE almacen = :a FOR UPDATE"), {"a": almacen})


def _periodo_por_id(con, periodo_id: int):
    from sqlalchemy import text
    per = con.execute(text("SELECT id, almacen, fecha_inicio, fecha_fin, regla_reparto, prueba_diluye "
                           "FROM rrhh.dist_periodos WHERE id = :i"), {"i": periodo_id}).mappings().first()
    if not per:
        raise ValueError(f"El periodo #{periodo_id} no existe.")
    car = con.execute(text("SELECT id, periodo_id, cargo, pct, cupos FROM rrhh.dist_periodo_cargos "
                           "WHERE periodo_id = :i ORDER BY id"), {"i": periodo_id}).mappings().all()
    alm = per["almacen"]
    hermanos = con.execute(text("SELECT id, almacen, fecha_inicio, fecha_fin, regla_reparto, prueba_diluye "
                                "FROM rrhh.dist_periodos WHERE almacen = :a AND id <> :i ORDER BY fecha_inicio"),
                           {"a": alm, "i": periodo_id}).mappings().all()
    p_dict = armar_periodos([dict(per)], [dict(x) for x in car])[0]
    h_dicts = armar_periodos([dict(x) for x in hermanos], [])
    return p_dict, h_dicts


def _periodos_db(con, almacen) -> list:
    from sqlalchemy import text
    per = con.execute(text("SELECT id, almacen, fecha_inicio, fecha_fin, regla_reparto, prueba_diluye "
                           "FROM rrhh.dist_periodos WHERE almacen = :a ORDER BY fecha_inicio"),
                      {"a": almacen}).mappings().all()
    car = con.execute(text("SELECT id, periodo_id, cargo, pct, cupos FROM rrhh.dist_periodo_cargos "
                           "WHERE periodo_id IN (SELECT id FROM rrhh.dist_periodos WHERE almacen = :a)"),
                      {"a": almacen}).mappings().all()
    return armar_periodos([dict(x) for x in per], [dict(x) for x in car])


def _insertar_cargos(con, periodo_id, cargos) -> None:
    from sqlalchemy import text
    for c in cargos:
        con.execute(text("INSERT INTO rrhh.dist_periodo_cargos (periodo_id, cargo, pct, cupos) "
                         "VALUES (:p, :c, :pc, :k)"),
                    {"p": periodo_id, "c": c["cargo"], "pc": round(float(c["pct"]), 3),
                     "k": int(c["cupos"]) if c.get("cupos") not in (None, "") and c["cupos"] == c["cupos"] else None})


def crear_periodo(con, *, almacen, inicio, fin, regla=REGLA_POOL, prueba_diluye=True, cargos=None,
                  usuario="sistema", rol="admin", motivo=None, nombre=None, **kwargs) -> int:
    from sqlalchemy import text
    _bloquear_almacen(con, almacen)
    inicio, fin = a_fecha(inicio), a_fecha(fin)
    cargos = cargos or []
    plan = plan_alta_periodo(_periodos_db(con, almacen), inicio, fin)
    if plan["errores"]:
        raise ValueError(" ".join(plan["errores"]))
    errs, total = validar_cargos(cargos, regla)
    if errs:
        raise ValueError(" ".join(errs))
    if plan["cerrar"]:
        previo, nuevo_fin = plan["cerrar"]
        con.execute(text("UPDATE rrhh.dist_periodos SET fecha_fin = :f WHERE id = :i"),
                    {"f": nuevo_fin, "i": previo["id"]})
        auditar(con, "distribucion_periodos", f"{almacen}:periodo:{previo['id']}", "CERRAR_PERIODO",
                {"fin": None}, {"fin": str(nuevo_fin), "por_nuevo_periodo_desde": str(inicio)},
                usuario, rol, motivo, nombre)
    pid = con.execute(text("""
        INSERT INTO rrhh.dist_periodos (almacen, fecha_inicio, fecha_fin, regla_reparto, prueba_diluye, creado_por)
        VALUES (:a, :i, :f, :r, :d, :u) RETURNING id
    """), {"a": almacen, "i": inicio, "f": fin, "r": regla, "d": bool(prueba_diluye), "u": usuario}).scalar()
    _insertar_cargos(con, pid, cargos)
    auditar(con, "distribucion_periodos", f"{almacen}:periodo:{pid}", "CREAR_PERIODO", None,
            {"almacen": almacen, "inicio": str(inicio), "fin": str(fin) if fin else None, "regla": regla,
             "prueba_diluye": bool(prueba_diluye), "total": total,
             "cargos": [{"cargo": c["cargo"], "pct": c["pct"], "cupos": c.get("cupos")} for c in cargos]},
            usuario, rol, motivo, nombre)
    return pid


def actualizar_periodo(con, *, periodo_id, inicio, fin, regla=REGLA_POOL, prueba_diluye=True,
                       usuario="sistema", rol="admin", motivo=None, nombre=None, **kwargs) -> None:
    from sqlalchemy import text
    actual, hermanos = _periodo_por_id(con, periodo_id)
    _bloquear_almacen(con, actual["almacen"])
    inicio, fin = a_fecha(inicio), a_fecha(fin)
    errs = validar_fechas_periodo(hermanos, inicio, fin, excluir_id=periodo_id)
    if errs:
        raise ValueError(" ".join(errs))
    errs_c, total = validar_cargos(actual["cargos"], regla)
    if errs_c:
        raise ValueError(" ".join(errs_c))
    con.execute(text("""
        UPDATE rrhh.dist_periodos
        SET fecha_inicio = :i, fecha_fin = :f, regla_reparto = :r, prueba_diluye = :d
        WHERE id = :id
    """), {"i": inicio, "f": fin, "r": regla, "d": bool(prueba_diluye), "id": periodo_id})
    auditar(con, "distribucion_periodos", f"{actual['almacen']}:periodo:{periodo_id}", "EDITAR_PERIODO",
            {"inicio": str(actual["inicio"]), "fin": str(actual["fin"]) if actual["fin"] else None,
             "regla": actual["regla"], "prueba_diluye": actual["prueba_diluye"]},
            {"inicio": str(inicio), "fin": str(fin) if fin else None, "regla": regla, "prueba_diluye": bool(prueba_diluye)},
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
            {"total": total_cargos(actual["cargos"]),
             "cargos": [{"cargo": c["cargo"], "pct": c["pct"], "cupos": c.get("cupos")} for c in actual["cargos"]]},
            {"total": total, "cargos": [{"cargo": c["cargo"], "pct": round(float(c["pct"]), 3),
                                         "cupos": c.get("cupos")} for c in cargos]},
            usuario, rol, motivo, nombre)
    return {"total": total, "restante": round(100.0 - total, 6)}


def eliminar_periodo(con, *, periodo_id, usuario="sistema", rol="admin",
                     motivo="Eliminación de período", nombre=None, **kwargs) -> None:
    from sqlalchemy import text
    if not motivo or len(motivo.strip()) < 5:
        raise ValueError("Indica el motivo de la eliminación (mínimo 5 caracteres).")
    actual, _ = _periodo_por_id(con, periodo_id)
    _bloquear_almacen(con, actual["almacen"])
    con.execute(text("DELETE FROM rrhh.dist_periodos WHERE id = :i"), {"i": periodo_id})
    auditar(con, "distribucion_periodos", f"{actual['almacen']}:periodo:{periodo_id}", "ELIMINAR_PERIODO",
            {"almacen": actual["almacen"], "inicio": str(actual["inicio"]),
             "fin": str(actual["fin"]) if actual["fin"] else None,
             "regla": actual["regla"], "prueba_diluye": actual["prueba_diluye"],
             "total": total_cargos(actual["cargos"]),
             "cargos": [{"cargo": c["cargo"], "pct": c["pct"], "cupos": c.get("cupos")} for c in actual["cargos"]]},
            None, usuario, rol, motivo, nombre)


# ============================================================
# ADAPTADORES DE INTEGRACIÓN Y COMPATIBILIDAD CON VISTAS
# ============================================================

CARGOS_ORDEN = {c: i for i, c in enumerate(CARGOS_BONO)}
resolver_periodo = periodo_de


def generar_rango_meses(desde=None, hasta=None, meses_atras: int = 12, meses_adelante: int = 2) -> list:
    """Genera lista de primeros de mes. Compatible con bono_reparticion.py."""
    if desde is None:
        hoy = date.today()
        m_act = inicio_mes(hoy)
        desde = sumar_meses(m_act, -meses_atras)
        if hasta is None:
            hasta = sumar_meses(m_act, meses_adelante)
    elif hasta is None:
        hasta = desde
    return lista_meses(desde, hasta)


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
        lista_cargos = list(cargos_pct) if cargos_pct else []

    tot = sum(float(c.get("pct", 0)) for c in lista_cargos)
    if tot > 100.0 + TOL:
        raise ValueError(f"La suma de los porcentajes ({tot:.3f}%) supera el 100%.")

    nom_aud = nombre_usuario or nombre or usuario
    if periodo_id:
        try:
            actualizar_periodo(con, periodo_id=periodo_id, inicio=fecha_inicio, fin=fecha_fin,
                               regla=REGLA_POOL, prueba_diluye=True, usuario=usuario, rol=rol,
                               motivo=motivo, nombre=nom_aud)
            guardar_cargos(con, periodo_id=periodo_id, cargos=lista_cargos,
                           usuario=usuario, rol=rol, motivo=motivo, nombre=nom_aud)
            return periodo_id
        except Exception:
            con.execute(text("""
                UPDATE rrhh.dist_periodos
                SET fecha_inicio = :fi, fecha_fin = :ff
                WHERE id = :id AND almacen = :a
            """), {"fi": fecha_inicio, "ff": fecha_fin, "id": periodo_id, "a": almacen})
            con.execute(text("DELETE FROM rrhh.dist_periodo_cargos WHERE periodo_id = :pid"), {"pid": periodo_id})
            for c in lista_cargos:
                con.execute(text("""
                    INSERT INTO rrhh.dist_periodo_cargos (periodo_id, cargo, pct, cupos)
                    VALUES (:pid, :c, :p, :k)
                """), {"pid": periodo_id, "c": c["cargo"], "p": round(float(c["pct"]), 3),
                         "k": c.get("cupos")})
            return periodo_id
    else:
        return crear_periodo(con, almacen=almacen, inicio=fecha_inicio, fin=fecha_fin,
                             regla=REGLA_POOL, prueba_diluye=True, cargos=lista_cargos,
                             usuario=usuario, rol=rol, motivo=motivo, nombre=nom_aud)



# ============================================================
# SHIM DE COMPATIBILIDAD (no eliminar): garantiza las claves que leen
# views/rrhh_distribucion.py y views/bono_reparticion.py, y el fallback
# legacy a rrhh.distribucion_puestos cuando rrhh.dist_periodos está vacía.
# ============================================================
_calcular_reparto_mes_base = calcular_reparto_mes


def calcular_reparto_mes(*args, **kwargs):
    r = _calcular_reparto_mes_base(*args, **kwargs)
    t = r.setdefault("totales", {})
    t.setdefault("asignado_politica", t.get("asignado", 0.0))
    t.setdefault("repartido", t.get("pagado", t.get("ocupado", 0.0)))
    t.setdefault("en_prueba", t.get("prueba", 0.0))
    t.setdefault("vacante", t.get("libre", 0.0))
    t.setdefault("sin_definir", round(100.0 - float(t.get("asignado", 0.0)), 6))
    t.setdefault("personas", len(r.get("personas", [])))
    for c in r.get("cargos", []):
        c.setdefault("pct_bolsa", c.get("pct_cargo", c.get("pct_total", 0.0)))
        c.setdefault("repartido", c.get("pct_efectivo", 0.0))
        c.setdefault("en_prueba", 0.0)
        c.setdefault("vacante", c.get("pct_libre", 0.0))
        c.setdefault("dias_totales",
                     sum(p["dias"] for p in r.get("personas", []) if p["cargo"] == c["cargo"]))
        c.setdefault("estado", "VIGENTE" if (c.get("pct_bolsa") or 0) > 0 else "SIN_BOLSA")
    for p in r.get("personas", []):
        p.setdefault("pct_bolsa_cargo", p.get("pct_cargo") or p.get("pct_plantilla") or 0.0)
        p.setdefault("dias_participa", p.get("dias", 0) - p.get("dias_prueba", 0))
    return r


_cargar_periodos_base = cargar_periodos


def _normalizar_periodo(p: dict) -> dict:
    """Garantiza las claves que leen las vistas: nombre, inicio/fecha_inicio, fin/fecha_fin."""
    if "inicio" not in p and "fecha_inicio" in p:
        p["inicio"] = a_fecha(p["fecha_inicio"])
    if "fecha_inicio" not in p and "inicio" in p:
        p["fecha_inicio"] = p["inicio"]
    if "fin" not in p and "fecha_fin" in p:
        p["fin"] = a_fecha(p["fecha_fin"])
    if "fecha_fin" not in p and "fin" in p:
        p["fecha_fin"] = p["fin"]
    if not p.get("nombre"):
        ini = p.get("inicio")
        fin = p.get("fin")
        p["nombre"] = (
            f"Política {ini.strftime('%d/%m/%Y') if ini else 's/f'} → "
            f"{fin.strftime('%d/%m/%Y') if fin else 'Abierto'}"
        )
    return p


def cargar_periodos(conn, almacen=None):
    periodos = _cargar_periodos_base(conn, almacen)
    if not periodos:
        # Fallback legacy: convierte rrhh.distribucion_puestos en periodos POR_CUPO
        try:
            legacy = cargar_plantilla(conn, almacen)
            if legacy:
                periodos = plantilla_a_periodos(legacy)
        except Exception:
            periodos = []
    return [_normalizar_periodo(p) for p in periodos]
