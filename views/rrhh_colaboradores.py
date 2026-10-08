import io
from datetime import date, timedelta
import pandas as pd
import streamlit as st
from openpyxl.worksheet.datavalidation import DataValidation
from sqlalchemy import text
from src.conexion_db import conn
from src.rrhh import ROLES_RRHH_CARGUE, auditar_rrhh, validar_suma_100

st.set_page_config(page_title="Colaboradores RRHH", layout="wide")
st.title("Talento Humano — Colaboradores y Distribución del Bono")
st.markdown("---")

rol = st.session_state.get("rol_actual")
if rol not in ROLES_RRHH_CARGUE:
    st.error("Acceso denegado. Solo Talento Humano, Gerencia y Administración.")
    st.stop()

# ============================================================
# CATÁLOGOS ESTÁNDAR
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
CARGOS = [
    "Gerente Punto", "Ejecutivo Comercial",
    "Vendedor 1", "Vendedor 2", "Vendedor 3",
    "Cajera", "Bodega 1", "Bodega 2", "Conductor",
]

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
# HELPERS DE NEGOCIO
# ============================================================
def _pdate(v):
    if v is None or (isinstance(v, float) and pd.isna(v)) or str(v).strip() == "":
        return None
    d = pd.to_datetime(v, errors="coerce")
    return None if pd.isna(d) else d.date()

def _cedula_ok(c):
    import re
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
        raise ValueError(f"Solape con {r[1]}/{r[2]} ({r[3]} -> {r[4]}).")
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

def _upsert_colaborador(con, d):
    res = con.execute(text("""
        INSERT INTO rrhh.colaboradores
            (cedula, nombre, slp_code, owner_code, usuario_portal, fecha_ingreso,
             periodo_prueba_meses, activo, almacen_actual, cargo_actual,
             en_prueba, prueba_inicio, prueba_fin, fecha_inactividad)
        VALUES (:ced,:nom,:slp,:own,:por,:ing,2,:act,:alm,:car,:pr,:pi,:pf,:fi)
        ON CONFLICT (cedula) DO UPDATE SET
            nombre = EXCLUDED.nombre, slp_code = EXCLUDED.slp_code,
            owner_code = EXCLUDED.owner_code, usuario_portal = EXCLUDED.usuario_portal,
            fecha_ingreso = EXCLUDED.fecha_ingreso, activo = EXCLUDED.activo,
            almacen_actual = EXCLUDED.almacen_actual, cargo_actual = EXCLUDED.cargo_actual,
            en_prueba = EXCLUDED.en_prueba, prueba_inicio = EXCLUDED.prueba_inicio,
            prueba_fin = EXCLUDED.prueba_fin, fecha_inactividad = EXCLUDED.fecha_inactividad
        RETURNING id
    """), d)
    return res.fetchone()[0]

# ============================================================
# PLANTILLA EXCEL (solo columnas de creación)
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
tab_res, tab_new, tab_edit, tab_dist, tab_hist = st.tabs(
    ["Resumen", "Crear y carga masiva", "Editar / Movimientos",
     "Distribución por Almacén", "Historial"])

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
        # --- Datos fuera del form para renderizado dinámico del periodo de prueba ---
        c1, c2, c3 = st.columns(3)
        ced = c1.text_input("Cédula *", key="new_ced")
        nom = c2.text_input("Nombre completo *", key="new_nom")
        f_ing = c3.date_input("Fecha de ingreso *", value=None, key="new_fing")
        c1, c2, c3 = st.columns(3)
        alm = c1.selectbox("Almacén *", ALMACENES, format_func=lambda x: f"{x[0]} - {x[1]}",
                           index=None, key="new_alm")
        car = c2.selectbox("Cargo *", CARGOS, index=None, key="new_car")
        prb = c3.radio("Periodo de prueba", ["NO", "SI"], horizontal=True, key="new_prb")

        # Periodo de prueba: se renderiza dinámicamente al elegir SI
        p_ini = p_fin = None
        if prb == "SI":
            c1, c2 = st.columns(2)
            p_ini = c1.date_input("Inicio periodo de prueba",
                                  value=f_ing, key="new_pi")
            p_fin = c2.date_input("Fin periodo de prueba",
                                  value=(f_ing + timedelta(days=60)) if f_ing else None,
                                  key="new_pf")

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
                if errs:
                    st.error("Corrige:\n" + "\n".join(errs))
                else:
                    try:
                        with conn.session as con:
                            cid = _upsert_colaborador(con, {
                                "ced": ced.strip(), "nom": nom.strip(),
                                "slp": None, "own": None, "por": None, "ing": f_ing,
                                "act": True, "alm": alm[0], "car": car,
                                "pr": prb == "SI", "pi": p_ini, "pf": p_fin, "fi": None})
                            if not con.execute(text(
                                "SELECT 1 FROM rrhh.asignaciones WHERE colaborador_id=:c LIMIT 1"),
                                    {"c": cid}).fetchone():
                                _nueva_asignacion(con, cid, alm[0], car, f_ing)
                            con.commit()
                        auditar_rrhh("colaboradores", cid, "CREAR", None,
                                     {"cedula": ced.strip(), "nombre": nom.strip()})
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
                        cid = _upsert_colaborador(con, {
                            "ced": ced, "nom": nom, "slp": None, "own": None, "por": None,
                            "ing": f_ing, "act": True, "alm": alm_c, "car": car_v,
                            "pr": pr, "pi": pi, "pf": pf, "fi": None})
                        if not con.execute(text(
                            "SELECT 1 FROM rrhh.asignaciones WHERE colaborador_id=:c LIMIT 1"),
                                {"c": cid}).fetchone():
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

            # --- DATOS BÁSICOS ---
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
                                con.execute(text("""
                                    UPDATE rrhh.colaboradores SET nombre=:nom, cedula=:ced
                                    WHERE id=:c
                                """), {"nom": n_nom.strip(), "ced": n_ced.strip(), "c": cid})
                                con.commit()
                            auditar_rrhh("colaboradores", cid, "UPDATE_DATOS", antes,
                                         {"nombre": n_nom.strip(), "cedula": n_ced.strip()})
                            st.success("Datos básicos guardados.")
                            st.rerun()

            # --- ASIGNACIÓN VIGENTE (corregir o crear inicial) ---
            with st.expander("Asignación de puesto (almacén/cargo)"):
                if est["asig_id"] is None:
                    st.warning("Este colaborador no tiene asignación vigente. Puedes crearla ahora (caso típico: se creó sin almacén por error).")
                    with st.form("form_creacion_asig"):
                        a1, a2, a3 = st.columns(3)
                        ca = a1.selectbox("Almacén", ALMACENES,
                                          format_func=lambda x: f"{x[0]} - {x[1]}", index=None)
                        cc = a2.selectbox("Cargo", CARGOS, index=None)
                        cd = a3.date_input("Fecha desde", value=est["fecha_ingreso"])
                        if st.form_submit_button("Crear asignación inicial"):
                            if ca is None or cc is None or cd is None:
                                st.error("Completa los tres campos.")
                            else:
                                try:
                                    with conn.session as con:
                                        _nueva_asignacion(con, cid, ca[0], cc, cd)
                                        con.execute(text("""
                                            UPDATE rrhh.colaboradores SET almacen_actual=:a, cargo_actual=:g
                                            WHERE id=:c
                                        """), {"a": ca[0], "g": cc, "c": cid})
                                        con.commit()
                                    auditar_rrhh("asignaciones", cid, "CREAR_ASIGNACION_INICIAL", None,
                                                 {"almacen": ca[0], "cargo": cc, "desde": str(cd)})
                                    st.success("Asignación inicial creada.")
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"{e}")
                else:
                    st.caption("Corrección: reescribe almacén/cargo de la asignación vigente sin crear historial de movimientos. El cambio queda auditado.")
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
                                auditar_rrhh("asignaciones", est["asig_id"], "CORRECCION_ASIGNACION",
                                             antes, {"almacen": ca[0], "cargo": cc})
                                st.success("Asignación vigente corregida.")
                                st.rerun()

            # --- TRASLADO ---
            with st.expander("Traslado de punto de venta (movimiento real)"):
                with st.form("form_traslado"):
                    t1, t2, t3c = st.columns(3)
                    t1.markdown(f"Origen actual: **{est['almacen'] or '—'}**")
                    td = t2.selectbox("Almacén destino", ALMACENES,
                                      format_func=lambda x: f"{x[0]} - {x[1]}", index=None)
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

            # --- CAMBIO DE CARGO ---
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

            # --- PERIODO DE PRUEBA (separado de inactivación) ---
            with st.expander("Periodo de prueba"):
                with st.form("form_prueba"):
                    q1, q2 = st.columns(2)
                    n_pr = q1.radio("En prueba", ["NO", "SI"],
                                    index=1 if est["en_prueba"] else 0, horizontal=True) == "SI"
                    n_pi = q1.date_input("Inicio prueba", value=est["prueba_inicio"])
                    n_pf = q2.date_input("Fin prueba", value=est["prueba_fin"])
                    if st.form_submit_button("Guardar periodo de prueba"):
                        errs = []
                        if n_pr and (n_pi is None or n_pf is None or n_pf <= n_pi):
                            errs.append("Fechas de prueba inválidas.")
                        if errs:
                            st.error("\n".join(errs))
                        else:
                            with conn.session as con:
                                con.execute(text("""
                                    UPDATE rrhh.colaboradores SET en_prueba=:pr, prueba_inicio=:pi, prueba_fin=:pf
                                    WHERE id=:c
                                """), {"pr": n_pr, "pi": n_pi, "pf": n_pf, "c": cid})
                                con.commit()
                            auditar_rrhh("colaboradores", cid, "UPDATE_PRUEBA",
                                         {"en_prueba": bool(est["en_prueba"])},
                                         {"en_prueba": n_pr, "inicio": str(n_pi), "fin": str(n_pf)})
                            st.success("Periodo de prueba guardado.")
                            st.rerun()

            # --- INACTIVAR / REACTIVAR (expander separado) ---
            with st.expander("Estado del colaborador (activo / inactivo)"):
                with st.form("form_estado"):
                    if est["activo"]:
                        n_ina = st.checkbox("Inactivar colaborador")
                        n_fi = st.date_input("Fecha de inactividad", value=None, disabled=not n_ina)
                        if st.form_submit_button("Aplicar cambio de estado"):
                            if n_ina and n_fi is None:
                                st.error("Indica la fecha de inactividad.")
                            elif not n_ina:
                                st.info("No se hicieron cambios.")
                            else:
                                with conn.session as con:
                                    con.execute(text("""
                                        UPDATE rrhh.colaboradores SET activo=FALSE, fecha_inactividad=:fi WHERE id=:c
                                    """), {"fi": n_fi, "c": cid})
                                    con.commit()
                                auditar_rrhh("colaboradores", cid, "INACTIVAR",
                                             {"activo": True},
                                             {"activo": False, "fecha_inactividad": str(n_fi)})
                                st.success("Colaborador inactivado.")
                                st.rerun()
                    else:
                        n_rea = st.checkbox("Reactivar colaborador")
                        n_fr = st.date_input("Fecha de reingreso", value=None, disabled=not n_rea)
                        if st.form_submit_button("Aplicar cambio de estado"):
                            if n_rea and n_fr is None:
                                st.error("Indica la fecha de reingreso.")
                            elif not n_rea:
                                st.info("No se hicieron cambios.")
                            else:
                                with conn.session as con:
                                    con.execute(text("""
                                        UPDATE rrhh.colaboradores SET activo=TRUE, fecha_inactividad=NULL WHERE id=:c
                                    """), {"c": cid})
                                    con.commit()
                                auditar_rrhh("colaboradores", cid, "REACTIVAR",
                                             {"activo": False},
                                             {"activo": True, "fecha_reingreso": str(n_fr)})
                                st.success("Colaborador reactivado.")
                                st.rerun()

# ------------------------------------------------------------
# DISTRIBUCIÓN POR ALMACÉN
# ------------------------------------------------------------
with tab_dist:
    st.caption("Regla de oro: por almacén y vigencia, la suma de % debe dar 100%.")
    alm = conn.query("SELECT codigo, nombre_serie FROM app.almacenes WHERE activo=TRUE ORDER BY codigo", ttl=0)
    if alm.empty:
        st.warning("app.almacenes vacía.")
    else:
        map_alm = dict(zip(alm["codigo"], alm["nombre_serie"].fillna(alm["codigo"])))
        sel_alm = st.selectbox("Almacén", list(map_alm.keys()), format_func=lambda k: f"{k} - {map_alm[k]}")
        df_d = conn.query("""
            SELECT id, almacen, cargo, pct, vigente_desde, vigente_hasta
            FROM rrhh.distribucion_puestos WHERE almacen = :a
            ORDER BY vigente_desde DESC, cargo
        """, params={"a": sel_alm}, ttl=0)
        if not df_d.empty:
            for vig, tot in df_d.groupby("vigente_desde")["pct"].sum().items():
                st.markdown(f"{'OK' if abs(float(tot)-100) < 0.001 else 'FALTA'} - Vigencia {vig}: {float(tot):.3f}%")
            st.dataframe(df_d, use_container_width=True, hide_index=True)
        with st.form("form_puesto"):
            p1, p2, p3, p4 = st.columns(4)
            cargo = p1.selectbox("Cargo *", CARGOS, index=None)
            pct = p2.number_input("% del bono *", 0.0, 100.0, step=0.5)
            v_desde = p3.date_input("Vigente desde *", value=None)
            v_hasta = p4.date_input("Vigente hasta", value=None)
            if st.form_submit_button("Guardar puesto"):
                if cargo is None or pct <= 0 or v_desde is None:
                    st.error("Cargo, % y vigencia son obligatorios.")
                else:
                    with conn.session as con:
                        con.execute(text("""
                            INSERT INTO rrhh.distribucion_puestos (almacen, cargo, pct, vigente_desde, vigente_hasta)
                            VALUES (:a,:c,:p,:vd,:vh)
                            ON CONFLICT (almacen, cargo, vigente_desde)
                            DO UPDATE SET pct = EXCLUDED.pct, vigente_hasta = EXCLUDED.vigente_hasta
                        """), {"a": sel_alm, "c": cargo, "p": float(pct), "vd": v_desde, "vh": v_hasta})
                        con.commit()
                    auditar_rrhh("distribucion_puestos", sel_alm, "UPSERT", None,
                                 {"cargo": cargo, "pct": float(pct)})
                    st.success("Puesto guardado.")
                    st.rerun()

# ------------------------------------------------------------
# HISTORIAL (append-only: nunca se sobrescribe)
# ------------------------------------------------------------
with tab_hist:
    st.caption("Cada cambio queda como una fila nueva. Si un colaborador se corrige 5 veces, verás 5 filas distintas con su 'antes' y 'después'.")
    df_as = conn.query("""
        SELECT a.id, c.nombre, c.cedula, a.almacen, a.cargo, a.fecha_desde,
               COALESCE(a.fecha_hasta::TEXT, 'Vigente') AS fecha_hasta
        FROM rrhh.asignaciones a JOIN rrhh.colaboradores c ON c.id = a.colaborador_id
        ORDER BY a.fecha_desde DESC LIMIT 300
    """, ttl=0)
    st.dataframe(df_as, use_container_width=True, hide_index=True)
    with st.expander("Auditoría de cambios"):
        df_h = conn.query("""
            SELECT id, fecha, usuario, accion, tabla, registro_id, antes, despues
            FROM rrhh.rrhh_auditoria
            WHERE tabla IN ('colaboradores','asignaciones','distribucion_puestos')
            ORDER BY fecha DESC LIMIT 300
        """, ttl=0)
        st.dataframe(df_h.drop(columns=["antes", "despues"]), use_container_width=True, hide_index=True)
        with st.expander("Ver antes / después de un cambio"):
            if not df_h.empty:
                rid = st.selectbox("ID del registro", df_h["id"].tolist())
                fila = df_h[df_h["id"] == rid].iloc[0]
                c1, c2 = st.columns(2)
                c1.json(fila["antes"] if fila["antes"] is not None else {})
                c2.json(fila["despues"] if fila["despues"] is not None else {})