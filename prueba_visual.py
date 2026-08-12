import streamlit as st
import plotly.express as px
import pandas as pd

st.set_page_config(page_title="Prueba Visual", layout="wide")
st.title("🧪 Comparativa Visual: El Antes y el Después")

# Simulamos tus datos de almacén (como los que tienes en cotizaciones)
datos = {
    'Almacen_Corto': ['EJECOM', 'ALM134', 'Q1', 'Q3', 'VILL', 'ARME'],
    'Total_Sin_IVA': [45000000, 32000000, 28000000, 15000000, 8500000, 5200000],
    'Cantidad_Docs': [120, 85, 65, 40, 22, 15]
}
df = pd.DataFrame(datos)

col1, col2 = st.columns(2)

with col1:
    st.subheader("❌ EL ANTES (Estilo Básico/Altair)")
    st.bar_chart(df, x='Almacen_Corto', y='Total_Sin_IVA') # Gráfico nativo aburrido

with col2:
    st.subheader("✅ EL DESPUÉS (Estilo Plotly Premium)")
    # Aquí está la magia de Plotly
    fig = px.bar(
        df, 
        x='Total_Sin_IVA', 
        y='Almacen_Corto', 
        orientation='h',
        color='Almacen_Corto',
        color_discrete_sequence=px.colors.sequential.Viridis, # Paleta de colores profesional
        text_auto='$,.0f' # Formato de moneda automático sobre las barras
    )
    
    # Configuración estética para que se vea como un Software SaaS
    fig.update_layout(
        plot_bgcolor='rgba(0,0,0,0)', # Fondo invisible
        paper_bgcolor='rgba(0,0,0,0)',
        xaxis_title="Monto Total ($)",
        yaxis_title="",
        showlegend=False,
        font=dict(family="Arial, sans-serif", size=12, color="black"),
        margin=dict(l=10, r=20, t=10, b=10)
    )
    fig.update_xaxes(showgrid=True, gridwidth=1, gridcolor='#f0f0f0') # Líneas de guía suaves
    fig.update_traces(textfont_size=12, textangle=0, textposition="outside", cliponaxis=False)
    
    st.plotly_chart(fig, use_container_width=True)

st.markdown("---")
st.caption("Ejecuta en tu terminal: `streamlit run prueba_visual.py` para ver esto en vivo.")