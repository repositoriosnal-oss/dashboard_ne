import streamlit as st
import plotly.express as px
import pandas as pd
import numpy as np
from src.conexion_db import cargar_y_transformar_notas, aplicar_seguridad_rls, conn
from sqlalchemy import text

# Configuración de página
st.set_page_config(
    page_title="Notas Crédito Abiertas",
    page_icon="🔙",
    layout="wide",
    initial_sidebar_state="expanded"
)

# === CSS GLOBAL COMPACTO (Estilo Power BI - Solo para ajustes de espaciado seguros) ===
st.markdown("""
<style>
    div[data-testid="stAppViewBlockContainer"] {
        padding-top: 1rem !important;
        padding-bottom: 0.5rem !important;
        max-width: 1400px !important;
    }
    .stVerticalBlock { gap: 0.5rem !important; }
    div[data-testid="stVerticalBlockBorderWrapper"] {
        padding: 5px 10px 5px 10px !important;
        overflow: hidden !important;
    }
    div[data-testid="stVerticalBlockBorderWrapper"] h5 {
        margin-top: 0 !important;
        margin-bottom: 0.5rem !important;
        font-size: 1rem !important;
    }
    div[data-testid="stMetric"] {
        text-align: center;
        padding-top: 12px;
    }
    div[data-testid="stMetricLabel"] {
        font-size: 1rem !important;
        font-weight: 600 !important;
        color: #555 !important;
        justify-content: center !important;
    }
    div[data-testid="stMetricValue"] {
        font-size: 1.6rem !important;
        font-weight: 700 !important;
        color: #1f77b4 !important;
        justify-content: center !important;
    }
</style>
""", unsafe_allow_html=True)

PLOTLY_CONFIG = {"displayModeBar": False, "responsive": True}

def altura_tabla(df: pd.DataFrame, alto_fila: int = 28, base: int = 38, maximo: int = 250, minimo: int = 120) -> int:
    alto = base + alto_fila * max(len(df), 1)
    return int(min(max(alto, minimo), maximo))

ORDEN_RANGOS = ["De 0 a 5 días", "De 5 a 10 días", "De 10 a 20 días", "De 20 a 30 días", "Mas de 30 días"]

@st.cache_data(ttl=300, show_spinner="Cargando datos desde SAP...")
def cargar_datos_cacheados():
    return cargar_y_transformar_notas()

df_completo = cargar_datos_cacheados()
df_seguro = aplicar_seguridad_rls(df_completo)

if df_seguro.empty:
    df_seguro['Documento_Str'] = pd.Series(dtype='str')
else:
    mapa_almacenes = {
        "EJECUTIVOS COMERCIALES": "EJECOM", "PUNTO 134": "ALM134", "7 DE AGOSTO": "7AGOS",
        "AVENIDA19": "AV19", "CENTRO 1": "Q1", "CENTRO 3": "Q3", "CENTRO 5": "Q5",
        "CENTRO 6": "Q6", "PUNTO170": "ALM170", "NORTE128": "ALM128", 
        "VILLAVICENCIO": "VILL", "ARMENIA": "ARME"
    }
    df_seguro['Almacen_Corto'] = df_seguro['Almacen'].map(mapa_almacenes).fillna(df_seguro['Almacen'])
    df_seguro['Estado_IVA'] = np.where((df_seguro['Precio_Total'] - df_seguro['Precio_Sin_IVA']) > 1, "Con IVA", "Sin IVA")
    
    agg_funcs = {}
    for col in df_seguro.columns:
        if col == 'Documento': continue
        elif col in ['Precio_Sin_IVA', 'Precio_Total']: agg_funcs[col] = 'sum'
        else: agg_funcs[col] = 'first'
            
    df_seguro = df_seguro.groupby('Documento', as_index=False).agg(agg_funcs)
    df_seguro['Documento_Str'] = df_seguro['Documento'].astype(str).str.strip()

rol = st.session_state.get('rol_actual', 'comercial')
usuario_logueado = st.session_state.get('usuario_actual', 'Usuario')

st.title("NOTAS CRÉDITO ABIERTAS")
if rol in ["admin", "gerente", "gerente_comercial"]:
    st.success("Vista general corporativa")
else:
    st.info(f"Viendo únicamente tus notas crédito asignadas")

# ==========================================
# 1. INICIALIZAR FILTROS DINÁMICOS EN SESSION_STATE
# ==========================================
st.session_state.setdefault("clicked_almacen_nc", None)
st.session_state.setdefault("clicked_rango_nc", None)
st.session_state.setdefault("clicked_colab_nc", None)

# ==========================================
# 2. FILTROS CONTEXTUALES EN EL SIDEBAR (MINIMALISTA)
# ==========================================
with st.sidebar:
    rangos_seleccionados = st.multiselect("Rango de días:", options=ORDEN_RANGOS, default=ORDEN_RANGOS, key="sb_rangos_nc")
    
    almacenes_opciones = sorted(df_seguro['Almacen_Corto'].dropna().unique().tolist()) if 'Almacen_Corto' in df_seguro.columns else []
    almacenes_seleccionados = st.multiselect("Punto de venta:", options=almacenes_opciones, default=almacenes_opciones, key="sb_alm_nc")
    
    iva_opciones = ["Con IVA", "Sin IVA"]
    iva_seleccionado = st.multiselect("Estado IVA:", options=iva_opciones, default=iva_opciones, key="sb_iva_nc")
    
    colaboradores_seleccionados = []
    if rol in ["admin", "gerente", "gerente_comercial", "admin_punto"] and not df_seguro.empty:
        df_temp = df_seguro[df_seguro['Almacen_Corto'].isin(almacenes_seleccionados)] if almacenes_seleccionados else df_seguro
        if 'Colaborador' in df_temp.columns:
            colab_opciones = sorted(df_temp['Colaborador'].dropna().unique().tolist())
            colaboradores_seleccionados = st.multiselect("Colaborador:", options=colab_opciones, default=colab_opciones, key="sb_colab_nc")
        
    st.markdown("**Filtrar por rango de fechas:**")
    fecha_minima = df_seguro['Fecha_Contabilizacion'].min().date() if not df_seguro.empty else pd.Timestamp.now().date()
    fecha_inicio = st.date_input("Desde:", value=fecha_minima, key="sb_fec_ini_nc")
    fecha_maxima = df_seguro['Fecha_Contabilizacion'].max().date() if not df_seguro.empty else pd.Timestamp.now().date()
    fecha_fin = st.date_input("Hasta:", value=fecha_maxima, key="sb_fec_fin_nc")

    st.markdown("---")
    
    # ✅ RETROALIMENTACIÓN NATIVA Y ESTABLE EN EL SIDEBAR
    st.markdown("##### 🔄 Filtros Dinámicos Activos")
    filtros_sidebar = []
    if st.session_state.clicked_almacen_nc:
        filtros_sidebar.append(f"🏭 **P. Venta:** {st.session_state.clicked_almacen_nc}")
    if st.session_state.clicked_rango_nc:
        filtros_sidebar.append(f"📅 **Rango:** {st.session_state.clicked_rango_nc}")
    if st.session_state.clicked_colab_nc:
        filtros_sidebar.append(f"👤 **Colaborador:** {st.session_state.clicked_colab_nc}")

    if filtros_sidebar:
        for f in filtros_sidebar:
            st.markdown(f"• {f}")
        st.markdown("---")
        st.caption("💡 *Haz clic nuevamente en la barra seleccionada para quitar el filtro.*")
    else:
        st.markdown("*Ninguno activo*")
        st.caption("💡 *Haz clic en cualquier gráfico para filtrar.*")

# ==========================================
# 3. LÓGICA DE FILTRADO BASE (Sidebar)
# ==========================================
df_filtrado = df_seguro.copy()
if rangos_seleccionados and 'Rango_Dias' in df_filtrado.columns: 
    df_filtrado = df_filtrado[df_filtrado['Rango_Dias'].isin(rangos_seleccionados)]
if almacenes_seleccionados and 'Almacen_Corto' in df_filtrado.columns: 
    df_filtrado = df_filtrado[df_filtrado['Almacen_Corto'].isin(almacenes_seleccionados)]
if iva_seleccionado and 'Estado_IVA' in df_filtrado.columns: 
    df_filtrado = df_filtrado[df_filtrado['Estado_IVA'].isin(iva_seleccionado)]
if colaboradores_seleccionados and 'Colaborador' in df_filtrado.columns: 
    df_filtrado = df_filtrado[df_filtrado['Colaborador'].isin(colaboradores_seleccionados)]
if 'Fecha_Contabilizacion' in df_filtrado.columns and not df_filtrado.empty:
    df_filtrado = df_filtrado[(df_filtrado['Fecha_Contabilizacion'].dt.date >= fecha_inicio) & (df_filtrado['Fecha_Contabilizacion'].dt.date <= fecha_fin)]

# ==========================================
# 4. APLICAR FILTROS DINÁMICOS ANTES DE RENDERIZAR
# ==========================================
if st.session_state.clicked_almacen_nc:
    df_filtrado = df_filtrado[df_filtrado['Almacen_Corto'] == st.session_state.clicked_almacen_nc]
if st.session_state.clicked_rango_nc:
    df_filtrado = df_filtrado[df_filtrado['Rango_Dias'] == st.session_state.clicked_rango_nc]
if st.session_state.clicked_colab_nc:
    df_filtrado = df_filtrado[df_filtrado['Colaborador'] == st.session_state.clicked_colab_nc]

tab_analitica, tab_gestion = st.tabs(["Analítica", "Seguimiento notas créditos"])

# ==========================================
# PESTAÑA 1: ANALÍTICA
# ==========================================
with tab_analitica:
    if df_seguro.empty or df_filtrado.empty:
        st.warning("No hay datos con los filtros actuales.")
    else:
        # ✅ 1. CALCULAR MÉTRICAS BASE PRIMERO (Para evitar NameError en bloques condicionales)
        total_docs = df_filtrado['Documento'].nunique()
        total_sin_iva = df_filtrado['Precio_Sin_IVA'].sum()
        promedio = (total_sin_iva / total_docs) if total_docs > 0 else 0

        kpi_cols = st.columns(3)
        kpi_ph1 = kpi_cols[0].empty()
        kpi_ph2 = kpi_cols[1].empty()
        kpi_ph3 = kpi_cols[2].empty()

        # ==========================================
        # LAYOUT CONDICIONAL SEGÚN EL ROL
        # ==========================================
        if rol in ["admin", "gerente", "gerente_comercial"]:
            # --- LAYOUT GERENCIAL (2 Columnas) ---
            col_izq, col_der = st.columns([1.2, 1])
            
            with col_izq:
                with st.container(border=True, height=450):
                    st.markdown("##### Notas Crédito por Punto de Venta")
                    df_alm = df_filtrado.groupby('Almacen_Corto').agg(Valor=('Precio_Sin_IVA', 'sum')).reset_index().sort_values('Valor', ascending=True)
                    fig_alm = px.bar(df_alm, x='Valor', y='Almacen_Corto', orientation='h', color_discrete_sequence=['#2ca02c'], text_auto='$,.0f')
                    fig_alm.update_layout(plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)', xaxis_title="", yaxis_title="", showlegend=False, margin=dict(l=10, r=10, t=10, b=10), height=400, autosize=True, bargap=0.1)
                    fig_alm.update_yaxes(automargin=True, tickfont_size=15)
                    fig_alm.update_traces(width=0.95, textfont_size=18, textposition="auto", cliponaxis=False)
                    
                    event_alm = st.plotly_chart(fig_alm, use_container_width=True, config=PLOTLY_CONFIG, key="sel_alm_nc", on_select="rerun")
                    
                    if event_alm and event_alm.selection and event_alm.selection.points:
                        nuevo_almacen = event_alm.selection.points[0].get('y')
                        if nuevo_almacen == st.session_state.clicked_almacen_nc:
                            st.session_state.clicked_almacen_nc = None
                            st.session_state.clicked_colab_nc = None
                        else:
                            st.session_state.clicked_almacen_nc = nuevo_almacen
                            st.session_state.clicked_colab_nc = None
                        st.rerun()

            with col_der:
                with st.container(border=True, height=215):
                    st.markdown("##### Notas Crédito precio sin IVA por días")
                    df_r_monto = df_filtrado.groupby('Rango_Dias').agg(Monto=('Precio_Sin_IVA', 'sum')).reset_index()
                    df_r_monto['Orden'] = df_r_monto['Rango_Dias'].map({r: i for i, r in enumerate(ORDEN_RANGOS)})
                    fig_rm = px.bar(df_r_monto.sort_values('Orden'), x='Rango_Dias', y='Monto', text_auto='$,.0f', color_discrete_sequence=['#1f77b4'])
                    fig_rm.update_layout(plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)', yaxis_title="", xaxis_title="", showlegend=False, margin=dict(l=10, r=10, t=10, b=10), height=160, autosize=True)
                    fig_rm.update_traces(textfont_size=18, textposition="outside")
                    fig_rm.update_xaxes(tickfont_size=12)
                    
                    event_rm = st.plotly_chart(fig_rm, use_container_width=True, config=PLOTLY_CONFIG, key="sel_rm_nc", on_select="rerun")
                    if event_rm and event_rm.selection and event_rm.selection.points:
                        nuevo_rango = event_rm.selection.points[0].get('x')
                        if nuevo_rango == st.session_state.clicked_rango_nc:
                            st.session_state.clicked_rango_nc = None
                        else:
                            st.session_state.clicked_rango_nc = nuevo_rango
                        st.rerun()
                
                st.markdown("") 

                with st.container(border=True, height=215):
                    st.markdown("##### Notas Crédito cantidad de documentos por días")
                    df_r_vol = df_filtrado.groupby('Rango_Dias').agg(Volumen=('Documento', 'nunique')).reset_index()
                    df_r_vol['Orden'] = df_r_vol['Rango_Dias'].map({r: i for i, r in enumerate(ORDEN_RANGOS)})
                    fig_rv = px.bar(df_r_vol.sort_values('Orden'), x='Rango_Dias', y='Volumen', text_auto=True, color_discrete_sequence=['#ff7f0e'])
                    fig_rv.update_layout(plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)', yaxis_title="", xaxis_title="", showlegend=False, margin=dict(l=10, r=10, t=10, b=10), height=160, autosize=True)
                    fig_rv.update_traces(textfont_size=18, textposition="outside")
                    fig_rv.update_xaxes(tickfont_size=12)
                    
                    event_rv = st.plotly_chart(fig_rv, use_container_width=True, config=PLOTLY_CONFIG, key="sel_rv_nc", on_select="rerun")
                    if event_rv and event_rv.selection and event_rv.selection.points:
                        nuevo_rango = event_rv.selection.points[0].get('x')
                        if nuevo_rango == st.session_state.clicked_rango_nc:
                            st.session_state.clicked_rango_nc = None
                        else:
                            st.session_state.clicked_rango_nc = nuevo_rango
                        st.rerun()

        else:
            # --- LAYOUT COMERCIAL (Ancho completo para gráficos de días) ---
            with st.container(border=True, height=215):
                st.markdown("##### Notas Crédito precio sin IVA por días")
                df_r_monto = df_filtrado.groupby('Rango_Dias').agg(Monto=('Precio_Sin_IVA', 'sum')).reset_index()
                df_r_monto['Orden'] = df_r_monto['Rango_Dias'].map({r: i for i, r in enumerate(ORDEN_RANGOS)})
                fig_rm = px.bar(df_r_monto.sort_values('Orden'), x='Rango_Dias', y='Monto', text_auto='$,.0f', color_discrete_sequence=['#1f77b4'])
                fig_rm.update_layout(plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)', yaxis_title="", xaxis_title="", showlegend=False, margin=dict(l=10, r=10, t=10, b=10), height=160, autosize=True)
                fig_rm.update_traces(textfont_size=18, textposition="outside")
                fig_rm.update_xaxes(tickfont_size=12)
                
                event_rm = st.plotly_chart(fig_rm, use_container_width=True, config=PLOTLY_CONFIG, key="sel_rm_nc", on_select="rerun")
                if event_rm and event_rm.selection and event_rm.selection.points:
                    nuevo_rango = event_rm.selection.points[0].get('x')
                    if nuevo_rango == st.session_state.clicked_rango_nc:
                        st.session_state.clicked_rango_nc = None
                    else:
                        st.session_state.clicked_rango_nc = nuevo_rango
                    st.rerun()
            
            st.markdown("") 

            with st.container(border=True, height=215):
                st.markdown("##### Notas Crédito cantidad de documentos por días")
                df_r_vol = df_filtrado.groupby('Rango_Dias').agg(Volumen=('Documento', 'nunique')).reset_index()
                df_r_vol['Orden'] = df_r_vol['Rango_Dias'].map({r: i for i, r in enumerate(ORDEN_RANGOS)})
                fig_rv = px.bar(df_r_vol.sort_values('Orden'), x='Rango_Dias', y='Volumen', text_auto=True, color_discrete_sequence=['#ff7f0e'])
                fig_rv.update_layout(plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)', yaxis_title="", xaxis_title="", showlegend=False, margin=dict(l=10, r=10, t=10, b=10), height=160, autosize=True)
                fig_rv.update_traces(textfont_size=18, textposition="outside")
                fig_rv.update_xaxes(tickfont_size=12)
                
                event_rv = st.plotly_chart(fig_rv, use_container_width=True, config=PLOTLY_CONFIG, key="sel_rv_nc", on_select="rerun")
                if event_rv and event_rv.selection and event_rv.selection.points:
                    nuevo_rango = event_rv.selection.points[0].get('x')
                    if nuevo_rango == st.session_state.clicked_rango_nc:
                        st.session_state.clicked_rango_nc = None
                    else:
                        st.session_state.clicked_rango_nc = nuevo_rango
                    st.rerun()

        # ==========================================
        # SECCIÓN INFERIOR (Colaborador y Resumen Almacén)
        # ==========================================
        if rol in ["admin", "gerente", "gerente_comercial"]:
            col_g4, col_g5 = st.columns([1.6, 1])

            with col_g4:
                with st.container(border=True, height=600):
                    st.markdown("##### Notas Crédito por Colaborador")
                    total_general_colab = df_filtrado['Precio_Sin_IVA'].sum()
                    df_col = df_filtrado.groupby('Colaborador').agg(Valor=('Precio_Sin_IVA', 'sum')).reset_index().sort_values('Valor', ascending=True).tail(20)
                    df_col['Porcentaje'] = (df_col['Valor'] / total_general_colab * 100).round(2) if total_general_colab > 0 else 0.0
                    df_col['Colaborador_Label'] = df_col.apply(lambda row: f"{row['Colaborador']} ({row['Porcentaje']:.1f}%)", axis=1)
                    
                    fig_col = px.bar(df_col, x='Valor', y='Colaborador_Label', orientation='h', color_discrete_sequence=['#9467bd'], text_auto='$,.0f', hover_data={'Porcentaje': ':.2f%', 'Colaborador': True})
                    fig_col.update_layout(plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)', xaxis_title="", yaxis_title="", showlegend=False, margin=dict(l=10, r=10, t=10, b=10), height=550, autosize=True, bargap=0.1)
                    fig_col.update_yaxes(automargin=True, tickfont_size=12)
                    fig_col.update_traces(width=0.95, textfont_size=18, textposition="auto", cliponaxis=False)
                    
                    event_col = st.plotly_chart(fig_col, use_container_width=True, config=PLOTLY_CONFIG, key="sel_col_nc", on_select="rerun")
                    if event_col and event_col.selection and event_col.selection.points:
                        label_seleccionado = event_col.selection.points[0].get('y')
                        nombre_colab = label_seleccionado.split(' (')[0].strip() if ' (' in label_seleccionado else label_seleccionado
                        if nombre_colab == st.session_state.clicked_colab_nc:
                            st.session_state.clicked_colab_nc = None
                        else:
                            st.session_state.clicked_colab_nc = nombre_colab
                        st.rerun()

            with col_g5:
                with st.container(border=True, height=600):
                    st.markdown("##### Notas Crédito por Almacén")
                    df_alm_resumen = df_filtrado.groupby('Almacen_Corto').agg(Cant_Docs=('Documento', 'nunique'), Valor_Sin_IVA=('Precio_Sin_IVA', 'sum'), Valor_Con_IVA=('Precio_Total', 'sum')).reset_index().sort_values('Valor_Sin_IVA', ascending=False)
                    df_alm_resumen['Porcentaje'] = (df_alm_resumen['Valor_Sin_IVA'] / total_sin_iva * 100).round(2) if total_sin_iva > 0 else 0.0
                    df_alm_resumen = df_alm_resumen.rename(columns={'Almacen_Corto': 'Almacén', 'Cant_Docs': 'Cant Docs', 'Valor_Sin_IVA': 'S/IVA', 'Valor_Con_IVA': 'C/IVA', 'Porcentaje': '%'})
                    col_config = {"S/IVA": st.column_config.NumberColumn(format="$%,.0f"), "C/IVA": st.column_config.NumberColumn(format="$%,.0f"), "%": st.column_config.NumberColumn(format="%.1f%%")}
                    st.dataframe(df_alm_resumen, use_container_width=True, hide_index=True, height=500, column_config=col_config, key="tabla_resumen_alm_nc")
        else:
            # Mensaje elegante para comerciales en lugar de espacio vacío
            st.markdown("---")
            st.info("*Despliega la ventana y obtén información de tus notas crédito*")

        # ==========================================
        # 5. RELLENAR KPIs (Visible para todos)
        # ==========================================
        with kpi_ph1.container(border=True, height=90):
            st.metric("Total Notas Crédito", value=f"{total_docs:,}")
        with kpi_ph2.container(border=True, height=90):
            st.metric("Total Sin IVA", value=f"${total_sin_iva:,.2f}")
        with kpi_ph3.container(border=True, height=90):
            st.metric("Valor Promedio", value=f"${promedio:,.2f}")

        # ==========================================
        # 6. DETALLE EN DESPLEGABLE (Con N° Cliente y Cliente agregados)
        # ==========================================
        with st.expander("Ver Detalle de notas crédito abiertas", expanded=False):
            # ✅ Se agregaron 'Numero_Cliente' y 'Cliente'
            columnas_mostrar = ['Documento', 'Numero_Cliente', 'Cliente', 'Almacen_Corto', 'Fecha_Texto', 'Dias', 'Rango_Dias', 'Colaborador', 'Estado_IVA', 'Precio_Sin_IVA', 'Precio_Total']
            columnas_existentes = [col for col in columnas_mostrar if col in df_filtrado.columns]
            df_tabla = df_filtrado[columnas_existentes].copy()
            
            rename_dict = {
                'Documento': 'Documento', 
                'Numero_Cliente': 'N° Cliente', # ✅ Agregado
                'Cliente': 'Cliente',           # ✅ Agregado
                'Almacen_Corto': 'Punto de Venta', 
                'Fecha_Texto': 'Fecha', 
                'Dias': 'Días', 
                'Rango_Dias': 'Rango', 
                'Colaborador': 'Comercial', 
                'Estado_IVA': 'IVA', 
                'Precio_Sin_IVA': 'Total S/IVA', 
                'Precio_Total': 'Total C/IVA'
            }
            rename_dict = {k: v for k, v in rename_dict.items() if k in df_tabla.columns}
            df_tabla = df_tabla.rename(columns=rename_dict)
            
            if 'Días' in df_tabla.columns: df_tabla = df_tabla.sort_values(by='Días', ascending=False)
            
            column_config_tabla = {
                "Total S/IVA": st.column_config.NumberColumn(format="$%,.2f"), 
                "Total C/IVA": st.column_config.NumberColumn(format="$%,.2f"), 
                "Días": st.column_config.NumberColumn(format="%d")
            }
            st.dataframe(df_tabla, use_container_width=True, hide_index=True, height=altura_tabla(df_tabla, maximo=400), column_config=column_config_tabla, key="tabla_detallada_nc")

# ==========================================
# PESTAÑA 2: GESTIÓN OPERATIVA
# ==========================================
with tab_gestion:
    if df_seguro.empty or df_filtrado.empty:
        st.warning("No hay datos disponibles para gestión operativa con los filtros actuales.")
    else:
        docs_activos = df_seguro['Documento_Str'].unique().tolist()
        df_b = conn.query("SELECT id, fecha, documento, usuario AS registrado_por, novedad, comentario_gerencia FROM app.novedades_notas_credito ORDER BY fecha DESC", ttl=10)

        if not df_b.empty and len(docs_activos) > 0:
            df_b['doc_str'] = df_b['documento'].astype(str).str.strip()
            df_b_filtrado = df_b[df_b['doc_str'].isin(docs_activos)].copy().drop(columns=['doc_str'])
        else:
            df_b_filtrado = pd.DataFrame()

        df_fechas_tabla = df_filtrado.copy() 
        columna_dias = None
        if not df_fechas_tabla.empty:
            for nombre in ['Días', 'Dias', 'Dias_Transcurridos', 'Dias_Transcurrido']:
                if nombre in df_fechas_tabla.columns:
                    columna_dias = nombre
                    break
            if columna_dias is None and 'Fecha_Contabilizacion' in df_fechas_tabla.columns:
                try:
                    df_fechas_tabla['Dias_Calculados'] = (pd.Timestamp.now().normalize() - df_fechas_tabla['Fecha_Contabilizacion']).dt.days
                    columna_dias = 'Dias_Calculados'
                except: pass

        df_criticos = pd.DataFrame()
        if not df_fechas_tabla.empty and columna_dias is not None:
            df_criticos = df_fechas_tabla[df_fechas_tabla[columna_dias] > 5].drop_duplicates(subset=['Documento']).sort_values(columna_dias, ascending=False)

        if not df_criticos.empty:
            k1, k2 = st.columns(2)
            with k1:
                with st.container(border=True, height=90): st.metric("Total Docs Críticos", f"{df_criticos['Documento'].nunique()}")
            with k2:
                with st.container(border=True, height=90): st.metric("Suma Total Crítica", f"${df_criticos['Precio_Sin_IVA'].sum():,.2f}")
        elif df_fechas_tabla.empty: st.info("Sin datos para evaluar.")
        elif columna_dias is None: st.warning("La columna de días no está disponible.")
        else: st.success("No tienes notas crédito críticas con los filtros seleccionados.")

        col_gest_izq, col_gest_der = st.columns([2, 1])

        with col_gest_izq:
            with st.container(border=True, height=450):
                st.markdown("##### Detalle de Notas Crédito críticas (> 5 días)")
                if not df_criticos.empty:
                    cols_crit = ['Documento', 'Cliente', 'Almacen_Corto', columna_dias, 'Precio_Sin_IVA']
                    cols_crit = [c for c in cols_crit if c in df_criticos.columns]
                    df_crit_mostrar = df_criticos[cols_crit].copy()
                    if columna_dias != 'Días': df_crit_mostrar = df_crit_mostrar.rename(columns={columna_dias: 'Días'})
                    st.dataframe(df_crit_mostrar, use_container_width=True, hide_index=True, height=380, column_config={"Precio_Sin_IVA": st.column_config.NumberColumn("Total sin IVA", format="$%,.2f")}, key="tabla_criticos_nc")
                else: 
                    st.dataframe(df_criticos.head(0), use_container_width=True, hide_index=True, height=380)
                    st.caption("No hay notas crédito críticas.")

            with st.container(border=True, height=450):
                st.markdown("##### Historial de novedades")
                if df_b_filtrado.empty: 
                    st.dataframe(df_b_filtrado.head(0), use_container_width=True, hide_index=True, height=380)
                    st.caption("Sin novedades activas.")
                else:
                    st.dataframe(df_b_filtrado, use_container_width=True, hide_index=True, height=380, column_config={"novedad": st.column_config.TextColumn(width="medium"), "comentario_gerencia": st.column_config.TextColumn(width="medium")}, key="tabla_novedades_nc")

        with col_gest_der:
            if rol not in ["comercial"] and not df_b_filtrado.empty:
                with st.container(border=True):
                    st.markdown("##### Gestionar novedad")
                    opciones_map = {f"ID: {r['id']} | Doc: {r['documento']} | {str(r['novedad'])[:30]}": r['id'] for _, r in df_b_filtrado.iterrows()}
                    opcion_sel = st.selectbox("Seleccionar:", [""] + list(opciones_map.keys()), key="doc_sel_gestion_nc")
                    if opcion_sel:
                        id_sel = opciones_map[opcion_sel]
                        fila_sel = df_b_filtrado[df_b_filtrado['id'] == id_sel].iloc[0]
                        if rol in ["admin", "gerente", "gerente_comercial", "admin_punto"]:
                            com_txt = st.text_area("Instrucción:", key=f"t_area_nc_{id_sel}", height=100)
                            if st.button("💾 Guardar", key=f"btn_save_nc_{id_sel}", use_container_width=True):
                                if com_txt.strip():
                                    with conn.session as session:
                                        session.execute(text("UPDATE app.novedades_notas_credito SET comentario_gerencia = :c WHERE id = :id"), {"c": com_txt, "id": int(id_sel)})
                                        session.commit()
                                    st.success("Guardado."); st.rerun()
                                else: st.warning("Escribe algo.")
                        es_dueno = str(fila_sel['registrado_por']).strip() == str(usuario_logueado).strip()
                        if rol in ["admin", "gerente"] or es_dueno:
                            if st.button("🗑️ Borrar", type="primary", key=f"btn_del_nc_{id_sel}", use_container_width=True):
                                with conn.session as session:
                                    session.execute(text("DELETE FROM app.novedades_notas_credito WHERE id = :id"), {"id": int(id_sel)})
                                    session.commit()
                                st.success("Eliminado."); st.rerun()

            with st.container(border=True):
                st.markdown("##### Registrar nueva novedad")
                with st.form("form_registro_nc", clear_on_submit=True):
                    if not df_fechas_tabla.empty and columna_dias is not None:
                        docs_opc = df_fechas_tabla[df_fechas_tabla[columna_dias] > 5]['Documento'].dropna().unique()
                        doc_sel = st.selectbox("Nro Nota Crédito:", sorted(docs_opc)) if len(docs_opc) > 0 else None
                    else: doc_sel = None
                    estado_opc = st.selectbox("Estado:", ["En revisión", "Pendiente aprobación", "Aplicada parcialmente", "Rechazada", "Cancelada"])
                    obs_opc = st.text_area("Observaciones:", height=100)
                    if st.form_submit_button("Guardar novedad", use_container_width=True):
                        if not doc_sel or not obs_opc: st.warning("Complete campos.")
                        else:
                            with conn.session as session:
                                session.execute(text("INSERT INTO app.novedades_notas_credito (documento, usuario, novedad) VALUES (:d, :u, :n)"), {"d": str(doc_sel).strip(), "u": usuario_logueado, "n": f"[{estado_opc}] {obs_opc}"})
                                session.commit()
                            st.success("Guardado."); st.rerun()