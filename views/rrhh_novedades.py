# ============================================================
# TALENTO HUMANO — Novedades y catálogo de faltas
# Acceso: admin, gerente (todo) y talento_humano (lo suyo).
# ============================================================
import streamlit as st
import pandas as pd
from sqlalchemy import text
from src.conexion_db import conn
from src.rrhh import ROLES_RRHH_CARGUE, auditar_rrhh

st.set_page_config(page_title="Talento Humano", layout="wide")
st.title("Talento Humano — Novedades")
st.markdown("---")

rol = st.session_state.get("rol_actual")
if rol not in ROLES_RRHH_CARGUE:
    st.error("⛔ Acceso denegado. Solo Talento Humano, Gerencia y Administración.")
    st.stop()

es_editable = True
tab_faltas, tab_nov = st.tabs(["📋 Catálogo de Faltas", "🗒️ Novedades del Personal"])

# ---------- PESTAÑA 1: CATÁLOGO TIPOS DE FALTA ----------
with tab_faltas:
    st.subheader("Tipos de falta (grados)")
    st.caption("El porcentaje de descuento de cada tipo se define en Variables del Bono"
               "por período/vigencia. Aquí solo se gestiona el catálogo.")

    df_tf = conn.query("SELECT id, nombre, activo FROM rrhh.tipos_falta ORDER BY id", ttl=0)
    st.dataframe(df_tf, use_container_width=True, hide_index=True)

    with st.form("alta_tipo_falta"):
        nuevo_tipo = st.text_input("Nuevo tipo de falta (ej: Falta Tipo 4)")
        if st.form_submit_button("➕ Agregar tipo", disabled=not es_editable):
            if not nuevo_tipo.strip():
                st.error("El nombre no puede estar vacío.")
            else:
                with conn.session as con:
                    res = con.execute(text("""
                        INSERT INTO rrhh.tipos_falta (nombre)
                        VALUES (:n) ON CONFLICT (nombre) DO NOTHING
                        RETURNING id
                    """), {"n": nuevo_tipo.strip()})
                    rid = res.fetchone()
                    con.commit()
                if rid:
                    auditar_rrhh("tipos_falta", rid[0], "CREATE", None, {"nombre": nuevo_tipo.strip()})
                    st.success(f"✅ Tipo '{nuevo_tipo.strip()}' creado.")
                    st.rerun()
                else:
                    st.warning("Ese tipo ya existe.")

# ---------- PESTAÑA 2: NOVEDADES ----------
with tab_nov:
    st.subheader("Registrar novedad")
    cols = conn.query("SELECT id, cedula, nombre FROM rrhh.colaboradores WHERE activo=TRUE ORDER BY nombre", ttl=0)
    
    if cols.empty:
        st.info("Aún no hay colaboradores registrados. El maestro de colaboradores "
                "(con carga manual y Excel) entra en la Fase 2.")
    else:
        opciones = {f"{c['nombre']} — CC {c['cedula']}": c["id"] for _, c in cols.iterrows()}
        tipos_falta = conn.query("SELECT id, nombre FROM rrhh.tipos_falta WHERE activo=TRUE ORDER BY id", ttl=0)
        map_tf = {r["nombre"]: r["id"] for _, r in tipos_falta.iterrows()}

        with st.form("form_novedad"):
            c1, c2 = st.columns(2)
            with c1:
                sel_col = st.selectbox("Colaborador", list(opciones.keys()))
                sel_tipo = st.selectbox("Tipo de novedad", ["FALTA", "INCAPACIDAD", "MEMORANDO", "VACACIONES"])
            with c2:
                sel_tf = st.selectbox("Grado de falta", list(map_tf.keys())) if sel_tipo == "FALTA" else None
                f_ini = st.date_input("Fecha inicio")
                f_fin = st.date_input("Fecha fin")
            soporte = st.text_input("Soporte (radicado/enlace del documento)", "")
            submitted = st.form_submit_button("💾 Guardar novedad", disabled=not es_editable)

        if submitted:
            if f_fin < f_ini:
                st.error("La fecha fin no puede ser anterior a la inicio.")
            elif sel_tipo == "VACACIONES" and (f_fin - f_ini).days > 60:
                st.error("¿Más de 60 días de vacaciones? Revisa las fechas.")
            else:
                with conn.session as con:
                    res = con.execute(text("""
                        INSERT INTO rrhh.novedades
                            (colaborador_id, tipo, tipo_falta_id, fecha_inicio,
                             fecha_fin, soporte, cargado_por)
                        VALUES (:col, :tipo, :tf, :fi, :ff, :so, :usr)
                        RETURNING id
                    """), {
                        "col": opciones[sel_col], "tipo": sel_tipo,
                        "tf": map_tf[sel_tf] if sel_tf else None,
                        "fi": f_ini, "ff": f_fin, "so": soporte or None,
                        "usr": st.session_state.get("usuario_actual"),
                    })
                    nid = res.fetchone()[0]
                    con.commit()
                auditar_rrhh("novedades", nid, "CREATE", None, {
                    "colaborador": sel_col, "tipo": sel_tipo, "falta": sel_tf,
                    "desde": str(f_ini), "hasta": str(f_fin)})
                st.success(f"✅ Novedad #{nid} registrada.")
                st.rerun()

    st.markdown("---")
    st.subheader("Novedades registradas")
    df_nov = conn.query("""
        SELECT n.id, c.nombre, c.cedula, n.tipo,
               COALESCE(tf.nombre, '-') AS grado_falta,
               n.fecha_inicio, n.fecha_fin, n.soporte, n.cargado_por
        FROM rrhh.novedades n
        JOIN rrhh.colaboradores c ON c.id = n.colaborador_id
        LEFT JOIN rrhh.tipos_falta tf ON tf.id = n.tipo_falta_id
        ORDER BY n.fecha_inicio DESC LIMIT 200
    """, ttl=0)
    
    if not df_nov.empty:
        def _domingos(r):
            import datetime
            d, dom = r["fecha_inicio"], 0
            while d <= r["fecha_fin"]:
                if d.weekday() == 6:
                    dom += 1
                d += datetime.timedelta(days=1)
            return dom
        
        df_nov = df_nov.assign(dias_totales=(pd.to_datetime(df_nov.fecha_fin) - pd.to_datetime(df_nov.fecha_inicio)).dt.days + 1,
                               domingos=df_nov.apply(_domingos, axis=1))
        st.dataframe(df_nov, use_container_width=True, hide_index=True)
        st.download_button("📥 Descargar novedades (CSV)",
                           df_nov.to_csv(index=False).encode(),
                           file_name="novedades_rrhh.csv", mime="text/csv")
    else:
        st.info("Sin novedades registradas todavía.")