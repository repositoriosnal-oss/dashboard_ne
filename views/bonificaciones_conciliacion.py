"""
CONCILIACIÓN BONO — Auditoría mensual por almacén y por ejecutivo comercial.
Pestañas: Almacenes | Ejecutivos Comerciales + informe completo descargable.
Los cálculos son idénticos a la versión validada; solo cambia presentación y orden.
"""
import io
import pandas as pd
import numpy as np
import streamlit as st
from src.conexion_db import (
    conn,
    MAP_SERIE_FACTURA,
    cargar_bonos_pagos,
    cargar_bonos_anticipos,
    cargar_pagos_consolidados,
    calcular_distribucion_bono_almacen,
    obtener_exentas_auditadas_por_almacen,
    resolver_periodo_metas,
    resolver_periodo_escala,
    resolver_periodo_bono,
    _match_intervalo,
)

st.set_page_config(page_title="Conciliación Bono", page_icon=":material/fact_check:",
                   layout="wide", initial_sidebar_state="expanded")
ROLES_BONIFICACIONES = ["admin", "gerente", "gerente_comercial"]
if st.session_state.get("rol_actual") not in ROLES_BONIFICACIONES:
    st.error("Acceso denegado. El módulo de Bonificaciones está disponible únicamente para Gerencia y Administrador.")
    st.stop()

# Tipografía compacta para métricas y tarjetas (UX de informe, no de dashboard)
st.markdown("""
<style>
div[data-testid="stMetric"] { text-align: left; padding-top: 8px; }
div[data-testid="stMetricLabel"] { font-size: 0.82rem !important; font-weight: 600; color: #555; }
div[data-testid="stMetricValue"] { font-size: 1.22rem !important; font-weight: 700; color: #1f77b4; }
div[data-testid="stVerticalBlockBorderWrapper"] { padding: 6px 12px; }
</style>
""", unsafe_allow_html=True)

st.title("Conciliación Bono")
st.caption("Auditoría mensual del bono: tramos de meta, escala de rentabilidad, exentas, anticipos y "
           "pagos consolidados por almacén; evaluación individual por ejecutivo comercial.")

EJECOM_NOM = "EJECUTIVOS COMERCIALES"


def aplicar_tramo_ventas(pvp, tramos_df):
    if tramos_df is None or tramos_df.empty:
        return 0.0
    tramo = _match_intervalo(float(pvp), tramos_df, "umbral_min", "min_inclusivo", "umbral_max", "max_inclusivo")
    return float(tramo["pct_aplica"]) if tramo is not None else 0.0


def aplicar_escala_rent(rent_ratio, escala_df):
    if escala_df is None or escala_df.empty:
        return -1.0
    rango = _match_intervalo(float(rent_ratio), escala_df, "cond_min", "min_inclusivo", "cond_max", "max_inclusivo")
    return float(rango["pct_bono"]) if rango is not None else -1.0


@st.cache_data(ttl=3600, show_spinner="Cargando base de facturas y NC...")
def cargar_facturas_nc_agregadas():
    q = """
        SELECT nombre_serie,
               fecha_contabilizacion::date AS fecha,
               SUM(precio_sin_iva) AS pvp,
               SUM(costo_total)  AS costo_total,
               SUM(rentabilidad) AS rentabilidad,
               SUM(precio_sin_iva) FILTER (WHERE tax_code IN ('IVAEXE','IVDEXE','IVAIEXE')) AS exentas
        FROM sap_raw.ventas_netas
        GROUP BY 1, 2
    """
    df = conn.query(q)
    if not df.empty:
        df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce")
        df["exentas"] = pd.to_numeric(df["exentas"], errors="coerce").fillna(0.0)
    return df


df_fac = cargar_facturas_nc_agregadas()
if df_fac.empty:
    st.warning("Sin datos de facturas/NC en sap_raw.ventas_netas. Ejecuta Forzar Sincronización SAP.")
    st.stop()
df_fac["nom_almacen"] = df_fac["nombre_serie"].map(MAP_SERIE_FACTURA).fillna("OTRO")

with st.sidebar:
    st.markdown("### Filtros")
    meses_disponibles = sorted(df_fac["fecha"].dt.to_period("M").unique())
    mes_default = pd.Period("2025-12", freq="M") if pd.Period("2025-12", freq="M") in meses_disponibles else meses_disponibles[-1]
    mes_sel = st.selectbox("Mes a evaluar:", options=meses_disponibles,
                           index=list(meses_disponibles).index(mes_default),
                           format_func=lambda p: p.strftime("%B %Y"), key="sb_conc_mes")
    alm_disp = sorted(n for n in df_fac["nom_almacen"].unique() if n != EJECOM_NOM)
    alm_sel = st.multiselect("Almacén:", alm_disp, default=alm_disp, key="sb_conc_alm")

desde = mes_sel.start_time.date()
hasta = mes_sel.end_time.date()
periodo_mes_str = f"{desde:%Y-%m}"

periodo_metas, tramos_metas = resolver_periodo_metas(desde)
periodo_escala, rangos_escala = resolver_periodo_escala(desde)
periodo_general, _, _ = resolver_periodo_bono(desde)
if periodo_metas is None or periodo_escala is None or periodo_general is None:
    st.error("No hay configuración de períodos para el mes seleccionado. Configura los períodos en Variables del Bono.")
    st.stop()

META_EJECUTIVO_MENSUAL = float(periodo_general.get("meta_ejecutivo_mensual") or 0) or 430_000_000.0
factor_desc_exentas = float(periodo_general["factor_desc_exentas"])

# Decisiones de Contabilidad del mes en UNA consulta: (tipo, almacén, comercial) -> pesos
_df_dec = conn.query("""
    SELECT tipo_registro, nombre_almacen, nombre_comercial, decision, valor_corregido
    FROM app.contabilidad_decisiones_exentas WHERE periodo_mes = :pm
""", params={"pm": periodo_mes_str}, ttl=0)
DEC_PESOS = {(r["tipo_registro"], r["nombre_almacen"], r["nombre_comercial"]): float(r["valor_corregido"])
             for _, r in _df_dec.iterrows()
             if r["decision"] == "corregido" and r["valor_corregido"] is not None}


def dec_pesos(tipo, alm, com=""):
    return DEC_PESOS.get((tipo, alm, com))


# ============================================================
# CÁLCULO 1: EJECOM (pool conciliado + evaluación individual)
# ============================================================
df_pagos_all = cargar_bonos_pagos()
if df_pagos_all.empty or "estado_comision" not in df_pagos_all.columns:
    st.warning("Sin datos de pagos. Ejecuta Forzar Sincronización SAP o verifica que Variables del Bono esté inicializado.")
    st.stop()

pagos_ej = df_pagos_all[
    (df_pagos_all["estado_comision"] == "Pago Aplicado en el Mes")
    & (df_pagos_all["nombre_almacen"] == EJECOM_NOM)
    & (df_pagos_all["fecha_aplicacion"].dt.date >= desde)
    & (df_pagos_all["fecha_aplicacion"].dt.date <= hasta)
]
liq_ej = (pagos_ej.groupby("nom_empleado")["liquidacion_ok"].sum()
          if not pagos_ej.empty else pd.Series(dtype=float))

pool = calcular_distribucion_bono_almacen(desde, hasta, [EJECOM_NOM])
pool_pagos = float(pool["liq_pagos"].sum()) if not pool.empty else 0.0
pool_ants = float(pool["liq_anticipos"].sum()) if not pool.empty else 0.0
pool_desc = float(pool["descuento_liq"].sum()) if not pool.empty else 0.0
pool_valor = float(pool["Total_Bono"].sum()) if not pool.empty else 0.0
factor_pool = (pool_valor / pool_pagos) if pool_pagos > 0 else 0.0

df_ventas_ej = conn.query("""
    SELECT nombre_vendedor, MAX(documento_identidad) AS cedula,
           SUM(precio_sin_iva) AS pvp, SUM(costo_total) AS costo, SUM(rentabilidad) AS rentabilidad,
           SUM(precio_sin_iva) FILTER (WHERE tax_code IN ('IVAEXE','IVDEXE','IVAIEXE')) AS exentas
    FROM sap_raw.ventas_netas
    WHERE nombre_almacen = 'EJECOM' AND fecha_contabilizacion::date BETWEEN :d AND :h
    GROUP BY nombre_vendedor
""", params={"d": desde, "h": hasta})

filas_ej = []
for _, r in df_ventas_ej.iterrows():
    nom = r["nombre_vendedor"]
    pvp, costo, rent = float(r["pvp"] or 0), float(r["costo"] or 0), float(r["rentabilidad"] or 0)
    cumple = pvp >= META_EJECUTIVO_MENSUAL
    pct_rent = (rent / costo * 100.0) if costo else 0.0
    pct_bono = aplicar_escala_rent(pct_rent / 100.0, rangos_escala)
    valor_bono = float(liq_ej.get(nom, 0.0)) * factor_pool
    bono_total = valor_bono * pct_bono if pct_bono >= 0 else valor_bono * (1.0 + pct_bono)
    dec_ej = dec_pesos("comercial", EJECOM_NOM, nom)
    filas_ej.append({
        "Ejecutivo": nom, "Cédula": r["cedula"], "PVP": pvp, "Meta": META_EJECUTIVO_MENSUAL,
        "% Cumplimiento": (pvp / META_EJECUTIVO_MENSUAL * 100.0) if META_EJECUTIVO_MENSUAL else 0.0,
        "Cumple_Meta": cumple,
        "PVP_faltante": max(0.0, META_EJECUTIVO_MENSUAL - pvp),
        "% Rent (Costo)": pct_rent, "% Bono": pct_bono * 100.0,
        "Valor_Bono": valor_bono, "Bono_Total": bono_total,
        "Bono_si_cumpliera": bono_total, "% Meta_Adic": 0.0,
        "Bono_Final": bono_total if cumple else 0.0,
        "Liq_Pagos": float(liq_ej.get(nom, 0.0)),          # liquidación de pagos propia del ejecutivo
        "Exentas_SAP": float(r["exentas"] or 0.0),         # exentas calculadas por SAP
        "Decision_Cont": dec_ej,                           # corrección de Contabilidad (None si no hay)
        "Exentas_Auditadas": dec_ej if dec_ej is not None else float(r["exentas"] or 0.0),
    })
df_ejec = pd.DataFrame(filas_ej).sort_values("PVP", ascending=False)
atribuido = float(df_ejec["Valor_Bono"].sum()) if not df_ejec.empty else 0.0
no_atribuido = pool_valor - atribuido
n_cumplen = int(df_ejec["Cumple_Meta"].sum()) if not df_ejec.empty else 0
bf_ejec = float(df_ejec["Bono_Final"].sum()) if not df_ejec.empty else 0.0

# ============================================================
# CÁLCULO 2: ALMACENES (tablas 1 a 4 + descomposición)
# ============================================================
dff = df_fac[(df_fac["fecha"].dt.date >= desde) & (df_fac["fecha"].dt.date <= hasta)]
dff = dff[dff["nom_almacen"] != EJECOM_NOM]
if alm_sel:
    dff = dff[dff["nom_almacen"].isin(alm_sel)]

tabla1 = pd.DataFrame()
fila_total1 = pd.DataFrame()
tabla2 = pd.DataFrame()
fila_total2 = pd.DataFrame()
t3 = pd.DataFrame()
fila_total3 = pd.DataFrame()
descomp = pd.DataFrame()
tabla_cons = pd.DataFrame()
fila_total_cons = pd.DataFrame()

if not dff.empty:
    # ---- Tabla 1 ----
    tabla1 = dff.groupby("nom_almacen", as_index=False).agg(
        Precio_Total_PVP=("pvp", "sum"),
        Suma_Costo_Total=("costo_total", "sum"),
        Suma_Rentabilidad=("rentabilidad", "sum"),
    )
    tabla1["Aplica_Bono_Del"] = tabla1["Precio_Total_PVP"].apply(lambda pvp: aplicar_tramo_ventas(pvp, tramos_metas) * 100.0)
    tabla1["Pct_Rent_Costo"] = np.where(tabla1["Suma_Costo_Total"] != 0,
                                        tabla1["Suma_Rentabilidad"] / tabla1["Suma_Costo_Total"] * 100.0, 0.0)
    tabla1 = tabla1.sort_values("Precio_Total_PVP", ascending=False)
    tp1, tc1, tr1 = (float(tabla1[c].sum()) for c in ("Precio_Total_PVP", "Suma_Costo_Total", "Suma_Rentabilidad"))
    fila_total1 = pd.DataFrame([{
        "nom_almacen": "Total", "Precio_Total_PVP": tp1, "Suma_Costo_Total": tc1, "Suma_Rentabilidad": tr1,
        "Aplica_Bono_Del": aplicar_tramo_ventas(tp1, tramos_metas) * 100.0,
        "Pct_Rent_Costo": (tr1 / tc1 * 100.0) if tc1 else 0.0}])

    # ---- Tabla 2 ----
    df_ant = cargar_bonos_anticipos()
    ant = pd.DataFrame()
    if not df_ant.empty and "tipo_factura" in df_ant.columns:
        ant = df_ant.copy()
        ant["tipo_factura"] = ant["tipo_factura"].astype(str).str.strip()
        ant = ant[ant["tipo_factura"] == "2"]
        ant = ant[ant["fecha_factura"].notna()]
        ant = ant[(ant["fecha_recibo_pago"].dt.date >= desde) & (ant["fecha_recibo_pago"].dt.date <= hasta)]
        ant = ant[ant["fecha_aplicacion"].notna()
                   & (ant["fecha_recibo_pago"].dt.to_period("M") != ant["fecha_aplicacion"].dt.to_period("M"))]
        ant["nom_almacen"] = ant["nombre_serie_pago"].map(MAP_SERIE_FACTURA).fillna("OTRO")
        if alm_sel:
            ant = ant[ant["nom_almacen"].isin(alm_sel)]
        ant = ant[ant["nom_almacen"] != EJECOM_NOM]
    g_ant = (ant.groupby("nom_almacen", as_index=False)["monto_aplicado"].sum()
             .rename(columns={"monto_aplicado": "Suma_Total_Anticipo"}) if not ant.empty
             else pd.DataFrame(columns=["nom_almacen", "Suma_Total_Anticipo"]))
    g_ex = dff.groupby("nom_almacen", as_index=False)["exentas"].sum().rename(
        columns={"exentas": "Suma_Precio_Total_Exentas"})
    exentas_auditadas = obtener_exentas_auditadas_por_almacen(periodo_mes_str)
    g_ex["Suma_Precio_Total_Exentas"] = g_ex.apply(
        lambda r: float(exentas_auditadas.get(r["nom_almacen"], r["Suma_Precio_Total_Exentas"])), axis=1)
    tabla2 = pd.merge(g_ant, g_ex, on="nom_almacen", how="outer").fillna(0.0)
    tabla2["Total_Exentas"] = tabla2["Suma_Total_Anticipo"] + tabla2["Suma_Precio_Total_Exentas"]
    tabla2 = tabla2.sort_values("Total_Exentas", ascending=False)
    fila_total2 = pd.DataFrame([{
        "nom_almacen": "Total",
        "Suma_Total_Anticipo": float(tabla2["Suma_Total_Anticipo"].sum()),
        "Suma_Precio_Total_Exentas": float(tabla2["Suma_Precio_Total_Exentas"].sum()),
        "Total_Exentas": float(tabla2["Total_Exentas"].sum())}])

    # ---- Tabla 3 ----
    distrib_bono = calcular_distribucion_bono_almacen(desde, hasta, alm_sel)
    t3 = tabla1[["nom_almacen", "Precio_Total_PVP", "Suma_Costo_Total", "Suma_Rentabilidad", "Pct_Rent_Costo"]].copy()
    t3 = t3.merge(tabla2[["nom_almacen", "Suma_Total_Anticipo", "Suma_Precio_Total_Exentas"]],
                  on="nom_almacen", how="left").fillna(0.0)
    t3["total_exentas"] = t3["Suma_Total_Anticipo"] + t3["Suma_Precio_Total_Exentas"]
    t3 = t3.merge(distrib_bono.rename(columns={
        "liq_pagos": "pagos_liq", "liq_anticipos": "ant_liq",
        "descuento_liq": "desc_liq", "Total_Bono": "Valor_Bono"}),
        left_on="nom_almacen", right_on="nombre_almacen", how="left").drop(columns=["nombre_almacen"], errors="ignore")
    t3[["pagos_liq", "ant_liq", "desc_liq", "Valor_Bono"]] = t3[["pagos_liq", "ant_liq", "desc_liq", "Valor_Bono"]].fillna(0.0)
    t3["Pct_Exentas"] = np.where(t3["Precio_Total_PVP"] != 0, t3["total_exentas"] / t3["Precio_Total_PVP"], 0.0)
    t3["Desc_Exentas"] = t3["Pct_Exentas"] * factor_desc_exentas
    for idx, row in t3.iterrows():
        dec = dec_pesos("almacen", row["nom_almacen"], "")
        if dec is not None:
            pvp_row = float(row["Precio_Total_PVP"])
            pct_ex = (dec / pvp_row) if pvp_row else 0.0
            t3.at[idx, "Pct_Exentas"] = pct_ex
            t3.at[idx, "Desc_Exentas"] = pct_ex * factor_desc_exentas
    r = t3["Pct_Rent_Costo"] / 100.0
    t3["Pct_Bono"] = r.apply(lambda rent: aplicar_escala_rent(rent, rangos_escala))
    t3["Bono_Total"] = np.where(t3["Pct_Bono"] >= 0,
                                t3["Valor_Bono"] * t3["Pct_Bono"],
                                t3["Valor_Bono"] + (t3["Valor_Bono"] * t3["Pct_Bono"]))
    meta_base = float(periodo_general["meta_adic_base"])
    meta_bloque = float(periodo_general["meta_adic_bloque"])
    meta_pct = float(periodo_general["meta_adic_pct"])
    dif_meta = t3["Precio_Total_PVP"] - meta_base
    t3["Pct_Meta_Adicional"] = np.where(dif_meta > 0, np.trunc(dif_meta / meta_bloque) * meta_pct, 0.0)
    t3["aplica"] = t3["Precio_Total_PVP"].apply(lambda pvp: aplicar_tramo_ventas(pvp, tramos_metas))
    t3["Bono_Final"] = np.where(t3["aplica"] != 0, (1.0 + t3["Pct_Meta_Adicional"]) * t3["Bono_Total"], 0.0)
    t3 = t3.sort_values("Valor_Bono", ascending=False)
    tp = float(t3["Precio_Total_PVP"].sum()); tc = float(t3["Suma_Costo_Total"].sum())
    tr = float(t3["Suma_Rentabilidad"].sum()); te = float(t3["total_exentas"].sum())
    tv = float(t3["Valor_Bono"].sum()); bt_sum = float(t3["Bono_Total"].sum()); bf_sum = float(t3["Bono_Final"].sum())

    fila_total3 = pd.DataFrame([{
        "nom_almacen": "Total",
        "Pct_Exentas": te / tp if tp else 0.0,
        "Desc_Exentas": (te / tp if tp else 0.0) * factor_desc_exentas,
        "Valor_Bono": tv,
        "Pct_Bono": (bt_sum / tv) if tv else 0.0,
        "Bono_Total": bt_sum,
        "Pct_Meta_Adicional": (float((t3["Pct_Meta_Adicional"] * t3["Valor_Bono"]).sum()) / tv) if tv else 0.0,
        "Bono_Final": bf_sum}])
    descomp = t3[["nom_almacen", "pagos_liq", "ant_liq", "desc_liq", "Valor_Bono"]].copy()

    # ---- Tabla 4: control consolidados ----
    df_cons = cargar_pagos_consolidados()
    dfc = pd.DataFrame()
    if not df_cons.empty:
        dfc = df_cons[(df_cons["fecha_recibo"].dt.date >= desde) & (df_cons["fecha_recibo"].dt.date <= hasta)]
        dfc = dfc[dfc["nombre_almacen"] != EJECOM_NOM]
        if alm_sel:
            dfc = dfc[dfc["nombre_almacen"].isin(alm_sel)]
    if not dfc.empty:
        tabla_cons = dfc[["pago_nro", "fecha_recibo", "nombre_almacen", "nombre_cliente",
                          "pr", "vr_aplicado", "diferencia", "n_facturas", "n_aplicaciones"]].copy()
        tabla_cons["descuento_liq"] = tabla_cons["diferencia"] * 0.008
        fila_total_cons = pd.DataFrame([{
            "pago_nro": "Total", "fecha_recibo": pd.NaT, "nombre_almacen": "",
            "nombre_cliente": f"{len(tabla_cons)} pagos observados",
            "pr": tabla_cons["pr"].sum(), "vr_aplicado": tabla_cons["vr_aplicado"].sum(),
            "diferencia": tabla_cons["diferencia"].sum(),
            "descuento_liq": tabla_cons["descuento_liq"].sum(),
            "n_facturas": None, "n_aplicaciones": None}])

# ============================================================
# INFORME COMPLETO (Excel multi-hoja)
# ============================================================
REN1 = {"nom_almacen": "Almacén", "Precio_Total_PVP": "Precio Total PVP", "Aplica_Bono_Del": "Aplica Bono del",
        "Suma_Costo_Total": "Suma de Costo total", "Suma_Rentabilidad": "Suma de Rentabilidad", "Pct_Rent_Costo": "% Rent (Costo)"}
REN3 = {"nom_almacen": "Almacén", "Pct_Exentas": "% Exentas", "Desc_Exentas": "DESC EXENTAS",
        "Valor_Bono": "Valor Bono", "Pct_Bono": "% Bono", "Bono_Total": "BONO TOTAL",
        "Pct_Meta_Adicional": "% Meta Adicional", "Bono_Final": "BONO FINAL"}


def _pct100(df):
    df = df.copy()
    for col in ["Pct_Exentas", "Desc_Exentas", "Pct_Bono", "Pct_Meta_Adicional"]:
        if col in df.columns:
            df[col] = df[col] * 100.0
    return df


def construir_informe_excel() -> bytes:
    buf = io.BytesIO()
    t3_exp = _pct100(pd.concat([t3, fila_total3], ignore_index=True)) if not t3.empty else t3
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        (pd.concat([tabla1, fila_total1], ignore_index=True).rename(columns=REN1)
         if not tabla1.empty else pd.DataFrame({"Nota": ["Sin datos de almacenes en el mes"]})).to_excel(
            xw, index=False, sheet_name="1. Resumen Almacén")
        (pd.concat([tabla2, fila_total2], ignore_index=True).rename(columns={
            "nom_almacen": "Almacén", "Suma_Total_Anticipo": "Suma de Total Anticipo",
            "Suma_Precio_Total_Exentas": "Suma de Precio total exentas", "Total_Exentas": "Total Exentas"})
         if not tabla2.empty else pd.DataFrame({"Nota": ["Sin datos"]})).to_excel(
            xw, index=False, sheet_name="2. Anticipos y Exentas")
        (t3_exp.rename(columns=REN3) if not t3_exp.empty else pd.DataFrame({"Nota": ["Sin datos"]})).to_excel(
            xw, index=False, sheet_name="3. Liquidación Almacén")
        (pd.concat([tabla_cons, fila_total_cons], ignore_index=True).rename(columns={
            "pago_nro": "Pago Nro", "fecha_recibo": "Fecha Recibo", "nombre_almacen": "Almacén",
            "nombre_cliente": "Cliente", "pr": "Total Recibo (PR)", "vr_aplicado": "VR Aplicado",
            "diferencia": "Diferencia", "descuento_liq": "Descuento Liquidación",
            "n_facturas": "Facturas", "n_aplicaciones": "Aplicaciones"})
         if not tabla_cons.empty else pd.DataFrame({"Nota": ["Sin pagos consolidados observados"]})).to_excel(
            xw, index=False, sheet_name="4. Control Consolidados")
        (descomp.rename(columns={"nom_almacen": "Almacén", "pagos_liq": "Liq. Pagos", "ant_liq": "Liq. Anticipos",
                                 "desc_liq": "Desc. Consolidados", "Valor_Bono": "Valor Bono"})
         if not descomp.empty else pd.DataFrame({"Nota": ["Sin datos"]})).to_excel(
            xw, index=False, sheet_name="5. Descomposición Valor Bono")
        df_ej_exp = df_ejec.copy()
        if not df_ej_exp.empty:
            if abs(no_atribuido) > 1.0:
                df_ej_exp = pd.concat([df_ej_exp, pd.DataFrame([{
                    "Ejecutivo": "Pendiente de atribución (sin vendedor coincidente)", "Cédula": "",
                    "PVP": 0.0, "Meta": 0.0, "% Cumplimiento": 0.0, "Cumple_Meta": False,
                    "PVP_faltante": np.nan, "% Rent (Costo)": np.nan, "% Bono": np.nan,
                    "Valor_Bono": no_atribuido, "Bono_Total": 0.0, "Bono_si_cumpliera": np.nan,
                    "% Meta_Adic": 0.0, "Bono_Final": 0.0, "Exentas_Auditadas": 0.0}])], ignore_index=True)
            df_ej_exp = pd.concat([df_ej_exp, pd.DataFrame([{
                "Ejecutivo": f"Total ({len(df_ejec)} ejecutivos)", "Cédula": "",
                "PVP": df_ejec["PVP"].sum(), "Meta": META_EJECUTIVO_MENSUAL * len(df_ejec),
                "% Cumplimiento": df_ejec["PVP"].sum() / (META_EJECUTIVO_MENSUAL * len(df_ejec)) * 100.0,
                "Cumple_Meta": n_cumplen, "PVP_faltante": df_ejec["PVP_faltante"].sum(),
                "% Rent (Costo)": np.nan, "% Bono": np.nan,
                "Valor_Bono": df_ejec["Valor_Bono"].sum(), "Bono_Total": df_ejec["Bono_Total"].sum(),
                "Bono_si_cumpliera": df_ejec["Bono_si_cumpliera"].sum(), "% Meta_Adic": np.nan,
                "Bono_Final": bf_ejec, "Exentas_Auditadas": df_ejec["Exentas_Auditadas"].sum()}])], ignore_index=True)
        df_ej_exp = df_ej_exp.drop(columns=["Liq_Pagos", "Exentas_SAP", "Decision_Cont"], errors="ignore")
        (df_ej_exp.rename(columns={
            "Cumple_Meta": "Cumple Meta", "PVP_faltante": "PVP faltante p/ meta",
            "Valor_Bono": "Valor Bono", "Bono_Total": "BONO TOTAL",
            "Bono_si_cumpliera": "Bono si cumpliera", "% Meta_Adic": "% Meta Adicional",
            "Bono_Final": "BONO FINAL", "Exentas_Auditadas": "Exentas Auditadas ($)"})
         if not df_ej_exp.empty else pd.DataFrame({"Nota": ["Sin ventas de ejecutivos en el mes"]})).to_excel(
            xw, index=False, sheet_name="6. Ejecutivos Individual")
    return buf.getvalue()


hdr_t, hdr_b = st.columns([5, 1])
with hdr_b:
    st.download_button("Descargar informe completo (Excel)",
                       data=construir_informe_excel(),
                       file_name=f"informe_conciliacion_bono_{periodo_mes_str}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       use_container_width=True)
with hdr_t:
    st.caption(f"Período evaluado: {mes_sel.strftime('%B %Y')} · Período de metas: {periodo_metas['nombre']} · "
               f"Período de escala: {periodo_escala['nombre']} · Factor DESC exentas: {factor_desc_exentas * 100:.0f}%")

tab_alm, tab_ej = st.tabs(["Almacenes", "Ejecutivos Comerciales"])

# ------------------------------------------------------------
# PESTAÑA 1: ALMACENES
# ------------------------------------------------------------
with tab_alm:
    kpi_almacenes = st.container()
    if dff.empty:
        st.info("Sin datos de almacenes en el mes y filtros seleccionados "
                "(EJECOM se audita en su propia pestaña).")
    else:
        # ---- KPIs y conciliación del pool de almacenes (arriba de las tablas) ----
        pvp_red = float(t3["Precio_Total_PVP"].sum())
        n_alms = len(t3)
        n_aplican = int((t3["aplica"] != 0).sum())
        vb_red = float(t3["Valor_Bono"].sum())
        bt_red = float(t3["Bono_Total"].sum())
        bf_red = float(t3["Bono_Final"].sum())
        pagos_red = float(t3["pagos_liq"].sum())
        ants_red = float(t3["ant_liq"].sum())
        desc_red = float(t3["desc_liq"].sum())
        k = st.columns(5)
        with k[0].container(border=True, height=92):
            st.metric("PVP red del mes", f"${pvp_red:,.0f}")
        with k[1].container(border=True, height=92):
            st.metric("Almacenes con bono", f"{n_aplican} de {n_alms}",
                      help="Almacenes cuyo PVP cayó en un tramo con % aplicable distinto de 0")
        with k[2].container(border=True, height=92):
            st.metric("Valor Bono (pool)", f"${vb_red:,.2f}",
                      help="Pagos aplicados + anticipos − descuentos consolidados de los almacenes")
        with k[3].container(border=True, height=92):
            st.metric("Bono Total (escala)", f"${bt_red:,.2f}",
                      help="Valor Bono × % de la escala de rentabilidad de cada almacén")
        with k[4].container(border=True, height=92):
            st.metric("BONO FINAL red", f"${bf_red:,.2f}",
                      help="Bono Total × (1 + % meta adicional) en los almacenes que aplican")
        with st.container(border=True):
            st.markdown("**Conciliación del pool de almacenes**")
            st.dataframe(pd.DataFrame([
                {"Concepto": "Liquidación de pagos aplicados", "Monto": pagos_red},
                {"Concepto": "Liquidación de anticipos", "Monto": ants_red},
                {"Concepto": "Descuentos por conciliación consolidada", "Monto": -desc_red},
                {"Concepto": "Valor Bono del pool", "Monto": vb_red},
                {"Concepto": "Verificación (debe ser $0.00)",
                 "Monto": pagos_red + ants_red - desc_red - vb_red},
            ]), use_container_width=True, hide_index=True,
                column_config={"Monto": st.column_config.NumberColumn(format="$%,.2f")})
            st.caption("El Valor Bono de cada almacén nace de la misma fuente única "
                       "(calcular_distribucion_bono_almacen, compartida con Detalle Bono): la fila de "
                       "verificación debe dar siempre $0.00. Si algún día no, hay un descuadre entre módulos.")
        st.markdown("---")
        st.subheader("1. Resumen por Almacén (tramos de meta)")
        st.dataframe(
            pd.concat([tabla1, fila_total1], ignore_index=True).rename(columns=REN1),
            use_container_width=True, hide_index=True,
            column_config={
                "Precio Total PVP": st.column_config.NumberColumn(format="$%,.2f"),
                "Aplica Bono del": st.column_config.NumberColumn(format="%.2f%%"),
                "Suma de Costo total": st.column_config.NumberColumn(format="$%,.2f"),
                "Suma de Rentabilidad": st.column_config.NumberColumn(format="$%,.2f"),
                "% Rent (Costo)": st.column_config.NumberColumn(format="%.2f%%")})

        st.markdown("---")
        st.subheader("2. Anticipos y Exentas por Almacén")
        st.dataframe(
            pd.concat([tabla2, fila_total2], ignore_index=True).rename(columns={
                "nom_almacen": "Almacén", "Suma_Total_Anticipo": "Suma de Total Anticipo",
                "Suma_Precio_Total_Exentas": "Suma de Precio total exentas", "Total_Exentas": "Total Exentas"}),
            use_container_width=True, hide_index=True,
            column_config={
                "Suma de Total Anticipo": st.column_config.NumberColumn(format="$%,.2f"),
                "Suma de Precio total exentas": st.column_config.NumberColumn(format="$%,.2f"),
                "Total Exentas": st.column_config.NumberColumn(format="$%,.2f")})

        st.markdown("---")
        st.subheader("2. Anticipos y Exentas por Almacén")
        st.dataframe(
            pd.concat([tabla2, fila_total2], ignore_index=True).rename(columns={
                "nom_almacen": "Almacén", "Suma_Total_Anticipo": "Suma de Total Anticipo",
                "Suma_Precio_Total_Exentas": "Suma de Precio total exentas", "Total_Exentas": "Total Exentas"}),
            use_container_width=True, hide_index=True,
            column_config={
                "Suma de Total Anticipo": st.column_config.NumberColumn(format="$%,.2f"),
                "Suma de Precio total exentas": st.column_config.NumberColumn(format="$%,.2f"),
                "Total Exentas": st.column_config.NumberColumn(format="$%,.2f")})

        # 👇 PEGA EL FRAGMENTO AQUÍ 👇
        est_conc = float(ant["liq_estimada"].sum()) if (not ant.empty and "liq_estimada" in ant.columns) else 0.0
        if est_conc > 0:
            st.caption(f"Incluye ${est_conc:,.2f} de liquidación ESTIMADA de anticipos del mismo mes "
                       "(switch activo en el período). Al aplicarse en SAP se convierte en liquidación real.")

        st.markdown("---")
        st.subheader("3. Liquidación del Bono por Almacén")


        st.markdown("---")
        st.subheader("3. Liquidación del Bono por Almacén")
        st.caption("La fila Total suma las filas: los tramos de meta se evalúan por almacén.")
        t3_mostrar = _pct100(pd.concat([t3, fila_total3], ignore_index=True))
        st.dataframe(
            t3_mostrar.rename(columns=REN3)[
                ["Almacén", "% Exentas", "DESC EXENTAS", "Valor Bono", "% Bono",
                 "BONO TOTAL", "% Meta Adicional", "BONO FINAL"]],
            use_container_width=True, hide_index=True,
            column_config={
                "% Exentas": st.column_config.NumberColumn(format="%.2f%%"),
                "DESC EXENTAS": st.column_config.NumberColumn(format="%.2f%%"),
                "Valor Bono": st.column_config.NumberColumn(format="$%,.2f"),
                "% Bono": st.column_config.NumberColumn(format="%.2f%%"),
                "BONO TOTAL": st.column_config.NumberColumn(format="$%,.2f"),
                "% Meta Adicional": st.column_config.NumberColumn(format="%.2f%%"),
                "BONO FINAL": st.column_config.NumberColumn(format="$%,.2f")})
        with st.expander("Descomposición del Valor Bono por almacén"):
            st.dataframe(
                descomp.rename(columns={"nom_almacen": "Almacén", "pagos_liq": "Liq. Pagos",
                                        "ant_liq": "Liq. Anticipos", "desc_liq": "Desc. Consolidados",
                                        "Valor_Bono": "Valor Bono"}),
                use_container_width=True, hide_index=True,
                column_config={
                    "Liq. Pagos": st.column_config.NumberColumn(format="$%,.2f"),
                    "Liq. Anticipos": st.column_config.NumberColumn(format="$%,.2f"),
                    "Desc. Consolidados": st.column_config.NumberColumn(format="$%,.2f"),
                    "Valor Bono": st.column_config.NumberColumn(format="$%,.2f")})

        st.markdown("---")
        st.subheader("4. Control: pagos en conciliación consolidada")
        st.caption("Recibos cuya suma aplicada supera el valor del recibo (conciliaciones con N pagos y M facturas). "
                   "Regla: diferencia mayor a $100. Respeta los filtros de mes y almacén.")
        if tabla_cons.empty:
            st.info("Sin pagos consolidados observados en el mes y almacenes seleccionados.")
        else:
            st.dataframe(
                pd.concat([tabla_cons, fila_total_cons], ignore_index=True).rename(columns={
                    "pago_nro": "Pago Nro", "fecha_recibo": "Fecha Recibo", "nombre_almacen": "Almacén",
                    "nombre_cliente": "Cliente", "pr": "Total Recibo (PR)", "vr_aplicado": "VR Aplicado",
                    "diferencia": "Diferencia", "descuento_liq": "Descuento Liquidación",
                    "n_facturas": "Facturas", "n_aplicaciones": "Aplicaciones"}),
                use_container_width=True, hide_index=True,
                column_config={
                    "Fecha Recibo": st.column_config.DateColumn(format="DD/MM/YYYY"),
                    "Almacén": st.column_config.TextColumn(width="medium"),
                    "Cliente": st.column_config.TextColumn(width="large"),
                    "Total Recibo (PR)": st.column_config.NumberColumn(format="$%,.2f"),
                    "VR Aplicado": st.column_config.NumberColumn(format="$%,.2f"),
                    "Diferencia": st.column_config.NumberColumn(format="$%,.2f"),
                    "Descuento Liquidación": st.column_config.NumberColumn(format="$%,.2f")})

# ------------------------------------------------------------
# PESTAÑA 2: EJECUTIVOS COMERCIALES
# ------------------------------------------------------------
#with tab_ej:
#    st.subheader(f"Ejecutivos Comerciales · Evaluación individual · {mes_sel.strftime('%B %Y')}")

#    with st.container(border=True):
#        st.markdown("**Cómo se calcula**")
#        c1, c2, c3 = st.columns(3)
#        c1.caption(f"**1. Meta individual.** Cada ejecutivo aplica al 100% solo si su PVP del mes es "
#                   f"≥ ${META_EJECUTIVO_MENSUAL:,.0f}. La meta se configura en Variables del Bono (ámbito Ejecutivos).")
#        c2.caption("**2. Si cumple.** Entra a la misma cadena que un almacén: escala de rentabilidad sobre su "
#                   "propia rentabilidad y Valor Bono proporcional a sus pagos aplicados en el mes.")
#        c3.caption("**3. Si no cumple.** Su Bono Final del mes es $0. Las columnas *PVP faltante* y "
#                   "*Bono si cumpliera* muestran la distancia y el impacto.")

with tab_ej:
    st.subheader(f"Ejecutivos Comerciales · Evaluación individual · {mes_sel.strftime('%B %Y')}")

    # ---- Marco de regla + KPIs generales ----
#    with st.container(border=True):
#        st.markdown("**Cómo se calcula**")
#        c1, c2, c3 = st.columns(3)
#        c1.caption(f"**1. Meta individual.** Cada ejecutivo aplica al 100% solo si su PVP del mes es "
#                   f"≥ ${META_EJECUTIVO_MENSUAL:,.0f} (configurable en Variables del Bono).")
#        c2.caption("**2. Si cumple.** Misma cadena que un almacén: escala de rentabilidad sobre su propia "
#                   "rentabilidad y Valor Bono proporcional a sus pagos aplicados.")
#        c3.caption("**3. Si no cumple.** Su Bono Final del mes es $0. Las columnas *PVP faltante* y "
#                   "*Bono si cumpliera* muestran la distancia y el impacto.")
    pvp_ej_tot = float(df_ventas_ej["pvp"].sum()) if not df_ventas_ej.empty else 0.0
    k = st.columns(5)
    with k[0].container(border=True, height=92):
        st.metric("PVP EJECOM del mes", f"${pvp_ej_tot:,.0f}")
    with k[1].container(border=True, height=92):
        st.metric("Cumplen meta", f"{n_cumplen} de {len(df_ejec)}")
    with k[2].container(border=True, height=92):
        st.metric("Valor Bono (pool)", f"${pool_valor:,.2f}",
                  help="Pagos aplicados + anticipos − descuentos consolidados de la serie EJECOM")
    with k[3].container(border=True, height=92):
        st.metric("Atribuido a ejecutivos", f"${atribuido:,.2f}")
    with k[4].container(border=True, height=92):
        st.metric("Bono Final ejecutivos", f"${bf_ejec:,.2f}")

    if df_ejec.empty:
        st.info("Sin ventas de ejecutivos comerciales en el mes seleccionado.")
    else:
        # ============================================================
        # 1. RESUMEN POR EJECUTIVO (META INDIVIDUAL)
        # ============================================================
        st.markdown("---")
        st.subheader("1. Resumen por Ejecutivo (meta individual)")
        t1_ej = df_ejec[["Ejecutivo", "Cédula", "PVP", "Meta", "% Cumplimiento", "Cumple_Meta",
                         "PVP_faltante", "% Rent (Costo)", "% Bono"]].copy()
        fila_t1 = pd.DataFrame([{
            "Ejecutivo": f"Total ({len(df_ejec)} ejecutivos)", "Cédula": "",
            "PVP": t1_ej["PVP"].sum(), "Meta": META_EJECUTIVO_MENSUAL * len(df_ejec),
            "% Cumplimiento": t1_ej["PVP"].sum() / (META_EJECUTIVO_MENSUAL * len(df_ejec)) * 100.0,
            "Cumple_Meta": n_cumplen, "PVP_faltante": t1_ej["PVP_faltante"].sum(),
            "% Rent (Costo)": np.nan, "% Bono": np.nan}])
        st.dataframe(
            pd.concat([t1_ej, fila_t1], ignore_index=True).rename(columns={
                "Cumple_Meta": "¿Cumple Meta?", "PVP_faltante": "PVP faltante",
                "% Rent (Costo)": "% Rent (Costo)", "% Bono": "% Bono (escala)"}),
            use_container_width=True, hide_index=True,
            column_config={
                "Ejecutivo": st.column_config.TextColumn(width="medium"),
                "Cédula": st.column_config.TextColumn(width="small"),
                "PVP": st.column_config.NumberColumn(format="$%,.2f"),
                "Meta": st.column_config.NumberColumn(format="$%,.0f"),
                "% Cumplimiento": st.column_config.NumberColumn(format="%.2f%%"),
                "¿Cumple Meta?": st.column_config.CheckboxColumn(),
                "PVP faltante": st.column_config.NumberColumn(format="$%,.2f"),
                "% Rent (Costo)": st.column_config.NumberColumn(format="%.2f%%"),
                "% Bono (escala)": st.column_config.NumberColumn(format="%.2f%%")})
        st.caption("La meta se evalúa por ejecutivo, no sobre el agregado EJECOM: cumple → aplica al 100%; "
                   "no cumple → Bono Final $0. Aquí no aplican los tramos de almacén.")

        # ============================================================
        # 2. ANTICIPOS Y EXENTAS POR EJECUTIVO
        # ============================================================
        st.markdown("---")
        st.subheader("2. Anticipos y Exentas por Ejecutivo")
        t2_ej = df_ejec[["Ejecutivo", "Cédula", "PVP", "Exentas_SAP", "Decision_Cont", "Exentas_Auditadas"]].copy()
        t2_ej["Decision_Cont"] = pd.to_numeric(t2_ej["Decision_Cont"], errors="coerce")
        t2_ej["% Exentas"] = np.where(t2_ej["PVP"] != 0, t2_ej["Exentas_Auditadas"] / t2_ej["PVP"], 0.0)
        fila_t2 = pd.DataFrame([{
            "Ejecutivo": "Total", "Cédula": "", "PVP": t2_ej["PVP"].sum(),
            "Exentas_SAP": t2_ej["Exentas_SAP"].sum(), "Decision_Cont": np.nan,
            "Exentas_Auditadas": t2_ej["Exentas_Auditadas"].sum(),
            "% Exentas": (t2_ej["Exentas_Auditadas"].sum() / t2_ej["PVP"].sum()) if t2_ej["PVP"].sum() else 0.0}])
        st.dataframe(
            pd.concat([t2_ej, fila_t2], ignore_index=True).rename(columns={
                "Exentas_SAP": "Total Exentas (SAP)", "Decision_Cont": "Corrección Contabilidad ($)",
                "Exentas_Auditadas": "Exentas finales auditadas", "% Exentas": "% Exentas"}),
            use_container_width=True, hide_index=True,
            column_config={
                "Ejecutivo": st.column_config.TextColumn(width="medium"),
                "Cédula": st.column_config.TextColumn(width="small"),
                "PVP": st.column_config.NumberColumn(format="$%,.2f"),
                "Total Exentas (SAP)": st.column_config.NumberColumn(format="$%,.2f"),
                "Corrección Contabilidad ($)": st.column_config.NumberColumn(format="$%,.2f"),
                "Exentas finales auditadas": st.column_config.NumberColumn(format="$%,.2f"),
                "% Exentas": st.column_config.NumberColumn(format="%.2f%%")})
        st.caption("Si Contabilidad registró una decisión *corregida* (módulo Auditoría Exentas), ese valor en pesos "
                   "reemplaza el calculado de SAP. Los anticipos de EJECOM no traen vendedor en el recibo SAP: "
                   "no se atribuyen por ejecutivo; entran al pool que se reparte en la sección 3.")

        # ============================================================
        # 3. LIQUIDACIÓN DEL BONO POR EJECUTIVO
        # ============================================================
        st.markdown("---")
        st.subheader("3. Liquidación del Bono por Ejecutivo")
        t3_ej = df_ejec[["Ejecutivo", "Liq_Pagos", "Valor_Bono", "% Bono", "Bono_Total",
                         "Cumple_Meta", "Bono_Final"]].copy()
        fila_t3 = pd.DataFrame([{
            "Ejecutivo": "Total", "Liq_Pagos": t3_ej["Liq_Pagos"].sum(),
            "Valor_Bono": t3_ej["Valor_Bono"].sum(), "% Bono": np.nan,
            "Bono_Total": t3_ej["Bono_Total"].sum(), "Cumple_Meta": n_cumplen,
            "Bono_Final": t3_ej["Bono_Final"].sum()}])
        st.dataframe(
            pd.concat([t3_ej, fila_t3], ignore_index=True).rename(columns={
                "Liq_Pagos": "Liq. Pagos (propia)", "Valor_Bono": "Valor Bono (atribuido)",
                "% Bono": "% Bono", "Bono_Total": "BONO TOTAL",
                "Cumple_Meta": "¿Cumple Meta?", "Bono_Final": "BONO FINAL"}),
            use_container_width=True, hide_index=True,
            column_config={
                "Ejecutivo": st.column_config.TextColumn(width="medium"),
                "Liq. Pagos (propia)": st.column_config.NumberColumn(format="$%,.2f"),
                "Valor Bono (atribuido)": st.column_config.NumberColumn(format="$%,.2f"),
                "% Bono": st.column_config.NumberColumn(format="%.2f%%"),
                "BONO TOTAL": st.column_config.NumberColumn(format="$%,.2f"),
                "¿Cumple Meta?": st.column_config.CheckboxColumn(),
                "BONO FINAL": st.column_config.NumberColumn(format="$%,.2f")})
        with st.expander("Descomposición del Valor Bono por Ejecutivo (del pool a cada uno)"):
            st.dataframe(pd.DataFrame([{
                "Liq. Pagos": pool_pagos, "Liq. Anticipos": pool_ants,
                "Desc. Consolidados": -pool_desc, "Valor Bono (pool)": pool_valor,
                "Factor de atribución": factor_pool}]),
                use_container_width=True, hide_index=True,
                column_config={
                    "Liq. Pagos": st.column_config.NumberColumn(format="$%,.2f"),
                    "Liq. Anticipos": st.column_config.NumberColumn(format="$%,.2f"),
                    "Desc. Consolidados": st.column_config.NumberColumn(format="$%,.2f"),
                    "Valor Bono (pool)": st.column_config.NumberColumn(format="$%,.2f"),
                    "Factor de atribución": st.column_config.NumberColumn(format="%.6f")})
            st.caption(f"Valor Bono atribuido = liquidación de pagos propia × factor de atribución "
                       f"({factor_pool:.6f}). El factor repone anticipos y descuentos consolidados de forma "
                       "proporcional, para que la suma de lo atribuido cuadre con el pool.")
            st.dataframe(
                df_ejec[["Ejecutivo", "Liq_Pagos", "Valor_Bono"]].rename(columns={
                    "Liq_Pagos": "Liq. Pagos", "Valor_Bono": "Valor Bono atribuido (= liq × factor)"}),
                use_container_width=True, hide_index=True,
                column_config={
                    "Liq. Pagos": st.column_config.NumberColumn(format="$%,.2f"),
                    "Valor Bono atribuido (= liq × factor)": st.column_config.NumberColumn(format="$%,.2f")})

        # ============================================================
        # 4. CONTROL DE CUADRE EJECOM
        # ============================================================
        st.markdown("---")
        st.subheader("4. Control de cuadre EJECOM")
        df_cuadre = pd.DataFrame([
            {"Concepto": "Valor Bono del pool (pagos + anticipos − consolidados)", "Monto": pool_valor},
            {"Concepto": "(−) Atribuido a ejecutivos por sus pagos", "Monto": -atribuido},
            {"Concepto": "(=) Pendiente de atribución (sin vendedor coincidente)", "Monto": no_atribuido},
            {"Concepto": "Verificación (debe ser $0.00)", "Monto": pool_valor - atribuido - no_atribuido},
        ])
        st.dataframe(df_cuadre, use_container_width=True, hide_index=True,
                     column_config={"Monto": st.column_config.NumberColumn(format="$%,.2f")})
        if abs(no_atribuido) > 1.0:
            st.caption(f"Quedan ${no_atribuido:,.2f} sin atribuir: cobros sin vendedor coincidente (grafías "
                       "distintas entre ventas y pagos, pagos sin vendedor o pagos sin ventas en el mes). Su bono "
                       "es $0 hasta atribuirlos con el vínculo SAP ↔ Talento Humano; no afectan a los demás.")
        else:
            st.caption("Pool completamente atribuido: cada peso del Valor Bono EJECOM tiene ejecutivo responsable.")

        st.markdown("---")
        st.caption(f"Ejecutivos que cumplen: {n_cumplen} de {len(df_ejec)} · "
                   f"Bono Final individual acumulado: ${bf_ejec:,.2f}")

#st.markdown("---")
#st.caption("Los porcentajes se guardan como fracción y se convierten a porcentaje solo para mostrar. "
#           "EJECOM queda fuera de las tablas de almacenes: su auditoría completa vive en la pestaña de ejecutivos "
#           "y en la hoja 6 del informe descargable.")








# Los porcentajes se guardan como fracción; convertir a porcentaje para mostrar
# (se aplica sobre la copia mostrada, no altera el cálculo)