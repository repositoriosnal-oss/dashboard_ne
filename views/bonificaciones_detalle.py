"""
DETALLE BONO — Auditoría detallada de pagos, anticipos y conciliación
Organizado por modo: Almacenes (réplica Power BI) | Ejecutivos Comerciales (EJECOM)
Estética inspirada en Variables del Bono con tarjetas CSS y contenedores limpios
"""
import streamlit as st
import pandas as pd
import numpy as np
from src.conexion_db import (cargar_bonos_pagos, cargar_bonos_anticipos,
                             cargar_pagos_consolidados, cargar_pagos_a_cuenta,
                             resumen_pagos_a_cuenta)
from datetime import date
from html import escape
from src.conexion_db import (
    cargar_bonos_pagos, 
    cargar_bonos_anticipos, 
    cargar_pagos_consolidados
)

# ============================================================
# CONFIGURACIÓN DE PÁGINA
# ============================================================
st.set_page_config(
    page_title="Detalle Bono",
    page_icon=":material/redeem:",
    layout="wide",
    initial_sidebar_state="expanded",
)

# 🔐 CONTROL DE ACCESO
ROLES_BONIFICACIONES = ["admin", "gerente", "gerente_comercial"]
if st.session_state.get("rol_actual") not in ROLES_BONIFICACIONES:
    st.error("⛔ Acceso denegado. El módulo de Bonificaciones está disponible únicamente para Gerencia y Administrador.")
    st.stop()

# ============================================================
# ESTILO Y FORMATO (inspirado en bono_variables.py)
# ============================================================
st.markdown("""
<style>
.kpi-grid { 
    display: grid; 
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 12px; 
    margin: 8px 0 20px 0; 
}
.kpi { 
    border: 1px solid rgba(128,128,128,.35); 
    border-radius: 8px; 
    padding: 14px 16px; 
    min-width: 0;
    background: rgba(255,255,255,0.02);
    transition: all 0.2s ease;
}
.kpi:hover {
    border-color: rgba(31, 119, 180, 0.5);
    box-shadow: 0 2px 8px rgba(0,0,0,0.1);
}
.kpi-l { 
    font-size: .75rem; 
    letter-spacing: .04em; 
    text-transform: uppercase; 
    opacity: .72; 
    line-height: 1.3; 
    font-weight: 600;
}
.kpi-v { 
    font-size: clamp(1.1rem, 1.8vw, 1.5rem); 
    font-weight: 700; 
    line-height: 1.3; 
    margin-top: 6px;
    font-variant-numeric: tabular-nums; 
    overflow-wrap: anywhere;
    color: #1f77b4;
}
.kpi-s { 
    font-size: .75rem; 
    opacity: .65; 
    margin-top: 4px; 
    line-height: 1.3; 
    overflow-wrap: anywhere; 
}
.section-header {
    border-left: 4px solid #1f77b4;
    padding-left: 12px;
    margin: 24px 0 16px 0;
}
.section-header h3 {
    margin: 0;
    font-size: 1.3rem;
    font-weight: 600;
}
.section-header p {
    margin: 4px 0 0 0;
    font-size: 0.85rem;
    opacity: 0.75;
}
</style>
""", unsafe_allow_html=True)

def tarjetas(items):
    """Renderiza una grid de tarjetas KPI estilo bono_variables"""
    html = ['<div class="kpi-grid">']
    for it in items:
        lab, val = it[0], it[1]
        sub = it[2] if len(it) > 2 else ""
        s = f'<div class="kpi-s">{escape(sub)}</div>' if sub else ""
        html.append(f'<div class="kpi"><div class="kpi-l">{escape(lab)}</div>'
                   f'<div class="kpi-v">{escape(val)}</div>{s}</div>')
    html.append("</div>")
    st.markdown("".join(html).replace("$  ", "$"), unsafe_allow_html=True)

def fmt_cop(v) -> str:
    """Formatea valores monetarios en pesos colombianos"""
    if v is None or pd.isna(v):
        return "-"
    return f"${v:,.0f}"

def fmt_cop_dec(v) -> str:
    """Formatea valores monetarios con decimales"""
    if v is None or pd.isna(v):
        return "-"
    return f"${v:,.2f}"

# ============================================================
# ENCABEZADO Y CARGA
# ============================================================
st.title("Detalle Bono")
st.caption("Auditoría detallada de pagos aplicados, anticipos diferidos y conciliación consolidada. "
           "Fuente única de verdad para la liquidación de bonificaciones.")

ALMACEN_EJECOM = "EJECUTIVOS COMERCIALES"

df = cargar_bonos_pagos()
if df.empty:
    st.warning("Sin datos de pagos aún. Ejecuta Forzar Sincronización SAP.")
    st.stop()

# 🔒 FILTRO FIJO: solo pagos aplicados en el mes.
df = df[df["estado_comision"] == "Pago Aplicado en el Mes"].copy()
df["nom_empleado"] = df["nom_empleado"].fillna("").replace("", "(Sin vendedor)")

# ============================================================
# FILTROS LATERALES (rango + modo de visualización)
# ============================================================
with st.sidebar:
    st.markdown("### 🔍 Filtros")
    f_min = df["fecha_aplicacion"].min().date()
    f_max = df["fecha_aplicacion"].max().date()
    
    def _clamp(d):
        return min(max(d, f_min), f_max)
    
    _def_hasta = f_max
    _def_desde = date(f_max.year, f_max.month, 1)   # primer día del último mes con datos
    col_f1, col_f2 = st.columns(2)
    with col_f1:
        desde = st.date_input("Desde:", value=_clamp(_def_desde),
                              min_value=f_min, max_value=f_max, key="sb_bono_desde")
    with col_f2:
        hasta = st.date_input("Hasta:", value=_clamp(_def_hasta),
                              min_value=f_min, max_value=f_max, key="sb_bono_hasta")
    
    st.markdown("---")
    modo = st.radio(
        "Modo de visualización",
        ["Almacenes", "Ejecutivos Comerciales"],
        key="sb_bono_modo",
        help="Almacenes: réplica Power BI por punto de venta. "
             "Ejecutivos: solo serie EJECOM, agrupado por comercial.",
    )
    
    alm_sel, ejec_sel = [], []
    if modo == "Almacenes":
        alm_disp = sorted(df["nombre_almacen"].dropna().unique().tolist())
        alm_sel = st.multiselect("Almacén:", alm_disp, default=alm_disp, key="sb_bono_alm")
    else:
        ej_disp = sorted(df.loc[df["nombre_almacen"] == ALMACEN_EJECOM, "nom_empleado"].unique().tolist())
        if "(Sin vendedor)" in ej_disp:
            ej_disp.remove("(Sin vendedor)")
            ej_disp.append("(Sin vendedor)")
        ejec_sel = st.multiselect("Ejecutivo comercial:", ej_disp, default=ej_disp, key="sb_bono_ejec")
        st.caption("En este modo todas las tablas se filtran a la serie EJECOM "
                   "y se agrupan por comercial.")

# ============================================================
# FILTRADO POR RANGO + MODO
# ============================================================
if isinstance(desde, date) and isinstance(hasta, date) and desde > hasta:
    st.warning("Rango inválido: Desde no puede ser mayor que Hasta.")
    st.stop()

dfp = df[
    (df["fecha_aplicacion"].dt.date >= desde)
    & (df["fecha_aplicacion"].dt.date <= hasta)
].copy()

if modo == "Almacenes":
    if alm_sel:
        dfp = dfp[dfp["nombre_almacen"].isin(alm_sel)]
else:
    dfp = dfp[dfp["nombre_almacen"] == ALMACEN_EJECOM]
    if ejec_sel:
        dfp = dfp[dfp["nom_empleado"].isin(ejec_sel)]

if dfp.empty:
    st.warning("Sin pagos en el rango y filtros seleccionados.")
    st.stop()

# 🆕 Anticipos del mes presente (Pagos a Cuenta) con los filtros actuales
pac = resumen_pagos_a_cuenta(desde, hasta,
                             alm_sel if modo == "Almacenes" else [ALMACEN_EJECOM])
df_pac = pac["filas"]


# ============================================================
# 📊 TARJETAS DE RESUMEN EJECUTIVO (KPIs principales)
# ============================================================
df_ant = cargar_bonos_anticipos()
ant = pd.DataFrame()
if not df_ant.empty:
    ant = df_ant[
        (df_ant["fecha_recibo_pago"].dt.date >= desde)
        & (df_ant["fecha_recibo_pago"].dt.date <= hasta)
    ].copy()
    if modo == "Almacenes":
        if alm_sel:
            ant = ant[ant["nombre_almacen"].isin(alm_sel)]
    else:
        ant = ant[ant["nombre_almacen"] == ALMACEN_EJECOM]
    
    if not ant.empty:
        ant["liq_app"] = np.where(
            ant["monto_validado"] > 0,
            (ant["monto_validado"] / 1.19) * 0.975 * 0.008 * ant["pct_bono"],
            0
        )

dfc = pd.DataFrame()
df_cons = cargar_pagos_consolidados()
if not df_cons.empty:
    dfc = df_cons[
        (df_cons["fecha_recibo"].dt.date >= desde)
        & (df_cons["fecha_recibo"].dt.date <= hasta)
    ].copy()
    if modo == "Almacenes":
        if alm_sel:
            dfc = dfc[dfc["nombre_almacen"].isin(alm_sel)]
    else:
        dfc = dfc[dfc["nombre_almacen"] == ALMACEN_EJECOM]

total_liq_pagos = float(dfp["liquidacion_ok"].sum())
total_liq_anticipos = float(ant["liq_app"].sum()) if not ant.empty else 0.0
total_bruto = total_liq_pagos + total_liq_anticipos
total_descuento = float((dfc["diferencia"] * 0.008).sum()) if not dfc.empty else 0.0
gran_total_bono = total_bruto - total_descuento

st.markdown('<div class="section-header"><h3>📊 Resumen Primera Etapa del Bono</h3>'
            '<p>Liquidación de pagos aplicados, anticipos diferidos y descuentos por conciliación</p></div>',
            unsafe_allow_html=True)

tarjetas([
    ("Total Pagos Aplicados", fmt_cop_dec(total_liq_pagos), 
     f"{len(dfp)} pagos en el período"),
    ("Total Anticipos Diferidos", fmt_cop_dec(total_liq_anticipos),
     f"{len(ant)} anticipos con Monto Final > 0" if not ant.empty else "Sin anticipos"),
    ("Descuento Conciliación", f"-{fmt_cop_dec(total_descuento)}",
     "0.8% de diferencia en pagos consolidados"),
    ("Gran Total Bono Ajustado", fmt_cop_dec(gran_total_bono),
     "Pagos + Anticipos − Descuentos"),
])

# ============================================================
# TABLA DE PAGOS (réplica Power BI; en modo Ejecutivos muestra el Comercial)
# ============================================================
st.markdown('<div class="section-header"><h3>Detalle de Pagos Aplicados</h3>'
            '<p>Réplica exacta del modelo Power BI: pagos aplicados en el mes con liquidación OK</p></div>',
            unsafe_allow_html=True)

agg_map = {
    "Almacen": ("nombre_almacen", "first"),
    "Suma_de_Total_Pagado": ("total_pagado", "sum"),
    "Suma_de_Total_Factura_Original": ("total_factura_original", "sum"),
    "Suma_de_Monto_Validado": ("monto_validado", "sum"),
    "Suma_de_Valor_Base": ("valor_base", "sum"),
    "Suma_de_Liquidacion_Ok": ("liquidacion_ok", "sum"),
}
if modo != "Almacenes":
    agg_map["Comercial"] = ("nom_empleado", "first")

tabla = dfp.groupby(["pago_nro", "estado_comision"], as_index=False).agg(**agg_map).sort_values("pago_nro")
fila_total = pd.DataFrame([{
    "pago_nro": "Total",
    "estado_comision": "",
    **({"Comercial": ""} if modo != "Almacenes" else {}),
    "Almacen": "",
    "Suma_de_Total_Pagado": tabla["Suma_de_Total_Pagado"].sum(),
    "Suma_de_Total_Factura_Original": tabla["Suma_de_Total_Factura_Original"].sum(),
    "Suma_de_Monto_Validado": tabla["Suma_de_Monto_Validado"].sum(),
    "Suma_de_Valor_Base": tabla["Suma_de_Valor_Base"].sum(),
    "Suma_de_Liquidacion_Ok": tabla["Suma_de_Liquidacion_Ok"].sum(),
}])

tabla_mostrar = pd.concat([tabla, fila_total], ignore_index=True)

rename_pagos = {
    "pago_nro": "Pago Nro",
    "estado_comision": "Estado Comisión",
    "Comercial": "Comercial",
    "Almacen": "Almacén",
    "Suma_de_Total_Pagado": "Suma de Total Pagado",
    "Suma_de_Total_Factura_Original": "Suma de Total Factura Original",
    "Suma_de_Monto_Validado": "Suma de Monto Validado",
    "Suma_de_Valor_Base": "Suma de Valor Base",
    "Suma_de_Liquidacion_Ok": "Suma de Liquidacion Ok",
}

cols_pagos = ["pago_nro", "estado_comision"]
cols_pagos += ["Comercial"] if modo != "Almacenes" else ["Almacen"]
cols_pagos += ["Suma_de_Total_Pagado", "Suma_de_Total_Factura_Original",
               "Suma_de_Monto_Validado", "Suma_de_Valor_Base", "Suma_de_Liquidacion_Ok"]

with st.container(border=True):
    st.dataframe(
        tabla_mostrar[cols_pagos].rename(columns=rename_pagos),
        use_container_width=True,
        hide_index=True,
        height=560,
        column_config={
            "Comercial": st.column_config.TextColumn(width="medium"),
            "Almacén": st.column_config.TextColumn(width="medium"),
            "Suma de Total Pagado": st.column_config.NumberColumn(format="$%,.2f"),
            "Suma de Total Factura Original": st.column_config.NumberColumn(format="$%,.2f"),
            "Suma de Monto Validado": st.column_config.NumberColumn(format="$%,.2f"),
            "Suma de Valor Base": st.column_config.NumberColumn(format="$%,.2f"),
            "Suma de Liquidacion Ok": st.column_config.NumberColumn(format="$%,.2f"),
        },
    )

# ============================================================
# 🎁 TABLA DE ANTICIPOS
# ============================================================
st.markdown('<div class="section-header"><h3> Anticipos (Aplicaciones Diferidas)</h3>'
            '<p>Recibos con aplicación diferida o saldo a cuenta: Monto Final > 0</p></div>',
            unsafe_allow_html=True)

if ant.empty:
    st.info("Sin anticipos en el rango y filtros seleccionados.")
else:
    grupo = ant.groupby("pago_nro").agg(
        Almacen=("nombre_almacen", "first"),
        total_pago_original=("total_pago_original", "first"),
        monto_aplicado=("monto_aplicado", "sum"),
        saldo_a_cuenta=("saldo_a_cuenta", "first"),
        monto_validado=("monto_validado", "sum"),
        liquidacion_ok_apps=("liq_app", "sum"),
    ).reset_index()
    
    grupo["monto_final"] = np.where(grupo["monto_validado"] > 0,
                                     grupo["monto_validado"], grupo["saldo_a_cuenta"])
    grupo = grupo[grupo["monto_final"] > 0].copy()
    
    if grupo.empty:
        st.info("Sin anticipos con Monto Final > 0 en el rango y filtros seleccionados.")
    else:
        grupo["valor_base"] = (grupo["monto_final"] / 1.19) * 0.975
        grupo["liquidacion_ok"] = np.where(grupo["monto_validado"] > 0,
                                           grupo["liquidacion_ok_apps"], 0)
        
        pagos_liq_total = float(dfp["liquidacion_ok"].sum())
        tabla_ant = grupo[["pago_nro", "Almacen", "total_pago_original", "monto_aplicado",
                           "saldo_a_cuenta", "monto_final", "liquidacion_ok"]].copy()
        tabla_ant["Valor_Bono"] = pagos_liq_total + tabla_ant["liquidacion_ok"]
        
        fila_total_ant = pd.DataFrame([{
            "pago_nro": "Total",
            "Almacen": "",
            "total_pago_original": tabla_ant["total_pago_original"].sum(),
            "monto_aplicado": tabla_ant["monto_aplicado"].sum(),
            "saldo_a_cuenta": tabla_ant["saldo_a_cuenta"].sum(),
            "monto_final": tabla_ant["monto_final"].sum(),
            "liquidacion_ok": tabla_ant["liquidacion_ok"].sum(),
            "Valor_Bono": pagos_liq_total + tabla_ant["liquidacion_ok"].sum(),
        }])
        
        tabla_ant_mostrar = pd.concat([tabla_ant, fila_total_ant], ignore_index=True)
        
        with st.container(border=True):
            st.dataframe(
                tabla_ant_mostrar.rename(columns={
                    "pago_nro": "Pago Nro",
                    "Almacen": "Almacén",
                    "total_pago_original": "Suma de Total Pago Original",
                    "monto_aplicado": "Suma de Monto Aplicado",
                    "saldo_a_cuenta": "Suma de Saldo a Cuenta",
                    "monto_final": "Suma de Monto Final",
                    "liquidacion_ok": "Suma de Liquidacion Ok",
                    "Valor_Bono": "Valor Bono",
                }),
                use_container_width=True,
                hide_index=True,
                height=420,
                column_config={
                    "Almacén": st.column_config.TextColumn(width="medium"),
                    "Suma de Total Pago Original": st.column_config.NumberColumn(format="$%,.2f"),
                    "Suma de Monto Aplicado": st.column_config.NumberColumn(format="$%,.2f"),
                    "Suma de Saldo a Cuenta": st.column_config.NumberColumn(format="$%,.2f"),
                    "Suma de Monto Final": st.column_config.NumberColumn(format="$%,.2f"),
                    "Suma de Liquidacion Ok": st.column_config.NumberColumn(format="$%,.2f"),
                    "Valor Bono": st.column_config.NumberColumn(format="$%,.2f"),
                },
            )
        
        if modo == "Ejecutivos Comerciales":
            st.caption("ℹ️ Los anticipos no traen código de vendedor desde SAP: en modo "
                      "Ejecutivos se muestran y suman a nivel EJECOM, sin atribuirlos a un comercial.")

# ============================================================
# 🚩 TABLA DE CONTROL: PAGOS EN CONCILIACIÓN CONSOLIDADA
# ============================================================
st.markdown('<div class="section-header"><h3>🚩 Control: Pagos en Conciliación Consolidada</h3>'
            '<p>Recibos cuya suma aplicada supera el valor del recibo (conciliaciones con N pagos y M facturas). '
            'Regla: diferencia > $100.</p></div>',
            unsafe_allow_html=True)

if dfc.empty:
    st.info("Sin pagos consolidados en el rango y filtros seleccionados.")
else:
    tabla_cons = dfc[[
        "pago_nro", "fecha_recibo", "nombre_almacen", "nombre_cliente",
        "pr", "vr_aplicado", "diferencia", "n_facturas", "n_aplicaciones",
    ]].copy()
    tabla_cons["descuento_liq"] = tabla_cons["diferencia"] * 0.008
    
    fila_total_cons = pd.DataFrame([{
        "pago_nro": "Total",
        "fecha_recibo": pd.NaT,
        "nombre_almacen": "",
        "nombre_cliente": f"{len(tabla_cons)} pagos observados",
        "pr": tabla_cons["pr"].sum(),
        "vr_aplicado": tabla_cons["vr_aplicado"].sum(),
        "diferencia": tabla_cons["diferencia"].sum(),
        "descuento_liq": tabla_cons["descuento_liq"].sum(),
        "n_facturas": None,
        "n_aplicaciones": None,
    }])
    
    tabla_cons_mostrar = pd.concat([tabla_cons, fila_total_cons], ignore_index=True)
    
    with st.container(border=True):
        st.dataframe(
            tabla_cons_mostrar.rename(columns={
                "pago_nro": "Pago Nro",
                "fecha_recibo": "Fecha Recibo",
                "nombre_almacen": "Almacén",
                "nombre_cliente": "Cliente",
                "pr": "Total Recibo (PR)",
                "vr_aplicado": "VR Aplicado",
                "diferencia": "Diferencia",
                "descuento_liq": "Descuento Liquidación",
                "n_facturas": "Facturas",
                "n_aplicaciones": "Aplicaciones",
            }),
            use_container_width=True,
            hide_index=True,
            height=420,
            column_config={
                "Fecha Recibo": st.column_config.DateColumn(format="DD/MM/YYYY"),
                "Almacén": st.column_config.TextColumn(width="medium"),
                "Cliente": st.column_config.TextColumn(width="large"),
                "Total Recibo (PR)": st.column_config.NumberColumn(format="$%,.2f"),
                "VR Aplicado": st.column_config.NumberColumn(format="$%,.2f"),
                "Diferencia": st.column_config.NumberColumn(format="$%,.2f"),
                "Descuento Liquidación": st.column_config.NumberColumn(format="$%,.2f"),
            },
        )

    # ============================================================
    # 📥 ANTICIPOS DEL MES PRESENTE (PAGOS A CUENTA · ORCT.PayNoDoc = 'Y')
    # ============================================================
    st.markdown("---")
    st.subheader("📥 Anticipos del Mes Presente (Pagos a Cuenta)")
    st.caption(
        "Recibos chulados como 'Pago a Cuenta' en SAP (`ORCT.PayNoDoc = 'Y`). "
        "Concepto **Anticipo** (`U_NAL_Tipo_Pago`) → suma como anticipo del mes y exige "
        "pedido abierto (`U_pedAbierto`). Concepto **Pago a Cartera** → NO suma. "
        "Estos totales son INFORMATIVOS: no entran al Gran Total Bono ni al Valor Bono por "
        "almacén hasta que Gerencia defina si se liquidan el mismo mes. Cuando el anticipo se "
        "aplique a una factura en un mes siguiente, ya entra por la tabla de Anticipos normal."
    )
    
    # 1. Cargar y filtrar (Mantiene tu lógica de fechas y modos)
    df_pac = cargar_pagos_a_cuenta() 
    
    if df_pac.empty:
        st.info("No hay pagos a cuenta registrados en la base de datos.")
    else:
        # Detectar nombre de columna de fecha por si en tu DB varía
        col_fecha = 'fecha_recibo_pago' if 'fecha_recibo_pago' in df_pac.columns else 'fecha_recibo'
        
        pac_filtrado = df_pac[
            (df_pac[col_fecha].dt.date >= desde) & 
            (df_pac[col_fecha].dt.date <= hasta)
        ].copy()
        
        # Aplicar filtros del sidebar (Almacenes o Ejecutivos)
        if modo == "Almacenes" and alm_sel:
            pac_filtrado = pac_filtrado[pac_filtrado["nombre_almacen"].isin(alm_sel)]
        elif modo != "Almacenes":
            pac_filtrado = pac_filtrado[pac_filtrado["nombre_almacen"] == ALMACEN_EJECOM]
            
        if pac_filtrado.empty:
            st.info("No hay anticipos registrados (pagos a cuenta) en el rango y filtros seleccionados.")
        else:
            # 2. Preparar datos y calcular métricas para la interfaz
            # Normalizar conceptos (por si vienen en mayúsculas/minúsculas)
            concepto_str = pac_filtrado.get("concepto_anticipo", pd.Series([""] * len(pac_filtrado))).astype(str).str.upper()
            pac_filtrado["es_anticipo"] = concepto_str.str.contains("ANTICIPO", na=False)
            pac_filtrado["es_cartera"] = concepto_str.str.contains("CARTERA", na=False)
            
            # Detectar si falta pedido abierto
            tiene_pedido = pac_filtrado.get("pedido_abierto", pd.Series([False] * len(pac_filtrado))).astype(bool)
            pac_filtrado["pedido_faltante"] = pac_filtrado["es_anticipo"] & (~tiene_pedido)
            
            # Columna de monto (por si en tu DB se llama 'total_pago' o 'total_pago_original')
            col_monto = 'total_pago_original' if 'total_pago_original' in pac_filtrado.columns else 'total_pago'
            if "liq_estimada" not in pac_filtrado.columns:
                pac_filtrado["liq_estimada"] = 0.0  # Placeholder si no viene de la DB
            
            # Diccionario 'pac' que exige la interfaz visual
            pac = {
                'recibidos': float(pac_filtrado[col_monto].sum()),
                'anticipo': float(pac_filtrado.loc[pac_filtrado["es_anticipo"], col_monto].sum()),
                'cartera': float(pac_filtrado.loc[pac_filtrado["es_cartera"], col_monto].sum()),
                'sin_concepto': float(pac_filtrado.loc[~pac_filtrado["es_anticipo"] & ~pac_filtrado["es_cartera"], col_monto].sum()),
                'liq_estimada': float(pac_filtrado["liq_estimada"].sum())
            }
            
            # Sobreescribimos df_pac con el filtrado para la tabla final
            df_pac = pac_filtrado 

            # 3. INTERFAZ VISUAL (Tarjetas KPI y Alertas)
            k1, k2, k3, k4, k5 = st.columns(5)
            with k1.container(border=True, height=120):
                st.metric("Recibido a Cuenta", f"${pac['recibidos']:,.2f}",
                          help="Total de recibos con PayNoDoc = 'Y' en el rango")
            with k2.container(border=True, height=120):
                st.metric("Concepto ANTICIPO (sí cuenta)", f"${pac['anticipo']:,.2f}")
            with k3.container(border=True, height=120):
                st.metric("Concepto PAGO A CARTERA (no cuenta)", f"${pac['cartera']:,.2f}")
            with k4.container(border=True, height=120):
                st.metric("Sin concepto", f"${pac['sin_concepto']:,.2f}",
                          help="Recibos a cuenta sin U_NAL_Tipo_Pago: revisar con contabilidad")
            with k5.container(border=True, height=120):
                st.metric("LIQ estimada anticipos (info)", f"${pac['liq_estimada']:,.2f}",
                          help="valor_base × 0.8% × % bono del mes del recibo. Estimación, no liquidación real.")

            faltan = df_pac[df_pac["pedido_faltante"]]
            if not faltan.empty:
                st.warning(f"⚠️ {len(faltan)} recibo(s) con concepto 'Anticipo' SIN pedido abierto "
                           f"(SAP ORCT.U_pedAbierto): {', '.join(faltan['pago_nro'].astype(str).head(10))}.")
            if pac["sin_concepto"] > 0:
                st.warning("Hay recibos a cuenta sin concepto de anticipo: no se clasifican ni como "
                           "anticipo ni como pago a cartera.")

            # 4. Tabla de Detalle (Usando variables dinámicas para evitar KeyErrors)
            cols_tabla = ["pago_nro", col_fecha, "nombre_almacen", "nombre_cliente",
                          col_monto, "monto_final", "concepto_anticipo",
                          "pedido_abierto", "es_anticipo", "liq_estimada"]
            
            # Asegurar que las columnas existan para no romper el dataframe
            for c in cols_tabla:
                if c not in df_pac.columns:
                    if c == "monto_final" and "saldo_a_cuenta" in df_pac.columns:
                        df_pac["monto_final"] = df_pac["saldo_a_cuenta"]
                    else:
                        df_pac[c] = None

            st.dataframe(
                df_pac[cols_tabla].rename(columns={
                    "pago_nro": "Recibo", col_fecha: "Fecha",
                    "nombre_almacen": "Almacén", "nombre_cliente": "Cliente",
                    col_monto: "Valor Recibo", "monto_final": "Saldo a Cuenta",
                    "concepto_anticipo": "Concepto (U_NAL_Tipo_Pago)",
                    "pedido_abierto": "Pedido Abierto (U_pedAbierto)",
                    "es_anticipo": "¿Cuenta como Anticipo?", "liq_estimada": "LIQ Estimada (info)"}),
                use_container_width=True, hide_index=True, height=400,
                column_config={
                    "Fecha": st.column_config.DateColumn(format="DD/MM/YYYY"),
                    "Almacén": st.column_config.TextColumn(width="medium"),
                    "Cliente": st.column_config.TextColumn(width="large"),
                    "Valor Recibo": st.column_config.NumberColumn(format="$%,.2f"),
                    "Saldo a Cuenta": st.column_config.NumberColumn(format="$%,.2f"),
                    "LIQ Estimada (info)": st.column_config.NumberColumn(format="$%,.2f"),
                    "¿Cuenta como Anticipo?": st.column_config.CheckboxColumn(),
                })

# ============================================================
# 📊 DISTRIBUCIÓN (por almacén o por ejecutivo, según el modo)
# ============================================================
col_grupo = "nombre_almacen" if modo == "Almacenes" else "nom_empleado"
etiqueta_grupo = "Punto de Venta (Almacén)" if modo == "Almacenes" else "Ejecutivo Comercial"

st.markdown(f'<div class="section-header"><h3>📊 Distribución por {etiqueta_grupo}</h3>'
            f'<p>Agrupación de pagos, anticipos y descuentos por {etiqueta_grupo.lower()}</p></div>',
            unsafe_allow_html=True)

resumen_pagos = dfp.groupby(col_grupo, as_index=False)["liquidacion_ok"].sum()
resumen_pagos = resumen_pagos.rename(columns={"liquidacion_ok": "liq_pagos"})

if not ant.empty and col_grupo in ant.columns:
    resumen_anticipos = ant.groupby(col_grupo, as_index=False)["liq_app"].sum()
    resumen_anticipos = resumen_anticipos.rename(columns={"liq_app": "liq_anticipos"})
else:
    resumen_anticipos = pd.DataFrame(columns=[col_grupo, "liq_anticipos"])

if not dfc.empty and col_grupo in dfc.columns:
    dfc_temp = dfc.copy()
    dfc_temp["descuento_liq"] = dfc_temp["diferencia"] * 0.008
    resumen_descuentos = dfc_temp.groupby(col_grupo, as_index=False)["descuento_liq"].sum()
else:
    resumen_descuentos = pd.DataFrame(columns=[col_grupo, "descuento_liq"])

resumen = pd.merge(resumen_pagos, resumen_anticipos, on=col_grupo, how="outer").fillna(0)
resumen = pd.merge(resumen, resumen_descuentos, on=col_grupo, how="outer").fillna(0)
resumen["Total_Bono"] = resumen["liq_pagos"] + resumen["liq_anticipos"] - resumen["descuento_liq"]
resumen = resumen.sort_values("Total_Bono", ascending=False)

if modo != "Almacenes":
    resid_pagos = float(dfp["liquidacion_ok"].sum()) - float(resumen["liq_pagos"].sum())
    ant_ej = float(ant["liq_app"].sum()) if not ant.empty else 0.0
    desc_ej = float((dfc["diferencia"] * 0.008).sum()) if not dfc.empty else 0.0
    if abs(resid_pagos) > 1 or abs(ant_ej) > 1 or abs(desc_ej) > 1:
        resumen = pd.concat([resumen, pd.DataFrame([{
            col_grupo: "No atribuido (sin vendedor / nivel EJECOM)",
            "liq_pagos": resid_pagos, "liq_anticipos": ant_ej, "descuento_liq": desc_ej}])],
            ignore_index=True)
        resumen["Total_Bono"] = resumen["liq_pagos"] + resumen["liq_anticipos"] - resumen["descuento_liq"]

fila_total_resumen = pd.DataFrame([{
    col_grupo: "TOTAL GENERAL",
    "liq_pagos": resumen["liq_pagos"].sum(),
    "liq_anticipos": resumen["liq_anticipos"].sum(),
    "descuento_liq": resumen["descuento_liq"].sum(),
    "Total_Bono": resumen["Total_Bono"].sum(),
}])

resumen_mostrar = pd.concat([resumen, fila_total_resumen], ignore_index=True)

with st.container(border=True):
    st.dataframe(
        resumen_mostrar.rename(columns={
            col_grupo: etiqueta_grupo,
            "liq_pagos": "Liquidación Pagos",
            "liq_anticipos": "Liquidación Anticipos",
            "descuento_liq": "Descuento Liquidación",
            "Total_Bono": "Total Bono",
        }),
        use_container_width=True,
        hide_index=True,
        column_config={
            etiqueta_grupo: st.column_config.TextColumn(width="medium"),
            "Liquidación Pagos": st.column_config.NumberColumn(format="$%,.2f"),
            "Liquidación Anticipos": st.column_config.NumberColumn(format="$%,.2f"),
            "Descuento Liquidación": st.column_config.NumberColumn(format="$%,.2f"),
            "Total Bono": st.column_config.NumberColumn(format="$%,.2f"),
        },
    )

if modo != "Almacenes":
    st.caption("En modo Ejecutivos, anticipos y descuentos por conciliación quedan en 0 por "
              "comercial porque el recibo no trae vendedor: se ven a nivel EJECOM en las "
              "tablas de arriba y en el Resumen.")

# ============================================================
# PIE DE PÁGINA
# ============================================================
st.markdown("---")
st.caption("💡 **Nota:** La Primera Etapa del Bono se compone de la suma de la Liquidación de Pagos "
          "(aplicados en el mes) más la Liquidación de Anticipos (aplicaciones diferidas con Monto Final > 0). "
          "Esta vista resume el impacto por cada Punto de Venta para la toma de decisiones gerenciales.")
st.markdown("---")
st.caption("🚧 **En construcción:** próxima iteración = anticipos + % Bono Aplicable Factura (tabla FACTURAS Y NC).")