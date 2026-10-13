"""
TALENTO HUMANO — Colaboradores: crear, editar, movimientos e historial.
Los porcentajes del bono viven en views/rrhh_distribucion.py (solo Gerencia/Admin).
Convención: asignaciones.fecha_hasta y colaboradores.fecha_inactividad son el
ÚLTIMO DÍA trabajado (inclusivo); NULL = vigente.
"""
import io
import re
from datetime import date, timedelta

import pandas as pd
import streamlit as st
from openpyxl.worksheet.datavalidation import DataValidation
from sqlalchemy import text

from src.conexion_db import conn
from src.rrhh import ROLES_RRHH_CARGUE, auditar_rrhh

st.set_page_config(page_title="Colaboradores RRHH", layout="wide")
st.title("Talento Humano — Colaboradores")
st.markdown("---")

rol = st.session_state.get("rol_actual")
if rol not in ROLES_RRHH_CARGUE:
    st.error("Acceso denegado. Solo Talento Humano, Gerencia y Administración.")
    st.stop()
#st.caption("Los porcentajes del bono por almacén/cargo se definen en "
#           "**Distribución del Bono** (Gerencia/Administración).")

# ============================================================
# CATÁLOGOS
# ============================================================
ALMACENES = [
    ("ALM128", "NORTE128"), ("ALM134", "PUNTO 134"), ("ALM170", "PUNTO170"),
    ("7AGOS", "7 DE AGOSTO"), ("AV19", "AVENIDA 19"), ("ARME", "ARMENIA"),
    ("CHIA", "CHIA"), ("EJECOM", "EJECUTIVOS COMERCIALES"),
    ("Q1", "CENTRO 1"), ("Q3", "CENTRO 3"), ("Q5", "CENTRO 5"), ("Q6", "CENTRO 6"),
    ("VILL", "VILLAVICENCIO"), ("GIRAR", "GIRARDOT"),
]
ALM_COD = dict(ALMACENES)
ALM_NORM = {**{c: c for c, _ in ALMACENES}, **{n: c for c, n in ALMACENES}}
CARGOS = ["Gerente Punto", "Ejecutivo Comercial", "Vendedor 1", "Vendedor 2",
          "Vendedor 3", "Cajera", "Bodega 1", "Bodega 2", "Conductor"]

# ============================================================
# MIGRACIÓN IDEMPOTENTE
# ============================================================
with conn.session as con:
    for ddl in [
        "ALTER TABLE rrhh.colaboradores ADD COLUMN IF NOT EXISTS almacen_actual TEXT",
        "ALTER TABLE rrhh.colaboradores ADD COLUMN IF NOT EXISTS cargo_actual TEXT",
        "ALTER TABLE rrhh.colaboradores ADD COLUMN IF NOT EXISTS en_prueba BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE rrhh.colaboradores ADD COLUMN IF NOT EXISTS prueba_inicio DATE",
        "ALTER TABLE rrhh.colaboradores ADD COLUMN IF NOT EXISTS prueba_fin DATE",
        "ALTER TABLE rrhh.colaboradores ADD COLUMN IF NOT EXISTS fecha_inactividad DATE",
    ]:
        con.execute(text(ddl))
    con.commit()

# ============================================================
# HELPERS DE NEGOCIO (misma transacción => sin solapes falsos)
# ============================================================
def _pdate(v):
    if v is None or (isinstance(v, float) and pd.isna(v)) or str(v).strip() == "":
        return None
    d = pd.to_datetime(v, errors="coerce")
    return None if pd.isna(d) else d.date()

def _cedula_ok(c):
    return bool(c) and re.fullmatch(r"\d{5,12}", c) is not None

def _solape(con, cid, desde, hasta, excluir_id=None):
    fh = hasta or date(2100, 12, 31)
    return con.execute(text("""
        SELECT id, almacen, cargo, fecha_desde, COALESCE(fecha_hasta, DATE '2100-12-31') AS fh
        FROM rrhh.asignaciones
        WHERE colaborador_id = :c AND id <> COALESCE(:ex, -1)
          AND fecha_desde < :fh AND COALESCE(fecha_hasta, DATE '2100-12-31') > :fd
    """), {"c": cid, "fd": desde, "fh": fh, "ex": excluir_id}).fetchall()

def _vigente(con, cid):
    return con.execute(text("""
        SELECT id, almacen, cargo, fecha_desde FROM rrhh.asignaciones
        WHERE colaborador_id = :c AND fecha_hasta IS NULL
    """), {"c": cid}).fetchone()

def _nueva_asignacion(con, cid, almacen, cargo, desde, hasta=None):
    conf = _solape(con, cid, desde, hasta)
    if conf:
        r = conf[0]
        raise ValueError(f"Solape con {r[1]}/{r[2]} ({r[3]} → {r[4]}).")
    res = con.execute(text("""
        INSERT INTO rrhh.asignaciones (colaborador_id, almacen, cargo, fecha_desde, fecha_hasta)
        VALUES (:c, :a, :g, :fd, :fh) RETURNING id
    """), {"c": cid, "a": almacen, "g": cargo, "fd": desde, "fh": hasta})
    return res.fetchone()[0]

def _aplicar_traslado(con, cid, destino, fin_origen, inicio_destino):
    if inicio_destino <= fin_origen:
        raise ValueError("La fecha de inicio en destino debe ser posterior a la fecha fin en origen.")
    v = _vigente(con, cid)
    if v is None:
        raise ValueError("El colaborador no tiene asignación vigente para trasladar.")
    conf = _solape(con, cid, inicio_destino, None, excluir_id=v[0])
    if conf:
        raise ValueError(f"Solape con otra asignación: {conf[0][1]}/{conf[0][2]}.")
    con.execute(text("UPDATE rrhh.asignaciones SET fecha_hasta = :fh WHERE id = :id"),
                {"fh": fin_origen, "id": v[0]})
    _nueva_asignacion(con, cid, destino, v[2], inicio_destino)
    con.execute(text("UPDATE rrhh.colaboradores SET almacen_actual = :a WHERE id = :c"),
                {"a": destino, "c": cid})

def _aplicar_cambio_cargo(con, cid, nuevo_cargo, fecha):
    v = _vigente(con, cid)
    if v is None:
        raise ValueError("El colaborador no tiene asignación vigente.")
    if v[2] == nuevo_cargo:
        raise ValueError("El cargo nuevo es igual al actual.")
    conf = _solape(con, cid, fecha, None, excluir_id=v[0])
    if conf:
        raise ValueError(f"Solape con otra asignación: {conf[0][1]}/{conf[0][2]}.")
    con.execute(text("UPDATE rrhh.asignaciones SET fecha_hasta = :fh WHERE id = :id"),
                {"fh": fecha - timedelta(days=1), "id": v[0]})
    _nueva_asignacion(con, cid, v[1], nuevo_cargo, fecha)
    con.execute(text("UPDATE rrhh.colaboradores SET cargo_actual = :g WHERE id = :c"),
                {"g": nuevo_cargo, "c": cid})

def _inactivar(con, cid, fecha):
    """Inactiva y CIERRA la asignación vigente en `fecha` (último día trabajado): libera el % del bono."""
    v = _vigente(con, cid)
    if v is not None and fecha < v[3]:
        raise ValueError(f"La fecha de inactividad ({fecha}) es anterior al inicio de su asignación vigente ({v[3]}).")
    if v is not None:
        con.execute(text("UPDATE rrhh.asignaciones SET fecha_hasta = :fh WHERE id = :id"),
                    {"fh": fecha, "id": v[0]})
    con.execute(text("UPDATE rrhh.colaboradores SET activo = FALSE, fecha_inactividad = :fi WHERE id = :c"),
                {"fi": fecha, "c": cid})

def _reactivar(con, cid, fecha):
    """Reactiva y ABRE una asignación nueva desde `fecha` en el último almacén/cargo."""
    est = con.execute(text("SELECT almacen_actual, cargo_actual, fecha_inactividad "
                           "FROM rrhh.colaboradores WHERE id = :c"), {"c": cid}).fetchone()
    fi = est[2]
    if fi and fecha <= fi:
        raise ValueError(f"El reingreso ({fecha}) debe ser posterior a la fecha de inactividad ({fi}).")
    ult = con.execute(text("SELECT almacen, cargo FROM rrhh.asignaciones WHERE colaborador_id = :c "
                           "ORDER BY fecha_desde DESC LIMIT 1"), {"c": cid}).fetchone()
    alm = est[0] or (ult[0] if ult else None)
    car = est[1] or (ult[1] if ult else None)
    if not alm or not car:
        raise ValueError("No tiene almacén/cargo registrado: créale primero la asignación inicial.")
    if fi:  # datos anteriores: la asignación pudo quedar abierta al inactivar
        con.execute(text("UPDATE rrhh.asignaciones SET fecha_hasta = :fh "
                         "WHERE colaborador_id = :c AND fecha_hasta IS NULL"), {"fh": fi, "c": cid})
    _nueva_asignacion(con, cid, alm, car, fecha)
    con.execute(text("UPDATE rrhh.colaboradores SET activo = TRUE, fecha_inactividad = NULL, "
                     "almacen_actual = :a, cargo_actual = :g WHERE id = :c"),
                {"a": alm, "g": car, "c": cid})

def _existe(con, cedula):
    return con.execute(text("SELECT id FROM rrhh.colaboradores WHERE cedula = :c"),
                       {"c": cedula}).fetchone()

def _crear_colaborador(con, d):
    res = con.execute(text("""
        INSERT INTO rrhh.colaboradores
            (cedula, nombre, slp_code, owner_code, usuario_portal, fecha_ingreso,
             periodo_prueba_meses, activo, almacen_actual, cargo_actual,
             en_prueba, prueba_inicio, prueba_fin, fecha_inactividad)
        VALUES (:ced,:nom,:slp,:own,:por,:ing,2,:act,:alm,:car,:pr,:pi,:pf,:fi)
        RETURNING id
    """), d)
    return res.fetchone()[0]

def _actualizar_datos_basicos(con, cid, d):
    """Recarga/edición segura: NUNCA pisa estado, fechas de movimiento ni puesto."""
    con.execute(text("""
        UPDATE rrhh.colaboradores SET
            nombre = :nom, slp_code = :slp, owner_code = :own, usuario_portal = :por,
            en_prueba = :pr, prueba_inicio = :pi, prueba_fin = :pf
        WHERE id = :cid
    """), {**d, "cid": cid})

# ============================================================
# PLANTILLA EXCEL (solo columnas de creación, con desplegables)
# ============================================================
def construir_plantilla():
    cols = ["cedula", "nombre", "almacen", "cargo", "fecha_ingreso",
            "en_periodo_prueba", "prueba_inicio", "prueba_fin"]
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        pd.DataFrame(columns=cols).to_excel(xw, index=False, sheet_name="colaboradores")
        ws = xw.sheets["colaboradores"]
        dv_alm = DataValidation(type="list", formula1='"' + ", ".join(c for c, _ in ALMACENES) + '"', allow_blank=True)
        dv_cargo = DataValidation(type="list", formula1='"' + ", ".join(CARGOS) + '"', allow_blank=True)
        dv_sn = DataValidation(type="list", formula1='"SI,NO"', allow_blank=True)
        ws.add_data_validation(dv_alm); dv_alm.add("C2:C1000")
        ws.add_data_validation(dv_cargo); dv_cargo.add("D2:D1000")
        ws.add_data_validation(dv_sn); dv_sn.add("F2:F1000")
        for col in ["E", "G", "H"]:
            for r in range(2, 1001):
                ws[f"{col}{r}"].number_format = "YYYY-MM-DD"
        pd.DataFrame({
            "campo": cols,
            "instruccion": [
                "Obligatorio, solo números (5-12 dígitos)", "Obligatorio",
                "Desplegable: código de almacén", "Desplegable: cargo",
                "Fecha AAAA-MM-DD", "SI / NO",
                "Solo si prueba = SI. Inicio (sugerido = fecha de ingreso)",
                "Solo si prueba = SI. Fin (sugerido = inicio + 2 meses)"],
        }).to_excel(xw, index=False, sheet_name="ayuda")
        pd.DataFrame({"almacen_codigo": [c for c, _ in ALMACENES],
                      "almacen_nombre": [n for _, n in ALMACENES]}).to_excel(
            xw, index=False, sheet_name="catalogo_almacenes")
        pd.DataFrame({"cargo": CARGOS}).to_excel(xw, index=False, sheet_name="catalogo_cargos")
    return buf.getvalue()

# ============================================================
# PESTAÑAS
# ============================================================
tab_res, tab_new, tab_edit, tab_hist = st.tabs(
    ["Resumen", "Crear y carga masiva", "Editar / Movimientos", "Historial"])

# ------------------------------------------------------------
# RESUMEN
# ------------------------------------------------------------
with tab_res:
    df = conn.query("""
        SELECT c.id, c.cedula, c.nombre,
               COALESCE(c.almacen_actual, a.almacen) AS almacen,
               COALESCE(c.cargo_actual, a.cargo) AS cargo,
               c.fecha_ingreso, c.en_prueba, c.prueba_inicio, c.prueba_fin,
               c.activo, c.fecha_inactividad
        FROM rrhh.colaboradores c
        LEFT JOIN LATERAL (SELECT almacen, cargo FROM rrhh.asignaciones
                           WHERE colaborador_id = c.id AND fecha_hasta IS NULL
                           ORDER BY fecha_desde DESC LIMIT 1) a ON TRUE
        ORDER BY c.nombre
    """, ttl=0)
    if df.empty:
        st.info("Aún no hay colaboradores. Créalos en la pestaña 'Crear y carga masiva'.")
    else:
        act = df[df["activo"]]
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Colaboradores", len(df)); c2.metric("Activos", len(act))
        c3.metric("Inactivos", len(df) - len(act))
        c4.metric("En prueba", int(act["en_prueba"].sum()))
        c5.metric("Sin puesto", int((act["almacen"].isna() | act["cargo"].isna()).sum()))
        f1, f2, f3 = st.columns(3)
        fa = f1.multiselect("Almacén", sorted(df["almacen"].dropna().unique()))
        fc = f2.multiselect("Cargo", sorted(df["cargo"].dropna().unique()))
        fe = f3.multiselect("Estado", ["ACTIVO", "INACTIVO"], default=["ACTIVO", "INACTIVO"])
        v = df.copy()
        v["estado"] = v["activo"].map({True: "ACTIVO", False: "INACTIVO"})
        if fa: v = v[v["almacen"].isin(fa)]
        if fc: v = v[v["cargo"].isin(fc)]
        if fe: v = v[v["estado"].isin(fe)]
        st.dataframe(v, use_container_width=True, hide_index=True)
        st.download_button("Descargar vista (CSV)", v.to_csv(index=False).encode("utf-8-sig"),
                           file_name="colaboradores_th.csv", mime="text/csv")

# ------------------------------------------------------------
# CREAR Y CARGA MASIVA
# ------------------------------------------------------------
with tab_new:
    modo = st.radio("Cómo cargar", ["Formulario individual", "Carga masiva (Excel)"], horizontal=True)
    if modo.startswith("Formulario"):
        # Widgets FUERA del form para render dinámico; el form solo agrupa el submit.
        c1, c2, c3 = st.columns(3)
        ced = c1.text_input("Cédula *", key="new_ced")
        nom = c2.text_input("Nombre completo *", key="new_nom")
        f_ing = c3.date_input("Fecha de ingreso *", value=None, key="new_fing")
        c1, c2, c3 = st.columns(3)
        alm = c1.selectbox("Almacén *", ALMACENES, format_func=lambda x: f"{x[0]} - {x[1]}",
                           index=None, key="new_alm")
        car = c2.selectbox("Cargo *", CARGOS, index=None, key="new_car")
        prb = c3.radio("Periodo de prueba", ["NO", "SI"], horizontal=True, key="new_prb")
        p_ini = p_fin = None
        if prb == "SI":
            c1, c2 = st.columns(2)
            p_ini = c1.date_input("Inicio periodo de prueba", value=f_ing, key="new_pi")
            p_fin = c2.date_input("Fin periodo de prueba",
                                  value=(f_ing + timedelta(days=60)) if f_ing else None, key="new_pf")
        with st.form("form_alta_submit"):
            if st.form_submit_button("Crear colaborador", type="primary"):
                errs = []
                if not _cedula_ok(ced.strip()): errs.append("Cédula inválida (solo números, 5-12 dígitos).")
                if not nom.strip(): errs.append("El nombre es obligatorio.")
                if f_ing is None: errs.append("La fecha de ingreso es obligatoria.")
                if alm is None: errs.append("Seleccione almacén.")
                if car is None: errs.append("Seleccione cargo.")
                if prb == "SI":
                    if p_ini is None or p_fin is None:
                        errs.append("Periodo de prueba: indique inicio y fin.")
                    else:
                        if f_ing and p_ini < f_ing:
                            errs.append("El inicio de la prueba no puede ser anterior al ingreso.")
                        if p_fin <= p_ini:
                            errs.append("El fin de la prueba debe ser posterior al inicio.")
                with conn.session as con:
                    if not errs and _existe(con, ced.strip()):
                        errs.append("La cédula ya existe: edítela en 'Editar / Movimientos'.")
                if errs:
                    st.error("Corrige:\n" + "\n".join(errs))
                else:
                    try:
                        with conn.session as con:
                            cid = _crear_colaborador(con, {
                                "ced": ced.strip(), "nom": nom.strip(),
                                "slp": None, "own": None, "por": None, "ing": f_ing,
                                "act": True, "alm": alm[0], "car": car,
                                "pr": prb == "SI", "pi": p_ini, "pf": p_fin, "fi": None})
                            _nueva_asignacion(con, cid, alm[0], car, f_ing)
                            con.commit()
                        auditar_rrhh("colaboradores", cid, "CREAR", None,
                                     {"cedula": ced.strip(), "nombre": nom.strip(),
                                      "almacen": alm[0], "cargo": car})
                        st.success(f"Colaborador {nom} creado (activo).")
                        st.rerun()
                    except Exception as e:
                        st.error(f"{e}")
    else:
        st.download_button("Descargar plantilla (.xlsx)", construir_plantilla(),
                           file_name="plantilla_colaboradores_th.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        upl = st.file_uploader("Subir Excel diligenciado", type=["xlsx"])
        if upl:
            dfx = pd.read_excel(upl, sheet_name="colaboradores")
            dfx.columns = [str(c).strip().lower() for c in dfx.columns]
            errs, ok = [], 0
            with conn.session as con:
                try:
                    for i, r in dfx.iterrows():
                        ced = str(r.get("cedula", "")).strip()
                        nom = str(r.get("nombre", "")).strip()
                        if not ced or not nom or str(ced).lower() == "nan":
                            continue
                        if not _cedula_ok(ced):
                            errs.append(f"Fila {i+2}: cédula inválida."); continue
                        if _existe(con, ced):
                            errs.append(f"Fila {i+2}: la cédula {ced} ya existe."); continue
                        alm_c = ALM_NORM.get(str(r.get("almacen", "")).strip())
                        car_v = str(r.get("cargo", "")).strip()
                        if not alm_c or car_v not in CARGOS:
                            errs.append(f"Fila {i+2}: almacén/cargo inválido."); continue
                        f_ing = _pdate(r.get("fecha_ingreso"))
                        if f_ing is None:
                            errs.append(f"Fila {i+2}: fecha_ingreso inválida."); continue
                        pr = str(r.get("en_periodo_prueba", "NO")).strip().upper() == "SI"
                        pi, pf = _pdate(r.get("prueba_inicio")), _pdate(r.get("prueba_fin"))
                        if pr and (pi is None or pf is None or pf <= pi or pi < f_ing):
                            errs.append(f"Fila {i+2}: fechas de prueba inválidas."); continue
                        cid = _crear_colaborador(con, {
                            "ced": ced, "nom": nom, "slp": None, "own": None, "por": None,
                            "ing": f_ing, "act": True, "alm": alm_c, "car": car_v,
                            "pr": pr, "pi": pi, "pf": pf, "fi": None})
                        _nueva_asignacion(con, cid, alm_c, car_v, f_ing)
                        ok += 1
                    if errs:
                        con.rollback()
                        st.error("No se importó nada. Corrige:\n" + "\n".join(errs))
                    else:
                        con.commit()
                        auditar_rrhh("colaboradores", "bulk", "IMPORT", None, {"filas": ok})
                        st.success(f"{ok} filas importadas.")
                        st.rerun()
                except Exception as e:
                    con.rollback()
                    st.error(f"{e}")

# ------------------------------------------------------------
# EDITAR / MOVIMIENTOS
# ------------------------------------------------------------
with tab_edit:
    lista = conn.query("SELECT id, cedula, nombre, activo FROM rrhh.colaboradores ORDER BY nombre", ttl=0)
    if lista.empty:
        st.info("Sin colaboradores.")
    else:
        ops = {f"{r.nombre} - CC {r.cedula}" + ("" if r.activo else " (inactivo)"): r.id
               for r in lista.itertuples()}
        sel = st.selectbox("Colaborador", list(ops), index=None)
        if sel:
            cid = ops[sel]
            with conn.session as con:
                est = con.execute(text("""
                    SELECT c.*, a.id AS asig_id, a.almacen, a.cargo, a.fecha_desde
                    FROM rrhh.colaboradores c
                    LEFT JOIN LATERAL (SELECT id, almacen, cargo, fecha_desde FROM rrhh.asignaciones
                                       WHERE colaborador_id=:c AND fecha_hasta IS NULL
                                       ORDER BY fecha_desde DESC LIMIT 1) a ON TRUE
                    WHERE c.id=:c
                """), {"c": cid}).mappings().fetchone()
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Almacén", est["almacen"] or "—")
            m2.metric("Cargo", est["cargo"] or "—")
            m3.metric("Estado", "ACTIVO" if est["activo"] else "INACTIVO")
            m4.metric("Prueba", "SÍ" if est["en_prueba"] else "NO")

            with st.expander("Datos básicos (corrige errores de carga)"):
                with st.form("form_basic"):
                    n_nom = st.text_input("Nombre completo", value=est["nombre"])
                    n_ced = st.text_input("Cédula", value=est["cedula"])
                    if st.form_submit_button("Guardar datos básicos"):
                        errs = []
                        if not _cedula_ok(n_ced.strip()): errs.append("Cédula inválida.")
                        if n_ced.strip() != est["cedula"]:
                            dup = conn.query("SELECT id FROM rrhh.colaboradores WHERE cedula=:c AND id<>:id",
                                             params={"c": n_ced.strip(), "id": cid}, ttl=0)
                            if not dup.empty: errs.append("La cédula ya pertenece a otro colaborador.")
                        if errs:
                            st.error("\n".join(errs))
                        else:
                            antes = {"nombre": est["nombre"], "cedula": est["cedula"]}
                            with conn.session as con:
                                con.execute(text("UPDATE rrhh.colaboradores SET nombre=:nom, cedula=:ced WHERE id=:c"),
                                            {"nom": n_nom.strip(), "ced": n_ced.strip(), "c": cid})
                                con.commit()
                            auditar_rrhh("colaboradores", cid, "UPDATE_DATOS", antes,
                                         {"nombre": n_nom.strip(), "cedula": n_ced.strip()})
                            st.success("Datos básicos guardados.")
                            st.rerun()

            with st.expander("Asignación de puesto (almacén/cargo)"):
                if est["asig_id"] is None:
                    st.warning("Sin asignación vigente (caso típico: se creó sin almacén por error).")
                    with st.form("form_creacion_asig"):
                        a1, a2, a3 = st.columns(3)
                        ca = a1.selectbox("Almacén", ALMACENES, format_func=lambda x: f"{x[0]} - {x[1]}", index=None)
                        cc = a2.selectbox("Cargo", CARGOS, index=None)
                        cd = a3.date_input("Fecha desde", value=est["fecha_ingreso"])
                        if st.form_submit_button("Crear asignación inicial"):
                            if ca is None or cc is None or cd is None:
                                st.error("Completa los tres campos.")
                            else:
                                try:
                                    with conn.session as con:
                                        _nueva_asignacion(con, cid, ca[0], cc, cd)
                                        con.execute(text("UPDATE rrhh.colaboradores SET almacen_actual=:a, cargo_actual=:g WHERE id=:c"),
                                                    {"a": ca[0], "g": cc, "c": cid})
                                        con.commit()
                                    auditar_rrhh("asignaciones", cid, "CREAR_ASIGNACION_INICIAL", None,
                                                 {"almacen": ca[0], "cargo": cc, "desde": str(cd)})
                                    st.success("Asignación inicial creada.")
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"{e}")
                else:
                    st.caption("Corrección: reescribe almacén/cargo de la asignación vigente sin crear movimiento. Queda auditado.")
                    with st.form("form_corr_asig"):
                        a1, a2 = st.columns(2)
                        ca = a1.selectbox("Almacén", ALMACENES, format_func=lambda x: f"{x[0]} - {x[1]}",
                                          index=[c for c, _ in ALMACENES].index(est["almacen"]) if est["almacen"] in ALM_COD else None)
                        cc = a2.selectbox("Cargo", CARGOS,
                                          index=CARGOS.index(est["cargo"]) if est["cargo"] in CARGOS else None)
                        if st.form_submit_button("Guardar corrección"):
                            if ca is None or cc is None:
                                st.error("Selecciona almacén y cargo.")
                            else:
                                antes = {"almacen": est["almacen"], "cargo": est["cargo"]}
                                with conn.session as con:
                                    con.execute(text("UPDATE rrhh.asignaciones SET almacen=:a, cargo=:g WHERE id=:i"),
                                                {"a": ca[0], "g": cc, "i": est["asig_id"]})
                                    con.execute(text("UPDATE rrhh.colaboradores SET almacen_actual=:a, cargo_actual=:g WHERE id=:c"),
                                                {"a": ca[0], "g": cc, "c": cid})
                                    con.commit()
                                auditar_rrhh("asignaciones", cid, "CORRECCION_ASIGNACION",
                                            antes, {"asig_id": est["asig_id"], "almacen": ca[0], "cargo": cc})
                                st.success("Asignación vigente corregida.")
                                st.rerun()

            with st.expander("Traslado de punto de venta (movimiento real)"):
                with st.form("form_traslado"):
                    t1, t2, t3c = st.columns(3)
                    t1.markdown(f"Origen actual: **{est['almacen'] or '—'}**")
                    td = t2.selectbox("Almacén destino", ALMACENES, format_func=lambda x: f"{x[0]} - {x[1]}", index=None)
                    tf = t3c.date_input("Fecha fin en origen", value=None)
                    ti = st.date_input("Fecha inicio en destino", value=None)
                    if st.form_submit_button("Registrar traslado"):
                        if td is None or tf is None or ti is None:
                            st.error("Completa destino y ambas fechas.")
                        else:
                            try:
                                with conn.session as con:
                                    _aplicar_traslado(con, cid, td[0], tf, ti)
                                    con.commit()
                                auditar_rrhh("asignaciones", cid, "TRASLADO",
                                             {"origen": est["almacen"]},
                                             {"destino": td[0], "fin_origen": str(tf), "inicio_destino": str(ti)})
                                st.success("Traslado registrado.")
                                st.rerun()
                            except Exception as e:
                                st.error(f"{e}")

            with st.expander("Cambio de cargo (movimiento real)"):
                with st.form("form_cargo"):
                    h1, h2 = st.columns(2)
                    h1.markdown(f"Cargo actual: **{est['cargo'] or '—'}**")
                    cn = h2.selectbox("Cargo nuevo", CARGOS, index=None)
                    cf = st.date_input("Fecha inicio nuevo cargo", value=None)
                    if st.form_submit_button("Registrar cambio de cargo"):
                        if cn is None or cf is None:
                            st.error("Completa cargo y fecha.")
                        else:
                            try:
                                with conn.session as con:
                                    _aplicar_cambio_cargo(con, cid, cn, cf)
                                    con.commit()
                                auditar_rrhh("asignaciones", cid, "CAMBIO_CARGO",
                                             {"cargo": est["cargo"]}, {"cargo": cn, "fecha": str(cf)})
                                st.success("Cambio registrado.")
                                st.rerun()
                            except Exception as e:
                                st.error(f"{e}")

            with st.expander("Periodo de prueba"):
                with st.form("form_prueba"):
                    q1, q2 = st.columns(2)
                    n_pr = q1.radio("En prueba", ["NO", "SI"], index=1 if est["en_prueba"] else 0, horizontal=True) == "SI"
                    n_pi = q1.date_input("Inicio prueba", value=est["prueba_inicio"])
                    n_pf = q2.date_input("Fin prueba", value=est["prueba_fin"])
                    if st.form_submit_button("Guardar periodo de prueba"):
                        if n_pr and (n_pi is None or n_pf is None or n_pf <= n_pi):
                            st.error("Fechas de prueba inválidas.")
                        else:
                            with conn.session as con:
                                con.execute(text("UPDATE rrhh.colaboradores SET en_prueba=:pr, prueba_inicio=:pi, prueba_fin=:pf WHERE id=:c"),
                                            {"pr": n_pr, "pi": n_pi, "pf": n_pf, "c": cid})
                                con.commit()
                            auditar_rrhh("colaboradores", cid, "UPDATE_PRUEBA",
                                         {"en_prueba": bool(est["en_prueba"])},
                                         {"en_prueba": n_pr, "inicio": str(n_pi), "fin": str(n_pf)})
                            st.success("Periodo de prueba guardado.")
                            st.rerun()

            # --- ESTADO: SIN st.form a propósito (el checkbox habilita la fecha al instante) ---
            with st.expander("Estado del colaborador (activo / inactivo)"):
                if est["activo"]:
                    n_ina = st.checkbox("Inactivar colaborador", key=f"chk_ina_{cid}")
                    n_fi = st.date_input("Fecha de inactividad (último día trabajado)",
                                         value=None, disabled=not n_ina, key=f"fi_ina_{cid}")
                    if st.button("Aplicar cambio de estado", type="primary", key=f"btn_ina_{cid}"):
                        if not n_ina:
                            st.info("Marca la casilla para inactivar.")
                        elif n_fi is None:
                            st.error("Indica la fecha de inactividad.")
                        else:
                            try:
                                with conn.session as con:
                                    _inactivar(con, cid, n_fi)
                                    con.commit()
                                auditar_rrhh("colaboradores", cid, "INACTIVAR",
                                             {"activo": True, "almacen": est["almacen"], "cargo": est["cargo"]},
                                             {"activo": False, "fecha_inactividad": str(n_fi),
                                              "asignacion_cerrada_el": str(n_fi)})
                                st.success("Colaborador inactivado: su asignación quedó cerrada y su % "
                                           "del bono se libera desde el día siguiente.")
                                st.rerun()
                            except Exception as e:
                                st.error(f"{e}")
                else:
                    n_rea = st.checkbox("Reactivar colaborador", key=f"chk_rea_{cid}")
                    n_fr = st.date_input("Fecha de reingreso", value=None,
                                         disabled=not n_rea, key=f"fi_rea_{cid}")
                    if st.button("Aplicar cambio de estado", type="primary", key=f"btn_rea_{cid}"):
                        if not n_rea:
                            st.info("Marca la casilla para reactivar.")
                        elif n_fr is None:
                            st.error("Indica la fecha de reingreso.")
                        else:
                            try:
                                with conn.session as con:
                                    _reactivar(con, cid, n_fr)
                                    con.commit()
                                auditar_rrhh("colaboradores", cid, "REACTIVAR",
                                             {"activo": False},
                                             {"activo": True, "fecha_reingreso": str(n_fr)})
                                st.success("Colaborador reactivado con asignación nueva desde el reingreso.")
                                st.rerun()
                            except Exception as e:
                                st.error(f"{e}")

# ------------------------------------------------------------
# HISTORIAL (append-only: nunca se sobrescribe)
# ------------------------------------------------------------
with tab_hist:
    st.caption("Cada cambio queda como una fila nueva con el colaborador afectado, "
               "el antes → después, y el nombre y rol de quien lo hizo. Nada se edita ni se borra.")

    # ---------- Auditoría de cambios (tabla principal) ----------
    df_h = conn.query("""
        SELECT a.id, a.fecha, a.usuario,
               COALESCE(a.nombre_usuario, u.nombre_completo, a.usuario) AS nombre_usuario,
               COALESCE(a.rol, u.rol) AS rol,
               a.accion, a.tabla, a.registro_id, a.antes, a.despues
        FROM rrhh.rrhh_auditoria a
        LEFT JOIN app.usuarios_portal u ON u.usuario = a.usuario
        WHERE a.tabla IN ('colaboradores','asignaciones')
        ORDER BY a.fecha DESC LIMIT 300
    """, ttl=0)

    if df_h.empty:
        st.info("Sin cambios registrados todavía.")
    else:
        # Catálogos para resolver a qué persona corresponde cada cambio
        cols_map = conn.query("SELECT id, cedula, nombre FROM rrhh.colaboradores", ttl=0)
        map_col = {int(r["id"]): (str(r["cedula"]), str(r["nombre"])) for _, r in cols_map.iterrows()}
        asig_map = conn.query("SELECT id, colaborador_id FROM rrhh.asignaciones", ttl=0)
        map_asig = {int(r["id"]): int(r["colaborador_id"]) for _, r in asig_map.iterrows()}

        def _resolver_persona(accion, registro_id):
            """registro_id suele ser el id del colaborador; en registros antiguos de
            CORRECCION_ASIGNACION era el id de la asignación."""
            rid = str(registro_id)
            if not rid.isdigit():
                return ("", "")                      # ej. carga masiva ("bulk")
            i = int(rid)
            if accion == "CORRECCION_ASIGNACION" and i in map_asig:
                i = map_asig[i]
            if i in map_col:
                return map_col[i]
            if i in map_asig and map_asig[i] in map_col:
                return map_col[map_asig[i]]
            return ("", "")

        def _detalle(antes, despues):
            """Resumen legible: 'campo: antes → después'."""
            ant = antes if isinstance(antes, dict) else {}
            des = despues if isinstance(despues, dict) else {}
            if not ant:
                return "; ".join(f"{k}: {v}" for k, v in des.items()) or "—"
            partes = []
            for k, v in des.items():
                va = ant.get(k)
                partes.append(f"{k}: {va} → {v}" if va != v else f"{k}: {v}")
            return "; ".join(partes) if partes else "—"

        pers = [_resolver_persona(r["accion"], r["registro_id"]) for _, r in df_h.iterrows()]
        df_h["cedula_col"] = [p[0] for p in pers]
        df_h["nombre_col"] = [p[1] for p in pers]
        df_h["detalle"] = [_detalle(a, d) for a, d in zip(df_h["antes"], df_h["despues"])]

        st.markdown("##### Auditoría de cambios")
        st.dataframe(
            df_h[["id", "fecha", "nombre_col", "cedula_col", "accion", "detalle",
                  "usuario", "nombre_usuario", "rol", "tabla"]].rename(columns={
                "id": "ID", "fecha": "Fecha", "nombre_col": "Colaborador",
                "cedula_col": "Cédula", "accion": "Acción",
                "detalle": "Detalle (antes → después)",
                "usuario": "Usuario (login)", "nombre_usuario": "Nombre autor",
                "rol": "Rol", "tabla": "Tabla"}),
            use_container_width=True, hide_index=True,
            column_config={
                "Fecha": st.column_config.DatetimeColumn(format="DD/MM/YYYY HH:mm"),
                "Colaborador": st.column_config.TextColumn(width="medium"),
                "Cédula": st.column_config.TextColumn(width="small"),
                "Acción": st.column_config.TextColumn(width="small"),
                "Detalle (antes → después)": st.column_config.TextColumn(width="large"),
                "Nombre autor": st.column_config.TextColumn(width="medium"),
                "Rol": st.column_config.TextColumn(width="small"),
                "Tabla": st.column_config.TextColumn(width="small"),
            })

        with st.expander("Ver antes / después completo (JSON) de un cambio"):
            rid = st.selectbox("ID del registro", df_h["id"].tolist())
            fila = df_h[df_h["id"] == rid].iloc[0]
            c1, c2 = st.columns(2)
            c1.markdown("**Antes**")
            c1.json(fila["antes"] if fila["antes"] is not None else {})
            c2.markdown("**Después**")
            c2.json(fila["despues"] if fila["despues"] is not None else {})

    # ---------- Línea de tiempo de asignaciones ----------
    st.markdown("##### Línea de tiempo de asignaciones")
    df_as = conn.query("""
        SELECT a.id, c.nombre, c.cedula, a.almacen, a.cargo, a.fecha_desde,
               COALESCE(a.fecha_hasta::TEXT, 'Vigente') AS fecha_hasta
        FROM rrhh.asignaciones a JOIN rrhh.colaboradores c ON c.id = a.colaborador_id
        ORDER BY a.fecha_desde DESC LIMIT 300
    """, ttl=0)
    st.dataframe(df_as, use_container_width=True, hide_index=True)