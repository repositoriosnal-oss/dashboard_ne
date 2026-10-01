import streamlit as st
import plotly.express as px
import pandas as pd
import numpy as np
from datetime import date, timedelta
from src.conexion_db import (
    conn, 
    cargar_inventario_articulos,
    sincronizar_rotacion_articulos,     # 🆕 TEMPORAL: para botón manual
    sincronizar_inventario_articulos,  # 🆕 TEMPORAL: para botón manual
)

# ============================================================
# CONFIGURACIÓN DE PÁGINA
# ============================================================
st.set_page_config(
    page_title="Rotación de Artículos",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="expanded",
)

# 🔐 CONTROL DE ACCESO: solo compras, gerencia y admin
ROLES_CON_ACCESO_COMPRAS = ["compras", "gerente", "admin"]
if st.session_state.get("rol_actual") not in ROLES_CON_ACCESO_COMPRAS:
    st.error("⛔ Acceso denegado. El módulo de Rotación de Artículos está disponible únicamente para el rol de Compras, Gerencia y Administrador.")
    st.stop()

# ============================================================
# CSS GLOBAL COMPACTO
# ============================================================
st.markdown("""
<style>
div[data-testid="stAppViewBlockContainer"] {
    padding-top: 1rem !important;
    padding-bottom: 0.5rem !important;
    max-width: 1400px !important;
}
.stVerticalBlock { gap: 0.5rem !important; }
div[data-testid="stMetricLabel"] {
    font-size: 0.95rem !important; font-weight: 600 !important; color: #555 !important;
}
div[data-testid="stMetricValue"] {
    font-size: 1.5rem !important; font-weight: 700 !important; color: #1f77b4 !important;
}
</style>
""", unsafe_allow_html=True)

PLOTLY_CONFIG = {"displayModeBar": False, "responsive": True}

MESES_CORTO = {1: "Ene", 2: "Feb", 3: "Mar", 4: "Abr", 5: "May", 6: "Jun",
               7: "Jul", 8: "Ago", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dic"}

# ✅ CONVENCIÓN (SUPUESTO A1): mes de 30 días para ventas diarias
DIAS_MES = 30
DIAS_STOCK_MIN = 15
DIAS_STOCK_MAX = 45

# 🆕 Estados de stock para filtro
ESTADOS_STOCK = ["🔴 Bajo mínimo", "🟢 En rango", "🟡 Sobre stock"]

# ============================================================
# CARGAS CACHEADAS (SQL agrega → rápido y liviano)
# ============================================================
@st.cache_data(ttl=3600, show_spinner="Cargando dimensiones...")
def cargar_dimensiones():
    df_fechas = conn.query("""
        SELECT MIN(fecha_contabilizacion)::date AS f_min,
               MAX(fecha_contabilizacion)::date AS f_max
        FROM sap_raw.rotacion_articulos
        WHERE fecha_contabilizacion >= '2025-04-01'
    """)
    df_almacenes = conn.query("""
        SELECT DISTINCT nombre_almacen FROM sap_raw.rotacion_articulos
        ORDER BY nombre_almacen
    """)
    df_lineas = conn.query("""
        SELECT DISTINCT nombre_linea FROM sap_raw.rotacion_articulos
        ORDER BY nombre_linea
    """)
    df_articulos = conn.query("""
        SELECT DISTINCT codigo_articulo FROM sap_raw.rotacion_articulos
        ORDER BY codigo_articulo
    """)
    return df_fechas, df_almacenes, df_lineas, df_articulos


@st.cache_data(ttl=3600, show_spinner="Calculando agregados...")
def cargar_rotacion_agregada(f_inicio, f_fin_excl, almacenes, lineas, articulos):
    """
    Fase 3: ahora devuelve 3 dataframes:
    - df_mensual: agregado por (anio, mes) para gráfico año vs año
    - df_articulo: agregado por (codigo_articulo, anio, mes) para promedios globales
    - df_articulo_almacen: agregado por (codigo_articulo, nombre_almacen, anio, mes) para Fase 3
    """
    condiciones = ["fecha_contabilizacion >= :f_ini", "fecha_contabilizacion < :f_fin"]
    params = {"f_ini": f_inicio, "f_fin": f_fin_excl}
    if almacenes:
        condiciones.append("nombre_almacen = ANY(:almacenes)")
        params["almacenes"] = list(almacenes)
    if lineas:
        condiciones.append("nombre_linea = ANY(:lineas)")
        params["lineas"] = list(lineas)
    if articulos:
        condiciones.append("codigo_articulo = ANY(:articulos)")
        params["articulos"] = list(articulos)
    where = " AND ".join(condiciones)

    # Query 1: agregado por (anio, mes) - para gráfico comparativo
    q_mensual = f"""
        SELECT EXTRACT(YEAR FROM fecha_contabilizacion)::int AS anio,
               EXTRACT(MONTH FROM fecha_contabilizacion)::int AS mes,
               COALESCE(SUM(cantidad), 0) AS unidades
        FROM sap_raw.rotacion_articulos
        WHERE {where}
        GROUP BY 1, 2
        ORDER BY 1, 2
    """
    
    # Query 2: agregado por (codigo_articulo, anio, mes) - para promedios globales
    q_articulo = f"""
        SELECT codigo_articulo,
               MAX(nombre_articulo) AS nombre_articulo,
               MAX(nombre_linea) AS nombre_linea,
               EXTRACT(YEAR FROM fecha_contabilizacion)::int AS anio,
               EXTRACT(MONTH FROM fecha_contabilizacion)::int AS mes,
               COALESCE(SUM(cantidad), 0) AS unidades
        FROM sap_raw.rotacion_articulos
        WHERE {where}
        GROUP BY codigo_articulo,
                 EXTRACT(YEAR FROM fecha_contabilizacion)::int,
                 EXTRACT(MONTH FROM fecha_contabilizacion)::int
    """
    
    # Query 3: agregado por (codigo_articulo, nombre_almacen, anio, mes) - para Fase 3
    q_articulo_almacen = f"""
        SELECT codigo_articulo,
               MAX(nombre_articulo) AS nombre_articulo,
               MAX(nombre_linea) AS nombre_linea,
               nombre_almacen,
               EXTRACT(YEAR FROM fecha_contabilizacion)::int AS anio,
               EXTRACT(MONTH FROM fecha_contabilizacion)::int AS mes,
               COALESCE(SUM(cantidad), 0) AS unidades
        FROM sap_raw.rotacion_articulos
        WHERE {where}
        GROUP BY codigo_articulo,
                 nombre_almacen,
                 EXTRACT(YEAR FROM fecha_contabilizacion)::int,
                 EXTRACT(MONTH FROM fecha_contabilizacion)::int
    """
    
    df_mensual = conn.query(q_mensual, params=params)
    df_articulo = conn.query(q_articulo, params=params)
    df_articulo_almacen = conn.query(q_articulo_almacen, params=params)
    
    return df_mensual, df_articulo, df_articulo_almacen


def promedio_sin_picos(serie_unidades, n):
    """Elimina los n meses con mayor venta y promedia el resto."""
    if len(serie_unidades) <= n:
        return np.nan
    return serie_unidades.sort_values(ascending=False).iloc[n:].mean()


# ============================================================
# ENCABEZADO
# ============================================================
st.title("Rotación de Artículos")
st.caption("Ventas en unidades")

# ============================================================
# DIMENSIONES Y DATOS
# ============================================================
df_fechas, df_almacenes, df_lineas, df_articulos_dim = cargar_dimensiones()

if df_fechas.empty or df_fechas["f_min"].iloc[0] is None:
    st.warning("No hay datos de rotación aún.")
    st.stop()

f_min = df_fechas["f_min"].iloc[0]
f_max = df_fechas["f_max"].iloc[0]

# Inventario (snapshot) + filtros compartidos (almacén/línea/artículo, SIN fecha)
df_inv = cargar_inventario_articulos()

# ============================================================
# FILTROS LATERALES
# ============================================================
with st.sidebar:
    st.markdown("Filtros de Rotación")

    st.markdown("**📅 Filtrar por rango de fechas:**")
    col_f1, col_f2 = st.columns(2)
    with col_f1:
        fecha_inicio = st.date_input("Desde:", value=f_min, min_value=f_min, max_value=f_max, key="sb_rot_fini")
    with col_f2:
        fecha_fin = st.date_input("Hasta:", value=f_max, min_value=f_min, max_value=f_max, key="sb_rot_ffin")

#cambio aplicado descomentar si falla
#    alm_disp = df_almacenes["nombre_almacen"].tolist()
#    alm_sel = st.multiselect("Almacén(es):", alm_disp, default=alm_disp, key="sb_rot_alm")

# Si falla borrar desde aqui
    # ✅ Unión de almacenes: ventas + inventario (para que el GENERAL cuadre con SAP/Power BI)
    alm_ventas = set(df_almacenes["nombre_almacen"].dropna().tolist())
    alm_inv = set(df_inv["nombre_almacen"].dropna().tolist()) if not df_inv.empty else set()
    alm_disp = sorted(alm_ventas | alm_inv)
    alm_sel = st.multiselect("Almacén(es):", alm_disp, default=alm_disp, key="sb_rot_alm")
# Borrar hasta aqui si falla

    lin_disp = df_lineas["nombre_linea"].tolist()
    lin_sel = st.multiselect("Línea(s):", lin_disp, default=lin_disp, key="sb_rot_lin")

    art_disp = df_articulos_dim["codigo_articulo"].tolist()
    art_sel = st.multiselect("Artículo(s) por código:", art_disp, default=[], key="sb_rot_art")
    st.caption("💡 Sin artículos seleccionados = todos.")

    st.markdown("---")
    picos_sel = st.selectbox("Picos a excluir del promedio:", [1, 2, 3, 4], index=0, key="sb_rot_picos")
    
    # 🆕 NUEVO FILTRO: Estado Stock
    estado_sel = st.multiselect(
        "Estado Stock:",
        ESTADOS_STOCK,
        default=ESTADOS_STOCK,
        key="sb_rot_estado",
        help="Filtra la tabla y alertas por el estado del stock actual"
    )

    # ============================================================
    # 🆕 TEMPORAL: Botón de sincronización manual
    # QUITAR este bloque cuando se activen las tareas programadas
    # ============================================================
    if st.session_state.get("rol_actual") in ["gerente", "admin"]:
        st.markdown("---")
        st.markdown("##### 🔄 Sincronización de Rotación")
    #    st.caption("Actualiza manualmente ventas e inventario (reemplaza al Programador de Tareas hasta su activación)")
        if st.button(
            "🔄 Actualizar Rotación + Inventario",
            icon=":material/sync:",
            type="primary",
            use_container_width=True,
            key="btn_sync_rotacion_manual",
        ):
            with st.spinner("Sincronizando rotación e inventario desde SAP..."):
                ok1 = sincronizar_rotacion_articulos()
                ok2 = sincronizar_inventario_articulos()
            if ok1 and ok2:
                st.cache_data.clear()
                st.sidebar.success("✅ Rotación e inventario actualizados")
                st.rerun()
            else:
                st.sidebar.error("❌ Error en la sincronización. Revisa la terminal.")
    # ============================================================
    # FIN BLOQUE TEMPORAL
    # ============================================================

if not (isinstance(fecha_inicio, date) and isinstance(fecha_fin, date)) or fecha_inicio > fecha_fin:
    st.warning("Selecciona un rango de fechas válido (Desde no puede ser mayor que Hasta).")
    st.stop()

if not alm_sel or not lin_sel:
    st.warning("Selecciona al menos un almacén y una línea.")
    st.stop()

# Inventario filtrado por las mismas dimensiones (sin fecha: es snapshot)
inv_filtrado = df_inv.copy() if not df_inv.empty else df_inv
if not inv_filtrado.empty:
    inv_filtrado = inv_filtrado[inv_filtrado["nombre_almacen"].isin(alm_sel)]
    inv_filtrado = inv_filtrado[inv_filtrado["nombre_linea"].isin(lin_sel)]
    if art_sel:
        inv_filtrado = inv_filtrado[inv_filtrado["codigo_articulo"].isin(art_sel)]

# ============================================================
# CARGA AGREGADA DE VENTAS (Fase 3: ahora devuelve 3 dataframes)
# ============================================================
f_ini_str = fecha_inicio.isoformat()
f_fin_excl_str = (fecha_fin + timedelta(days=1)).isoformat()

df_mensual, df_articulo, df_articulo_almacen = cargar_rotacion_agregada(
    f_inicio=f_ini_str, f_fin_excl=f_fin_excl_str,
    almacenes=alm_sel, lineas=lin_sel, articulos=art_sel if art_sel else None,
)

if df_mensual.empty:
    st.warning("No hay datos con los filtros seleccionados.")
    st.stop()

# ============================================================
# KPIs FILA 1: VENTAS
# ============================================================
total_unidades = float(df_mensual["unidades"].sum())
num_meses = len(df_mensual)
promedio_mensual = total_unidades / num_meses if num_meses else 0.0
prom_sin_pico = promedio_sin_picos(df_mensual["unidades"], picos_sel)

kpi_cols = st.columns(3)
with kpi_cols[0].container(border=True, height=130):
    st.metric("Total Unidades Vendidas", f"{total_unidades:,.0f}")
with kpi_cols[1].container(border=True, height=130):
    st.metric("Promedio Unidades / Mes", f"{promedio_mensual:,.0f}",
              help=f"Calculado sobre {num_meses} mes(es) del rango seleccionado")
with kpi_cols[2].container(border=True, height=130):
    st.metric(f"Promedio Sin {picos_sel} Pico(s)",
              f"{prom_sin_pico:,.0f}" if not np.isnan(prom_sin_pico) else "—",
              help="Excluye el/los mes(es) con mayor venta y promedia el resto")

# ============================================================
# 🆕 RESTAURADO: KPIs FILA 2: INVENTARIO + COBERTURA (desde inv_filtrado, SIN filtro de Estado)
# ============================================================
inv_onhand = float(inv_filtrado["on_hand"].sum()) if not inv_filtrado.empty else 0.0
inv_disp = float(inv_filtrado["disponible"].sum()) if not inv_filtrado.empty else 0.0
inv_costo = float(inv_filtrado["costo_disponible"].sum()) if not inv_filtrado.empty else 0.0

prom_sin_2_global = promedio_sin_picos(df_mensual["unidades"], 2)
ventas_diarias_global = (prom_sin_2_global / DIAS_MES) if (not np.isnan(prom_sin_2_global) and prom_sin_2_global > 0) else 0.0
cobertura_global = (inv_disp / ventas_diarias_global) if ventas_diarias_global > 0 else np.nan

kpi_cols2 = st.columns(4)
with kpi_cols2[0].container(border=True, height=130):
    st.metric("En Stock (Und)", f"{inv_onhand:,.0f}", help="Suma de OnHand del filtro")
with kpi_cols2[1].container(border=True, height=130):
    st.metric("Disponible (Und)", f"{inv_disp:,.0f}", help="OnHand + OnOrder − Comprometido")
with kpi_cols2[2].container(border=True, height=130):
    st.metric("Costo Disponible", f"${inv_costo:,.0f}", help="Costo del artículo × Disponible")
with kpi_cols2[3].container(border=True, height=130):
    st.metric("Cobertura (días)", f"{cobertura_global:,.1f}" if not np.isnan(cobertura_global) else "—",
              help="Disponible ÷ ventas diarias (promedio sin 2 picos / 30)")

st.markdown("---")

# ============================================================
# GRÁFICO COMPARATIVO AÑO VS AÑO
# ============================================================
st.subheader("Comparación Mensual por Año (Unidades)")

df_mensual_plot = df_mensual.copy()
df_mensual_plot["mes_txt"] = df_mensual_plot["mes"].map(MESES_CORTO)
df_mensual_plot["anio_txt"] = df_mensual_plot["anio"].astype(str)

fig = px.bar(
    df_mensual_plot,
    x="mes_txt", y="unidades", color="anio_txt",
    barmode="group",
    category_orders={"mes_txt": [MESES_CORTO[k] for k in sorted(MESES_CORTO)]},
    text_auto=",.0f",
)
fig.update_traces(textfont_size=10, textposition="outside")
fig.update_layout(
    plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
    xaxis_title="Mes", yaxis_title="Unidades Vendidas",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    margin=dict(l=20, r=20, t=40, b=60),
    height=500,
)
fig.update_yaxes(tickformat=",.0f")
st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CONFIG)

# ============================================================
# 🆕 TABLA DE INVENTARIO POR PUNTO DE VENTA (con fila GENERAL)
# ============================================================
st.markdown("---")
st.subheader(" Inventario Actual por Punto de Venta")

if inv_filtrado.empty:
    st.info("Sin datos de inventario con los filtros actuales (o ejecuta `etl_rotacion_articulos.py` para crear la tabla).")
else:
    df_inv_alm = inv_filtrado.groupby("nombre_almacen").agg(
        En_Stock=("on_hand", "sum"),
        Comprometido=("comprometido", "sum"),
        Pedido=("on_order", "sum"),
        Disponible=("disponible", "sum"),
        Costo_Disponible=("costo_disponible", "sum"),
    ).reset_index().sort_values("Disponible", ascending=False)

    fila_general = pd.DataFrame([{
        "nombre_almacen": " GENERAL (TODOS)",
        "En_Stock": df_inv_alm["En_Stock"].sum(),
        "Comprometido": df_inv_alm["Comprometido"].sum(),
        "Pedido": df_inv_alm["Pedido"].sum(),
        "Disponible": df_inv_alm["Disponible"].sum(),
        "Costo_Disponible": df_inv_alm["Costo_Disponible"].sum(),
    }])
    df_inv_alm = pd.concat([fila_general, df_inv_alm], ignore_index=True)

    for c in ["En_Stock", "Comprometido", "Pedido", "Disponible"]:
        df_inv_alm[c] = df_inv_alm[c].round(0)
    df_inv_alm["Costo_Disponible"] = df_inv_alm["Costo_Disponible"].round(0)

    st.dataframe(
        df_inv_alm,
        use_container_width=True,
        hide_index=True,
        height=420,
        column_config={
            "En_Stock": st.column_config.NumberColumn(format="%,.0f"),
            "Comprometido": st.column_config.NumberColumn(format="%,.0f"),
            "Pedido": st.column_config.NumberColumn(format="%,.0f"),
            "Disponible": st.column_config.NumberColumn(format="%,.0f"),
            "Costo_Disponible": st.column_config.NumberColumn(format="$%,.0f"),
        },
    )

st.markdown("---")

# ============================================================
# TABLA DETALLADA POR ARTÍCULO Y ALMACÉN (Fase 3)
# ============================================================
# st.subheader("Detalle por Artículo y Almacén (Mín/Máx por Punto de Venta)")

# Fase 3: construir tabla por (artículo, almacén)
df_art_alm = df_articulo_almacen.copy()
df_art_alm = df_art_alm.sort_values(["codigo_articulo", "nombre_almacen", "unidades"], ascending=[True, True, False])
df_art_alm["rk"] = df_art_alm.groupby(["codigo_articulo", "nombre_almacen"]).cumcount() + 1

tabla = df_art_alm.groupby(["codigo_articulo", "nombre_almacen"]).agg(
    Linea=("nombre_linea", "first"),
    Articulo=("nombre_articulo", "first"),
    Meses_Con_Venta=("unidades", "count"),
    Total_Unidades=("unidades", "sum"),
    Promedio_Total=("unidades", "mean"),
).reset_index()

for n in (1, 2, 3, 4):
    s = (df_art_alm[df_art_alm["rk"] > n]
         .groupby(["codigo_articulo", "nombre_almacen"])["unidades"]
         .mean()
         .rename(f"Prom_Sin_{n}_Pico"))
    tabla = tabla.merge(s, left_on=["codigo_articulo", "nombre_almacen"], right_index=True, how="left")
    tabla[f"Prom_Sin_{n}_Pico"] = tabla[f"Prom_Sin_{n}_Pico"].fillna(0).round(1)

# Merge con inventario por (artículo, almacén)
if not inv_filtrado.empty:
    inv_item = inv_filtrado.groupby(["codigo_articulo", "nombre_almacen"]).agg(
        Disponible=("disponible", "sum"),
        En_Stock=("on_hand", "sum"),
        Costo_Und=("costo_articulo", "first"),
        Costo_Disponible=("costo_disponible", "sum"),
        Unid_Empaque=("unid_empaque_pro", "max"),
        Unid_Paquete=("unid_paquete", "max"),
    ).reset_index()
    tabla = tabla.merge(inv_item, on=["codigo_articulo", "nombre_almacen"], how="left")
else:
    for c in ["Disponible", "En_Stock", "Costo_Und", "Costo_Disponible", "Unid_Empaque", "Unid_Paquete"]:
        tabla[c] = np.nan

tabla[["Disponible", "En_Stock", "Costo_Disponible"]] = tabla[["Disponible", "En_Stock", "Costo_Disponible"]].fillna(0)
tabla["Costo_Und"] = tabla["Costo_Und"].fillna(0).round(2)
tabla["Unid_Empaque"] = tabla["Unid_Empaque"].fillna(0)
tabla["Unid_Paquete"] = tabla["Unid_Paquete"].fillna(0)

# Derivados estratégicos (Fase 3: por almacén)
tabla["Ventas_Diarias"] = (tabla["Prom_Sin_2_Pico"] / DIAS_MES).round(2)
tabla["Stock_Min"] = (tabla["Ventas_Diarias"] * DIAS_STOCK_MIN).round(0)
tabla["Stock_Max"] = (tabla["Ventas_Diarias"] * DIAS_STOCK_MAX).round(0)
tabla["Faltante_vs_Max"] = (tabla["Stock_Max"] - tabla["Disponible"]).round(0)

def _sugerencia(row):
    emp = row["Unid_Empaque"] if row["Unid_Empaque"] > 0 else (row["Unid_Paquete"] if row["Unid_Paquete"] > 0 else 1)
    falt = max(0.0, row["Faltante_vs_Max"])
    return round(falt / emp) * emp

tabla["Sugerencia_Compra"] = tabla.apply(_sugerencia, axis=1).round(0)
tabla["Dias_Cobertura"] = np.where(
    tabla["Ventas_Diarias"] > 0,
    (tabla["Disponible"] / tabla["Ventas_Diarias"]).round(1),
    np.nan,
)

def _estado_stock(row):
    if row["Disponible"] < row["Stock_Min"]:
        return "🔴 Bajo mínimo"
    if row["Disponible"] > row["Stock_Max"]:
        return "🟡 Sobre stock"
    return "🟢 En rango"

tabla["Estado_Stock"] = tabla.apply(_estado_stock, axis=1)

# Cálculos financieros
tabla["Costo_Sobre_Stock"] = (
    np.maximum(0, tabla["Disponible"] - tabla["Stock_Max"]) * tabla["Costo_Und"]
).round(0)
tabla["Costo_Deficit"] = (
    np.maximum(0, tabla["Stock_Min"] - tabla["Disponible"]) * tabla["Costo_Und"]
).round(0)

tabla["Total_Unidades"] = tabla["Total_Unidades"].round(0)

# 🆕 DIAGNÓSTICO: mostrar cuántas filas hay antes y después del filtro
filas_antes = len(tabla)

# Aplicar filtro de Estado Stock SOLO para visualización (no para cálculos)
tabla_mostrar = tabla.copy()
if estado_sel and len(estado_sel) < len(ESTADOS_STOCK):
    tabla_mostrar = tabla_mostrar[tabla_mostrar["Estado_Stock"].isin(estado_sel)].copy()

filas_despues = len(tabla_mostrar)

# Mensaje de diagnóstico si hay diferencia grande
if filas_antes > 0 and filas_despues == 0:
    st.warning(f"⚠️ El filtro de Estado Stock eliminó todas las {filas_antes} filas. Prueba seleccionando todos los estados.")
elif filas_antes == 0:
    st.warning("No hay datos de ventas por artículo×almacén en el rango seleccionado.")
else:
    tabla_mostrar = tabla_mostrar.sort_values("Total_Unidades", ascending=False)

    # ============================================================
    # 🚨 ALERTAS ESTRATÉGICAS DE INVENTARIO
    # ============================================================
    st.subheader("Alertas de Inventario")

    total_costo_sobre = float(tabla_mostrar["Costo_Sobre_Stock"].sum())
    total_costo_deficit = float(tabla_mostrar["Costo_Deficit"].sum())
    count_sobre = int((tabla_mostrar["Estado_Stock"] == "🟡 Sobre stock").sum())
    count_bajo_min = int((tabla_mostrar["Estado_Stock"] == "🔴 Bajo mínimo").sum())
    count_en_rango = int((tabla_mostrar["Estado_Stock"] == "🟢 En rango").sum())

    col_top_deficit, col_top_sobre = st.columns(2)

    with col_top_deficit:
        st.markdown("##### 🔴 TOP 15 — Mayor Déficit")
        df_deficit = (
            tabla_mostrar[tabla_mostrar["Costo_Deficit"] > 0]
            .sort_values("Costo_Deficit", ascending=False)
            .head(15)
        )
        if df_deficit.empty:
            st.success("✅ No hay artículos con déficit en los filtros actuales.")
        else:
            df_deficit_show = df_deficit[[
                "codigo_articulo", "nombre_almacen",
                "Linea", "Articulo", "Disponible", "Stock_Min",
                "Costo_Und", "Costo_Deficit", "Sugerencia_Compra"
            ]].copy()
            df_deficit_show["Faltan Und"] = (df_deficit_show["Stock_Min"] - df_deficit_show["Disponible"]).round(0)
            st.dataframe(
                df_deficit_show.rename(columns={
                    "codigo_articulo": "Código",
                    "nombre_almacen": "Almacén",
                    "Linea": "Línea", "Articulo": "Artículo",
                    "Disponible": "Disp.", "Stock_Min": "Mín",
                    "Costo_Und": "Costo Und", "Costo_Deficit": "Costo Déficit",
                    "Sugerencia_Compra": "Suger. Compra",
                }),
                use_container_width=True, hide_index=True, height=420,
                column_config={
                    "Código": st.column_config.TextColumn(width="small"),
                    "Almacén": st.column_config.TextColumn(width="small"),
                    "Disp.": st.column_config.NumberColumn(format="%,.0f"),
                    "Mín": st.column_config.NumberColumn(format="%,.0f"),
                    "Faltan Und": st.column_config.NumberColumn(format="%,.0f"),
                    "Costo Und": st.column_config.NumberColumn(format="$%,.2f"),
                    "Costo Déficit": st.column_config.NumberColumn(format="$%,.0f"),
                    "Suger. Compra": st.column_config.NumberColumn(format="%,.0f"),
                },
            )

    with col_top_sobre:
        st.markdown("##### 🟡 TOP 15 — Mayor Sobre-Stock")
        df_sobre = (
            tabla_mostrar[tabla_mostrar["Costo_Sobre_Stock"] > 0]
            .sort_values("Costo_Sobre_Stock", ascending=False)
            .head(15)
        )
        if df_sobre.empty:
            st.success("✅ No hay artículos con sobre-stock en los filtros actuales.")
        else:
            df_sobre_show = df_sobre[[
                "codigo_articulo", "nombre_almacen",
                "Linea", "Articulo", "Disponible", "Stock_Max",
                "Costo_Und", "Costo_Sobre_Stock"
            ]].copy()
            df_sobre_show["Exceso Und"] = (df_sobre_show["Disponible"] - df_sobre_show["Stock_Max"]).round(0)
            st.dataframe(
                df_sobre_show.rename(columns={
                    "codigo_articulo": "Código",
                    "nombre_almacen": "Almacén",
                    "Linea": "Línea", "Articulo": "Artículo",
                    "Disponible": "Disp.", "Stock_Max": "Máx",
                    "Costo_Und": "Costo Und", "Costo_Sobre_Stock": "Costo Exceso",
                }),
                use_container_width=True, hide_index=True, height=420,
                column_config={
                    "Código": st.column_config.TextColumn(width="small"),
                    "Almacén": st.column_config.TextColumn(width="small"),
                    "Disp.": st.column_config.NumberColumn(format="%,.0f"),
                    "Máx": st.column_config.NumberColumn(format="%,.0f"),
                    "Exceso Und": st.column_config.NumberColumn(format="%,.0f"),
                    "Costo Und": st.column_config.NumberColumn(format="$%,.2f"),
                    "Costo Exceso": st.column_config.NumberColumn(format="$%,.0f"),
                },
            )

    st.markdown("---")

    # ============================================================
    # TABLA DETALLADA FINAL
    # ============================================================
    st.subheader("Tabla detalle por Almacén")

    st.dataframe(
        tabla_mostrar.rename(columns={
            "codigo_articulo": "Código",
            "nombre_almacen": "Almacén",
            "Linea": "Línea",
            "Articulo": "Artículo",
            "Meses_Con_Venta": "Meses_Con_Ventas",
            "Total_Unidades": "Total Unidades",
            "Promedio_Total": "Promedio Total",
            "Prom_Sin_1_Pico": "Prom. Sin 1 Pico",
            "Prom_Sin_2_Pico": "Prom. Sin 2 Picos",
            "Prom_Sin_3_Pico": "Prom. Sin 3 Picos",
            "Prom_Sin_4_Pico": "Prom. Sin 4 Picos",
            "En_Stock": "En_Stock",
            "Disponible": "Disponible",
            "Costo_Und": "Costo Und",
            "Costo_Disponible": "Costo Disponible",
            "Ventas_Diarias": "Vta Diaria",
            "Stock_Min": "Stock Mín (15d)",
            "Stock_Max": "Stock Máx (45d)",
            "Faltante_vs_Max": "Faltante vs Máx",
            "Costo_Sobre_Stock": "Costo Sobre-Stock",
            "Costo_Deficit": "Costo Déficit",
            "Sugerencia_Compra": "Sugerencia Compra",
            "Dias_Cobertura": "Cobertura (días)",
            "Estado_Stock": "Estado Stock",
        }),
        use_container_width=True,
        hide_index=True,
        height=520,
        column_config={
            "Código": st.column_config.TextColumn(width="small"),
            "Almacén": st.column_config.TextColumn(width="small"),
            "Total Unidades": st.column_config.NumberColumn(format="%,.0f"),
            "Promedio Total": st.column_config.NumberColumn(format="%,.1f"),
            "Prom. Sin 1 Pico": st.column_config.NumberColumn(format="%,.1f"),
            "Prom. Sin 2 Picos": st.column_config.NumberColumn(format="%,.1f"),
            "Prom. Sin 3 Picos": st.column_config.NumberColumn(format="%,.1f"),
            "Prom. Sin 4 Picos": st.column_config.NumberColumn(format="%,.1f"),
            "En_Stock": st.column_config.NumberColumn(format="%,.0f"),
            "Disponible": st.column_config.NumberColumn(format="%,.0f"),
            "Costo Und": st.column_config.NumberColumn(format="$%,.2f"),
            "Costo Disponible": st.column_config.NumberColumn(format="$%,.0f"),
            "Vta Diaria": st.column_config.NumberColumn(format="%,.2f"),
            "Stock Mín (15d)": st.column_config.NumberColumn(format="%,.0f"),
            "Stock Máx (45d)": st.column_config.NumberColumn(format="%,.0f"),
            "Faltante vs Máx": st.column_config.NumberColumn(format="%,.0f"),
            "Costo Sobre-Stock": st.column_config.NumberColumn(format="$%,.0f"),
            "Costo Déficit": st.column_config.NumberColumn(format="$%,.0f"),
            "Sugerencia Compra": st.column_config.NumberColumn(format="%,.0f"),
            "Cobertura (días)": st.column_config.NumberColumn(format="%,.1f"),
        },
    )

# ============================================================
# TABLA GENERAL POR ARTÍCULO: ACUMULADO SIN CLASIFICAR POR ALMACÉN
# ============================================================
st.markdown("---")
st.subheader("Detalle General por Artículo")

# Esta tabla consolida el total por artículo, sin desagregar por almacén.
# Respeta los filtros actuales de fecha, almacén, línea, artículo y estado stock.

df_art_gen = df_articulo.copy()

if df_art_gen.empty:
    st.info("No hay datos generales por artículo con los filtros actuales.")
else:
    df_art_gen = df_art_gen.sort_values(["codigo_articulo", "unidades"], ascending=[True, False])
    df_art_gen["rk"] = df_art_gen.groupby("codigo_articulo").cumcount() + 1

    tabla_general = df_art_gen.groupby("codigo_articulo").agg(
        Linea=("nombre_linea", "first"),
        Articulo=("nombre_articulo", "first"),
        Meses_Con_Venta=("unidades", "count"),
        Total_Unidades=("unidades", "sum"),
        Promedio_Total=("unidades", "mean"),
    ).reset_index()

    # Promedios sin picos por artículo, sin clasificar por almacén
    for n in (1, 2, 3, 4):
        s = (
            df_art_gen[df_art_gen["rk"] > n]
            .groupby("codigo_articulo")["unidades"]
            .mean()
            .rename(f"Prom_Sin_{n}_Pico")
        )
        tabla_general = tabla_general.merge(
            s,
            left_on="codigo_articulo",
            right_index=True,
            how="left"
        )
        tabla_general[f"Prom_Sin_{n}_Pico"] = tabla_general[f"Prom_Sin_{n}_Pico"].fillna(0).round(1)

    # Inventario consolidado por artículo, sin clasificar por almacén
    if not inv_filtrado.empty:
        inv_item_general = inv_filtrado.groupby("codigo_articulo").agg(
            Disponible=("disponible", "sum"),
            En_Stock=("on_hand", "sum"),
            Costo_Und=("costo_articulo", "first"),
            Costo_Disponible=("costo_disponible", "sum"),
            Unid_Empaque=("unid_empaque_pro", "max"),
            Unid_Paquete=("unid_paquete", "max"),
        ).reset_index()

        tabla_general = tabla_general.merge(
            inv_item_general,
            on="codigo_articulo",
            how="left"
        )
    else:
        for c in ["Disponible", "En_Stock", "Costo_Und", "Costo_Disponible", "Unid_Empaque", "Unid_Paquete"]:
            tabla_general[c] = np.nan

    tabla_general[["Disponible", "En_Stock", "Costo_Disponible"]] = tabla_general[
        ["Disponible", "En_Stock", "Costo_Disponible"]
    ].fillna(0)

    tabla_general["Costo_Und"] = tabla_general["Costo_Und"].fillna(0).round(2)
    tabla_general["Unid_Empaque"] = tabla_general["Unid_Empaque"].fillna(0)
    tabla_general["Unid_Paquete"] = tabla_general["Unid_Paquete"].fillna(0)

    # Derivados estratégicos generales por artículo
    tabla_general["Ventas_Diarias"] = (tabla_general["Prom_Sin_2_Pico"] / DIAS_MES).round(2)
    tabla_general["Stock_Min"] = (tabla_general["Ventas_Diarias"] * DIAS_STOCK_MIN).round(0)
    tabla_general["Stock_Max"] = (tabla_general["Ventas_Diarias"] * DIAS_STOCK_MAX).round(0)
    tabla_general["Faltante_vs_Max"] = (tabla_general["Stock_Max"] - tabla_general["Disponible"]).round(0)

    def _sugerencia_general(row):
        emp = row["Unid_Empaque"] if row["Unid_Empaque"] > 0 else (
            row["Unid_Paquete"] if row["Unid_Paquete"] > 0 else 1
        )
        falt = max(0.0, row["Faltante_vs_Max"])
        return round(falt / emp) * emp

    tabla_general["Sugerencia_Compra"] = tabla_general.apply(_sugerencia_general, axis=1).round(0)

    tabla_general["Dias_Cobertura"] = np.where(
        tabla_general["Ventas_Diarias"] > 0,
        (tabla_general["Disponible"] / tabla_general["Ventas_Diarias"]).round(1),
        np.nan,
    )

    def _estado_stock_general(row):
        if row["Disponible"] < row["Stock_Min"]:
            return "🔴 Bajo mínimo"
        if row["Disponible"] > row["Stock_Max"]:
            return "🟡 Sobre stock"
        return "🟢 En rango"

    tabla_general["Estado_Stock"] = tabla_general.apply(_estado_stock_general, axis=1)

    tabla_general["Costo_Sobre_Stock"] = (
        np.maximum(0, tabla_general["Disponible"] - tabla_general["Stock_Max"]) * tabla_general["Costo_Und"]
    ).round(0)

    tabla_general["Costo_Deficit"] = (
        np.maximum(0, tabla_general["Stock_Min"] - tabla_general["Disponible"]) * tabla_general["Costo_Und"]
    ).round(0)

    tabla_general["Total_Unidades"] = tabla_general["Total_Unidades"].round(0)

    # Respetar filtro de Estado Stock del sidebar
    tabla_general_mostrar = tabla_general.copy()
    if estado_sel and len(estado_sel) < len(ESTADOS_STOCK):
        tabla_general_mostrar = tabla_general_mostrar[
            tabla_general_mostrar["Estado_Stock"].isin(estado_sel)
        ].copy()

    if tabla_general_mostrar.empty:
        st.info("No hay artículos generales con el Estado Stock seleccionado.")
    else:
        tabla_general_mostrar = tabla_general_mostrar.sort_values("Total_Unidades", ascending=False)

        st.dataframe(
            tabla_general_mostrar.rename(columns={
                "codigo_articulo": "Código",
                "Linea": "Línea",
                "Articulo": "Artículo",
                "Meses_Con_Venta": "Meses_Con_Ventas",
                "Total_Unidades": "Total Unidades",
                "Promedio_Total": "Promedio Total",
                "Prom_Sin_1_Pico": "Prom. Sin 1 Pico",
                "Prom_Sin_2_Pico": "Prom. Sin 2 Picos",
                "Prom_Sin_3_Pico": "Prom. Sin 3 Picos",
                "Prom_Sin_4_Pico": "Prom. Sin 4 Picos",
                "Disponible": "Disponible",
                "En_Stock": "En_Stock",
                "Costo_Und": "Costo Und",
                "Costo_Disponible": "Costo Disponible",
                "Unid_Empaque": "Unidad Empaque",
                "Unid_Paquete": "Unidad Paquete",
                "Ventas_Diarias": "Vta Diaria",
                "Stock_Min": "Stock Mín (15d)",
                "Stock_Max": "Stock Máx (45d)",
                "Faltante_vs_Max": "Faltante vs Máx",
                "Sugerencia_Compra": "Sugerencia Compra",
                "Dias_Cobertura": "Cobertura (días)",
                "Estado_Stock": "Estado Stock",
                "Costo_Sobre_Stock": "Costo Sobre-Stock",
                "Costo_Deficit": "Costo Déficit Stock",
            }),
            use_container_width=True,
            hide_index=True,
            height=520,
            column_config={
                "Código": st.column_config.TextColumn(width="small"),
                "Línea": st.column_config.TextColumn(width="medium"),
                "Artículo": st.column_config.TextColumn(width="large"),
                "Meses_Con_Ventas": st.column_config.NumberColumn(format="%,.0f"),
                "Total Unidades": st.column_config.NumberColumn(format="%,.0f"),
                "Promedio Total": st.column_config.NumberColumn(format="%,.1f"),
                "Prom. Sin 1 Pico": st.column_config.NumberColumn(format="%,.1f"),
                "Prom. Sin 2 Picos": st.column_config.NumberColumn(format="%,.1f"),
                "Prom. Sin 3 Picos": st.column_config.NumberColumn(format="%,.1f"),
                "Prom. Sin 4 Picos": st.column_config.NumberColumn(format="%,.1f"),
                "Disponible": st.column_config.NumberColumn(format="%,.0f"),
                "En_Stock": st.column_config.NumberColumn(format="%,.0f"),
                "Costo Und": st.column_config.NumberColumn(format="$%,.2f"),
                "Costo Disponible": st.column_config.NumberColumn(format="$%,.0f"),
                "Unidad Empaque": st.column_config.NumberColumn(format="%,.0f"),
                "Unidad Paquete": st.column_config.NumberColumn(format="%,.0f"),
                "Vta Diaria": st.column_config.NumberColumn(format="%,.2f"),
                "Stock Mín (15d)": st.column_config.NumberColumn(format="%,.0f"),
                "Stock Máx (45d)": st.column_config.NumberColumn(format="%,.0f"),
                "Faltante vs Máx": st.column_config.NumberColumn(format="%,.0f"),
                "Sugerencia Compra": st.column_config.NumberColumn(format="%,.0f"),
                "Cobertura (días)": st.column_config.NumberColumn(format="%,.1f"),
                "Costo Sobre-Stock": st.column_config.NumberColumn(format="$%,.0f"),
                "Costo Déficit Stock": st.column_config.NumberColumn(format="$%,.0f"),
            },
        )

# st.markdown("---")
# st.caption("📦 Inventario = snapshot (se refresca completo en cada sincronización) · Ventas desde el 01/04/2025 · "
#           "Convención: mes = 30 días · Stock Mín = 15 días · Stock Máx = 45 días · Sugerencia redondeada al empaque (U_Nal_UN_EM_PRO).")