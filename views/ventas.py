import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd
import numpy as np
from datetime import datetime
from src.conexion_db import (
    cargar_y_transformar_ventas,
    aplicar_seguridad_rls_ventas,
)

# ============================================================
# CONFIGURACIÓN DE PÁGINA
# ============================================================
st.set_page_config(
    page_title="Dashboard de Ventas vs Metas",
    page_icon=":material/trending_up:",
    layout="wide",
    initial_sidebar_state="expanded",
)

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

# ============================================================
# CONSTANTES DE METAS MENSUALES
# ============================================================
META_PUNTO_VENTA_MENSUAL = 2_580_000_000
META_EJECUTIVO_MENSUAL = 430_000_000
ALMACEN_EJECUTIVOS = "EJECOM"

# ============================================================
# CARGA DE DATOS
# ============================================================
@st.cache_data(ttl=300, show_spinner="Cargando datos...")
def cargar_datos():
    return cargar_y_transformar_ventas()

# ... (código anterior de imports y carga de datos) ...

df_completo = cargar_datos()
df_seguro = aplicar_seguridad_rls_ventas(df_completo)

# ========================================== a
# 🔍 MODO DIAGNÓSTICO DE PRODUCCIÓN (SOLO PARA ESTE TEST)
# ==========================================
#rol = st.session_state.get("rol_actual", "comercial")
#if rol in ["comercial", "admin_punto"]:
#    st.warning("🔍 MODO DIAGNÓSTICO DE PRODUCCIÓN ACTIVADO")
#    
#    st.markdown("### 1. Datos de la Sesión del Usuario (Lo que trajo el Login)")
#    st.write(f"- **Rol:** `{rol}`")
#    st.write(f"- **sap_branch_code:** `{st.session_state.get('sap_branch_code')}` (tipo: {type(st.session_state.get('sap_branch_code')).__name__})")
#    st.write(f"- **sap_owner_code:** `{st.session_state.get('sap_owner_code')}` (tipo: {type(st.session_state.get('sap_owner_code')).__name__})")
#    
#    st.markdown("### 2. Estado del DataFrame ANTES del filtro RLS (`df_completo`)")
#    st.write(f"- **Total filas en df_completo:** {len(df_completo)}")
#    if not df_completo.empty:
#        if 'Almacen_Corto' in df_completo.columns:
#            st.write("- **Valores únicos de `Almacen_Corto` (primeros 10):**", df_completo['Almacen_Corto'].dropna().astype(str).str.strip().unique().tolist()[:10])
#        if 'propietario_doc' in df_completo.columns:
#            st.write("- **Valores únicos de `propietario_doc` (primeros 10):**", df_completo['propietario_doc'].dropna().astype(str).str.strip().unique().tolist()[:10])
#        if 'codigo_vendedor' in df_completo.columns:
#            st.write("- **Valores únicos de `codigo_vendedor` (primeros 10):**", df_completo['codigo_vendedor'].dropna().astype(str).str.strip().unique().tolist()[:10])
#
#    st.markdown("### 3. Resultado DESPUÉS del filtro RLS (`df_seguro`)")
#    st.write(f"- **Total filas en df_seguro:** {len(df_seguro)}")
#    
#    if len(df_seguro) == 0 and len(df_completo) > 0:
#        st.error("❌ EL FILTRO RLS ESTÁ ELIMINANDO TODAS LAS FILAS.")
#        st.info("💡 Compara los valores del 'Paso 1' con los del 'Paso 2'. Si no son idénticos (incluyendo espacios o mayúsculas), esa es la causa.")
#    st.markdown("---")
# ==========================================
# FIN BLOQUE DE DIAGNÓSTICO
# ==========================================

if df_seguro.empty:
    df_seguro = pd.DataFrame(columns=[
        "nombre_almacen", "Almacen_Corto", "nombre_vendedor", "codigo_vendedor",
        "precio_sin_iva", "precio_con_iva", "rentabilidad", "fecha_contabilizacion", 
        "documento", "codigo_cliente", "nombre_cliente"
    ])

rol = st.session_state.get("rol_actual", "comercial")
branch_usuario = str(st.session_state.get("sap_branch_code", "")).strip().upper()

# Mapa para normalizar nombres de almacén
mapa_inverso = {
    "EJECUTIVOS COMERCIALES": "EJECOM", "PUNTO 134": "ALM134", "7 DE AGOSTO": "7AGOS", 
    "AVENIDA19": "AV19", "CENTRO 1": "Q1", "CENTRO 3": "Q3", "CENTRO 5": "Q5", "CENTRO 6": "Q6",
    "PUNTO170": "ALM170", "NORTE128": "ALM128", "VILLAVICENCIO": "VILL", "ARMENIA": "ARME", "CHIA": "CHIA"
}
branch_corto = mapa_inverso.get(branch_usuario, branch_usuario)

# ============================================================
# BANDERAS DE ROL
# ============================================================
es_gerencial = rol in ["admin", "gerente", "gerente_comercial"]
es_admin_punto = rol == "admin_punto"
es_comercial = rol == "comercial"

# ============================================================
# ENCABEZADO
# ============================================================
st.title("Dashboard de Ventas vs Metas")

if es_gerencial:
    st.success("Vista corporativa completa")
elif es_admin_punto:
    st.success(f"Vista del punto de venta: {branch_corto or 'N/A'}")
else:
    st.info("Viendo únicamente tus ventas asignadas")

# ============================================================
# INICIALIZAR FILTROS DINÁMICOS (Drill-down)
# ============================================================
st.session_state.setdefault("clicked_almacen_ventas", None)
st.session_state.setdefault("clicked_vendedor_ventas", None)
st.session_state.setdefault("clicked_mes_ventas", None)
st.session_state.setdefault("ultimo_modo_vista", None)

# ============================================================
# FILTROS LATERALES
# ============================================================
with st.sidebar:
    st.markdown("### Filtros")
    
    meses_disp = sorted(df_seguro["Mes_Texto"].dropna().unique().tolist()) if "Mes_Texto" in df_seguro.columns else []
    mes_actual_texto = datetime.now().strftime("%b %Y")
    default_meses = [mes_actual_texto] if mes_actual_texto in meses_disp else meses_disp
    meses_sel = st.multiselect("Mes(es):", meses_disp, default=default_meses, key="sb_meses_ventas")

    almacenes_disp = sorted(df_seguro["Almacen_Corto"].dropna().unique().tolist())
    almacenes_sel = st.multiselect("Punto de venta:", almacenes_disp, default=almacenes_disp, key="sb_alm_ventas")

    vendedores_disp = sorted(df_seguro["nombre_vendedor"].dropna().unique().tolist())
    vendedores_sel = st.multiselect("Vendedor:", vendedores_disp, default=vendedores_disp, key="sb_vend_ventas")

#    ====================================================================================       
#    Filtro de boton para quitar filtros graficos
#    ====================================================================================  st.markdown("---")
    
    # ✅ Solo mostrar el botón si hay algún filtro dinámico activo
    if st.session_state.clicked_almacen_ventas or st.session_state.clicked_vendedor_ventas or st.session_state.clicked_mes_ventas:
        if st.button("Limpiar Filtros de Gráfico", use_container_width=True):
            st.session_state.clicked_almacen_ventas = None
            st.session_state.clicked_vendedor_ventas = None
            st.session_state.clicked_mes_ventas = None
            st.rerun()






#    ====================================================================================       
#    Filtro de boton para quitar filtros graficos y texto de filtros aplicados
#    ====================================================================================  st.markdown("---")

#    st.markdown("---")
#    st.markdown("##### Filtros Dinámicos Activos")
#    filtros_sidebar = []
#    if st.session_state.clicked_almacen_ventas:
#        filtros_sidebar.append(f"P. Venta: {st.session_state.clicked_almacen_ventas}")
#    if st.session_state.clicked_vendedor_ventas:
#        filtros_sidebar.append(f"Vendedor: {st.session_state.clicked_vendedor_ventas}")
#    if st.session_state.clicked_mes_ventas:
#        filtros_sidebar.append(f"Mes: {st.session_state.clicked_mes_ventas}")
#        
#    if filtros_sidebar:
#        for f in filtros_sidebar:
#            st.markdown(f"• {f}")
#        st.markdown("---")
#        if st.button("Limpiar Filtros de Gráfico", use_container_width=True):
#            st.session_state.clicked_almacen_ventas = None
#            st.session_state.clicked_vendedor_ventas = None 
#            st.session_state.clicked_mes_ventas = None
#            st.rerun()
#        st.caption("Haz clic nuevamente en la barra seleccionada para quitar el filtro.")
#    else:
#        st.markdown("*Ninguno activo*")
#        st.caption("Haz clic en cualquier barra de los gráficos para filtrar en cascada.")

#    ============================================================
#    Se aplica para que en el sidebar salga el mensaje descrito
#    ============================================================
#    st.markdown("---")
#    num_meses_sidebar = len(meses_sel) if meses_sel else 1
#    st.markdown("### Metas Configuradas")
#    st.caption(f"Meta mensual por P. Venta: ${META_PUNTO_VENTA_MENSUAL:,.0f}")
#    st.caption(f"Meta mensual por Ejecutivo: ${META_EJECUTIVO_MENSUAL:,.0f}")
#    if num_meses_sidebar > 1:
#        st.info(f"Meta Ajustada: Al seleccionar {num_meses_sidebar} meses en el filtro, la meta se multiplica automáticamente x{num_meses_sidebar}.")

# ============================================================
# APLICAR FILTROS BASE (Sidebar)
# ============================================================
df = df_seguro.copy()
if meses_sel and "Mes_Texto" in df.columns:
    df = df[df["Mes_Texto"].isin(meses_sel)]
if almacenes_sel and "Almacen_Corto" in df.columns:
    df = df[df["Almacen_Corto"].isin(almacenes_sel)]
if vendedores_sel and "nombre_vendedor" in df.columns:
    df = df[df["nombre_vendedor"].isin(vendedores_sel)]

# ============================================================
# LÓGICA DE METAS POR ROL Y VISTA
# ============================================================
if es_gerencial:
    vista_sel = st.radio(
        "Modo de visualización",
        options=["Puntos de Venta", "Ejecutivos Comerciales"],
        horizontal=True,
        label_visibility="collapsed",
    )
    modo_vista = "puntos" if vista_sel == "Puntos de Venta" else "ejecutivos"
elif es_admin_punto:
    modo_vista = "puntos"
elif es_comercial:
    # ✅ DETECCIÓN INFALIBLE: Basada en los datos reales filtrados por RLS, no en la configuración del usuario
    if not df.empty and "Almacen_Corto" in df.columns:
        almacen_real = str(df["Almacen_Corto"].mode()[0]).upper()
        if ALMACEN_EJECUTIVOS in almacen_real:
            modo_vista = "ejecutivos"
        else:
            modo_vista = "puntos"
    else:
        modo_vista = "puntos"
else:
    modo_vista = "puntos"

# Limpiar drill-down si cambia el modo de vista
if st.session_state.ultimo_modo_vista != modo_vista:
    st.session_state.clicked_almacen_ventas = None
    st.session_state.clicked_vendedor_ventas = None
    st.session_state.clicked_mes_ventas = None
    st.session_state.ultimo_modo_vista = modo_vista
    st.rerun()

# ============================================================
# 🔧 FIX: ACOTAR EL UNIVERSO DE DATOS AL ALMACÉN "EJECOM" EN MODO EJECUTIVOS
# ------------------------------------------------------------
# Antes, al elegir "Ejecutivos Comerciales" (gerencial/admin) se agrupaba
# TODO el dataframe filtrado (todos los puntos de venta) por vendedor, así
# que cualquier vendedor de cualquier almacén aparecía comparado contra la
# meta de 430M. Esto también mantiene coherentes la tendencia mensual, la
# tabla de resumen y el detalle de documentos con el modo seleccionado.
# ============================================================
if modo_vista == "ejecutivos" and "Almacen_Corto" in df.columns:
    df = df[df["Almacen_Corto"].astype(str).str.upper() == ALMACEN_EJECUTIVOS]

# ============================================================
# APLICAR FILTROS DINÁMICOS (Drill-down en cascada)
# ============================================================
if st.session_state.clicked_almacen_ventas:
    df = df[df["Almacen_Corto"] == st.session_state.clicked_almacen_ventas]
if st.session_state.clicked_vendedor_ventas:
    df = df[df["nombre_vendedor"] == st.session_state.clicked_vendedor_ventas]
if st.session_state.clicked_mes_ventas:
    df = df[df["Mes_Texto"] == st.session_state.clicked_mes_ventas]

# ============================================================
# CÁLCULO DE VENTAS Y METAS DINÁMICAS
# ============================================================
if df.empty:
    st.warning("No hay datos de ventas con los filtros actuales.")
    st.stop()

meses_reales_en_df = df["Mes_Texto"].dropna().unique() if "Mes_Texto" in df.columns else []
num_meses_para_meta = len(meses_reales_en_df) if len(meses_reales_en_df) > 0 else 1

meta_punto_ajustada = META_PUNTO_VENTA_MENSUAL * num_meses_para_meta
meta_ejecutivo_ajustada = META_EJECUTIVO_MENSUAL * num_meses_para_meta

if st.session_state.clicked_vendedor_ventas and modo_vista in ("puntos", "ejecutivos"):
    # ✅ VISTA DE TOP 20 CLIENTES (Activada por toggle)
    # 🔧 FIX: antes exigía modo_vista == "puntos", así que un comercial de
    # EJECOM (modo "ejecutivos") nunca llegaba aquí al hacer clic en su
    # propia barra: la condición "modo_vista == 'ejecutivos'" de abajo se
    # evaluaba primero y se quedaba ahí. Ahora esta vista tiene prioridad
    # en ambos modos, igual que ya ocurría para el resto de roles.
    df_grupo = df.groupby("nombre_cliente").agg(
        Ventas=("precio_sin_iva", "sum"),
        Rentabilidad=("rentabilidad", "sum")
    ).reset_index()
    df_grupo = df_grupo.rename(columns={"nombre_cliente": "Entidad"})
    df_grupo = df_grupo.sort_values("Ventas", ascending=False).head(20)
    df_grupo["Meta"] = 0
    titulo_grafico = f"Top 20 Clientes de: {st.session_state.clicked_vendedor_ventas}"
    etiqueta_entidad = "Cliente"
    nivel_drill = "cliente"

elif modo_vista == "ejecutivos":
    # ✅ Para comerciales de EJECOM, usamos sus datos directamente (ya filtrados por RLS)
    # Esto evita fallos si el nombre del almacén tiene variaciones de texto en la BD
    df_grupo = df.groupby("nombre_vendedor").agg(
        Ventas=("precio_sin_iva", "sum"),
        Rentabilidad=("rentabilidad", "sum")
    ).reset_index()
    df_grupo = df_grupo.rename(columns={"nombre_vendedor": "Entidad"})
    df_grupo["Meta"] = meta_ejecutivo_ajustada  # Aplica la meta de 430M * meses
    titulo_grafico = "Ventas vs Meta por Ejecutivo Comercial"
    etiqueta_entidad = "Ejecutivo"
    nivel_drill = "vendedor"

elif st.session_state.clicked_almacen_ventas and modo_vista == "puntos":
    df_grupo = df.groupby("nombre_vendedor").agg(
        Ventas=("precio_sin_iva", "sum"),
        Rentabilidad=("rentabilidad", "sum")
    ).reset_index()
    df_grupo = df_grupo.rename(columns={"nombre_vendedor": "Entidad"})
    df_grupo["Meta"] = meta_punto_ajustada
    titulo_grafico = f"Ventas por Vendedor en: {st.session_state.clicked_almacen_ventas}"
    etiqueta_entidad = "Vendedor"
    nivel_drill = "vendedor"
    
else:
    # ✅ VISTA PRINCIPAL (Tus Ventas vs Meta del Almacén)
    df_grupo = df.groupby("Almacen_Corto").agg(
        Ventas=("precio_sin_iva", "sum"),
        Rentabilidad=("rentabilidad", "sum")
    ).reset_index()
    df_grupo = df_grupo.rename(columns={"Almacen_Corto": "Entidad"})
    df_grupo["Meta"] = meta_punto_ajustada
    
    if es_comercial:
        titulo_grafico = f"Tus Ventas vs Meta del Almacén ({branch_corto})"
    else:
        titulo_grafico = "Ventas vs Meta por Punto de Venta"
        
    etiqueta_entidad = "Punto de Venta"
    nivel_drill = "almacen"

# Cálculos derivados seguros
total_ventas_grupo = df_grupo["Ventas"].sum()
df_grupo["% Participación"] = (df_grupo["Ventas"] / total_ventas_grupo * 100).round(2) if total_ventas_grupo > 0 else 0.0

if "Meta" in df_grupo.columns and (df_grupo["Meta"] > 0).any():
    df_grupo["% Cumplimiento"] = (df_grupo["Ventas"] / df_grupo["Meta"] * 100).round(2)
else:
    df_grupo["% Cumplimiento"] = 0.0

df_grupo["Diferencia"] = df_grupo["Ventas"] - df_grupo["Meta"]
df_grupo["Estado"] = df_grupo["% Cumplimiento"].apply(
    lambda x: "🟢 Cumple" if x >= 100 else ("🟡 Cerca" if x >= 80 else "🔴 No Cumple")
)
df_grupo = df_grupo.sort_values("Ventas", ascending=False)

# ============================================================
# 🔧 FIX: UTILIDAD ANTI-BUCLE PARA GRÁFICOS INTERACTIVOS
# ------------------------------------------------------------
# st.plotly_chart(..., on_select="rerun") conserva la selección del punto
# clicado entre reruns. Como el código reaccionaba a "event.selection.points"
# directamente y llamaba a st.rerun() de nuevo, cada vez que el gráfico se
# volvía a dibujar CON LOS MISMOS DATOS (p. ej. la vista "Ejecutivos
# Comerciales" de un comercial de EJECOM, que solo tiene una barra: la suya)
# Streamlit seguía "viendo" el mismo clic y volvía a alternar el filtro
# (on/off/on/off...) por siempre → el bucle infinito reportado.
# Esta función solo deja pasar una selección si es distinta a la última que
# ya se procesó para esa clave; si no hay selección, limpia la firma para
# permitir volver a hacer clic sobre la misma barra más adelante.
# ============================================================
def procesar_clic_grafico(event, clave_firma: str):
    if not (event and event.selection and event.selection.points):
        st.session_state[clave_firma] = None
        return None

    punto = event.selection.points[0]
    firma_actual = f"{punto.get('x')}|{punto.get('y')}|{punto.get('curveNumber')}"

    if st.session_state.get(clave_firma) == firma_actual:
        return None  # Ya se procesó este clic en un rerun anterior

    st.session_state[clave_firma] = firma_actual
    return punto

# ============================================================
# KPIs GENERALES
# ============================================================
total_ventas = df_grupo["Ventas"].sum()
total_meta = df_grupo["Meta"].sum()
cumplimiento_global = (total_ventas / total_meta * 100) if total_meta > 0 else 0
entidades = df_grupo["Entidad"].nunique()
entidades_cumplen = (df_grupo["% Cumplimiento"] >= 100).sum()

if es_gerencial:
    kpi_cols = st.columns(4)
    with kpi_cols[0].container(border=True, height=130):
        st.metric("Ventas Totales", value=f"${total_ventas:,.0f}")
    with kpi_cols[1].container(border=True, height=130):
        st.metric("Meta Total", value=f"${total_meta:,.0f}", help=f"Meta mensual x {num_meses_para_meta} mes(es)")
    with kpi_cols[2].container(border=True, height=130):
        st.metric("Cumplimiento", value=f"{cumplimiento_global:.1f}%", delta=f"{cumplimiento_global - 100:.1f}%")
    with kpi_cols[3].container(border=True, height=130):
        st.metric("Entidades que cumplen", value=f"{entidades_cumplen} / {entidades}")
else:
    kpi_cols = st.columns(3)
    with kpi_cols[0].container(border=True, height=130):
        st.metric("Ventas Totales", value=f"${total_ventas:,.0f}")
    with kpi_cols[1].container(border=True, height=130):
        st.metric("Meta Asignada", value=f"${total_meta:,.0f}", help=f"Meta mensual x {num_meses_para_meta} mes(es)")
    with kpi_cols[2].container(border=True, height=130):
        st.metric("Cumplimiento", value=f"{cumplimiento_global:.1f}%", delta=f"{cumplimiento_global - 100:.1f}%")

st.markdown("---")

# ============================================================
# GRÁFICO PRINCIPAL INTERACTIVO
# ============================================================
st.subheader(titulo_grafico)

fig = go.Figure()
fig.add_trace(go.Bar(
    x=df_grupo["Entidad"],
    y=df_grupo["Ventas"],
    name="Ventas",
    marker_color="#1f77b4",
    text=[f"${v:,.0f}" for v in df_grupo["Ventas"]],
    textposition="outside",
    textfont=dict(size=11),
))

if nivel_drill != "cliente":
    fig.add_trace(go.Bar(
        x=df_grupo["Entidad"],
        y=df_grupo["Meta"],
        name="Meta",
        marker_color="#ff7f0e",
        text=[f"${m:,.0f}" for m in df_grupo["Meta"]],
        textposition="outside",
        textfont=dict(size=11),
    ))

fig.update_layout(
    barmode="group",
    plot_bgcolor="rgba(0,0,0,0)",
    paper_bgcolor="rgba(0,0,0,0)",
    yaxis_title="Valor ($)",
    xaxis_title=etiqueta_entidad,
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    margin=dict(l=20, r=20, t=40, b=80),
    height=500,
    xaxis=dict(tickangle=-30),
)
fig.update_yaxes(tickformat="$,.0f")

event = st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CONFIG, key="chart_ventas_main", on_select="rerun")

# ============================================================
# 🔧 LÓGICA DE TOGGLE ESTABLE (guard anti-bucle aplicado)
# ============================================================
punto_click_main = procesar_clic_grafico(event, "_firma_click_main_ventas")
if punto_click_main:
    entidad_seleccionada = punto_click_main.get('x')
    
    if nivel_drill == "almacen":
        if es_comercial:
            # ✅ TOGGLE DIRECTO PARA COMERCIALES: Clic en "Tus Ventas" muestra sus clientes
            if st.session_state.clicked_vendedor_ventas:
                st.session_state.clicked_vendedor_ventas = None # Desactivar
            else:
                vendedores_unicos = df["nombre_vendedor"].dropna().unique()
                if len(vendedores_unicos) > 0:
                    st.session_state.clicked_vendedor_ventas = str(vendedores_unicos[0])
        else:
            # Lógica normal para admin/gerente
            if st.session_state.clicked_almacen_ventas == entidad_seleccionada:
                st.session_state.clicked_almacen_ventas = None
            else:
                st.session_state.clicked_almacen_ventas = entidad_seleccionada
            st.session_state.clicked_vendedor_ventas = None
        st.rerun()
        
    elif nivel_drill == "vendedor":
        if st.session_state.clicked_vendedor_ventas == entidad_seleccionada:
            st.session_state.clicked_vendedor_ventas = None
        else:
            st.session_state.clicked_vendedor_ventas = entidad_seleccionada
        st.rerun()
        
    elif nivel_drill == "cliente":
        # ✅ CORRECCIÓN CLAVE: Permite al comercial volver a la vista principal al hacer clic en el gráfico de clientes
        if es_comercial:
            st.session_state.clicked_vendedor_ventas = None
            st.rerun()

# ============================================================
# GRÁFICO DE TENDENCIA MENSUAL (INTERACTIVO)
# ============================================================
st.markdown("---")
st.subheader("Tendencia de Ventas Acumuladas por Mes")

df_mensual = df.copy()
df_mensual["Anio"] = df_mensual["fecha_contabilizacion"].dt.year
df_mensual["Mes_Num"] = df_mensual["fecha_contabilizacion"].dt.month
df_mensual["Etiqueta"] = df_mensual["fecha_contabilizacion"].dt.strftime("%b %Y")

df_mensual_agg = df_mensual.groupby(["Anio", "Mes_Num", "Etiqueta"]).agg(
    Ventas=("precio_sin_iva", "sum")
).reset_index().sort_values(["Anio", "Mes_Num"])

fig_mensual = px.bar(
    df_mensual_agg, 
    x="Etiqueta", 
    y="Ventas", 
    text_auto="$,.0f",
    color_discrete_sequence=["#2ca02c"],
    hover_data={"Anio": True, "Mes_Num": False}
)
fig_mensual.update_layout(
    plot_bgcolor="rgba(0,0,0,0)", 
    paper_bgcolor="rgba(0,0,0,0)", 
    yaxis_title="Ventas Acumuladas ($)", 
    xaxis_title="Mes / Año", 
    showlegend=False, 
    margin=dict(l=20, r=20, t=20, b=80), 
    height=400,
    xaxis=dict(tickangle=-45)
)
fig_mensual.update_traces(textfont_size=10, textposition="outside")
fig_mensual.update_yaxes(tickformat="$,.0f")

event_mensual = st.plotly_chart(fig_mensual, use_container_width=True, config=PLOTLY_CONFIG, key="chart_mensual_ventas", on_select="rerun")

punto_click_mensual = procesar_clic_grafico(event_mensual, "_firma_click_mensual_ventas")
if punto_click_mensual:
    mes_seleccionado = punto_click_mensual.get('x')
    if st.session_state.clicked_mes_ventas == mes_seleccionado:
        st.session_state.clicked_mes_ventas = None
    else:
        st.session_state.clicked_mes_ventas = mes_seleccionado
    st.rerun()

# ============================================================
# GRÁFICO DE VENTAS POR COLABORADOR (SOLO GERENCIAL Y ADMIN PUNTO)
# ============================================================
if (es_gerencial or es_admin_punto) and modo_vista == "puntos":
    st.markdown("---")
    st.subheader("Ventas por Colaborador")
    
    if st.session_state.clicked_almacen_ventas:
        df_colab = df[df["Almacen_Corto"] == st.session_state.clicked_almacen_ventas]
    else:
        df_colab = df.copy()
    
    if not df_colab.empty and "nombre_vendedor" in df_colab.columns:
        total_general_colab = df_colab["precio_sin_iva"].sum()
        df_col_resumen = df_colab.groupby("nombre_vendedor").agg(
            Valor=("precio_sin_iva", "sum")
        ).reset_index().sort_values("Valor", ascending=True).tail(20)
        
        df_col_resumen["Porcentaje"] = (df_col_resumen["Valor"] / total_general_colab * 100).round(2) if total_general_colab > 0 else 0.0
        df_col_resumen["Colaborador_Label"] = df_col_resumen.apply(
            lambda row: f"{row['nombre_vendedor']} ({row['Porcentaje']:.1f}%)", axis=1
        )
        
        fig_col = px.bar(
            df_col_resumen, 
            x="Valor", 
            y="Colaborador_Label", 
            orientation="h", 
            color_discrete_sequence=["#9467bd"], 
            text_auto="$,.0f", 
            hover_data={"Porcentaje": ":.2f%", "nombre_vendedor": True}
        )
        fig_col.update_layout(
            plot_bgcolor="rgba(0,0,0,0)", 
            paper_bgcolor="rgba(0,0,0,0)", 
            xaxis_title="", 
            yaxis_title="", 
            showlegend=False, 
            margin=dict(l=10, r=10, t=10, b=10), 
            height=550, 
            autosize=True, 
            bargap=0.1
        )
        fig_col.update_yaxes(automargin=True, tickfont_size=12)
        fig_col.update_traces(width=0.95, textfont_size=18, textposition="auto", cliponaxis=False)
        
        event_col = st.plotly_chart(fig_col, use_container_width=True, config=PLOTLY_CONFIG, key="chart_colaboradores_ventas", on_select="rerun")
        
        punto_click_col = procesar_clic_grafico(event_col, "_firma_click_colaboradores_ventas")
        if punto_click_col:
            label_seleccionado = punto_click_col.get('y')
            nombre_colab = label_seleccionado.split(' (')[0].strip() if ' (' in label_seleccionado else label_seleccionado
            
            if st.session_state.clicked_vendedor_ventas == nombre_colab:
                st.session_state.clicked_vendedor_ventas = None
            else:
                st.session_state.clicked_vendedor_ventas = nombre_colab
            st.rerun()

# ============================================================
# TABLA RESUMEN POR ENTIDAD (SOLO GERENCIAL)
# ============================================================
if es_gerencial:
    st.markdown("---")
    st.subheader("Resumen por Entidad")

    df_tabla = df_grupo.copy()
    df_tabla = df_tabla.rename(columns={
        "Entidad": etiqueta_entidad,
        "Ventas": "Ventas Reales",
        "Meta": "Meta Asignada",
        "% Cumplimiento": "% Cumplimiento",
        "% Participación": "% Participación",
        "Diferencia": "Diferencia ($)",
        "Estado": "Estado",
    })

    columnas_tabla = [
        etiqueta_entidad, "Ventas Reales", "Meta Asignada", 
        "% Participación", "% Cumplimiento", "Diferencia ($)", "Estado"
    ]

    if "Rentabilidad" in df_tabla.columns:
        df_tabla["Rentabilidad Total"] = df_grupo["Rentabilidad"]
        columnas_tabla.insert(3, "Rentabilidad Total")

    col_config = {
        "Ventas Reales": st.column_config.NumberColumn(format="$%,.0f"),
        "Meta Asignada": st.column_config.NumberColumn(format="$%,.0f"),
        "% Participación": st.column_config.NumberColumn(format="%.2f%%"),
        "% Cumplimiento": st.column_config.NumberColumn(format="%.2f%%"),
        "Diferencia ($)": st.column_config.NumberColumn(format="$%,.0f"),
    }

    if "Rentabilidad Total" in df_tabla.columns:
        col_config["Rentabilidad Total"] = st.column_config.NumberColumn(format="$%,.0f")

    st.dataframe(
        df_tabla[columnas_tabla], 
        use_container_width=True, 
        hide_index=True, 
        column_config=col_config, 
        height=400
    )

# ============================================================
# DESPLEGABLE DE DETALLE POR DOCUMENTO
# ============================================================
with st.expander("Ver Detalle de Documentos de Venta", expanded=False):
    st.caption("Desglose a nivel de documento. Los filtros del sidebar y los clics en gráficos se aplican aquí automáticamente.")
    
    if df.empty:
        st.info("No hay documentos que coincidan con los filtros aplicados.")
    else:
        agg_funcs = {}
        for col in df.columns:
            if col == 'documento':
                continue
            elif col in ['precio_sin_iva', 'precio_con_iva']:
                agg_funcs[col] = 'sum'
            elif col == 'rentabilidad' and es_gerencial:
                agg_funcs[col] = 'sum'
            else:
                agg_funcs[col] = 'first'
        
        df_detalle = df.groupby('documento', as_index=False).agg(agg_funcs)
        
        if 'fecha_contabilizacion' in df_detalle.columns:
            df_detalle['Fecha'] = df_detalle['fecha_contabilizacion'].dt.strftime('%d/%m/%Y')
        
        cols_mostrar = [
            'documento', 'codigo_cliente', 'nombre_cliente', 'Almacen_Corto', 
            'nombre_vendedor', 'Fecha', 'precio_sin_iva', 'precio_con_iva'
        ]
        
        if es_gerencial and 'rentabilidad' in df_detalle.columns:
            cols_mostrar.append('rentabilidad')
            
        cols_existentes = [c for c in cols_mostrar if c in df_detalle.columns]
        df_detalle = df_detalle[cols_existentes].copy()
        
        rename_dict = {
            'documento': 'Documento',
            'codigo_cliente': 'N° Cliente',
            'nombre_cliente': 'Cliente',
            'Almacen_Corto': 'Punto de Venta',
            'nombre_vendedor': 'Vendedor',
            'Fecha': 'Fecha',
            'precio_sin_iva': 'Venta S/IVA',
            'precio_con_iva': 'Venta C/IVA',
        }
        
        if es_gerencial and 'rentabilidad' in df_detalle.columns:
            rename_dict['rentabilidad'] = 'Rentabilidad'
            
        rename_dict = {k: v for k, v in rename_dict.items() if k in df_detalle.columns}
        df_detalle = df_detalle.rename(columns=rename_dict)
        
        if 'Fecha' in df_detalle.columns:
            df_detalle = df_detalle.sort_values(by='Fecha', ascending=False)
            
        col_config_detalle = {
            "Venta S/IVA": st.column_config.NumberColumn(format="$%,.2f"),
            "Venta C/IVA": st.column_config.NumberColumn(format="$%,.2f"),
        }
        
        if es_gerencial and "Rentabilidad" in df_detalle.columns:
            col_config_detalle["Rentabilidad"] = st.column_config.NumberColumn(format="$%,.2f")
        
        st.dataframe(
            df_detalle, 
            use_container_width=True, 
            hide_index=True, 
            height=400, 
            column_config=col_config_detalle
        )