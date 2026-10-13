"""
TALENTO HUMANO — Novedades por periodo: faltas (Tipo 1/2/3) y ausencias.
Pestañas: Resumen | Crear y cargar | Editar periodo | Historial.
Reglas: 1 día de ausencia = 3,3333% (base 30); dominical opcional = +1 día (primer
domingo posterior a la fecha fin); máx. 3 faltas por persona y periodo;
total = % faltas + % días, con tope de 100%.
"""
import io
import json
from collections import Counter
from datetime import date

import pandas as pd
import streamlit as st
from openpyxl.worksheet.datavalidation import DataValidation
from sqlalchemy import text

from src import rrhh_dist as dist
from src import rrhh_nov as nov
from src.conexion_db import conn
from src.rrhh import ROLES_RRHH_ADMIN, ROLES_RRHH_CARGUE

st.set_page_config(page_title="Novedades RRHH", layout="wide")
st.title("Talento Humano — Novedades por Periodo")
st.markdown("---")
rol = st.session_state.get("rol_actual")
usuario = st.session_state.get("usuario_actual", "desconocido")
nombre_usr = st.session_state.get("nombre_completo", usuario)
if rol not in ROLES_RRHH_CARGUE:
    st.error("Acceso denegado. Solo Talento Humano, Gerencia y Administración.")
    st.stop()
st.caption("1 día de ausencia = 3,3333% (base 30). Dominical opcional = +1 día. "
           "Máx. 3 faltas por persona y periodo. Tope del descuento: 100%.")

# ============================================================
# CATÁLOGOS Y DATOS
# ============================================================
# Catálogo limpio: solo grados válidos (nunca Leve/Grave/Gravísima)
GRADOS = conn.query("""
    SELECT id, nombre FROM rrhh.tipos_falta
    WHERE activo AND nombre NOT IN ('Falta Leve', 'Falta Grave', 'Falta Gravísima')
    ORDER BY nombre
""", ttl=0)
MAP_GRADO = {r["nombre"]: r["id"] for _, r in GRADOS.iterrows()}

COLS = conn.query("""
    SELECT c.id, c.cedula, c.nombre,
           COALESCE(c.almacen_actual, a.almacen) AS almacen,
           COALESCE(c.cargo_actual, a.cargo) AS cargo
    FROM rrhh.colaboradores c
    LEFT JOIN LATERAL (SELECT almacen, cargo FROM rrhh.asignaciones
                       WHERE colaborador_id = c.id AND fecha_hasta IS NULL
                       ORDER BY fecha_desde DESC LIMIT 1) a ON TRUE
    WHERE c.activo ORDER BY c.nombre
""", ttl=0)
MAP_COL = {f"{r['nombre']} — CC {r['cedula']}": r["id"] for _, r in COLS.iterrows()}
CED_ID = {r["cedula"]: r["id"] for _, r in COLS.iterrows()}
COL_INFO = {r["id"]: r for _, r in COLS.iterrows()}


def _pdate(v):
    if v is None or (isinstance(v, float) and pd.isna(v)) or str(v).strip() == "":
        return None
    d = pd.to_datetime(v, errors="coerce")
    return None if pd.isna(d) else d.date()


def _pbool(v):
    if v is None:
        return False
    try:
        if pd.isna(v):
            return False
    except (TypeError, ValueError):
        pass
    if isinstance(v, bool):
        return v
    return str(v).strip().upper() in ("SI", "SÍ", "TRUE", "1")


def _texto(v):
    return str(v).strip() if pd.notna(v) and str(v).strip() else None


def cargar_novedades():
    df = conn.query("""
        SELECT n.id, n.colaborador_id, c.cedula, c.nombre, n.tipo, n.tipo_falta_id,
               tf.nombre AS grado, n.fecha_inicio, n.fecha_fin, n.incluye_dominical,
               n.observacion, n.cargado_por, n.creado_en
        FROM rrhh.novedades n
        JOIN rrhh.colaboradores c ON c.id = n.colaborador_id
        LEFT JOIN rrhh.tipos_falta tf ON tf.id = n.tipo_falta_id
        ORDER BY n.fecha_inicio DESC, n.id DESC
    """, ttl=0)
    if not df.empty:
        df["fecha_inicio"] = pd.to_datetime(df["fecha_inicio"]).dt.date
        df["fecha_fin"] = pd.to_datetime(df["fecha_fin"]).dt.date
    return df


def cargar_config():
    df = conn.query("""
        SELECT fc.id, fc.tipo_falta_id, tf.nombre AS grado, fc.pct_descuento,
               fc.vigente_desde, fc.vigente_hasta
        FROM rrhh.faltas_config fc JOIN rrhh.tipos_falta tf ON tf.id = fc.tipo_falta_id
        ORDER BY fc.vigente_desde, tf.id
    """, ttl=0)
    if not df.empty:
        df["vigente_desde"] = pd.to_datetime(df["vigente_desde"]).dt.date
        df["vigente_hasta"] = pd.to_datetime(df["vigente_hasta"], errors="coerce").dt.date
    return df.to_dict("records")


NOV = cargar_novedades()
CONFIG = cargar_config()
MES_ACTUAL = dist.inicio_mes(date.today())
MESES = list(reversed(dist.lista_meses(dist.sumar_meses(MES_ACTUAL, -24),
                                       dist.sumar_meses(MES_ACTUAL, 3))))


def selector_mes(key, opciones=None):
    ops = opciones or MESES
    idx = ops.index(MES_ACTUAL) if MES_ACTUAL in ops else 0
    return st.selectbox("Periodo", ops, index=idx, format_func=dist.etiqueta_mes, key=key)


def novedades_del_mes(df, mes):
    m = dist.inicio_mes(mes)
    out = []
    for _, r in df.iterrows():
        if r["tipo"] == "FALTA":
            toca = dist.inicio_mes(r["fecha_inicio"]) == m
        else:
            toca = m in nov.dias_por_mes(r["fecha_inicio"], r["fecha_fin"], r["incluye_dominical"])
        if toca:
            out.append(r.to_dict())
    return out


def auditar_con(con, tabla, rid, accion, antes, despues):
    con.execute(text("""
        INSERT INTO rrhh.rrhh_auditoria
            (tabla, registro_id, accion, antes, despues, usuario, rol, nombre_usuario)
        VALUES (:t, :r, :a, CAST(:b AS jsonb), CAST(:d AS jsonb), :u, :rol, :nom)
    """), {"t": tabla, "r": str(rid), "a": accion,
           "b": json.dumps(antes, default=str) if antes is not None else None,
           "d": json.dumps(despues, default=str) if despues is not None else None,
           "u": usuario, "rol": rol, "nom": nombre_usr})


def insertar_novedad(con, p):
    nid = con.execute(text("""
        INSERT INTO rrhh.novedades
            (colaborador_id, tipo, tipo_falta_id, fecha_inicio, fecha_fin,
             incluye_dominical, observacion, cargado_por)
        VALUES (:cid, :tipo, :tf, :fi, :ff, :dom, :obs, :usr)
        RETURNING id
    """), {"cid": p["cid"], "tipo": p["tipo"], "tf": p.get("tipo_falta_id"),
           "fi": p["fi"], "ff": p["ff"], "dom": p["dom"],
           "obs": p.get("obs"), "usr": usuario}).scalar()
    auditar_con(con, "novedades", nid, "CREATE", None, {
        "colaborador": COL_INFO[p["cid"]]["nombre"], "cedula": COL_INFO[p["cid"]]["cedula"],
        "tipo": p["tipo"], "grado": p.get("grado"),
        "desde": str(p["fi"]), "hasta": str(p["ff"]),
        "incluye_dominical": p["dom"], "observacion": p.get("obs"),
        "dias_por_mes": ({str(k): v for k, v in
                          nov.dias_por_mes(p["fi"], p["ff"], p["dom"]).items()}
                         if p["tipo"] != "FALTA" else None)})
    return nid


tab_res, tab_new, tab_edit, tab_hist = st.tabs(
    ["Resumen", "Crear y cargar", "Editar periodo", "Historial"])

# ------------------------------------------------------------
# RESUMEN
# ------------------------------------------------------------
with tab_res:
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        mes = selector_mes("res_mes")
    filas = novedades_del_mes(NOV, mes)
    por_persona = {}
    for n in filas:
        por_persona.setdefault(n["colaborador_id"], []).append(n)
    rows = []
    for cid, ns in por_persona.items():
        info = COL_INFO.get(cid)
        if info is None:
            continue
        r = nov.calcular_persona_mes(ns, CONFIG, mes)
        rows.append({
            "Cédula": info["cedula"], "Nombre": info["nombre"],
            "Almacén": info["almacen"], "Cargo": info["cargo"],
            "Grados de falta": ", ".join(f["grado"] or "?" for f in r["faltas"]) or "—",
            "% faltas": r["pct_faltas"],
            "Días ausencia": r["dias_total"],
            "Detalle días": ", ".join(f"{k}: {v}" for k, v in r["detalle_dias"].items()) or "—",
            "% días": r["pct_dias"],
            "% total descuento": r["total"],
            "Tope 100%": "SÍ" if r["tope_aplicado"] else "",
        })
    df_res = pd.DataFrame(rows)
    with c2:
        fa = st.multiselect("Almacén", sorted(df_res["Almacén"].dropna().unique()) if not df_res.empty else [], key="res_alm")
    with c3:
        fc = st.multiselect("Cargo", sorted(df_res["Cargo"].dropna().unique()) if not df_res.empty else [], key="res_cargo")
    with c4:
        fq = st.text_input("Buscar (nombre o cédula)", key="res_q")
    v = df_res
    if not v.empty:
        if fa: v = v[v["Almacén"].isin(fa)]
        if fc: v = v[v["Cargo"].isin(fc)]
        if fq.strip():
            v = v[v["Nombre"].str.contains(fq, case=False, na=False) | v["Cédula"].str.contains(fq, na=False)]
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Personas con novedades", 0 if v.empty else len(v))
    k2.metric("Faltas registradas", sum(1 for n in filas if n["tipo"] == "FALTA"))
    k3.metric("Días de ausencia", 0 if v.empty else int(v["Días ausencia"].sum()))
    k4.metric("% descuento máximo", 0.0 if v.empty else float(v["% total descuento"].max()))
    k5.metric("Suma % descuentos", 0.0 if v.empty else float(v["% total descuento"].sum()))
    st.markdown("##### Porcentajes por grado vigentes en el periodo")
    st.dataframe(pd.DataFrame([
        {"Grado": g["nombre"], "% descuento": nov.pct_falta_vigente(CONFIG, g["id"], mes)}
        for _, g in GRADOS.iterrows()]), hide_index=True, use_container_width=True,
        column_config={"% descuento": st.column_config.NumberColumn(format="%.2f%%")})
    if v.empty:
        st.info("Sin novedades en el periodo seleccionado.")
    else:
        st.dataframe(v, use_container_width=True, hide_index=True,
                     column_config={
                         "% faltas": st.column_config.NumberColumn(format="%.2f%%"),
                         "% días": st.column_config.NumberColumn(format="%.2f%%"),
                         "% total descuento": st.column_config.NumberColumn(format="%.2f%%")})
        st.download_button("Descargar resumen (CSV)", v.to_csv(index=False).encode("utf-8-sig"),
                           file_name=f"novedades_{mes:%Y_%m}.csv", mime="text/csv")
    with st.expander("Configurar % por grado (solo Gerencia/Admin)"):
        if rol in ROLES_RRHH_ADMIN:
            with st.form("form_cfg"):
                g1, g2, g3 = st.columns(3)
                gsel = g1.selectbox("Grado", list(MAP_GRADO.keys()))
                gpct = g2.number_input("% de descuento", 0.0, 100.0, step=0.5, key="cfg_pct")
                gvd = g3.date_input("Vigente desde (mes)", value=mes, key="cfg_vd")
                if st.form_submit_button("Guardar %"):
                    with conn.session as con:
                        con.execute(text("""
                            INSERT INTO rrhh.faltas_config (tipo_falta_id, pct_descuento, vigente_desde)
                            VALUES (:tf, :p, :vd)
                            ON CONFLICT (tipo_falta_id, vigente_desde)
                            DO UPDATE SET pct_descuento = EXCLUDED.pct_descuento
                        """), {"tf": MAP_GRADO[gsel], "p": float(gpct), "vd": gvd})
                        auditar_con(con, "faltas_config", f"{gsel}:{gvd}", "UPSERT_PCT",
                                    None, {"grado": gsel, "pct": float(gpct), "vigente_desde": str(gvd)})
                        con.commit()
                    st.success("Porcentaje guardado.")
                    st.rerun()
            st.dataframe(pd.DataFrame(CONFIG), use_container_width=True, hide_index=True)
        else:
            st.info("Solo lectura: los porcentajes los define Gerencia/Administración.")

# ------------------------------------------------------------
# CREAR Y CARGAR (dos tablas simples + vista previa unificada)
# ------------------------------------------------------------
with tab_new:
    mes = selector_mes("new_mes")
    save_n = st.session_state.get("nov_save_n", 0)

    st.markdown("##### 1) Registro de faltas")
    st.info("En esta tabla se registran **únicamente faltas Tipo 1, Tipo 2 o Tipo 3** "
            "(sanciones o descargos). Si una persona tuvo **dos faltas en el mismo mes**, "
            "agrégala **dos veces**: una fila por cada falta, con su fecha y una observación "
            "que explique lo sucedido. La fecha de la falta define el periodo (mes) al que "
            "se aplica el descuento.")
    ed_f = st.data_editor(
        pd.DataFrame(columns=["Colaborador", "Grado de falta", "Fecha de la falta", "Observación"]),
        num_rows="dynamic", use_container_width=True, hide_index=True,
        key=f"ed_faltas_{save_n}",
        column_config={
            "Colaborador": st.column_config.SelectboxColumn(
                "Colaborador", options=list(MAP_COL.keys()), required=True, width="large"),
            "Grado de falta": st.column_config.SelectboxColumn(
                "Grado de falta", options=list(MAP_GRADO.keys()), required=True, width="medium"),
            "Fecha de la falta": st.column_config.DateColumn(
                "Fecha de la falta", required=True, width="small"),
            "Observación": st.column_config.TextColumn(
                "Observación (justifica lo sucedido)", width="large"),
        })

    st.markdown("##### 2) Registro de ausencias")
    st.info("Registre aquí incapacidades, vacaciones, memorandos, permisos y ausencias sin aviso. "
            "Indique fecha de inicio y fecha final (último día). Si el documento carga también el "
            "dominical, marque la casilla: se sumará 1 día. Una ausencia que cruza dos meses se "
            "descuenta proporcionalmente en cada mes.")
    ed_a = st.data_editor(
        pd.DataFrame(columns=["Colaborador", "Tipo de ausencia", "Fecha inicio", "Fecha fin",
                              "Incluye dominical", "Observación"]),
        num_rows="dynamic", use_container_width=True, hide_index=True,
        key=f"ed_aus_{save_n}",
        column_config={
            "Colaborador": st.column_config.SelectboxColumn(
                "Colaborador", options=list(MAP_COL.keys()), required=True, width="large"),
            "Tipo de ausencia": st.column_config.SelectboxColumn(
                "Tipo de ausencia", options=list(nov.TIPOS_AUSENCIA), required=True, width="medium"),
            "Fecha inicio": st.column_config.DateColumn("Fecha inicio", required=True, width="small"),
            "Fecha fin": st.column_config.DateColumn("Fecha fin", required=True, width="small"),
            "Incluye dominical": st.column_config.CheckboxColumn(
                "Incluye dominical (+1 día)", default=False, width="small"),
            "Observación": st.column_config.TextColumn("Observación", width="large"),
        })

    # ---- Plan unificado ----
    plan, errs_all = [], []
    for i, r in ed_f.iterrows():
        if not _texto(r["Colaborador"]):
            continue
        cid = MAP_COL[r["Colaborador"]]
        grado = r["Grado de falta"] if _texto(r["Grado de falta"]) in MAP_GRADO else None
        fi = _pdate(r["Fecha de la falta"])
        e, w = nov.validar_novedad("FALTA", grado, fi, fi, False, periodo=mes)
        errs_all += [f"Faltas, fila {i + 1}: {x}" for x in e]
        plan.append({"origen": "Falta", "cid": cid, "tipo": "FALTA", "grado": grado,
                     "tipo_falta_id": MAP_GRADO.get(grado), "fi": fi, "ff": fi,
                     "dom": False, "obs": _texto(r["Observación"]), "avisos": w})
    for i, r in ed_a.iterrows():
        if not _texto(r["Colaborador"]):
            continue
        cid = MAP_COL[r["Colaborador"]]
        tipo = _texto(r["Tipo de ausencia"])
        fi, ff = _pdate(r["Fecha inicio"]), _pdate(r["Fecha fin"])
        dom = _pbool(r["Incluye dominical"])
        e, w = nov.validar_novedad(tipo, None, fi, ff, dom, periodo=mes)
        errs_all += [f"Ausencias, fila {i + 1}: {x}" for x in e]
        plan.append({"origen": "Ausencia", "cid": cid, "tipo": tipo, "grado": None,
                     "tipo_falta_id": None, "fi": fi, "ff": ff, "dom": dom,
                     "obs": _texto(r["Observación"]), "avisos": w})

    # Tope de 3 faltas por persona y periodo (existentes + nuevas)
    filas_mes = novedades_del_mes(NOV, mes)
    faltas_tot = (Counter(n["colaborador_id"] for n in filas_mes if n["tipo"] == "FALTA")
                  + Counter(p["cid"] for p in plan if p["tipo"] == "FALTA"))
    for cid, n in faltas_tot.items():
        if n > nov.MAX_FALTAS_POR_PERIODO:
            errs_all.append(f"{COL_INFO[cid]['nombre']} quedaría con {n} faltas en el periodo "
                            f"(máx. {nov.MAX_FALTAS_POR_PERIODO}).")

    if plan:
        prev = []
        for p in plan:
            if p["tipo"] == "FALTA":
                pf = nov.pct_falta_vigente(CONFIG, p["tipo_falta_id"], mes) or 0.0
                dm = 0
            else:
                dm = nov.dias_por_mes(p["fi"], p["ff"], p["dom"]).get(mes, 0) if p["fi"] and p["ff"] else 0
                pf = 0.0
            prev.append({
                "Colaborador": COL_INFO[p["cid"]]["nombre"],
                "Cédula": COL_INFO[p["cid"]]["cedula"],
                "Categoría": p["origen"],
                "Detalle": p["grado"] or p["tipo"],
                "Desde": p["fi"], "Hasta": p["ff"],
                "Dominical": "SÍ" if p["dom"] else "",
                "Días en el periodo": dm,
                "% días": nov.pct_por_dias(dm),
                "% falta": pf,
                "Observación": p["obs"] or "",
            })
        st.markdown("##### Vista previa (todo lo que se guardará)")
        st.dataframe(pd.DataFrame(prev), use_container_width=True, hide_index=True,
                     column_config={
                         "Desde": st.column_config.DateColumn(),
                         "Hasta": st.column_config.DateColumn(),
                         "% días": st.column_config.NumberColumn(format="%.2f%%"),
                         "% falta": st.column_config.NumberColumn(format="%.2f%%")})
        tot_rows = []
        por_persona = {}
        for p in plan:
            por_persona.setdefault(p["cid"], []).append(p)
        for cid, ps in por_persona.items():
            ns = [{"tipo": q["tipo"], "tipo_falta_id": q["tipo_falta_id"], "grado": q["grado"],
                   "fecha_inicio": q["fi"], "fecha_fin": q["ff"],
                   "incluye_dominical": q["dom"]} for q in ps]
            r = nov.calcular_persona_mes(ns, CONFIG, mes)
            tot_rows.append({"Colaborador": COL_INFO[cid]["nombre"],
                             "% faltas": r["pct_faltas"],
                             "Días de ausencia": r["dias_total"],
                             "% días": r["pct_dias"],
                             "% total descuento": r["total"],
                             "Tope 100%": "SÍ" if r["tope_aplicado"] else ""})
        st.markdown("##### Total por persona en el periodo")
        st.dataframe(pd.DataFrame(tot_rows), use_container_width=True, hide_index=True,
                     column_config={
                         "% faltas": st.column_config.NumberColumn(format="%.2f%%"),
                         "% días": st.column_config.NumberColumn(format="%.2f%%"),
                         "% total descuento": st.column_config.NumberColumn(format="%.2f%%")})
        for p in plan:
            for w in p["avisos"]:
                st.warning(f"{COL_INFO[p['cid']]['nombre']}: {w}")

    if errs_all:
        st.error("Corrige:\n" + "\n".join(errs_all))
    if st.button("Guardar novedades", type="primary",
                 disabled=(not plan) or bool(errs_all), key="nov_save_btn"):
        with conn.session as con:
            for p in plan:
                insertar_novedad(con, p)
            con.commit()
        st.session_state["nov_save_n"] = save_n + 1   # limpia las dos tablas
        st.success(f"{len(plan)} novedad(es) registradas y auditadas.")
        st.rerun()

    # ---------- Carga masiva Excel ----------
    st.markdown("---")
    st.subheader("Carga masiva (Excel)")
    st.caption("Una fila por novedad: si una persona tuvo dos faltas, o una falta y una ausencia, "
               "use una fila por cada una. La columna 'tipo' define si la fila es FALTA o ausencia.")
    cols_x = ["cedula", "tipo", "grado_falta", "fecha_falta", "fecha_inicio", "fecha_fin",
              "incluye_dominical", "observacion"]
    bufx = io.BytesIO()
    with pd.ExcelWriter(bufx, engine="openpyxl") as xw:
        pd.DataFrame(columns=cols_x).to_excel(xw, index=False, sheet_name="novedades")
        ws = xw.sheets["novedades"]
        dv_t = DataValidation(type="list", formula1='"' + ",".join(nov.TIPOS_NOVEDAD) + '"', allow_blank=False)
        dv_g = DataValidation(type="list", formula1='"' + ",".join(list(MAP_GRADO.keys())) + '"', allow_blank=True)
        dv_sn = DataValidation(type="list", formula1='"SI,NO"', allow_blank=True)
        ws.add_data_validation(dv_t); dv_t.add("B2:B1000")
        ws.add_data_validation(dv_g); dv_g.add("C2:C1000")
        ws.add_data_validation(dv_sn); dv_sn.add("G2:G1000")
        for col in ("D", "E", "F"):
            for r in range(2, 1001):
                ws[f"{col}{r}"].number_format = "YYYY-MM-DD"
        pd.DataFrame({"campo": cols_x, "instruccion": [
            "Cédula del colaborador activo",
            "Desplegable: FALTA o tipo de ausencia",
            "(solo aplica si tipo = FALTA) Desplegable Tipo 1/2/3",
            "(solo aplica si tipo = FALTA) Día de la falta; define el periodo",
            "(solo ausencias) AAAA-MM-DD",
            "(solo ausencias) AAAA-MM-DD, mayor o igual al inicio",
            "(solo ausencias) SI / NO",
            "Observación o justificación (opcional)"]}
        ).to_excel(xw, index=False, sheet_name="ayuda")
    st.download_button("Descargar plantilla (.xlsx)", bufx.getvalue(),
                       file_name="plantilla_novedades_th.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    upl = st.file_uploader("Subir Excel diligenciado", type=["xlsx"], key="nov_upl")
    if upl:
        dfx = pd.read_excel(upl, sheet_name="novedades")
        dfx.columns = [str(c).strip().lower() for c in dfx.columns]
        plan_x, errs_x = [], []
        for i, r in dfx.iterrows():
            ced = str(r.get("cedula", "")).strip()
            cid = CED_ID.get(ced)
            if cid is None:
                errs_x.append(f"Fila {i + 2}: cédula {ced} no existe o no está activa.")
                continue
            tipo = str(r.get("tipo", "")).strip().upper()
            if tipo == "FALTA":
                grado = _texto(r.get("grado_falta"))
                fi = _pdate(r.get("fecha_falta")) or _pdate(r.get("fecha_inicio"))
                ff, dom = fi, False
            else:
                grado = None
                fi, ff = _pdate(r.get("fecha_inicio")), _pdate(r.get("fecha_fin"))
                dom = _pbool(r.get("incluye_dominical"))
            e, w = nov.validar_novedad(tipo, grado, fi, ff, dom, periodo=mes)
            errs_x += [f"Fila {i + 2}: {x}" for x in e]
            plan_x.append({"origen": "Excel", "cid": cid, "tipo": tipo, "grado": grado,
                           "tipo_falta_id": MAP_GRADO.get(grado), "fi": fi, "ff": ff,
                           "dom": dom, "obs": _texto(r.get("observacion")), "avisos": w})
        fx_tot = (Counter(n["colaborador_id"] for n in filas_mes if n["tipo"] == "FALTA")
                  + Counter(p["cid"] for p in plan_x if p["tipo"] == "FALTA"))
        for cid, n in fx_tot.items():
            if n > nov.MAX_FALTAS_POR_PERIODO:
                errs_x.append(f"{COL_INFO[cid]['nombre']} quedaría con {n} faltas en el periodo.")
        if plan_x:
            st.dataframe(pd.DataFrame(plan_x).drop(columns=["cid", "tipo_falta_id", "avisos", "origen"]),
                         use_container_width=True, hide_index=True)
        if errs_x:
            st.error("No se importará nada. Corrige:\n" + "\n".join(errs_x))
        elif plan_x and st.button(f"Confirmar carga ({len(plan_x)} filas)", type="primary", key="x_save"):
            with conn.session as con:
                for p in plan_x:
                    insertar_novedad(con, p)
                con.commit()
            st.success(f"{len(plan_x)} novedades importadas y auditadas.")
            st.rerun()

# ------------------------------------------------------------
# EDITAR PERIODO
# ------------------------------------------------------------
with tab_edit:
    if rol in ROLES_RRHH_ADMIN:
        ops = MESES
        st.caption("Rol Gerencia/Admin: puede intervenir cualquier periodo.")
    else:
        ops = [dist.sumar_meses(MES_ACTUAL, -1), MES_ACTUAL, dist.sumar_meses(MES_ACTUAL, 1)]
        st.caption("Talento Humano: solo periodo anterior, vigente y siguiente.")
    mes = selector_mes("edit_mes", ops)
    dfp = NOV[NOV["fecha_inicio"].apply(dist.inicio_mes) == mes] if not NOV.empty else NOV
    if dfp.empty:
        st.info("Sin novedades registradas con inicio en este periodo.")
    else:
        st.dataframe(dfp[["id", "nombre", "cedula", "tipo", "grado", "fecha_inicio",
                          "fecha_fin", "incluye_dominical", "observacion", "cargado_por"]],
                     use_container_width=True, hide_index=True)

        def _label(r):
            grado = r["grado"] if isinstance(r["grado"], str) else ""
            return (f"#{r['id']} · {r['nombre']} · {r['tipo']}"
                    + (f" / {grado}" if grado else "")
                    + f" · {r['fecha_inicio']} → {r['fecha_fin']}")

        ops_sel = {_label(r): r["id"] for _, r in dfp.iterrows()}
        sel = st.selectbox("Novedad a intervenir", list(ops_sel.keys()), key="edit_sel")
        rid = ops_sel[sel]
        r = dfp[dfp["id"] == rid].iloc[0]
        with st.form("form_edit_nov"):
            e1, e2, e3 = st.columns(3)
            n_tipo = e1.selectbox("Tipo", list(nov.TIPOS_NOVEDAD),
                                  index=list(nov.TIPOS_NOVEDAD).index(r["tipo"]))
            idx_grado = (list(MAP_GRADO.keys()).index(r["grado"])
                         if isinstance(r["grado"], str) and r["grado"] in MAP_GRADO else None)
            n_grado = e2.selectbox("Grado (solo FALTA)", list(MAP_GRADO.keys()), index=idx_grado)
            n_dom = e3.checkbox("Incluye dominical", value=bool(r["incluye_dominical"]))
            e1, e2 = st.columns(2)
            n_fi = e1.date_input("Fecha inicio", value=r["fecha_inicio"])
            n_ff = e2.date_input("Fecha fin", value=r["fecha_fin"])
            n_obs = st.text_input("Observación", value=r["observacion"] or "")
            if st.form_submit_button("Guardar cambios"):
                e, w = nov.validar_novedad(n_tipo, n_grado if n_tipo == "FALTA" else None,
                                           n_fi, n_ff, n_dom, periodo=mes)
                if e:
                    st.error("\n".join(e))
                else:
                    antes = {k: r[k] for k in ("tipo", "grado", "fecha_inicio", "fecha_fin",
                                               "incluye_dominical", "observacion")}
                    with conn.session as con:
                        con.execute(text("""
                            UPDATE rrhh.novedades SET tipo=:t, tipo_falta_id=:tf,
                                fecha_inicio=:fi, fecha_fin=:ff, incluye_dominical=:dom,
                                observacion=:obs WHERE id=:id
                        """), {"t": n_tipo,
                               "tf": MAP_GRADO.get(n_grado) if n_tipo == "FALTA" else None,
                               "fi": n_fi, "ff": n_ff, "dom": n_dom,
                               "obs": n_obs or None, "id": rid})
                        auditar_con(con, "novedades", rid, "UPDATE", antes,
                                    {"tipo": n_tipo, "grado": n_grado, "desde": str(n_fi),
                                     "hasta": str(n_ff), "incluye_dominical": n_dom})
                        con.commit()
                    st.success("Novedad actualizada y auditada.")
                    st.rerun()
        with st.expander("Anular novedad"):
            mot = st.text_input("Motivo de la anulación (obligatorio)", key="edit_mot")
            conf = st.checkbox("Confirmo que anulo esta novedad", key="edit_conf")
            if st.button("Anular", type="primary", disabled=not conf, key="edit_del"):
                if len(mot.strip()) < 5:
                    st.error("Indica el motivo (mín. 5 caracteres).")
                else:
                    with conn.session as con:
                        con.execute(text("DELETE FROM rrhh.novedades WHERE id=:id"), {"id": rid})
                        auditar_con(con, "novedades", rid, "ANULAR",
                                    {k: r[k] for k in ("tipo", "grado", "fecha_inicio", "fecha_fin")},
                                    {"motivo": mot.strip()})
                        con.commit()
                    st.success("Novedad anulada; el movimiento quedó en el historial.")
                    st.rerun()

# ------------------------------------------------------------
# HISTORIAL
# ------------------------------------------------------------
with tab_hist:
    st.caption("Append-only: cada creación, edición o anulación es una fila nueva con autor y rol.")
    df_h = conn.query("""
        SELECT a.id, a.fecha, a.usuario,
               COALESCE(a.nombre_usuario, u.nombre_completo, a.usuario) AS nombre,
               COALESCE(a.rol, u.rol) AS rol,
               a.accion, a.tabla, a.registro_id, a.antes, a.despues
        FROM rrhh.rrhh_auditoria a
        LEFT JOIN app.usuarios_portal u ON u.usuario = a.usuario
        WHERE a.tabla IN ('novedades','faltas_config')
        ORDER BY a.fecha DESC LIMIT 300
    """, ttl=0)
    if df_h.empty:
        st.info("Sin movimientos auditados todavía.")
    else:
        st.dataframe(df_h[["id", "fecha", "nombre", "rol", "accion", "tabla", "registro_id"]]
                     .rename(columns={"id": "ID", "fecha": "Fecha", "nombre": "Autor",
                                      "rol": "Rol", "accion": "Acción", "tabla": "Tabla",
                                      "registro_id": "Registro"}),
                     use_container_width=True, hide_index=True)
        with st.expander("Ver antes / después de un movimiento"):
            rid = st.selectbox("ID del registro", df_h["id"].tolist(), key="hist_sel")
            fila = df_h[df_h["id"] == rid].iloc[0]
            c1, c2 = st.columns(2)
            c1.markdown("**Antes**"); c1.json(fila["antes"] if fila["antes"] is not None else {})
            c2.markdown("**Después**"); c2.json(fila["despues"] if fila["despues"] is not None else {})
    st.markdown("##### Novedades cargadas (con autor)")
    if not NOV.empty:
        st.dataframe(NOV[["id", "nombre", "cedula", "tipo", "grado", "fecha_inicio", "fecha_fin",
                          "incluye_dominical", "cargado_por", "creado_en"]],
                     use_container_width=True, hide_index=True)