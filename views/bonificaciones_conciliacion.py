import streamlit as st
import pandas as pd
import numpy as np
from datetime import date
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
    obtener_decision_contabilidad,
)

# ============================================================
# CONFIGURACIÓN DE PÁGINA
# ============================================================
st.set_page_config(
    page_title="Conciliación Bono",
    page_icon=":material/fact_check:",
    layout="wide",
    initial_sidebar_state="expanded",
)

# 🔐 CONTROL DE ACCESO
ROLES_BONIFICACIONES = ["admin", "gerente", "gerente_comercial"]
if st.session_state.get("rol_actual") not in ROLES_BONIFICACIONES:
    st.error("⛔ Acceso denegado. El módulo de Bonificaciones está disponible únicamente para Gerencia y Administrador.")
    st.stop()

st.title("Conciliación Bono")


# ============================================================
# FUNCIONES AUXILIARES
# ============================================================
def aplicar_tramo_ventas(pvp, tramos_df):
    """Evalúa un PVP contra los tramos de meta de ventas y retorna el % aplicable."""
    if tramos_df is None or tramos_df.empty:
        return 0.0
    tramo = _match_intervalo(float(pvp), tramos_df, "umbral_min", "min_inclusivo", "umbral_max", "max_inclusivo")
    return float(tramo["pct_aplica"]) if tramo is not None else 0.0


def aplicar_escala_rent(rent_ratio, escala_df):
    """Evalúa una rentabilidad contra la escala y retorna el % bono."""
    if escala_df is None or escala_df.empty:
        return -1.0
    rango = _match_intervalo(float(rent_ratio), escala_df, "cond_min", "min_inclusivo", "cond_max", "max_inclusivo")
    return float(rango["pct_bono"]) if rango is not None else -1.0


# ============================================================
# CARGA AGREGADA
# ============================================================
@st.cache_data(ttl=3600, show_spinner="Cargando base de facturas y NC...")
def cargar_facturas_nc_agregadas():
    q = """
        SELECT nombre_serie,
               fecha_contabilizacion::date AS fecha,
               SUM(precio_sin_iva) AS pvp,
               SUM(costo_total)  AS costo_total,
               SUM(rentabilidad) AS rentabilidad,
               SUM(precio_sin_iva) FILTER (WHERE tax_code IN ('IVAEXE', 'IVDEXE', 'IVAIEXE')) AS exentas
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
    st.warning("Sin datos de facturas/NC en `sap_raw.ventas_netas`. Ejecuta **Forzar Sincronización SAP**.")
    st.stop()

df_fac["nom_almacen"] = df_fac["nombre_serie"].map(MAP_SERIE_FACTURA).fillna("OTRO")

f_min = df_fac["fecha"].min().date()
f_max = df_fac["fecha"].max().date()


# ============================================================
# FILTROS LATERALES - SELECTOR DE MES
# ============================================================
with st.sidebar:
    st.markdown("### Filtros")

    # Selector de mes único
    meses_disponibles = sorted(df_fac["fecha"].dt.to_period("M").unique())
    mes_default = pd.Period("2025-12", freq="M") if pd.Period("2025-12", freq="M") in meses_disponibles else meses_disponibles[-1]
    
    mes_sel = st.selectbox(
        "Mes a evaluar:",
        options=meses_disponibles,
        index=list(meses_disponibles).index(mes_default),
        format_func=lambda p: p.strftime("%B %Y"),
        key="sb_conc_mes"
    )
    
    alm_disp = sorted(df_fac["nom_almacen"].unique().tolist())
    alm_sel = st.multiselect("Nom Almacen:", alm_disp, default=alm_disp, key="sb_conc_alm")

# Convertir período a rango de fechas
desde = mes_sel.start_time.date()
hasta = mes_sel.end_time.date()

# ============================================================
# RESOLVER PERÍODOS PARA EL MES SELECCIONADO
# ============================================================
periodo_metas, tramos_metas = resolver_periodo_metas(desde)
periodo_escala, rangos_escala = resolver_periodo_escala(desde)
periodo_general, _, _ = resolver_periodo_bono(desde)

if periodo_metas is None or periodo_escala is None or periodo_general is None:
    st.error("⚠️ No hay configuración de períodos para el mes seleccionado. Configura los períodos en **Variables del Bono**.")
    st.stop()

st.info(f"📅 Evaluando **{mes_sel.strftime('%B %Y')}** con período de metas: **{periodo_metas['nombre']}**, período de escala: **{periodo_escala['nombre']}**")

# ============================================================
# FILTRADO POR MES Y ALMACÉN
# ============================================================
dff = df_fac[(df_fac["fecha"].dt.date >= desde) & (df_fac["fecha"].dt.date <= hasta)]
if alm_sel:
    dff = dff[dff["nom_almacen"].isin(alm_sel)]

if dff.empty:
    st.warning("Sin datos en el mes y almacenes seleccionados.")
    st.stop()

# ============================================================
# TABLA 1: RESUMEN POR ALMACÉN (con tramos dinámicos)
# ============================================================
tabla = dff.groupby("nom_almacen", as_index=False).agg(
    Precio_Total_PVP=("pvp", "sum"),
    Suma_Costo_Total=("costo_total", "sum"),
    Suma_Rentabilidad=("rentabilidad", "sum"),
)

# Aplicar tramos dinámicos del período
tabla["Aplica_Bono_Del"] = tabla["Precio_Total_PVP"].apply(lambda pvp: aplicar_tramo_ventas(pvp, tramos_metas) * 100.0)

tabla["Pct_Rent_Costo"] = np.where(
    tabla["Suma_Costo_Total"] != 0,
    tabla["Suma_Rentabilidad"] / tabla["Suma_Costo_Total"] * 100.0,
    0.0,
)

tabla = tabla.sort_values("Precio_Total_PVP", ascending=False)

# Fila Total
total_pvp = float(tabla["Precio_Total_PVP"].sum())
total_costo = float(tabla["Suma_Costo_Total"].sum())
total_renta = float(tabla["Suma_Rentabilidad"].sum())
total_bono = aplicar_tramo_ventas(total_pvp, tramos_metas) * 100.0
total_pct_rent = (total_renta / total_costo * 100.0) if total_costo != 0 else 0.0

fila_total = pd.DataFrame([{
    "nom_almacen": "Total",
    "Precio_Total_PVP": total_pvp,
    "Suma_Costo_Total": total_costo,
    "Suma_Rentabilidad": total_renta,
    "Aplica_Bono_Del": total_bono,
    "Pct_Rent_Costo": total_pct_rent,
}])
tabla_mostrar = pd.concat([tabla, fila_total], ignore_index=True)

st.dataframe(
    tabla_mostrar.rename(columns={
        "nom_almacen": "Nom Almacen",
        "Precio_Total_PVP": "Precio Total PVP",
        "Aplica_Bono_Del": "Aplica Bono del",
        "Suma_Costo_Total": "Suma de Costo total",
        "Suma_Rentabilidad": "Suma de Rentabilidad",
        "Pct_Rent_Costo": "% Rent (Costo)",
    })[["Nom Almacen", "Precio Total PVP", "Aplica Bono del",
        "Suma de Costo total", "Suma de Rentabilidad", "% Rent (Costo)"]],
    use_container_width=True,
    hide_index=True,
    column_config={
        "Precio Total PVP": st.column_config.NumberColumn(format="$%,.2f"),
        "Aplica Bono del": st.column_config.NumberColumn(format="%.2f%%"),
        "Suma de Costo total": st.column_config.NumberColumn(format="$%,.2f"),
        "Suma de Rentabilidad": st.column_config.NumberColumn(format="$%,.2f"),
        "% Rent (Costo)": st.column_config.NumberColumn(format="%.2f%%"),
    },
)


# ============================================================
# TABLA 2: ANTICIPOS + EXENTAS POR ALMACÉN
# ============================================================
st.markdown("---")
st.subheader("Anticipos y Exentas por Almacén")

df_ant = cargar_bonos_anticipos()

ant_ok = False
ant = pd.DataFrame()

if df_ant.empty or "tipo_factura" not in df_ant.columns:
    st.info(
        "Sin datos de anticipos o falta la columna `tipo_factura`. "
        "Ejecuta **Forzar Sincronización SAP** después de agregar `U_NAL_Tipo_Factura` al query."
    )
else:
    ant = df_ant.copy()
    ant["tipo_factura"] = ant["tipo_factura"].astype(str).str.strip()
    ant = ant[ant["tipo_factura"] == "2"]
    ant = ant[ant["fecha_factura"].notna()]
    ant = ant[(ant["fecha_recibo_pago"].dt.date >= desde) & (ant["fecha_recibo_pago"].dt.date <= hasta)]
    ant = ant[
        ant["fecha_aplicacion"].notna()
        & (ant["fecha_recibo_pago"].dt.to_period("M") != ant["fecha_aplicacion"].dt.to_period("M"))
    ]
    ant["nom_almacen"] = ant["nombre_serie_pago"].map(MAP_SERIE_FACTURA).fillna("OTRO")
    if alm_sel:
        ant = ant[ant["nom_almacen"].isin(alm_sel)]
    ant_ok = True

if ant_ok:
    g_ant = (
        ant.groupby("nom_almacen", as_index=False)["monto_aplicado"]
        .sum()
        .rename(columns={"monto_aplicado": "Suma_Total_Anticipo"})
    )
else:
    g_ant = pd.DataFrame(columns=["nom_almacen", "Suma_Total_Anticipo"])

g_ex = (
    dff.groupby("nom_almacen", as_index=False)["exentas"]
    .sum()
    .rename(columns={"exentas": "Suma_Precio_Total_Exentas"})
)

# 🏦 Aplicar auditoría de Contabilidad: si hay valor corregido, reemplaza el calculado
exentas_auditadas = obtener_exentas_auditadas_por_almacen(str(mes_sel))
g_ex["Suma_Precio_Total_Exentas"] = g_ex.apply(
    lambda r: float(exentas_auditadas.get(r["nom_almacen"], r["Suma_Precio_Total_Exentas"])),
    axis=1,
)

tabla2 = pd.merge(g_ant, g_ex, on="nom_almacen", how="outer").fillna(0.0)
tabla2["Total_Exentas"] = tabla2["Suma_Total_Anticipo"] + tabla2["Suma_Precio_Total_Exentas"]
tabla2 = tabla2.sort_values("Total_Exentas", ascending=False)

total_ant = float(tabla2["Suma_Total_Anticipo"].sum())
total_exf = float(tabla2["Suma_Precio_Total_Exentas"].sum())

fila_total2 = pd.DataFrame([{
    "nom_almacen": "Total",
    "Suma_Total_Anticipo": total_ant,
    "Suma_Precio_Total_Exentas": total_exf,
    "Total_Exentas": total_ant + total_exf,
}])
tabla2_mostrar = pd.concat([tabla2, fila_total2], ignore_index=True)

st.dataframe(
    tabla2_mostrar.rename(columns={
        "nom_almacen": "Nom Almacen",
        "Suma_Total_Anticipo": "Suma de Total Anticipo",
        "Suma_Precio_Total_Exentas": "Suma de Precio total exentas",
        "Total_Exentas": "Total Exentas",
    })[["Nom Almacen", "Suma de Total Anticipo", "Suma de Precio total exentas", "Total Exentas"]],
    use_container_width=True,
    hide_index=True,
    column_config={
        "Suma de Total Anticipo": st.column_config.NumberColumn(format="$%,.2f"),
        "Suma de Precio total exentas": st.column_config.NumberColumn(format="$%,.2f"),
        "Total Exentas": st.column_config.NumberColumn(format="$%,.2f"),
    },
)

# ============================================================
# TABLA 3: LIQUIDACIÓN DEL BONO POR ALMACÉN
# ============================================================
st.markdown("---")
st.subheader("Liquidación del Bono por Almacén")

# --- Guard: verificar que haya datos de pagos antes de continuar ---
df_pagos = cargar_bonos_pagos()
if df_pagos.empty or "estado_comision" not in df_pagos.columns:
    st.warning("⚠️ Sin datos de pagos. Ejecuta **Forzar Sincronización SAP** o verifica que **Variables del Bono** esté inicializado.")
    st.stop()

# --- Valor Bono conciliado por almacén: misma fuente que "Detalle Bono" ▸
# "Distribución por Punto de Venta" (columna "Total Bono"), respetando los
# filtros de mes/almacén de este módulo. Única fuente de verdad: src/conexion_db.py.
distrib_bono = calcular_distribucion_bono_almacen(desde, hasta, alm_sel)

# --- Ensamble ---
t3 = tabla[["nom_almacen", "Precio_Total_PVP", "Suma_Costo_Total", "Suma_Rentabilidad", "Pct_Rent_Costo"]].copy()
t3 = t3.merge(
    tabla2[["nom_almacen", "Suma_Total_Anticipo", "Suma_Precio_Total_Exentas"]],
    on="nom_almacen", how="left",
).fillna(0.0)
t3["total_exentas"] = t3["Suma_Total_Anticipo"] + t3["Suma_Precio_Total_Exentas"]
t3 = t3.merge(
    distrib_bono.rename(columns={
        "liq_pagos": "pagos_liq",
        "liq_anticipos": "ant_liq",
        "descuento_liq": "desc_liq",
        "Total_Bono": "Valor_Bono",
    }),
    left_on="nom_almacen", right_on="nombre_almacen", how="left",
).drop(columns=["nombre_almacen"], errors="ignore")
t3[["pagos_liq", "ant_liq", "desc_liq", "Valor_Bono"]] = (
    t3[["pagos_liq", "ant_liq", "desc_liq", "Valor_Bono"]].fillna(0.0)
)

# % Exentas y DESC EXENTAS (con factor dinámico del período)
factor_desc_exentas = float(periodo_general["factor_desc_exentas"])
t3["Pct_Exentas"] = np.where(t3["Precio_Total_PVP"] != 0, t3["total_exentas"] / t3["Precio_Total_PVP"], 0.0)
t3["Desc_Exentas"] = t3["Pct_Exentas"] * factor_desc_exentas

# 🏦 APLICAR DECISIONES DE CONTABILIDAD (si existen)
from src.conexion_db import obtener_decision_contabilidad
periodo_mes_str = mes_sel.strftime("%Y-%m") if hasattr(mes_sel, 'strftime') else str(mes_sel)
for idx, row in t3.iterrows():
    dec_cont = obtener_decision_contabilidad(periodo_mes_str, row["nom_almacen"], "")
    if dec_cont is not None:
        t3.at[idx, "Desc_Exentas"] = dec_cont

# % Bono (con escala dinámica del período)
r = t3["Pct_Rent_Costo"] / 100.0
t3["Pct_Bono"] = r.apply(lambda rent: aplicar_escala_rent(rent, rangos_escala))

# BONO TOTAL
t3["Bono_Total"] = np.where(
    t3["Pct_Bono"] >= 0,
    t3["Valor_Bono"] * t3["Pct_Bono"],
    t3["Valor_Bono"] + (t3["Valor_Bono"] * t3["Pct_Bono"]),
)

# % Meta Adicional (con parámetros dinámicos del período)
meta_base = float(periodo_general["meta_adic_base"])
meta_bloque = float(periodo_general["meta_adic_bloque"])
meta_pct = float(periodo_general["meta_adic_pct"])
dif_meta = t3["Precio_Total_PVP"] - meta_base
t3["Pct_Meta_Adicional"] = np.where(dif_meta > 0, np.trunc(dif_meta / meta_bloque) * meta_pct, 0.0)

# BONO FINAL (con tramos dinámicos)
t3["aplica"] = t3["Precio_Total_PVP"].apply(lambda pvp: aplicar_tramo_ventas(pvp, tramos_metas))
t3["Bono_Final"] = np.where(t3["aplica"] != 0, (1.0 + t3["Pct_Meta_Adicional"]) * t3["Bono_Total"], 0.0)

t3 = t3.sort_values("Valor_Bono", ascending=False)

# --- Fila Total ---
tp = float(t3["Precio_Total_PVP"].sum())
tc = float(t3["Suma_Costo_Total"].sum())
tr = float(t3["Suma_Rentabilidad"].sum())
te = float(t3["total_exentas"].sum())
tv = float(t3["Valor_Bono"].sum())
r_tot = (tr / tc) if tc != 0 else 0.0
pct_bono_tot = aplicar_escala_rent(r_tot, rangos_escala)
bono_total_tot = tv * pct_bono_tot if pct_bono_tot >= 0 else tv + tv * pct_bono_tot
dif_tot = tp - meta_base
meta_tot = np.trunc(dif_tot / meta_bloque) * meta_pct if dif_tot > 0 else 0.0
aplica_tot = aplicar_tramo_ventas(tp, tramos_metas)
bono_final_tot = (1.0 + meta_tot) * bono_total_tot if aplica_tot != 0 else 0.0

fila_total3 = pd.DataFrame([{
    "nom_almacen": "Total",
    "Pct_Exentas": te / tp if tp != 0 else 0.0,
    "Desc_Exentas": (te / tp if tp != 0 else 0.0) * factor_desc_exentas,
    "Valor_Bono": tv,
    "Pct_Bono": pct_bono_tot,
    "Bono_Total": bono_total_tot,
    "Pct_Meta_Adicional": meta_tot,
    "Bono_Final": bono_final_tot,
}])

t3_mostrar = pd.concat([t3, fila_total3], ignore_index=True)
for col in ["Pct_Exentas", "Desc_Exentas", "Pct_Bono", "Pct_Meta_Adicional"]:
    t3_mostrar[col] = t3_mostrar[col] * 100.0

st.dataframe(
    t3_mostrar.rename(columns={
        "nom_almacen": "Nom Almacen",
        "Pct_Exentas": "% Exentas",
        "Desc_Exentas": "DESC EXENTAS",
        "Valor_Bono": "Valor Bono",
        "Pct_Bono": "% Bono",
        "Bono_Total": "BONO TOTAL",
        "Pct_Meta_Adicional": "% Meta Adicional",
        "Bono_Final": "BONO FINAL",
    })[["Nom Almacen", "% Exentas", "DESC EXENTAS", "Valor Bono",
        "% Bono", "BONO TOTAL", "% Meta Adicional", "BONO FINAL"]],
    use_container_width=True,
    hide_index=True,
    column_config={
        "% Exentas": st.column_config.NumberColumn(format="%.2f%%"),
        "DESC EXENTAS": st.column_config.NumberColumn(format="%.2f%%"),
        "Valor Bono": st.column_config.NumberColumn(format="$%,.2f"),
        "% Bono": st.column_config.NumberColumn(format="%.2f%%"),
        "BONO TOTAL": st.column_config.NumberColumn(format="$%,.2f"),
        "% Meta Adicional": st.column_config.NumberColumn(format="%.2f%%"),
        "BONO FINAL": st.column_config.NumberColumn(format="$%,.2f"),
    },
)

# --- Auditoría del Valor Bono ---
with st.expander("🔎 Descomposición del Valor Bono por almacén"):
    st.dataframe(
        t3[["nom_almacen", "pagos_liq", "ant_liq", "desc_liq", "Valor_Bono"]].rename(columns={
            "nom_almacen": "Almacén",
            "pagos_liq": "Liq. Pagos",
            "ant_liq": "Liq. Anticipos",
            "desc_liq": "Desc. Consolidados",
            "Valor_Bono": "Valor Bono",
        }),
        use_container_width=True, hide_index=True,
        column_config={
            "Liq. Pagos": st.column_config.NumberColumn(format="$%,.2f"),
            "Liq. Anticipos": st.column_config.NumberColumn(format="$%,.2f"),
            "Desc. Consolidados": st.column_config.NumberColumn(format="$%,.2f"),
            "Valor Bono": st.column_config.NumberColumn(format="$%,.2f"),
        },
    )









# Los porcentajes se guardan como fracción; convertir a porcentaje para mostrar
# (se aplica sobre la copia mostrada, no altera el cálculo)