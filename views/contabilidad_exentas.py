import streamlit as st
import pandas as pd
import numpy as np
from src.conexion_db import (
    conn,
    cargar_exentas_por_almacen,
    cargar_exentas_por_comercial_ejecom,
    guardar_decision_contabilidad,
    obtener_decision_contabilidad,
    obtener_decisiones_por_periodo,
    resolver_periodo_bono,
)

# ============================================================
# CONFIGURACIÓN Y ACCESO
# ============================================================
st.set_page_config(page_title="Auditoría Exentas", page_icon=":material/account_balance:",
                   layout="wide", initial_sidebar_state="expanded")

ROLES_CONTABILIDAD = ["admin", "gerente", "contabilidad"]
if st.session_state.get("rol_actual") not in ROLES_CONTABILIDAD:
    st.error("Acceso denegado. Este módulo es exclusivo para Contabilidad, Gerencia y Administrador.")
    st.stop()

st.title("Auditoría de Descuentos por Exentas")
st.caption("Contabilidad audita y valida el valor de ventas exentas por punto de venta y ejecutivo comercial.")

# ============================================================
# SIDEBAR: MES A AUDITAR
# ============================================================
with st.sidebar:
    st.markdown("### Período a auditar")
    q_meses = """
        SELECT DISTINCT TO_CHAR(fecha_contabilizacion, 'YYYY-MM') AS mes
        FROM sap_raw.ventas_netas ORDER BY mes DESC
    """
    df_meses = conn.query(q_meses)
    if df_meses.empty:
        st.warning("No hay datos de ventas. Ejecuta Forzar Sincronización SAP.")
        st.stop()
    mes_sel = st.selectbox("Mes a auditar:", df_meses["mes"].tolist(), index=0, key="sb_cont_mes")
    st.info(f"Auditando período: {mes_sel}")

periodo_general, _, _ = resolver_periodo_bono(f"{mes_sel}-01")
if periodo_general is None:
    st.error("No hay período configurado para este mes.")
    st.stop()
factor_desc_exentas = float(periodo_general["factor_desc_exentas"])

# ============================================================
# HELPER: estado + valor corregido de una decisión
# ============================================================
def leer_decision(pm, almacen, comercial=""):
    df = conn.query("""
        SELECT decision, valor_corregido FROM app.contabilidad_decisiones_exentas
        WHERE periodo_mes=:pm AND nombre_almacen=:na AND nombre_comercial=:nc
        ORDER BY fecha_decision DESC LIMIT 1
    """, params={"pm": pm, "na": almacen, "nc": comercial})
    if df.empty:
        return "Pendiente", None
    d = df.iloc[0]
    if d["decision"] == "aprobado":
        return "Aprobado", None
    if d["decision"] == "corregido":
        return "Corregido", (float(d["valor_corregido"]) if d["valor_corregido"] is not None else None)
    return "Pendiente", None

# ============================================================
# CARGA Y CÁLCULO
# ============================================================
df_exentas = cargar_exentas_por_almacen(mes_sel)
df_ejecom = cargar_exentas_por_comercial_ejecom(mes_sel)
if df_exentas.empty:
    st.warning("No hay datos de ventas para el período seleccionado.")
    st.stop()

for df in (df_exentas, df_ejecom):
    df["pct_exentas"] = np.where(df["pvp_total"] != 0, df["total_exentas"] / df["pvp_total"], 0.0)

usuario = st.session_state.get("usuario_actual", "desconocido")
ESTADOS = ["Pendiente", "Aprobado", "Corregido"]

# ============================================================
# BLOQUE REUTILIZABLE: resumen + editor + guardado
# ============================================================
def pintar_bloque(df, tipo, clave_id, clave_nombre, titulo_tabla, titulo_editor, es_ejecutivo=False):
    # Estado y valor corregido actuales + Valor Final
    estados, corregidos, finales = [], [], []
    for _, r in df.iterrows():
        nom = r[clave_nombre]
        est, val = leer_decision(mes_sel, "EJECUTIVOS COMERCIALES" if es_ejecutivo else nom,
                                 nom if es_ejecutivo else "")
        estados.append(est)
        corregidos.append(val)
        finales.append(val if (est == "Corregido" and val is not None) else float(r["total_exentas"]))
    df = df.copy()
    df["estado"] = estados
    df["valor_corregido"] = corregidos
    df["valor_final"] = finales

    # --- Tabla resumen (solo lectura) ---
    st.markdown("---")
    st.subheader(titulo_tabla)
    cols = [clave_nombre, "pvp_total", "total_exentas", "pct_exentas", "estado", "valor_final"]
    st.dataframe(
        df[cols].rename(columns={
            clave_nombre: "Nombre", "pvp_total": "PVP Total", "total_exentas": "Total Exentas ($)",
            "pct_exentas": "% Exentas", "estado": "Estado", "valor_final": "Valor Final ($)",
        }),
        use_container_width=True, hide_index=True,
        column_config={
            "Nombre": st.column_config.TextColumn(width="medium"),
            "PVP Total": st.column_config.NumberColumn(format="$%,.2f"),
            "Total Exentas ($)": st.column_config.NumberColumn(format="$%,.2f"),
            "% Exentas": st.column_config.NumberColumn(format="%.2f%%"),
            "Estado": st.column_config.TextColumn(width="small"),
            "Valor Final ($)": st.column_config.NumberColumn(format="$%,.2f"),
        },
    )

    # --- Editor por fila (fuera de st.form → el bloqueo reacciona al instante) ---
    st.subheader(titulo_editor)
    edits = {}
    for _, r in df.iterrows():
        nom = r[clave_nombre]
        c1, c2, c3, c4 = st.columns([3, 2, 2, 2])
        with c1:
            st.write(f"**{nom}**")
            st.caption(f"PVP ${r['pvp_total']:,.0f} · Exentas ${r['total_exentas']:,.0f}")
        with c2:
            estado = st.selectbox("Estado", ESTADOS, index=ESTADOS.index(r["estado"]),
                                  key=f"est_{tipo}_{clave_id}_{nom}")
        with c3:
            valor_corr = st.number_input(
                "Valor Corregido ($)", min_value=0.0,
                value=float(r["valor_corregido"]) if r["valor_corregido"] is not None else float(r["total_exentas"]),
                disabled=(estado != "Corregido"),   # 🔒 solo editable en Corregido
                key=f"val_{tipo}_{clave_id}_{nom}",
            )
        with c4:
            st.write(f"Valor Final: **${r['valor_final']:,.0f}**")
        edits[nom] = {"estado": estado, "valor": valor_corr,
                      "estado_prev": r["estado"], "valor_prev": r["valor_corregido"]}

    if st.button(f"Guardar Decisiones ({titulo_editor})", type="primary", key=f"btn_{tipo}"):
        guardadas = 0
        for nom, e in edits.items():
            nuevo_val = e["valor"] if e["estado"] == "Corregido" else None
            if e["estado"] == "Corregido" and (nuevo_val is None or nuevo_val == 0):
                st.warning(f"{nom}: si el estado es 'Corregido' debes capturar un valor > 0.")
                continue
            if e["estado"] != e["estado_prev"] or (e["estado"] == "Corregido" and nuevo_val != e["valor_prev"]):
                guardar_decision_contabilidad(
                    periodo_mes=mes_sel, tipo_registro=tipo,
                    nombre_almacen="EJECUTIVOS COMERCIALES" if es_ejecutivo else nom,
                    nombre_comercial=nom if es_ejecutivo else "",
                    documento_comercial=str(r_doc.get(nom, "")) if es_ejecutivo else "",
                    valor_sistema=float(df.loc[df[clave_nombre] == nom, "total_exentas"].iloc[0]),
                    valor_corregido=nuevo_val, decision=e["estado"].lower(), usuario=usuario,
                )
                guardadas += 1
        if guardadas:
            st.success(f"{guardadas} decisiones guardadas.")
            st.rerun()
        else:
            st.info("Sin cambios que guardar.")
    return df

# Mapeo de cédulas para ejecutivos (para el documento_comercial)
r_doc = {r["nombre_vendedor"]: r["documento_identidad"] for _, r in df_ejecom.iterrows()} if not df_ejecom.empty else {}

# --- Almacenes ---
df_alm = df_exentas[df_exentas["nombre_almacen"] != "EJECUTIVOS COMERCIALES"].copy()
pintar_bloque(df_alm, "almacen", "alm", "nombre_almacen",
              "Descuento por Almacén", "Registrar Decisiones - Almacenes", es_ejecutivo=False)

# --- Ejecutivos Comerciales ---
if df_ejecom.empty:
    st.markdown("---")
    st.info("No hay ventas registradas para Ejecutivos Comerciales en este período.")
else:
    pintar_bloque(df_ejecom, "comercial", "ej", "nombre_vendedor",
                  "Ejecutivos Comerciales - Auditoría Individual",
                  "Registrar Decisiones - Ejecutivos Comerciales", es_ejecutivo=True)

# ============================================================
# HISTORIAL
# ============================================================
st.markdown("---")
with st.expander("Historial de Decisiones del Período"):
    df_hist = obtener_decisiones_por_periodo(mes_sel)
    if df_hist.empty:
        st.info("No hay decisiones registradas para este período.")
    else:
        st.dataframe(
            df_hist[["tipo_registro", "nombre_almacen", "nombre_comercial", "valor_sistema",
                     "valor_corregido", "decision", "usuario_decision", "fecha_decision"]].rename(columns={
                "tipo_registro": "Tipo", "nombre_almacen": "Almacén", "nombre_comercial": "Comercial",
                "valor_sistema": "Valor Sistema ($)", "valor_corregido": "Valor Corregido ($)",
                "decision": "Decisión", "usuario_decision": "Usuario", "fecha_decision": "Fecha Decisión",
            }),
            use_container_width=True, hide_index=True,
            column_config={
                "Tipo": st.column_config.TextColumn(width="small"),
                "Almacén": st.column_config.TextColumn(width="medium"),
                "Comercial": st.column_config.TextColumn(width="medium"),
                "Valor Sistema ($)": st.column_config.NumberColumn(format="$%,.2f"),
                "Valor Corregido ($)": st.column_config.NumberColumn(format="$%,.2f"),
                "Decisión": st.column_config.TextColumn(width="small"),
                "Usuario": st.column_config.TextColumn(width="small"),
                "Fecha Decisión": st.column_config.DatetimeColumn(format="DD/MM/YYYY HH:mm"),
            },
        )