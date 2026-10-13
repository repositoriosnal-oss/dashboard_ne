"""
TALENTO HUMANO — Motor de novedades por periodo (lógica pura, sin BD ni Streamlit).
Reglas: periodo = mes calendario; falta pertenece al mes de su fecha_inicio; las ausencias
se reparten por los meses que toca su rango; el dominical opcional suma 1 día el primer
domingo posterior a la fecha fin; base de días = 30 (1 día = 3,3333%);
total = %faltas + %días con tope 100%; máx. 3 faltas por persona y periodo.
"""
from __future__ import annotations

from datetime import date, timedelta

from src import rrhh_dist as dist

BASE_DIAS_MES = 30
PCT_DIA = 100.0 / BASE_DIAS_MES          # 3,3333 % por día
MAX_FALTAS_POR_PERIODO = 3
TOPE_PCT = 100.0
TIPOS_AUSENCIA = ("AUSENCIA", "INCAPACIDAD", "MEMORANDO", "VACACIONES", "PERMISO")
TIPOS_NOVEDAD = ("FALTA",) + TIPOS_AUSENCIA


def primer_domingo_despues(d: date) -> date:
    """Primer domingo ESTRICTAMENTE posterior a d (el dominical de la semana)."""
    delta = (6 - d.weekday()) % 7 or 7
    return d + timedelta(days=delta)


def fecha_dominical(ff: date, incluye: bool):
    return primer_domingo_despues(ff) if incluye else None


def dias_por_mes(fi: date, ff: date, incluye_dominical: bool = False) -> dict:
    """{primer_día_del_mes: días de ausencia en ese mes}, incluido el dominical si aplica."""
    out = {}
    m = dist.inicio_mes(fi)
    while m <= dist.inicio_mes(ff):
        d = dist.dias_solape(fi, ff, m, dist.fin_mes(m))
        if d:
            out[m] = d
        m = dist.sumar_meses(m, 1)
    dom = fecha_dominical(ff, incluye_dominical)
    if dom:
        k = dist.inicio_mes(dom)
        out[k] = out.get(k, 0) + 1
    return out


def pct_por_dias(dias) -> float:
    return round(dias * PCT_DIA, 4)


def pct_falta_vigente(config, tipo_falta_id, mes):
    """% del grado según la versión de faltas_config que rige en el mes."""
    m = dist.inicio_mes(mes)
    cand = []
    for r in config:
        if r.get("tipo_falta_id") != tipo_falta_id:
            continue
        
        # ✅ SANITIZACIÓN: Convertimos a fecha pura para evitar errores con NaT/NaN de pandas
        desde = dist.a_fecha(r.get("vigente_desde"))
        hasta = dist.a_fecha(r.get("vigente_hasta"))
        
        if desde is None:
            continue
            
        if dist.inicio_mes(desde) <= m:
            # Si 'hasta' es NaT/NaN, a_fecha lo vuelve None y se interpreta como abierto
            if hasta is None or dist.inicio_mes(hasta) >= m:
                cand.append(r)
                
    if not cand:
        return None
        
    return float(max(cand, key=lambda r: dist.a_fecha(r["vigente_desde"]))["pct_descuento"])


def calcular_persona_mes(novedades, config, mes) -> dict:
    """novedades: lista de dicts de UNA persona (tipo, tipo_falta_id, grado,
    fecha_inicio, fecha_fin, incluye_dominical). Devuelve el desglose del mes."""
    mes = dist.inicio_mes(mes)
    faltas, detalle_dias, dominicales = [], {}, []
    pct_faltas = 0.0
    
    for n in novedades:
        # ✅ SANITIZACIÓN de fechas de novedades
        fi = dist.a_fecha(n.get("fecha_inicio"))
        ff = dist.a_fecha(n.get("fecha_fin"))
        
        # Si las fechas son inválidas o nulas, saltamos esta novedad
        if fi is None or ff is None:
            continue

        if n["tipo"] == "FALTA":
            if dist.inicio_mes(fi) != mes:
                continue
            p = pct_falta_vigente(config, n["tipo_falta_id"], mes)
            faltas.append({"grado": n.get("grado"), "pct": p, "fecha": fi})
            pct_faltas += p or 0.0
            
        elif n["tipo"] in TIPOS_AUSENCIA:
            d = dias_por_mes(fi, ff, bool(n.get("incluye_dominical"))).get(mes, 0)
            if d:
                detalle_dias[n["tipo"]] = detalle_dias.get(n["tipo"], 0) + d
            dom = fecha_dominical(ff, bool(n.get("incluye_dominical")))
            if dom and dist.inicio_mes(dom) == mes:
                dominicales.append(dom)
                
    dias_total = sum(detalle_dias.values())
    pct_dias = pct_por_dias(dias_total)
    total = pct_faltas + pct_dias
    
    return {
        "faltas": faltas,
        "pct_faltas": round(pct_faltas, 4),
        "detalle_dias": detalle_dias,
        "dias_total": dias_total,
        "pct_dias": pct_dias,
        "dominicales": dominicales,
        "tope_aplicado": total > TOPE_PCT,
        "total": round(min(total, TOPE_PCT), 4),
    }


def validar_novedad(tipo, grado, fi, ff, incluye_dom, periodo=None):
    errores, avisos = [], []
    if tipo not in TIPOS_NOVEDAD:
        return [f"Tipo inválido: {tipo}."], avisos
    if fi is None or ff is None:
        return ["Fecha inicio y fecha fin son obligatorias."], avisos
    if ff < fi:
        errores.append("La fecha fin no puede ser anterior a la inicio.")
    if periodo is not None and dist.inicio_mes(fi) != dist.inicio_mes(periodo):
        errores.append("La fecha inicio debe quedar dentro del periodo seleccionado.")
    if tipo == "FALTA":
        if not grado:
            errores.append("La falta requiere grado (Tipo 1/2/3).")
        if incluye_dom:
            avisos.append("El dominical no aplica a faltas: se ignorará.")
    else:
        if incluye_dom and ff.weekday() == 6:
            avisos.append("El rango ya termina en domingo: verifica que no cargues el dominical dos veces.")
        if tipo == "VACACIONES" and (ff - fi).days + 1 > 60:
            avisos.append("Vacaciones de más de 60 días: confirma el rango.")
    return errores, avisos


def solapes_ausencias(novedades):
    """Pares de ausencias de la misma persona que se traslapan (riesgo de doble descuento)."""
    aus = [n for n in novedades if n["tipo"] in TIPOS_AUSENCIA]
    return [(a, b) for i, a in enumerate(aus) for b in aus[i + 1:]
            if dist.dias_solape(a["fecha_inicio"], a["fecha_fin"],
                                b["fecha_inicio"], b["fecha_fin"]) > 0]