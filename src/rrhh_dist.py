"""
Distribución del bono por almacén, cargo, persona y PERIODO (mes).

Independiente de src/rrhh_th.py (que sigue sin integrarse). Solo depende de las tablas que ya existen:
  rrhh.colaboradores, rrhh.asignaciones, rrhh.distribucion_puestos, app.almacenes, rrhh.rrhh_auditoria.

Modelo
------
* PLANTILLA = estructura del almacén: por cargo, `cupos` (cuántas personas ocupan ese cargo) y `pct`
  (porcentaje del bono POR PERSONA). El total de la plantilla es  Σ pct × cupos  y debe ser 100.
  Ej. Centro 6: Gerente 22 (1 cupo), Vendedor 2 11 (2 cupos) -> aporta 22.
* VERSIONES: todas las filas con el mismo `vigente_desde` forman una versión. Rige para un mes la
  versión con mayor `vigente_desde` (normalizada al día 1 del mes) menor o igual a ese mes. Una versión
  nueva sustituye a la anterior desde su mes, sin tocar la anterior (el pasado se conserva).
* OCUPACIÓN DEL MES = quién estuvo en el almacén ese mes, con las fechas de rrhh.asignaciones
  (ingreso, traslado, cambio de cargo) recortadas por la inactivación del colaborador. Cada persona
  ocupa un cupo en proporción a los días del mes que estuvo (fracción = días / días del mes).
* Cupo no ocupado = porcentaje LIBERADO (alerta). Más personas que cupos = SOBRECUPO (alerta).
* Almacenes con modo_bono = 'INDIVIDUAL' (EJECOM) no usan plantilla: su meta es individual.

Convención de fechas: fecha_hasta y fecha_inactividad son el ÚLTIMO DÍA trabajado (inclusivo).
Este archivo no importa streamlit ni sqlalchemy a nivel de módulo (se prueba sin infraestructura).
"""
from __future__ import annotations

import calendar
import json
from datetime import date, datetime, timedelta

# cargos que reparten bono por porcentaje (el Ejecutivo Comercial se evalúa individual en EJECOM)
CARGOS_BONO = ["Gerente Punto", "Vendedor 1", "Vendedor 2", "Vendedor 3",
               "Cajera", "Bodega 1", "Bodega 2", "Conductor"]
MODO_PORCENTAJE = "PORCENTAJE"
MODO_INDIVIDUAL = "INDIVIDUAL"
TOL = 0.0005
MESES_ES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto",
            "Septiembre", "Octubre", "Noviembre", "Diciembre"]


# ============================================================
# FECHAS
# ============================================================
def a_fecha(v):
    """date | datetime | pandas.Timestamp | NaT | None -> date | None."""
    if v is None:
        return None
    try:
        import pandas as pd
        if pd.isna(v):
            return None
    except (ImportError, TypeError, ValueError):
        pass
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    import pandas as pd
    return pd.to_datetime(v).date()


def inicio_mes(d: date) -> date:
    return date(d.year, d.month, 1)


def fin_mes(d: date) -> date:
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def sumar_meses(d: date, n: int) -> date:
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, 1)


def lista_meses(desde: date, hasta: date) -> list:
    """Primeros de mes entre `desde` y `hasta` (ambos inclusive)."""
    out, m = [], inicio_mes(desde)
    while m <= inicio_mes(hasta):
        out.append(m)
        m = sumar_meses(m, 1)
    return out


def etiqueta_mes(d: date) -> str:
    return f"{MESES_ES[d.month - 1]} {d.year}"


def dias_solape(a_desde, a_hasta, b_desde, b_hasta) -> int:
    """Días (inclusivos) comunes de dos rangos; hasta=None significa abierto."""
    lo = max(a_desde, b_desde)
    hi = min(a_hasta or date.max, b_hasta or date.max)
    return max(0, (hi - lo).days + 1)


# ============================================================
# PLANTILLA: VERSIONES Y VALIDACIÓN DEL 100 %
# ============================================================
def total_plantilla(filas) -> float:
    """Σ pct × cupos de una lista de filas de plantilla."""
    return round(sum(float(f["pct"]) * int(f.get("cupos") or 1) for f in filas), 6)


def estado_total(total: float) -> str:
    if abs(total - 100.0) <= TOL:
        return "COMPLETA"
    return "EXCEDIDA" if total > 100.0 else "INCOMPLETA"


def version_vigente(plantilla, mes: date):
    """(vigente_desde, filas) de la versión que rige en el mes, o (None, [])."""
    mes = inicio_mes(mes)
    candidatas = {}
    for f in plantilla:
        desde = a_fecha(f["vigente_desde"])
        hasta = a_fecha(f.get("vigente_hasta"))
        if inicio_mes(desde) <= mes and (hasta is None or inicio_mes(hasta) >= mes):
            candidatas.setdefault(desde, []).append(f)
    if not candidatas:
        return None, []
    v = max(candidatas)
    return v, candidatas[v]


def versiones(plantilla) -> list:
    """Resumen por versión (más reciente primero): desde, puestos, total, estado."""
    grupos = {}
    for f in plantilla:
        grupos.setdefault(a_fecha(f["vigente_desde"]), []).append(f)
    return [{"vigente_desde": d, "puestos": len(fs), "total": total_plantilla(fs),
             "estado": estado_total(total_plantilla(fs))} for d, fs in sorted(grupos.items(), reverse=True)]


def capacidad_restante(filas_version, cargo_editado=None) -> dict:
    """Lo asignado por los demás puestos y lo que queda por asignar (para el editor)."""
    otros = total_plantilla([f for f in filas_version if f["cargo"] != cargo_editado])
    return {"otros": otros, "restante": round(100.0 - otros, 6)}


# ============================================================
# OCUPACIÓN POR PERIODO
# ============================================================
def fin_efectivo(a: dict):
    """
    Último día que la persona cuenta en esa asignación: el menor entre fecha_hasta y la inactivación.
    Devuelve (fin | None, anomalia | None). Un inactivo sin fecha de inactividad y con asignación
    abierta no se puede ubicar en el tiempo: se excluye y se avisa.
    """
    hasta = a.get("hasta")
    if not a.get("activo", True):
        fi = a.get("fecha_inactividad")
        if fi:
            return (fi if hasta is None else min(hasta, fi)), None
        if hasta is None:
            return a["desde"] - timedelta(days=1), "INACTIVO_SIN_FECHA"
    return hasta, None


def _orden_cargo(c):
    return CARGOS_BONO.index(c) if c in CARGOS_BONO else len(CARGOS_BONO)


def calcular_reparto_mes(almacen: str, mes: date, plantilla, asignaciones, modo: str = MODO_PORCENTAJE) -> dict:
    """
    plantilla: filas de rrhh.distribucion_puestos del almacén (todas las versiones).
    asignaciones: filas de rrhh.asignaciones (con datos del colaborador) de cualquier almacén.
    """
    m_ini, m_fin = inicio_mes(mes), fin_mes(mes)
    dias_mes = m_fin.day
    ver, filas = version_vigente(plantilla, m_ini) if modo == MODO_PORCENTAJE else (None, [])
    pct_cargo = {f["cargo"]: float(f["pct"]) for f in filas}
    cupos_cargo = {f["cargo"]: int(f.get("cupos") or 1) for f in filas}

    personas, alertas, anomalias = [], [], []
    for a in asignaciones:
        if a["almacen"] != almacen:
            continue
        fin, anom = fin_efectivo(a)
        if anom:
            anomalias.append(a)
            continue
        tramo_fin = min(fin or m_fin, m_fin)
        tramo_ini = max(a["desde"], m_ini)
        dias = dias_solape(a["desde"], fin, m_ini, m_fin)
        if dias <= 0:
            continue
        dias_prueba = 0
        if a.get("prueba_inicio") and a.get("prueba_fin"):
            dias_prueba = dias_solape(tramo_ini, tramo_fin, a["prueba_inicio"], a["prueba_fin"])
        pct = pct_cargo.get(a["cargo"]) if modo == MODO_PORCENTAJE else None
        fr = dias / dias_mes
        fr_part = (dias - dias_prueba) / dias_mes
        personas.append({
            "colaborador_id": a["colaborador_id"], "cedula": a["cedula"], "nombre": a["nombre"],
            "cargo": a["cargo"], "desde_en_mes": tramo_ini, "hasta_en_mes": tramo_fin,
            "dias": dias, "dias_prueba": dias_prueba, "dias_participa": dias - dias_prueba,
            "fraccion": fr, "pct_plantilla": pct,
            "pct_efectivo": None if pct is None else pct * fr_part,
            "pct_en_prueba": None if pct is None else pct * (dias_prueba / dias_mes)})
    personas.sort(key=lambda p: (_orden_cargo(p["cargo"]), p["cargo"], p["desde_en_mes"], p["nombre"]))

    for a in anomalias:
        alertas.append({"nivel": "warning", "tipo": "DATOS",
                        "mensaje": f"{a['nombre']} (CC {a['cedula']}) está inactivo sin fecha de inactividad: "
                                   "no se puede ubicar en el tiempo y se excluyó del reparto."})

    if modo == MODO_INDIVIDUAL:
        alertas.append({"nivel": "info", "tipo": "INDIVIDUAL",
                        "mensaje": "Almacén de evaluación individual: no reparte porcentaje del bono; cada "
                                   "ejecutivo cumple su meta o no."})
        return {"mes": m_ini, "modo": modo, "version": None, "dias_mes": dias_mes, "cargos": [],
                "personas": personas, "alertas": alertas,
                "totales": {"asignado": 0.0, "ocupado": 0.0, "libre": 0.0, "pagado": 0.0,
                            "prueba": 0.0, "exceso": 0.0}}

    # ---------------- resumen por cargo
    cargos_res = []
    for cargo in sorted(set(pct_cargo) | {p["cargo"] for p in personas}, key=lambda c: (_orden_cargo(c), c)):
        gente = [p for p in personas if p["cargo"] == cargo]
        ocup = sum(p["fraccion"] for p in gente)
        pct, cupos = pct_cargo.get(cargo), cupos_cargo.get(cargo, 0)
        libres = max(0.0, cupos - ocup)
        sobre = max(0.0, ocup - cupos) if cargo in pct_cargo else 0.0
        cargos_res.append({
            "cargo": cargo, "cupos": cupos, "pct_persona": pct,
            "pct_total": None if pct is None else pct * cupos, "personas": len(gente),
            "ocupacion": ocup, "cupos_libres": libres,
            "pct_libre": 0.0 if pct is None else pct * libres,
            "sobrecupo": sobre, "pct_exceso": 0.0 if pct is None else pct * sobre})

    asignado = total_plantilla(filas)
    ocupado = sum(c["pct_persona"] * min(c["ocupacion"], c["cupos"]) for c in cargos_res if c["pct_persona"] is not None)
    totales = {"asignado": asignado, "ocupado": ocupado, "libre": max(0.0, asignado - ocupado),
               "pagado": sum(p["pct_efectivo"] or 0.0 for p in personas),
               "prueba": sum(p["pct_en_prueba"] or 0.0 for p in personas),
               "exceso": sum(c["pct_exceso"] for c in cargos_res)}

    # ---------------- alertas
    if ver is None:
        if personas:
            alertas.append({"nivel": "error", "tipo": "SIN_PLANTILLA",
                            "mensaje": f"No hay plantilla de porcentajes vigente en {etiqueta_mes(m_ini)} y el "
                                       f"almacén tiene {len(personas)} persona(s) en el periodo."})
        else:
            alertas.append({"nivel": "info", "tipo": "SIN_PLANTILLA",
                            "mensaje": "Sin plantilla vigente ni personas en este periodo."})
    else:
        est = estado_total(asignado)
        if est == "INCOMPLETA":
            alertas.append({"nivel": "warning", "tipo": "PLANTILLA_INCOMPLETA",
                            "mensaje": f"La plantilla suma {asignado:.2f}%: faltan {100 - asignado:.2f}% por asignar."})
        elif est == "EXCEDIDA":
            alertas.append({"nivel": "error", "tipo": "PLANTILLA_EXCEDIDA",
                            "mensaje": f"La plantilla suma {asignado:.2f}% (más de 100%). Corrígela."})
        for c in cargos_res:
            if c["pct_persona"] is None:
                alertas.append({"nivel": "error", "tipo": "SIN_PCT",
                                "mensaje": f"{c['personas']} persona(s) con cargo «{c['cargo']}» no tienen porcentaje "
                                           "en la plantilla vigente."})
                continue
            if c["cupos_libres"] > TOL:
                alertas.append({"nivel": "warning", "tipo": "VACANTE",
                                "mensaje": f"{c['cargo']}: {c['cupos_libres']:.2f} cupo(s) sin ocupar "
                                           f"→ {c['pct_libre']:.2f}% del bono liberado."})
            if c["sobrecupo"] > TOL:
                alertas.append({"nivel": "error", "tipo": "SOBRECUPO",
                                "mensaje": f"{c['cargo']}: hay {c['ocupacion']:.2f} cupo(s) ocupados y la plantilla "
                                           f"solo define {c['cupos']}. El reparto excedería {c['pct_exceso']:.2f}%."})
    return {"mes": m_ini, "modo": modo, "version": ver, "dias_mes": dias_mes, "cargos": cargos_res,
            "personas": personas, "alertas": alertas, "totales": totales}


def evolucion(almacen: str, meses, plantilla, asignaciones, modo: str = MODO_PORCENTAJE) -> list:
    """Una fila por mes con los totales del reparto (para auditar cómo cambió en el tiempo)."""
    filas = []
    for m in meses:
        r = calcular_reparto_mes(almacen, m, plantilla, asignaciones, modo)
        t = r["totales"]
        filas.append({"mes": m, "personas": len(r["personas"]), "asignado": t["asignado"],
                      "ocupado": t["ocupado"], "libre": t["libre"], "pagado": t["pagado"],
                      "en_prueba": t["prueba"],
                      "alertas": sum(1 for a in r["alertas"] if a["nivel"] in ("warning", "error")),
                      "version": r["version"]})
    return filas


def movimientos_mes(almacen: str, mes: date, asignaciones) -> list:
    """Altas, salidas, traslados y cambios de cargo del almacén durante el mes."""
    m_ini, m_fin = inicio_mes(mes), fin_mes(mes)
    por_persona = {}
    for a in asignaciones:
        por_persona.setdefault(a["colaborador_id"], []).append(a)
    posicion = {}
    for lst in por_persona.values():
        lst.sort(key=lambda x: x["desde"])
        for i, x in enumerate(lst):
            posicion[id(x)] = i
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
            movs.append({"fecha": a["desde"], "tipo": tipo, "nombre": a["nombre"],
                         "cedula": a["cedula"], "cargo": a["cargo"]})
        if fin is not None and m_ini <= fin < m_fin:
            if sig is not None and sig["almacen"] != almacen:
                tipo = f"Sale por traslado a {sig['almacen']}"
            elif sig is not None and sig["cargo"] != a["cargo"]:
                tipo = f"Cambio de cargo ({a['cargo']} → {sig['cargo']})"
            elif sig is None and not a.get("activo", True):
                tipo = "Inactivación"
            else:
                tipo = "Fin de asignación"
            movs.append({"fecha": fin, "tipo": tipo, "nombre": a["nombre"],
                         "cedula": a["cedula"], "cargo": a["cargo"]})
    movs.sort(key=lambda m: (m["fecha"], m["nombre"]))
    return movs


# ============================================================
# BASE DE DATOS (sqlalchemy se importa dentro de cada función)
# ============================================================
def migrar(con) -> None:
    """Idempotente. `con` es una sesión (conn.session); el llamador hace commit."""
    from sqlalchemy import text

    con.execute(text("ALTER TABLE rrhh.distribucion_puestos "
                     "ADD COLUMN IF NOT EXISTS cupos INT NOT NULL DEFAULT 1 CHECK (cupos >= 1)"))
    con.execute(text("ALTER TABLE app.almacenes "
                     "ADD COLUMN IF NOT EXISTS modo_bono TEXT NOT NULL DEFAULT 'PORCENTAJE'"))
    # Regla de negocio: EJECOM (Ejecutivos Comerciales) se evalúa por meta individual.
    con.execute(text("UPDATE app.almacenes SET modo_bono = 'INDIVIDUAL' "
                     "WHERE codigo = 'EJECOM' AND modo_bono <> 'INDIVIDUAL'"))


def cargar_almacenes(conn) -> list:
    df = conn.query("SELECT codigo, COALESCE(nombre_serie, codigo) AS nombre, modo_bono "
                    "FROM app.almacenes WHERE activo ORDER BY codigo", ttl=0)
    return df.to_dict("records")


def cargar_plantilla(conn, almacen: str | None = None) -> list:
    sql = ("SELECT id, almacen, cargo, pct, cupos, vigente_desde, vigente_hasta "
           "FROM rrhh.distribucion_puestos {w} ORDER BY almacen, vigente_desde, cargo")
    if almacen:
        df = conn.query(sql.format(w="WHERE almacen = :a"), params={"a": almacen}, ttl=0)
    else:
        df = conn.query(sql.format(w=""), ttl=0)
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
        f["activo"] = bool(f["activo"])
    return filas


def auditar(con, tabla, registro_id, accion, antes, despues, usuario, rol, motivo=None, nombre=None) -> None:
    """
    Escribe en rrhh.rrhh_auditoria dentro de la MISMA transacción del cambio: si la auditoría falla,
    el cambio no se guarda (en un motor de comisiones no puede haber cambios sin rastro).
    """
    from sqlalchemy import text

    pay = dict(despues or {})
    pay["motivo"] = motivo
    con.execute(text("""
        INSERT INTO rrhh.rrhh_auditoria
            (tabla, registro_id, accion, antes, despues, usuario, rol, nombre_usuario)
        VALUES (:t, :r, :a, CAST(:b AS jsonb), CAST(:d AS jsonb), :u, :rol, :nom)
    """), {"t": tabla, "r": str(registro_id), "a": accion,
           "b": json.dumps(antes, default=str) if antes is not None else None,
           "d": json.dumps(pay, default=str), "u": usuario or "desconocido",
           "rol": rol or "desconocido", "nom": nombre or usuario or "desconocido"})


def _bloquear_almacen(con, almacen: str) -> str:
    """Serializa ediciones concurrentes del mismo almacén y devuelve su modo de bono."""
    from sqlalchemy import text

    con.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"dist:{almacen}"})
    modo = con.execute(text("SELECT modo_bono FROM app.almacenes WHERE codigo = :a"), {"a": almacen}).scalar()
    if modo is None:
        raise ValueError(f"El almacén {almacen} no existe en el catálogo.")
    if modo == MODO_INDIVIDUAL:
        raise ValueError("Este almacén se evalúa por meta individual: no tiene plantilla de porcentajes.")
    return modo


def _filas_version(con, almacen, vigente_desde) -> list:
    from sqlalchemy import text

    r = con.execute(text("SELECT id, cargo, pct, cupos FROM rrhh.distribucion_puestos "
                         "WHERE almacen = :a AND vigente_desde = :v"),
                    {"a": almacen, "v": vigente_desde}).mappings().all()
    return [{"id": x["id"], "cargo": x["cargo"], "pct": float(x["pct"]), "cupos": int(x["cupos"])} for x in r]


def guardar_puesto(con, *, almacen, cargo, pct, cupos, vigente_desde, usuario, rol, motivo=None, nombre=None) -> dict:
    """Crea o ajusta un puesto de la versión `vigente_desde`. Nunca permite pasar de 100 %."""
    from sqlalchemy import text

    if cargo not in CARGOS_BONO:
        raise ValueError(f"El cargo «{cargo}» no reparte bono por porcentaje.")
    pct, cupos = float(pct), int(cupos)
    if not (0 < pct <= 100) or cupos < 1:
        raise ValueError("El porcentaje debe estar entre 0 y 100 (exclusivo 0) y los cupos ser al menos 1.")
    _bloquear_almacen(con, almacen)
    filas = _filas_version(con, almacen, vigente_desde)
    cap = capacidad_restante(filas, cargo)
    nuevo_total = cap["otros"] + pct * cupos
    if nuevo_total > 100.0 + TOL:
        raise ValueError(f"No se guardó: {almacen} quedaría en {nuevo_total:.3f}%. Solo quedan "
                         f"{cap['restante']:.3f}% por asignar (este puesto aporta {pct * cupos:.3f}%).")
    previo = next((f for f in filas if f["cargo"] == cargo), None)
    pid = con.execute(text("""
        INSERT INTO rrhh.distribucion_puestos (almacen, cargo, pct, cupos, vigente_desde)
        VALUES (:a, :c, :p, :k, :v)
        ON CONFLICT (almacen, cargo, vigente_desde)
        DO UPDATE SET pct = EXCLUDED.pct, cupos = EXCLUDED.cupos
        RETURNING id
    """), {"a": almacen, "c": cargo, "p": pct, "k": cupos, "v": vigente_desde}).scalar()
    auditar(con, "distribucion_puestos", f"{almacen}:{cargo}:{vigente_desde}",
            "ACTUALIZAR_PUESTO" if previo else "CREAR_PUESTO",
            {"pct": previo["pct"], "cupos": previo["cupos"]} if previo else None,
            {"almacen": almacen, "cargo": cargo, "pct": pct, "cupos": cupos,
             "vigente_desde": str(vigente_desde), "total_version": round(nuevo_total, 3)},
            usuario, rol, motivo, nombre)
    return {"id": pid, "total": nuevo_total, "restante": 100.0 - nuevo_total}


def eliminar_puesto(con, *, puesto_id, usuario, rol, motivo, nombre=None) -> None:
    from sqlalchemy import text

    fila = con.execute(text("SELECT almacen, cargo, pct, cupos, vigente_desde FROM rrhh.distribucion_puestos "
                            "WHERE id = :i"), {"i": int(puesto_id)}).mappings().first()
    if fila is None:
        raise ValueError("El puesto ya no existe.")
    _bloquear_almacen(con, fila["almacen"])
    con.execute(text("DELETE FROM rrhh.distribucion_puestos WHERE id = :i"), {"i": int(puesto_id)})
    auditar(con, "distribucion_puestos", f"{fila['almacen']}:{fila['cargo']}:{fila['vigente_desde']}",
            "ELIMINAR_PUESTO", {"pct": float(fila["pct"]), "cupos": int(fila["cupos"])},
            {"almacen": fila["almacen"], "cargo": fila["cargo"], "vigente_desde": str(fila["vigente_desde"])},
            usuario, rol, motivo, nombre)


def copiar_version(con, *, almacen, mes_destino: date, usuario, rol, motivo=None, nombre=None) -> int:
    """Crea la versión de `mes_destino` copiando la que rige justo antes. Devuelve # de puestos copiados."""
    from sqlalchemy import text

    mes_destino = inicio_mes(mes_destino)
    _bloquear_almacen(con, almacen)
    todas = con.execute(text("SELECT cargo, pct, cupos, vigente_desde, vigente_hasta "
                             "FROM rrhh.distribucion_puestos WHERE almacen = :a"), {"a": almacen}).mappings().all()
    todas = [dict(x, pct=float(x["pct"]), cupos=int(x["cupos"])) for x in todas]
    if any(inicio_mes(a_fecha(x["vigente_desde"])) == mes_destino for x in todas):
        raise ValueError(f"Ya existe una versión para {etiqueta_mes(mes_destino)}: edítala en lugar de copiar.")
    base_desde, base = version_vigente(todas, sumar_meses(mes_destino, -1))
    if not base:
        raise ValueError("No hay una versión anterior que copiar. Agrega los puestos manualmente.")
    for f in base:
        con.execute(text("INSERT INTO rrhh.distribucion_puestos (almacen, cargo, pct, cupos, vigente_desde) "
                         "VALUES (:a, :c, :p, :k, :v)"),
                    {"a": almacen, "c": f["cargo"], "p": f["pct"], "k": f["cupos"], "v": mes_destino})
    auditar(con, "distribucion_puestos", f"{almacen}:version:{mes_destino}", "CREAR_VERSION", None,
            {"almacen": almacen, "vigente_desde": str(mes_destino), "copiada_de": str(base_desde),
             "puestos": len(base), "total": total_plantilla(base)}, usuario, rol, motivo, nombre)
    return len(base)