"""
VISTA: REPARTICIÓN Y CONCILIACIÓN DEL BONO POR COLABORADOR

Consolida el BONO FINAL por almacén de Conciliación con las políticas de distribución
por cargo de Talento Humano y la cascada completa de descuentos:
1. Descuentos de Inventarios (en pesos $)
2. Faltas RRHH Tipo 1, Tipo 2, Tipo 3 (en porcentaje %)
3. Ausencias, Permisos, Vacaciones e Incapacidades (en porcentaje %)
4. Novedades Administrativas de Gerencia (en porcentaje %)
"""

from __future__ import annotations

import importlib
from datetime import date
import numpy as np
import pandas as pd
import streamlit as st

from src import rrhh_dist as dist
from src import rrhh_nov as nov
from src import bono_reparto as reparto

# Forzar recarga en caliente para evitar módulos desfasados en caché de Streamlit
try:
    importlib.reload(dist)
    importlib.reload(nov)
    importlib.reload(reparto)
except Exception:
    pass

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
# CONFIGURACIÓN Y CONTROL DE ACCESO
# ============================================================
st.set_page_config(
    page_title="Repartición del Bono",
    page_icon=":material/payments:",
    layout="wide",
    initial_sidebar_state="expanded"
)

ROLES_BONIFICACIONES = ["admin", "gerente", "gerente_comercial"]
rol_actual = st.session_state.get("rol_actual")
usuario_actual = st.session_state.get("usuario_actual", "admin")
nombre_usuario = st.session_state.get("nombre_completo", usuario_actual)

if rol_actual not in ROLES_BONIFICACIONES:
    st.error("⛔ Acceso denegado. Este módulo solo es accesible por Gerencia o Administración.")
    st.stop()

st.title("Repartición y Liquidación del Bono por Colaborador")
st.caption(f"Sesión: **{nombre_usuario}** · Rol: **{rol_actual.upper()}**. Conciliación comercial y liquidación neta a pagar.")

# ============================================================
# FUNCIONES AUXILIARES DE CÁLCULO DE CONCILIACIÓN
# ============================================================
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

@st.cache_data(ttl=3600, show_spinner="Cargando ventas netas de SAP...")
def cargar_ventas_sap():
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

# ============================================================
# CARGA DE CATÁLOGOS BASE
# ============================================================
df_fac = cargar_ventas_sap()
if df_fac.empty:
    st.warning("⚠️ Sin datos de facturación en `sap_raw.ventas_netas`. Ejecuta **Forzar Sincronización SAP**.")
    st.stop()

df_fac["nom_almacen"] = df_fac["nombre_serie"].map(MAP_SERIE_FACTURA).fillna("OTRO")

ALMACENES_DB = dist.cargar_almacenes(conn)
NOM_A_COD = {a["nombre"]: a["codigo"] for a in ALMACENES_DB}
COD_A_NOM = {a["codigo"]: a["nombre"] for a in ALMACENES_DB}
MODO_ALM = {a["codigo"]: a["modo_bono"] for a in ALMACENES_DB}

# Meses disponibles (con fallback automático a lista_meses si fuera necesario)
if not df_fac.empty and df_fac["fecha"].notna().any():
    meses_disponibles = sorted(df_fac["fecha"].dt.to_period("M").dropna().unique())
else:
    hoy = date.today()
    mes_act = dist.inicio_mes(hoy)
    mes_min = dist.sumar_meses(mes_act, -12)
    mes_max = dist.sumar_meses(mes_act, 2)
    meses_disponibles = [pd.Period(m.strftime("%Y-%m"), freq="M") for m in dist.lista_meses(mes_min, mes_max)]

mes_default = pd.Period("2025-12", freq="M") if pd.Period("2025-12", freq="M") in meses_disponibles else meses_disponibles[-1]

# ============================================================
# FILTROS SUPERIORES
# ============================================================
col_f1, col_f2, col_f3 = st.columns([1.5, 2, 2.5])

mes_sel = col_f1.selectbox(
    "Selecciona el Mes a Liquidar:",
    options=list(reversed(meses_disponibles)),
    index=list(reversed(meses_disponibles)).index(mes_default) if mes_default in meses_disponibles else 0,
    format_func=lambda p: p.strftime("%B %Y").capitalize(),
    key="sb_reparto_mes"
)

desde = mes_sel.start_time.date()
hasta = mes_sel.end_time.date()
mes_date = dist.inicio_mes(desde)

# Resolver períodos generales y metas
periodo_metas, tramos_metas = resolver_periodo_metas(desde)
periodo_escala, rangos_escala = resolver_periodo_escala(desde)
periodo_general, _, _ = resolver_periodo_bono(desde)

if periodo_metas is None or periodo_escala is None or periodo_general is None:
    st.error("⚠️ No hay configuración de períodos en **Variables del Bono** para el mes seleccionado.")
    st.stop()

# Opciones de Almacén
lista_almacenes = sorted(df_fac["nom_almacen"].unique().tolist())
TODOS_ALMS = "🏢 Todos los Almacenes"
opciones_almacen = [TODOS_ALMS] + [f"{NOM_A_COD.get(a, a)} — {a}" for a in lista_almacenes]

alm_sel_label = col_f2.selectbox(
    "Filtrar por Almacén:",
    options=opciones_almacen,
    index=0,
    key="sb_reparto_alm"
)

busqueda_colab = col_f3.text_input(
    "Buscar colaborador (nombre o cédula):",
    placeholder="Escribe para filtrar...",
    key="inp_filtro_colab"
)

# ============================================================
# MOTOR DE CONCILIACIÓN DEL BONO POR ALMACÉN (TABLA 3)
# ============================================================
@st.cache_data(ttl=300, show_spinner="Calculando liquidación de bonos de los almacenes...")
def calcular_tabla_liquidacion_almacenes(desde_d, hasta_d, periodo_mes_str):
    dff = df_fac[(df_fac["fecha"].dt.date >= desde_d) & (df_fac["fecha"].dt.date <= hasta_d)]
    if dff.empty:
        return pd.DataFrame()

    t1 = dff.groupby("nom_almacen", as_index=False).agg(
        Precio_Total_PVP=("pvp", "sum"),
        Suma_Costo_Total=("costo_total", "sum"),
        Suma_Rentabilidad=("rentabilidad", "sum"),
    )
    t1["Pct_Rent_Costo"] = np.where(
        t1["Suma_Costo_Total"] != 0,
        t1["Suma_Rentabilidad"] / t1["Suma_Costo_Total"] * 100.0,
        0.0,
    )

    # Anticipos y Exentas
    df_ant = cargar_bonos_anticipos()
    if not df_ant.empty and "tipo_factura" in df_ant.columns:
        ant = df_ant.copy()
        ant["tipo_factura"] = ant["tipo_factura"].astype(str).str.strip()
        ant = ant[ant["tipo_factura"] == "2"]
        ant = ant[ant["fecha_factura"].notna()]
        ant = ant[(ant["fecha_recibo_pago"].dt.date >= desde_d) & (ant["fecha_recibo_pago"].dt.date <= hasta_d)]
        ant = ant[
            ant["fecha_aplicacion"].notna()
            & (ant["fecha_recibo_pago"].dt.to_period("M") != ant["fecha_aplicacion"].dt.to_period("M"))
        ]
        ant["nom_almacen"] = ant["nombre_serie_pago"].map(MAP_SERIE_FACTURA).fillna("OTRO")
        g_ant = ant.groupby("nom_almacen", as_index=False)["monto_aplicado"].sum().rename(columns={"monto_aplicado": "Suma_Total_Anticipo"})
    else:
        g_ant = pd.DataFrame(columns=["nom_almacen", "Suma_Total_Anticipo"])

    g_ex = dff.groupby("nom_almacen", as_index=False)["exentas"].sum().rename(columns={"exentas": "Suma_Precio_Total_Exentas"})
    exentas_auditadas = obtener_exentas_auditadas_por_almacen(periodo_mes_str)
    g_ex["Suma_Precio_Total_Exentas"] = g_ex.apply(
        lambda r: float(exentas_auditadas.get(r["nom_almacen"], r["Suma_Precio_Total_Exentas"])),
        axis=1,
    )

    t2 = pd.merge(g_ant, g_ex, on="nom_almacen", how="outer").fillna(0.0)
    t2["total_exentas"] = t2["Suma_Total_Anticipo"] + t2["Suma_Precio_Total_Exentas"]

    # Valor Bono conciliado
    distrib_bono = calcular_distribucion_bono_almacen(desde_d, hasta_d, None)
    t3 = t1.merge(t2[["nom_almacen", "total_exentas"]], on="nom_almacen", how="left").fillna(0.0)
    t3 = t3.merge(
        distrib_bono.rename(columns={"Total_Bono": "Valor_Bono"}),
        left_on="nom_almacen", right_on="nombre_almacen", how="left"
    ).drop(columns=["nombre_almacen"], errors="ignore")
    t3["Valor_Bono"] = t3["Valor_Bono"].fillna(0.0)

    # Escala de rentabilidad
    r = t3["Pct_Rent_Costo"] / 100.0
    t3["Pct_Bono"] = r.apply(lambda rent: aplicar_escala_rent(rent, rangos_escala))

    # Bono Total
    t3["Bono_Total"] = np.where(
        t3["Pct_Bono"] >= 0,
        t3["Valor_Bono"] * t3["Pct_Bono"],
        t3["Valor_Bono"] + (t3["Valor_Bono"] * t3["Pct_Bono"]),
    )

    # Meta Adicional
    meta_base = float(periodo_general["meta_adic_base"])
    meta_bloque = float(periodo_general["meta_adic_bloque"])
    meta_pct = float(periodo_general["meta_adic_pct"])
    dif_meta = t3["Precio_Total_PVP"] - meta_base
    t3["Pct_Meta_Adicional"] = np.where(dif_meta > 0, np.trunc(dif_meta / meta_bloque) * meta_pct, 0.0)

    # Bono Final
    t3["aplica"] = t3["Precio_Total_PVP"].apply(lambda pvp: aplicar_tramo_ventas(pvp, tramos_metas))
    t3["Bono_Final"] = np.where(t3["aplica"] != 0, (1.0 + t3["Pct_Meta_Adicional"]) * t3["Bono_Total"], 0.0)

    return t3

periodo_mes_str = mes_sel.strftime("%Y-%m")
df_liq_almacenes = calcular_tabla_liquidacion_almacenes(desde, hasta, periodo_mes_str)

if df_liq_almacenes.empty:
    st.warning("No hay datos de liquidación para el período seleccionado.")
    st.stop()

# Diccionario rápido de liquidación por nombre de almacén
MAP_LIQ_ALM = {
    r["nom_almacen"]: {
        "pvp": float(r["Precio_Total_PVP"]),
        "aplica": float(r["aplica"]) != 0,
        "rent_costo": float(r["Pct_Rent_Costo"]),
        "valor_bono": float(r["Valor_Bono"]),
        "bono_total": float(r["Bono_Total"]),
        "pct_adic": float(r["Pct_Meta_Adicional"]),
        "bono_final": float(r["Bono_Final"]),
    }
    for _, r in df_liq_almacenes.iterrows()
}

# ============================================================
# CARGA DE DATOS DE TALENTO HUMANO, DISTRIBUCIÓN Y DEDUCCIONES
# ============================================================
PERIODOS_DIST = dist.cargar_periodos(conn)
CARGOS_CONFIG = dist.cargar_cargos_config(conn)
ASIGNACIONES = dist.cargar_asignaciones(conn)

DESCUENTOS_INV = reparto.cargar_descuentos_inventarios_mes(conn, mes_date)
NOVEDADES_RRHH, CONFIG_FALTAS = reparto.cargar_novedades_rrhh_mes(conn, mes_date)
NOVEDADES_ADMIN = reparto.cargar_novedades_admin_mes(conn, mes_date)

# ============================================================
# CONSOLIDACIÓN GENERAL DE COLABORADORES
# ============================================================
todos_los_colaboradores: list[dict] = []

for alm in ALMACENES_DB:
    cod_alm = alm["codigo"]
    nom_alm = alm["nombre"]
    modo = alm.get("modo_bono", dist.MODO_PORCENTAJE)
    datos_liq = MAP_LIQ_ALM.get(nom_alm, {
        "pvp": 0.0, "aplica": False, "valor_bono": 0.0, "bono_final": 0.0
    })

    # Si es individual (EJECOM), no usa bolsa
    if modo == dist.MODO_INDIVIDUAL:
        continue

    # Calcular reparto de cargos (parámetros nombrados)
    rep_dist = dist.calcular_reparto_mes(
        almacen=cod_alm,
        mes=mes_date,
        periodos=PERIODOS_DIST,
        cargos_config=CARGOS_CONFIG,
        asignaciones=ASIGNACIONES,
        modo=modo
    )

    filas_alm = reparto.consolidar_reparticion_almacen(
        almacen_codigo=cod_alm,
        almacen_nombre=nom_alm,
        mes=mes_date,
        valor_bono_almacen=datos_liq["valor_bono"],
        bono_final_almacen=datos_liq["bono_final"],
        aplica_meta=datos_liq["aplica"],
        reparto_dist=rep_dist,
        descuentos_inv=DESCUENTOS_INV,
        novedades_rrhh=NOVEDADES_RRHH,
        config_faltas=CONFIG_FALTAS,
        novedades_admin=NOVEDADES_ADMIN
    )
    todos_los_colaboradores.extend(filas_alm)

df_colabs_total = pd.DataFrame(todos_los_colaboradores)

# Filtrado según selector
if alm_sel_label != TODOS_ALMS:
    cod_sel = alm_sel_label.split(" — ")[0].strip()
    df_filtrado = df_colabs_total[df_colabs_total["almacen_codigo"] == cod_sel].copy() if not df_colabs_total.empty else pd.DataFrame()
else:
    df_filtrado = df_colabs_total.copy() if not df_colabs_total.empty else pd.DataFrame()

# Filtrado por búsqueda de texto
if busqueda_colab.strip() and not df_filtrado.empty:
    b = busqueda_colab.strip().lower()
    df_filtrado = df_filtrado[
        df_filtrado["nombre"].str.lower().str.contains(b, na=False) |
        df_filtrado["cedula"].str.lower().str.contains(b, na=False) |
        df_filtrado["cargo"].str.lower().str.contains(b, na=False)
    ]

# ============================================================
# TARJETAS DE MÉTRICAS GLOBALES SUPERIORES
# ============================================================
alms_aplicaron = sum(1 for v in MAP_LIQ_ALM.values() if v["aplica"])
alms_no_aplicaron = len(MAP_LIQ_ALM) - alms_aplicaron

total_pvp_red = sum(v["pvp"] for v in MAP_LIQ_ALM.values())
total_bono_base_red = sum(v["valor_bono"] for v in MAP_LIQ_ALM.values())
total_bono_final_red = sum(v["bono_final"] for v in MAP_LIQ_ALM.values())

tot_bruto_colabs = float(df_colabs_total["bono_bruto"].sum()) if not df_colabs_total.empty else 0.0
tot_desc_colabs = float(df_colabs_total["total_descuentos"].sum()) if not df_colabs_total.empty else 0.0
tot_neto_colabs = float(df_colabs_total["bono_neto"].sum()) if not df_colabs_total.empty else 0.0
cant_beneficiarios = int((df_colabs_total["bono_neto"] > 0).sum()) if not df_colabs_total.empty else 0

m1, m2, m3, m4, m5, m6 = st.columns(6)
m1.metric("Ventas Totales Red (PVP)", f"${total_pvp_red:,.0f}")
m2.metric("Almacenes con Bono", f"{alms_aplicaron} Sí · {alms_no_aplicaron} No")
m3.metric("Bono Base (Valor Bono)", f"${total_bono_base_red:,.0f}")
m4.metric("BONO FINAL Generado", f"${total_bono_final_red:,.0f}")
m5.metric("Total Descuentos Deducidos", f"-${tot_desc_colabs:,.0f}", delta_color="inverse")
m6.metric("Gran Total Neto a Pagar", f"${tot_neto_colabs:,.0f}")

st.markdown("---")

# ============================================================
# PESTAÑAS DE VISTA
# ============================================================
tab_reparto_persona, tab_resumen_almacen, tab_auditoria_desc = st.tabs([
    "👥 Liquidación y Repartición por Colaborador",
    "🏬 Resumen y Cumplimiento por Almacén",
    "🔍 Detalle de Deducciones Cruzadas"
])

# ------------------------------------------------------------
# PESTAÑA 1: LIQUIDACIÓN Y REPARTICIÓN POR COLABORADOR
# ------------------------------------------------------------
with tab_reparto_persona:
    st.subheader(f"Liquidación Individual · {alm_sel_label}")
    st.caption(
        "Consolida el Bono Base, el adicional por superar meta (BONO FINAL), y la secuencia de deducciones "
        "(Inventarios $, Faltas %, Ausencias % y Novedades Gerencia %) para obtener el **Bono Neto a Pagar**."
    )

    if df_filtrado.empty:
        st.info("No se encontraron registros de colaboradores para los filtros seleccionados.")
    else:
        # Fila de Totales Consolidados para la vista
        fila_totales = pd.DataFrame([{
            "almacen_nombre": "TOTALES",
            "cargo": "—",
            "nombre": f"{len(df_filtrado)} colaboradores",
            "cedula": "—",
            "pct_bolsa_cargo": None,
            "pct_efectivo": None,
            "bono_base": float(df_filtrado["bono_base"].sum()),
            "bono_adicional": float(df_filtrado["bono_adicional"].sum()),
            "bono_bruto": float(df_filtrado["bono_bruto"].sum()),
            "desc_inv_pesos": float(df_filtrado["desc_inv_pesos"].sum()),
            "pct_faltas": None,
            "desc_faltas_pesos": float(df_filtrado["desc_faltas_pesos"].sum()),
            "pct_ausencias": None,
            "desc_ausencias_pesos": float(df_filtrado["desc_ausencias_pesos"].sum()),
            "pct_admin": None,
            "desc_admin_pesos": float(df_filtrado["desc_admin_pesos"].sum()),
            "total_descuentos": float(df_filtrado["total_descuentos"].sum()),
            "bono_neto": float(df_filtrado["bono_neto"].sum()),
            "saldo_pendiente": float(df_filtrado["saldo_pendiente"].sum()),
            "estado": f"{cant_beneficiarios} a pagar"
        }])

        cols_orden = [
            "almacen_nombre", "cargo", "nombre", "cedula",
            "pct_bolsa_cargo", "pct_efectivo",
            "bono_base", "bono_adicional", "bono_bruto",
            "desc_inv_pesos", "desc_faltas_pesos", "desc_ausencias_pesos", "desc_admin_pesos",
            "total_descuentos", "bono_neto", "estado"
        ]

        df_tabla_final = pd.concat([df_filtrado[cols_orden], fila_totales[cols_orden]], ignore_index=True)

        st.dataframe(
            df_tabla_final.rename(columns={
                "almacen_nombre": "Almacén",
                "cargo": "Cargo",
                "nombre": "Colaborador",
                "cedula": "Cédula",
                "pct_bolsa_cargo": "% Bolsa Cargo",
                "pct_efectivo": "% Efectivo",
                "bono_base": "Valor Bono (Base)",
                "bono_adicional": "Adic. Meta",
                "bono_bruto": "BONO FINAL (Bruto)",
                "desc_inv_pesos": "Desc. Inventarios ($)",
                "desc_faltas_pesos": "Desc. Faltas ($)",
                "desc_ausencias_pesos": "Desc. Ausencias ($)",
                "desc_admin_pesos": "Desc. Gerencia ($)",
                "total_descuentos": "Total Deducciones ($)",
                "bono_neto": "BONO NETO A PAGAR ($)",
                "estado": "Estado"
            }),
            use_container_width=True,
            hide_index=True,
            column_config={
                "% Bolsa Cargo": st.column_config.NumberColumn(format="%.2f%%"),
                "% Efectivo": st.column_config.NumberColumn(format="%.2f%%"),
                "Valor Bono (Base)": st.column_config.NumberColumn(format="$%,.2f"),
                "Adic. Meta": st.column_config.NumberColumn(format="$%,.2f"),
                "BONO FINAL (Bruto)": st.column_config.NumberColumn(format="$%,.2f"),
                "Desc. Inventarios ($)": st.column_config.NumberColumn(format="$%,.2f"),
                "Desc. Faltas ($)": st.column_config.NumberColumn(format="$%,.2f"),
                "Desc. Ausencias ($)": st.column_config.NumberColumn(format="$%,.2f"),
                "Desc. Gerencia ($)": st.column_config.NumberColumn(format="$%,.2f"),
                "Total Deducciones ($)": st.column_config.NumberColumn(format="$%,.2f"),
                "BONO NETO A PAGAR ($)": st.column_config.NumberColumn(format="$%,.2f"),
            }
        )

        # Botón de Descarga
        csv_colabs = df_tabla_final.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "📥 Descargar Liquidación Detallada de Colaboradores (CSV)",
            data=csv_colabs,
            file_name=f"liquidacion_colaboradores_{mes_sel.strftime('%Y_%m')}.csv",
            mime="text/csv"
        )

        # Inspección individual
        st.markdown("---")
        with st.expander("🔎 Ver Desglose y Justificación Individual de un Colaborador"):
            opciones_cols = [
                f"{r['nombre']} (CC {r['cedula']}) — {r['almacen_nombre']} [{r['cargo']}]"
                for _, r in df_filtrado.iterrows()
            ]
            if opciones_cols:
                sel_col_label = st.selectbox("Selecciona colaborador para ver soportes:", opciones_cols)
                idx_sel = opciones_cols.index(sel_col_label)
                item_sel = df_filtrado.iloc[idx_sel]
                raw_ded = item_sel["_deducciones_raw"]

                d1, d2, d3, d4 = st.columns(4)
                d1.metric("Bono Bruto Asignado", f"${item_sel['bono_bruto']:,.2f}")
                d2.metric("Total Descuentos", f"-${item_sel['total_descuentos']:,.2f}", delta_color="inverse")
                d3.metric("Bono Neto a Recibir", f"${item_sel['bono_neto']:,.2f}")
                d4.metric("Saldo Pendiente", f"${item_sel['saldo_pendiente']:,.2f}" if item_sel['saldo_pendiente'] > 0 else "$0.00")

                col_det1, col_det2 = st.columns(2)
                with col_det1:
                    st.markdown("##### 📦 Descuentos de Inventarios (en Pesos)")
                    if raw_ded["items_inv"]:
                        for inv in raw_ded["items_inv"]:
                            st.write(f"• **${inv['monto']:,.2f}** — *{inv['concepto']}*: {inv['observacion']}")
                    else:
                        st.caption("Sin descuentos de inventarios en este período.")

                    st.markdown("##### 📋 Faltas Disciplinarias RRHH (Tipo 1, 2, 3)")
                    if raw_ded["detalle_faltas"]:
                        for f in raw_ded["detalle_faltas"]:
                            st.write(f"• **{f['grado']}** ({f['pct']}%) el {f['fecha']}")
                    else:
                        st.caption("Sin faltas disciplinarias registradas.")

                with col_det2:
                    st.markdown("##### 🏖️ Ausencias, Vacaciones y Permisos")
                    if raw_ded["detalle_ausencias"]:
                        for tipo_aus, d_cant in raw_ded["detalle_ausencias"].items():
                            st.write(f"• **{tipo_aus}**: {d_cant} día(s) ({d_cant * (100.0/30.0):.2f}%)")
                    else:
                        st.caption("Sin ausencias o permisos en el período.")

                    st.markdown("##### ⚖️ Novedades Administrativas de Gerencia")
                    if raw_ded["items_admin"]:
                        for adm in raw_ded["items_admin"]:
                            st.write(f"• **{adm['porcentaje_descuento']:.2f}%** — *{adm['concepto']}*: {adm['observacion']}")
                    else:
                        st.caption("Sin novedades gerenciales aplicadas.")

# ------------------------------------------------------------
# PESTAÑA 2: RESUMEN Y CUMPLIMIENTO POR ALMACÉN
# ------------------------------------------------------------
with tab_resumen_almacen:
    st.subheader(f"Cumplimiento y Liquidación de Almacenes · {mes_sel.strftime('%B %Y')}")
    st.caption("Visión consolidada por punto de venta: cumplimiento de meta, escala de rentabilidad y montos totales.")

    filas_resumen_alm = []
    for alm in ALMACENES_DB:
        cod = alm["codigo"]
        nom = alm["nombre"]
        modo = alm.get("modo_bono", dist.MODO_PORCENTAJE)
        liq = MAP_LIQ_ALM.get(nom, {
            "pvp": 0.0, "aplica": False, "rent_costo": 0.0, "valor_bono": 0.0, "bono_final": 0.0, "pct_adic": 0.0
        })

        colabs_este_alm = [c for c in todos_los_colaboradores if c["almacen_codigo"] == cod]
        tot_bruto = sum(c["bono_bruto"] for c in colabs_este_alm)
        tot_desc = sum(c["total_descuentos"] for c in colabs_este_alm)
        tot_neto = sum(c["bono_neto"] for c in colabs_este_alm)

        filas_resumen_alm.append({
            "Código": cod,
            "Almacén": nom,
            "Modalidad": modo,
            "Ventas PVP": liq["pvp"],
            "¿Aplica Bono?": "✅ SÍ" if liq["aplica"] else "❌ NO",
            "% Rent. Costo": liq["rent_costo"],
            "Valor Bono Base": liq["valor_bono"],
            "% Meta Adicional": liq["pct_adic"],
            "BONO FINAL Almacén": liq["bono_final"],
            "Colaboradores": len(colabs_este_alm),
            "Total Descuentos": tot_desc,
            "Gran Total Neto": tot_neto
        })

    df_res_alm = pd.DataFrame(filas_resumen_alm)
    st.dataframe(
        df_res_alm,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Ventas PVP": st.column_config.NumberColumn(format="$%,.2f"),
            "% Rent. Costo": st.column_config.NumberColumn(format="%.2f%%"),
            "Valor Bono Base": st.column_config.NumberColumn(format="$%,.2f"),
            "% Meta Adicional": st.column_config.NumberColumn(format="%.2f%%"),
            "BONO FINAL Almacén": st.column_config.NumberColumn(format="$%,.2f"),
            "Total Descuentos": st.column_config.NumberColumn(format="$%,.2f"),
            "Gran Total Neto": st.column_config.NumberColumn(format="$%,.2f"),
        }
    )

# ------------------------------------------------------------
# PESTAÑA 3: DETALLE DE DEDUCCIONES CRUZADAS
# ------------------------------------------------------------
with tab_auditoria_desc:
    st.subheader(f"Auditoría de Descuentos Registrados · {mes_sel.strftime('%B %Y')}")
    st.caption("Consolidado de todas las deducciones registradas en el sistema para este período.")

    sub_t1, sub_t2, sub_t3 = st.tabs([
        "📦 Inventarios ($)",
        "📋 Novedades RRHH (Faltas y Ausencias)",
        "⚖️ Novedades Gerencia (%)"
    ])

    with sub_t1:
        if DESCUENTOS_INV:
            df_di = pd.DataFrame(DESCUENTOS_INV)
            st.dataframe(
                df_di[["almacen_codigo", "colaborador_id", "monto", "concepto", "observacion"]].rename(columns={
                    "almacen_codigo": "Almacén",
                    "colaborador_id": "ID Colaborador",
                    "monto": "Monto ($)",
                    "concepto": "Concepto",
                    "observacion": "Observación"
                }),
                use_container_width=True,
                hide_index=True,
                column_config={"Monto ($)": st.column_config.NumberColumn(format="$%,.2f")}
            )
        else:
            st.info("Sin descuentos de inventarios registrados en este período.")

    with sub_t2:
        if NOVEDADES_RRHH:
            df_nr = pd.DataFrame(NOVEDADES_RRHH)
            st.dataframe(
                df_nr[["colaborador_id", "tipo", "fecha_inicio", "fecha_fin", "incluye_dominical", "observacion"]].rename(columns={
                    "colaborador_id": "ID Colaborador",
                    "tipo": "Tipo Novedad",
                    "fecha_inicio": "Fecha Inicio",
                    "fecha_fin": "Fecha Fin",
                    "incluye_dominical": "Incluye Dominical",
                    "observacion": "Observación"
                }),
                use_container_width=True,
                hide_index=True
            )
        else:
            st.info("Sin novedades de talento humano en este período.")

    with sub_t3:
        if NOVEDADES_ADMIN:
            df_na = pd.DataFrame(NOVEDADES_ADMIN)
            st.dataframe(
                df_na[["almacen_codigo", "colaborador_id", "porcentaje_descuento", "concepto", "observacion"]].rename(columns={
                    "almacen_codigo": "Almacén",
                    "colaborador_id": "ID Colaborador",
                    "porcentaje_descuento": "% Descuento",
                    "concepto": "Concepto",
                    "observacion": "Observación"
                }),
                use_container_width=True,
                hide_index=True,
                column_config={"% Descuento": st.column_config.NumberColumn(format="%.2f%%")}
            )
        else:
            st.info("Sin novedades de gerencia en este período.")