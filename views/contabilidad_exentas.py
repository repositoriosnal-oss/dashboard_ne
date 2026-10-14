"""
CONTABILIDAD — Auditoría de descuentos por exentas, por periodo.
Pestañas:
  Resumen del periodo  : acumulado del mes con corte de datos, estados y valores finales.
  Editar decisiones    : aprueba/corrige por almacén y por ejecutivo. Permite crear el primer
                         registro de CUALQUIER periodo, tenga o no ventas sincronizadas,
                         esté cerrado o en curso.
  Historial y auditoría: filtro por periodo (o todos), decisiones y movimientos con autor y rol.
"""
from datetime import date
import pandas as pd
import streamlit as st

from src import rrhh_dist as dist
from src.conexion_db import (
    conn,
    cargar_exentas_por_almacen,
    cargar_exentas_por_comercial_ejecom,
    guardar_decision_contabilidad,
    obtener_decisiones_por_periodo,
    resolver_periodo_bono,
)

st.set_page_config(page_title="Auditoría Exentas", page_icon=":material/account_balance:",
                   layout="wide", initial_sidebar_state="expanded")
ROLES_CONTABILIDAD = ["admin", "gerente", "contabilidad"]
if st.session_state.get("rol_actual") not in ROLES_CONTABILIDAD:
    st.error("Acceso denegado. Este módulo es exclusivo para Contabilidad, Gerencia y Administrador.")
    st.stop()

st.title("Auditoría de Descuentos por Exentas")
st.caption("Contabilidad audita y valida el valor de exentas por punto de venta y por ejecutivo "
           "comercial. Puedes aprobar o corregir cualquier periodo, cerrado o en curso.")
usuario = st.session_state.get("usuario_actual", "desconocido")
MES_ACTUAL = dist.inicio_mes(date.today())
ESTADOS = ["Pendiente", "Aprobado", "Corregido"]

# Periodos auditables: desde la primera venta hasta el mes actual (inclusive)
rng = conn.query("""
    SELECT MIN(fecha_contabilizacion)::date AS ini, MAX(fecha_contabilizacion)::date AS fin
    FROM sap_raw.ventas_netas
""")
if not rng.empty and rng.iloc[0]["ini"] is not None:
    ini = dist.inicio_mes(rng.iloc[0]["ini"])
    fin = max(dist.inicio_mes(rng.iloc[0]["fin"]), MES_ACTUAL)
    CORTE_GLOBAL = rng.iloc[0]["fin"]
else:
    ini = fin = MES_ACTUAL
    CORTE_GLOBAL = None
MESES = list(reversed(dist.lista_meses(ini, fin)))

# Catálogo de almacenes físicos (EJECOM se audita aparte, por ejecutivo)
ALM_CAT = conn.query("""
    SELECT codigo, COALESCE(nombre_serie, codigo) AS nombre
    FROM app.almacenes WHERE activo AND codigo <> 'EJECOM' ORDER BY codigo
""", ttl=0)


def leer_decision(pm, almacen, comercial=""):
    df = conn.query("""
        SELECT decision, valor_corregido FROM app.contabilidad_decisiones_exentas
        WHERE periodo_mes = :pm AND nombre_almacen = :na AND nombre_comercial = :nc
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


def acumulado(mes_str):
    """Acumulado del mes (lo que se lleva al corte) + fecha de corte."""
    df_ex = cargar_exentas_por_almacen(mes_str)
    mapa = {}
    if not df_ex.empty:
        for _, r in df_ex.iterrows():
            mapa[r["nombre_almacen"]] = (float(r["pvp_total"]), float(r["total_exentas"]))
    corte = conn.query(
        "SELECT MAX(fecha_contabilizacion)::date AS c FROM sap_raw.ventas_netas "
        "WHERE TO_CHAR(fecha_contabilizacion, 'YYYY-MM') = :m",
        params={"m": mes_str}, ttl=0).iloc[0]["c"]
    return df_ex, mapa, corte


def tabla_almacenes(mes_str, mapa):
    """Una fila por almacén del catálogo, con o sin ventas: permite el primer registro."""
    rows = []
    for _, a in ALM_CAT.iterrows():
        pvp, ex = mapa.get(a["nombre"], (0.0, 0.0))
        est, val = leer_decision(mes_str, a["nombre"], "")
        rows.append({"nombre": a["nombre"], "pvp_total": pvp, "total_exentas": ex,
                     "pct_exentas": (ex / pvp) if pvp else 0.0,
                     "estado": est, "valor_corregido": val,
                     "valor_final": val if (est == "Corregido" and val is not None) else ex})
    return pd.DataFrame(rows)


def tabla_ejecutivos(mes_str, df_ej):
    rows = []
    for _, r in df_ej.iterrows():
        est, val = leer_decision(mes_str, "EJECUTIVOS COMERCIALES", r["nombre_vendedor"])
        pvp, ex = float(r["pvp_total"]), float(r["total_exentas"])
        rows.append({"nombre": r["nombre_vendedor"], "cedula": r["documento_identidad"],
                     "pvp_total": pvp, "total_exentas": ex,
                     "pct_exentas": (ex / pvp) if pvp else 0.0,
                     "estado": est, "valor_corregido": val,
                     "valor_final": val if (est == "Corregido" and val is not None) else ex})
    return pd.DataFrame(rows)


CFG_NUM = {
    "PVP Total": st.column_config.NumberColumn(format="$%,.2f"),
    "Total Exentas ($)": st.column_config.NumberColumn(format="$%,.2f"),
    "% Exentas": st.column_config.NumberColumn(format="%.2f%%"),
    "Estado": st.column_config.TextColumn(width="small"),
    "Valor Final ($)": st.column_config.NumberColumn(format="$%,.2f"),
}

tab_res, tab_edit, tab_hist = st.tabs(
    ["Resumen del periodo", "Editar decisiones", "Historial y auditoría"])

# ------------------------------------------------------------
# RESUMEN DEL PERIODO (acumulado con corte de datos)
# ------------------------------------------------------------
with tab_res:
    mes = st.selectbox("Periodo a auditar", MESES, index=0,
                       format_func=dist.etiqueta_mes, key="res_mes")
    mes_str = f"{mes:%Y-%m}"
    df_ex, mapa, corte = acumulado(mes_str)
    df_ej = cargar_exentas_por_comercial_ejecom(mes_str)
    per, _, _ = resolver_periodo_bono(f"{mes_str}-01")
    factor = float(per["factor_desc_exentas"]) if per is not None else None

    if corte is None:
        st.info(f"Sin ventas sincronizadas en {dist.etiqueta_mes(mes)}: el acumulado del mes es 0. "
                "Si el mes está en curso o ya cerró, extiende la ventana de sincronización "
                "(FECHA_FIN_AUDITORIA en conexion_db.py) y ejecuta Forzar Sincronización SAP.")
    else:
        txt = f"Corte del acumulado: **{corte}**."
        if CORTE_GLOBAL is not None and corte < dist.fin_mes(mes) and mes >= MES_ACTUAL:
            txt += " Mes en curso: estás viendo lo que se lleva del mes (auditoría parcial)."
        st.caption(txt)
    if factor is not None:
        st.caption(f"Factor DESC exentas del periodo: {factor * 100:.0f}%")

    df_alm = tabla_almacenes(mes_str, mapa)
    df_ejc = tabla_ejecutivos(mes_str, df_ej) if not df_ej.empty else pd.DataFrame()

    dec = pd.concat([df_alm["estado"], df_ejc["estado"] if not df_ejc.empty else pd.Series(dtype=object)])
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Almacenes con exentas", int((df_alm["total_exentas"] > 0).sum()))
    k2.metric("Exentas acumuladas", f"${df_alm['total_exentas'].sum():,.0f}")
    k3.metric("Pendientes", int((dec == "Pendiente").sum()))
    k4.metric("Aprobadas", int((dec == "Aprobado").sum()))
    k5.metric("Corregidas", int((dec == "Corregido").sum()))

    st.markdown("##### Descuento por almacén")
    st.dataframe(
        df_alm[["nombre", "pvp_total", "total_exentas", "pct_exentas", "estado", "valor_final"]]
        .rename(columns={"nombre": "Almacén", "pvp_total": "PVP Total",
                         "total_exentas": "Total Exentas ($)", "pct_exentas": "% Exentas",
                         "estado": "Estado", "valor_final": "Valor Final ($)"}),
        use_container_width=True, hide_index=True, column_config=CFG_NUM)

    st.markdown("##### Ejecutivos comerciales (evaluación individual)")
    if df_ejc.empty:
        st.info("Sin ejecutivos comerciales con ventas en el periodo.")
    else:
        st.dataframe(
            df_ejc[["nombre", "cedula", "pvp_total", "total_exentas", "pct_exentas", "estado", "valor_final"]]
            .rename(columns={"nombre": "Comercial", "cedula": "Cédula", "pvp_total": "PVP Total",
                             "total_exentas": "Total Exentas ($)", "pct_exentas": "% Exentas",
                             "estado": "Estado", "valor_final": "Valor Final ($)"}),
            use_container_width=True, hide_index=True,
            column_config={"Cédula": st.column_config.TextColumn(width="small"), **CFG_NUM})
    st.download_button("Descargar resumen (CSV)",
                       pd.concat([df_alm, df_ejc]).to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"exentas_{mes_str}.csv", mime="text/csv")

# ------------------------------------------------------------
# EDITAR DECISIONES (primer registro posible en cualquier periodo)
# ------------------------------------------------------------
with tab_edit:
    mes_e = st.selectbox("Periodo a editar", MESES, index=0,
                         format_func=dist.etiqueta_mes, key="edit_mes")
    mes_e_str = f"{mes_e:%Y-%m}"
    st.caption("Puedes aprobar o corregir cualquier periodo, cerrado o en curso. Si el periodo aún "
               "no tiene ventas sincronizadas, el valor del sistema es 0 y tu decisión queda como "
               "primer registro del periodo.")

    def pintar_editor(df, tipo, clave, titulo, mes_str, es_ejecutivo=False):
        st.subheader(titulo)
        if df.empty:
            st.info("Sin datos en este periodo.")
            return
        edits = {}
        for _, r in df.iterrows():
            nom = r[clave]
            c1, c2, c3, c4 = st.columns([3, 2, 2, 2])
            with c1:
                st.write(f"**{nom}**")
                extra = f" · CC {r['cedula']}" if es_ejecutivo else ""
                st.caption(f"PVP ${r['pvp_total']:,.0f} · Exentas ${r['total_exentas']:,.0f}{extra}")
            with c2:
                estado = st.selectbox("Estado", ESTADOS, index=ESTADOS.index(r["estado"]),
                                      key=f"est_{tipo}_{mes_str}_{nom}")
            with c3:
                valor_corr = st.number_input(
                    "Valor corregido ($)",
                    value=float(r["valor_corregido"]) if r["valor_corregido"] is not None
                    else float(r["total_exentas"]),
                    step=1000.0,
                    disabled=(estado != "Corregido"),
                    key=f"val_{tipo}_{mes_str}_{nom}",
                    help="Admite valores negativos: si las notas crédito del mes superan "
                         "las facturas exentas, el neto queda negativo y Contabilidad "
                         "puede aprobarlo así o corregirlo.")
            with c4:
                st.write(f"Valor final: **${r['valor_final']:,.0f}**")
            edits[nom] = {"estado": estado, "valor": valor_corr,
                          "estado_prev": r["estado"], "valor_prev": r["valor_corregido"],
                          "total_exentas": float(r["total_exentas"]),
                          "cedula": r.get("cedula")}
        if st.button(f"Guardar decisiones ({titulo})", type="primary", key=f"btn_{tipo}_{mes_str}"):
            guardadas, errs = 0, []
            for nom, e in edits.items():
                nuevo_val = e["valor"] if e["estado"] == "Corregido" else None
                if e["estado"] == "Corregido" and nuevo_val is None:
                    errs.append(f"{nom}: el estado Corregido requiere un valor.")
                    continue
                if e["estado"] != e["estado_prev"] or \
                   (e["estado"] == "Corregido" and nuevo_val != e["valor_prev"]):
                    try:
                        guardar_decision_contabilidad(
                            periodo_mes=mes_str, tipo_registro=tipo,
                            nombre_almacen="EJECUTIVOS COMERCIALES" if es_ejecutivo else nom,
                            nombre_comercial=nom if es_ejecutivo else "",
                            documento_comercial=str(e["cedula"] or "") if es_ejecutivo else "",
                            valor_sistema=e["total_exentas"],
                            valor_corregido=nuevo_val,
                            decision=e["estado"].lower(), usuario=usuario)
                        guardadas += 1
                    except Exception as ex:
                        errs.append(f"{nom}: {ex}")
            if errs:
                st.error("Estos cambios NO se guardaron:\n" + "\n".join(errs))
            if guardadas:
                st.success(f"{guardadas} decisión(es) guardadas y auditadas.")
                st.rerun()
            elif not errs:
                st.info("Sin cambios que guardar.")

    if ALM_CAT.empty:
        st.warning("El catálogo de almacenes está vacío: ejecuta el bootstrap de RRHH o "
                   "seed_almacenes_desde_maps().")
    else:
        _, mapa_e, _ = acumulado(mes_e_str)
        pintar_editor(tabla_almacenes(mes_e_str, mapa_e), "almacen", "nombre",
                      "Registrar decisiones - Almacenes", mes_e_str)
    df_ej_e = cargar_exentas_por_comercial_ejecom(mes_e_str)
    if df_ej_e.empty:
        st.info("Sin ejecutivos comerciales con ventas en el periodo: cuando existan ventas "
                "podrás auditarlos individualmente aquí.")
    else:
        pintar_editor(tabla_ejecutivos(mes_e_str, df_ej_e), "comercial", "nombre",
                      "Registrar decisiones - Ejecutivos Comerciales", mes_e_str, es_ejecutivo=True)

# ------------------------------------------------------------
# HISTORIAL Y AUDITORÍA (con filtro por periodo)
# ------------------------------------------------------------
with tab_hist:
    periodos = conn.query("""
        SELECT DISTINCT periodo_mes FROM app.contabilidad_decisiones_exentas
        ORDER BY periodo_mes DESC
    """, ttl=0)["periodo_mes"].tolist()
    flt = st.selectbox("Filtrar por periodo", ["Todos los periodos"] + periodos, key="hist_filtro")
    pm = None if flt == "Todos los periodos" else flt

    st.markdown("##### Decisiones registradas")
    if pm:
        df_dec = obtener_decisiones_por_periodo(pm)
    else:
        df_dec = conn.query("""
            SELECT * FROM app.contabilidad_decisiones_exentas
            ORDER BY periodo_mes DESC, tipo_registro, nombre_almacen, nombre_comercial
        """, ttl=0)
    if df_dec.empty:
        st.info("Sin decisiones para el filtro elegido.")
    else:
        st.dataframe(
            df_dec[["periodo_mes", "tipo_registro", "nombre_almacen", "nombre_comercial",
                    "valor_sistema", "valor_corregido", "decision", "usuario_decision",
                    "fecha_decision"]].rename(columns={
                "periodo_mes": "Periodo", "tipo_registro": "Tipo", "nombre_almacen": "Almacén",
                "nombre_comercial": "Comercial", "valor_sistema": "Valor Sistema ($)",
                "valor_corregido": "Valor Corregido ($)", "decision": "Decisión",
                "usuario_decision": "Usuario", "fecha_decision": "Fecha Decisión"}),
            use_container_width=True, hide_index=True,
            column_config={"Fecha Decisión": st.column_config.DatetimeColumn(format="DD/MM/YYYY HH:mm")})

    st.markdown("##### Auditoría de cambios (autor, rol y valores)")
    q = """
        SELECT a.momento, a.usuario, COALESCE(u.nombre_completo, a.usuario) AS nombre,
               COALESCE(u.rol, '—') AS rol, a.accion, a.valores_anteriores, a.valores_nuevos
        FROM app.bono_auditoria a
        LEFT JOIN app.usuarios_portal u ON u.usuario = a.usuario
        WHERE a.tabla = 'contabilidad_decisiones_exentas'
    """
    par = {}
    if pm:
        q += " AND a.valores_nuevos->>'periodo' = :pm"
        par["pm"] = pm
    q += " ORDER BY a.momento DESC LIMIT 300"
    df_aud = conn.query(q, params=par, ttl=0)
    if df_aud.empty:
        st.info("Sin movimientos auditados para el filtro elegido.")
    else:
        def _j(v):
            return v if isinstance(v, dict) else {}
        vn = df_aud["valores_nuevos"].apply(_j)
        df_show = pd.DataFrame({
            "Fecha": df_aud["momento"], "Usuario": df_aud["usuario"], "Nombre": df_aud["nombre"],
            "Rol": df_aud["rol"], "Acción": df_aud["accion"],
            "Periodo": vn.apply(lambda d: d.get("periodo", "")),
            "Almacén/Comercial": vn.apply(lambda d: d.get("comercial") or d.get("almacen", "")),
            "Decisión": vn.apply(lambda d: d.get("decision", "")),
            "Valor corregido": vn.apply(lambda d: d.get("valor_corregido")),
        })
        st.dataframe(df_show, use_container_width=True, hide_index=True,
                     column_config={"Fecha": st.column_config.DatetimeColumn(format="DD/MM/YYYY HH:mm")})
        with st.expander("Ver detalle completo de un movimiento"):
            rid = st.selectbox(
                "Registro", df_show.index.tolist(),
                format_func=lambda i: f"{df_show.iloc[i]['Periodo']} · "
                                      f"{df_show.iloc[i]['Almacén/Comercial']} · "
                                      f"{df_show.iloc[i]['Acción']} · {df_show.iloc[i]['Fecha']:%d/%m/%Y %H:%M}")
            f = df_aud.iloc[rid]
            c1, c2 = st.columns(2)
            c1.markdown("**Antes**")
            if f["valores_anteriores"]:
                c1.json(f["valores_anteriores"])
            else:
                c1.caption("Sin valores anteriores: es el primer registro del periodo.")
            c2.markdown("**Después**")
            c2.json(f["valores_nuevos"] if f["valores_nuevos"] is not None else {})