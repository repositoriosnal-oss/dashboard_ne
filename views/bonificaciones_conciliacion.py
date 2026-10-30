"""
CONCILIACIÓN BONO
Auditoría mensual del bono por almacén y por ejecutivo comercial.

Ambas pestañas siguen el mismo esquema de informe:
    1. Resumen y cumplimiento de meta
    2. Exentas
    3. Liquidación del bono
    4. Descomposición del Valor Bono
    5. Control de pagos en conciliación consolidada
El cálculo individual de ejecutivos vive en src/bono_ejecom.py (probado de forma aislada).
"""
import io
from datetime import date
from html import escape

import numpy as np
import pandas as pd
import streamlit as st

from src.conexion_db import (
    conn,
    MAP_SERIE_FACTURA,
    cargar_bonos_pagos,
    cargar_bonos_anticipos,
    cargar_pagos_consolidados,
    calcular_distribucion_bono_almacen,
    resolver_periodo_metas,
    resolver_periodo_escala,
    resolver_periodo_bono,
)
from src.bono_ejecom import (
    FACTOR_BASE,
    FACTOR_IVA,
    META_EJECUTIVO_DEFECTO,
    TASA_ANTICIPO,
    TASA_DESCUENTO_CONSOLIDADO,
    bono_total,
    clave,
    liquidar_ejecutivos,
    pct_escala,
    pct_tramo,
    tramos_por_defecto,
)

st.set_page_config(page_title="Conciliación Bono", layout="wide", initial_sidebar_state="expanded")

ROLES_BONIFICACIONES = ["admin", "gerente", "gerente_comercial"]
if st.session_state.get("rol_actual") not in ROLES_BONIFICACIONES:
    st.error("Acceso denegado. El módulo de Bonificaciones está disponible únicamente para Gerencia y Administrador.")
    st.stop()

EJECOM_NOM = "EJECUTIVOS COMERCIALES"
EJECOM_COD = "EJECOM"
TOL_CUADRE = 1.0                 # pesos
APLICA_META_ADICIONAL_EJECOM = False   # pendiente de definición de negocio (ver Criterios de cálculo)
MESES_ES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto",
            "Septiembre", "Octubre", "Noviembre", "Diciembre"]

# ============================================================
# ESTILO: tarjetas de KPI con borde, tipografía compacta y rejilla adaptable
# (colores neutros: funcionan en tema claro y oscuro)
# ============================================================
st.markdown("""
<style>
.kpi-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(175px, 1fr));
            gap: 10px; margin: 4px 0 16px 0; }
.kpi { border: 1px solid rgba(128,128,128,.35); border-radius: 6px; padding: 10px 12px; min-width: 0; }
.kpi-l { font-size: .70rem; letter-spacing: .04em; text-transform: uppercase; opacity: .72; line-height: 1.25; }
.kpi-v { font-size: clamp(1rem, 1.5vw, 1.28rem); font-weight: 650; line-height: 1.25; margin-top: 5px;
         font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.kpi-s { font-size: .72rem; opacity: .62; margin-top: 3px; line-height: 1.25; }
h2, h3 { letter-spacing: -.01em; }
</style>
""", unsafe_allow_html=True)


# ============================================================
# FORMATO
# ============================================================
def fmt_mes(p) -> str:
    return f"{MESES_ES[p.month - 1]} {p.year}"


def fmt_cop(v) -> str:
    if v is None or pd.isna(v):
        return "-"
    return f"-${abs(v):,.0f}" if v < 0 else f"${v:,.0f}"


def fmt_pct(v, dec=1) -> str:
    return "-" if v is None or pd.isna(v) else f"{v * 100:,.{dec}f}%"


def md(s: str) -> str:
    """Escapa el signo $ para que st.caption/st.markdown no lo interprete como fórmula."""
    return s.replace("$", r"\$")


def tarjetas(items):
    """items: lista de (etiqueta, valor, nota_opcional)."""
    html = ['<div class="kpi-grid">']
    for it in items:
        lab, val = it[0], it[1]
        sub = it[2] if len(it) > 2 else ""
        s = f'<div class="kpi-s">{escape(sub)}</div>' if sub else ""
        html.append(f'<div class="kpi"><div class="kpi-l">{escape(lab)}</div>'
                    f'<div class="kpi-v">{escape(val)}</div>{s}</div>')
    html.append("</div>")
    # el signo $ se escribe como entidad para que Markdown no lo interprete como fórmula
    st.markdown("".join(html).replace("$", "&#36;"), unsafe_allow_html=True)


def cop():
    return st.column_config.NumberColumn(format="$%,.2f")


def pct():
    return st.column_config.NumberColumn(format="%.2f%%")


def x100(df, cols):
    df = df.copy()
    for c in cols:
        if c in df.columns:
            df[c] = df[c] * 100.0
    return df


def tabla(df, config=None):
    st.dataframe(df, use_container_width=True, hide_index=True, column_config=config or {})


def _canon_alm(x) -> str:
    """Clave de almacén sin espacios: 'PUNTO 170' y 'PUNTO170' son el mismo almacén."""
    return "".join(str(x).upper().split())


# ============================================================
# ESCALARES SOBRE LOS HELPERS VECTORIZADOS (misma semántica que _match_intervalo)
# ============================================================
def tramo_escalar(v, tramos) -> float:
    return float(pct_tramo(pd.Series([float(v)]), tramos).iloc[0])


def escala_escalar(v, escala) -> float:
    return float(pct_escala(pd.Series([float(v)]), escala).iloc[0])


# ============================================================
# CARGA DE DATOS
# ============================================================
@st.cache_data(ttl=3600, show_spinner="Cargando base de facturas y notas crédito...")
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


@st.cache_data(ttl=3600, show_spinner=False)
def cargar_ventas_ejecom_mes(desde, hasta):
    return conn.query("""
        SELECT nombre_vendedor, MAX(documento_identidad) AS cedula,
               SUM(precio_sin_iva) AS pvp, SUM(costo_total) AS costo, SUM(rentabilidad) AS rentabilidad,
               SUM(precio_sin_iva) FILTER (WHERE tax_code IN ('IVAEXE','IVDEXE','IVAIEXE')) AS exentas
        FROM sap_raw.ventas_netas
        WHERE nombre_almacen = 'EJECOM' AND fecha_contabilizacion::date BETWEEN :d AND :h
        GROUP BY nombre_vendedor
    """, params={"d": desde, "h": hasta})


@st.cache_data(ttl=3600, show_spinner=False)
def cargar_ventas_ejecom_historico():
    return conn.query("""
        SELECT EXTRACT(YEAR FROM fecha_contabilizacion)::int AS anio,
               EXTRACT(MONTH FROM fecha_contabilizacion)::int AS mes,
               nombre_vendedor, SUM(precio_sin_iva) AS pvp
        FROM sap_raw.ventas_netas
        WHERE nombre_almacen = 'EJECOM'
        GROUP BY 1, 2, 3
    """)


def cargar_decisiones_mes(periodo_mes: str) -> dict:
    """
    Decisiones de Contabilidad del mes en UNA consulta.
    Devuelve {(almacén_canónico, comercial_canónico): pesos} solo para decisiones 'corregido'.
    Gana la decisión más reciente, igual que obtener_decision_contabilidad: si la última es
    'aprobado' o pendiente, se usa el valor calculado.
    """
    df = conn.query("""
        SELECT nombre_almacen, nombre_comercial, decision, valor_corregido
        FROM app.contabilidad_decisiones_exentas
        WHERE periodo_mes = :pm
        ORDER BY fecha_decision
    """, params={"pm": periodo_mes}, ttl=0)
    out = {}
    for r in df.itertuples(index=False):
        k = (_canon_alm(r.nombre_almacen), clave(r.nombre_comercial or ""))
        if r.decision == "corregido" and pd.notna(r.valor_corregido):
            out[k] = float(r.valor_corregido)
        else:
            out.pop(k, None)
    return out


df_fac = cargar_facturas_nc_agregadas()
if df_fac.empty:
    st.warning("Sin datos de facturas y notas crédito en sap_raw.ventas_netas. Ejecute Forzar Sincronización SAP.")
    st.stop()
df_fac["nom_almacen"] = df_fac["nombre_serie"].map(MAP_SERIE_FACTURA).fillna("OTRO")

# ============================================================
# FILTROS
# ============================================================
with st.sidebar:
    st.markdown("### Filtros")
    meses_disponibles = sorted(df_fac["fecha"].dt.to_period("M").unique())
    mes_sel = st.selectbox("Mes a evaluar", options=meses_disponibles, index=len(meses_disponibles) - 1,
                           format_func=fmt_mes, key="sb_conc_mes")
    alm_disp = sorted(n for n in df_fac["nom_almacen"].unique() if n != EJECOM_NOM)
    alm_sel = st.multiselect("Almacén", alm_disp, default=alm_disp, key="sb_conc_alm")

desde = mes_sel.start_time.date()
hasta = mes_sel.end_time.date()
periodo_mes_str = f"{desde:%Y-%m}"

# ============================================================
# PERÍODOS VIGENTES (almacenes y ejecutivos)
# ============================================================
periodo_metas, tramos_metas = resolver_periodo_metas(desde)
periodo_escala, rangos_escala = resolver_periodo_escala(desde)
periodo_general, _, _ = resolver_periodo_bono(desde)
if periodo_metas is None or periodo_escala is None or periodo_general is None:
    st.error("No hay configuración de períodos para el mes seleccionado. Configure los períodos en Variables del Bono.")
    st.stop()

factor_desc_exentas = float(periodo_general["factor_desc_exentas"])

# Ejecutivos: tramos y escala del ámbito EJECOM; si no existen, valores por defecto.
meta_cfg = float(periodo_general.get("meta_ejecutivo_mensual") or 0) or META_EJECUTIVO_DEFECTO
periodo_metas_ej, tramos_ej = resolver_periodo_metas(desde, EJECOM_COD)
fuente_meta_ej = periodo_metas_ej["nombre"] if periodo_metas_ej is not None else f"valor por defecto ({fmt_cop(meta_cfg)})"
if tramos_ej is None or len(tramos_ej) == 0:
    tramos_ej = tramos_por_defecto(meta_cfg)
periodo_escala_ej, escala_ej = resolver_periodo_escala(desde, EJECOM_COD)
fuente_escala_ej = periodo_escala_ej["nombre"] if periodo_escala_ej is not None else "escala de almacenes"
if escala_ej is None or len(escala_ej) == 0:
    escala_ej = rangos_escala

_cache_tramos = {}


def resolver_tramos_ej(anio: int, mes: int):
    """Tramos EJECOM vigentes en el mes de la factura (con respaldo al período evaluado)."""
    k = (anio, mes)
    if k not in _cache_tramos:
        _, t = resolver_periodo_metas(date(anio, mes, 1), EJECOM_COD)
        _cache_tramos[k] = t if t is not None and len(t) else tramos_ej
    return _cache_tramos[k]


# ============================================================
# DECISIONES DE CONTABILIDAD (una consulta por mes)
# ============================================================
DEC = cargar_decisiones_mes(periodo_mes_str)
DEC_ALM = {a: v for (a, c), v in DEC.items() if c == ""}
DEC_EJ = {c: v for (a, c), v in DEC.items() if a == _canon_alm(EJECOM_NOM) and c != ""}

# ============================================================
# BASES DE PAGOS, ANTICIPOS Y CONSOLIDADOS
# ============================================================
df_pagos_all = cargar_bonos_pagos()
if df_pagos_all.empty or "estado_comision" not in df_pagos_all.columns:
    st.warning("Sin datos de pagos. Ejecute Forzar Sincronización SAP o verifique que Variables del Bono esté inicializado.")
    st.stop()
df_ant_all = cargar_bonos_anticipos()
df_cons_all = cargar_pagos_consolidados()

# ============================================================
# CÁLCULO: ALMACENES
# ============================================================
dff = df_fac[(df_fac["fecha"].dt.date >= desde) & (df_fac["fecha"].dt.date <= hasta)]
dff = dff[dff["nom_almacen"] != EJECOM_NOM]
if alm_sel:
    dff = dff[dff["nom_almacen"].isin(alm_sel)]

tabla1 = tabla2 = t3 = descomp = tabla_cons = pd.DataFrame()
fila_total1 = fila_total2 = fila_total3 = fila_total_cons = pd.DataFrame()

if not dff.empty:
    # ---- 1. Resumen
    tabla1 = dff.groupby("nom_almacen", as_index=False).agg(
        Precio_Total_PVP=("pvp", "sum"), Suma_Costo_Total=("costo_total", "sum"),
        Suma_Rentabilidad=("rentabilidad", "sum"))
    tabla1["Aplica_Bono_Del"] = pct_tramo(tabla1["Precio_Total_PVP"], tramos_metas).values * 100.0
    tabla1["Pct_Rent_Costo"] = np.where(tabla1["Suma_Costo_Total"] != 0,
                                        tabla1["Suma_Rentabilidad"] / tabla1["Suma_Costo_Total"] * 100.0, 0.0)
    tabla1 = tabla1.sort_values("Precio_Total_PVP", ascending=False)
    tp1, tc1, tr1 = (float(tabla1[c].sum()) for c in ("Precio_Total_PVP", "Suma_Costo_Total", "Suma_Rentabilidad"))
    fila_total1 = pd.DataFrame([{
        "nom_almacen": "Total", "Precio_Total_PVP": tp1, "Suma_Costo_Total": tc1, "Suma_Rentabilidad": tr1,
        "Aplica_Bono_Del": tramo_escalar(tp1, tramos_metas) * 100.0,
        "Pct_Rent_Costo": (tr1 / tc1 * 100.0) if tc1 else 0.0}])

    # ---- 2. Anticipos y exentas (exentas con el valor auditado por Contabilidad)
    ant = pd.DataFrame()
    if not df_ant_all.empty and "tipo_factura" in df_ant_all.columns:
        ant = df_ant_all.copy()
        ant["tipo_factura"] = ant["tipo_factura"].astype(str).str.strip()
        ant = ant[(ant["tipo_factura"] == "2") & ant["fecha_factura"].notna()]
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
    g_ex["Suma_Precio_Total_Exentas"] = [
        DEC_ALM.get(_canon_alm(a), v) for a, v in zip(g_ex["nom_almacen"], g_ex["Suma_Precio_Total_Exentas"])]
    tabla2 = pd.merge(g_ant, g_ex, on="nom_almacen", how="outer").fillna(0.0)
    tabla2["Total_Exentas"] = tabla2["Suma_Total_Anticipo"] + tabla2["Suma_Precio_Total_Exentas"]
    tabla2 = tabla2.sort_values("Total_Exentas", ascending=False)
    fila_total2 = pd.DataFrame([{
        "nom_almacen": "Total",
        "Suma_Total_Anticipo": float(tabla2["Suma_Total_Anticipo"].sum()),
        "Suma_Precio_Total_Exentas": float(tabla2["Suma_Precio_Total_Exentas"].sum()),
        "Total_Exentas": float(tabla2["Total_Exentas"].sum())}])

    # ---- 3. Liquidación
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
    t3["Pct_Bono"] = pct_escala(t3["Pct_Rent_Costo"] / 100.0, rangos_escala).values
    t3["Bono_Total"] = bono_total(t3["Valor_Bono"], t3["Pct_Bono"])
    meta_base = float(periodo_general["meta_adic_base"])
    meta_bloque = float(periodo_general["meta_adic_bloque"])
    meta_pct = float(periodo_general["meta_adic_pct"])
    dif_meta = t3["Precio_Total_PVP"] - meta_base
    t3["Pct_Meta_Adicional"] = np.where(dif_meta > 0, np.trunc(dif_meta / meta_bloque) * meta_pct, 0.0)
    t3["aplica"] = pct_tramo(t3["Precio_Total_PVP"], tramos_metas).values
    t3["Bono_Final"] = np.where(t3["aplica"] != 0, (1.0 + t3["Pct_Meta_Adicional"]) * t3["Bono_Total"], 0.0)
    t3 = t3.sort_values("Valor_Bono", ascending=False)
    tp = float(t3["Precio_Total_PVP"].sum())
    te = float(t3["total_exentas"].sum())
    tv = float(t3["Valor_Bono"].sum())
    bt_sum = float(t3["Bono_Total"].sum())
    bf_sum = float(t3["Bono_Final"].sum())
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

    # ---- 5. Control de consolidados
    if not df_cons_all.empty:
        dfc = df_cons_all[(df_cons_all["fecha_recibo"].dt.date >= desde) & (df_cons_all["fecha_recibo"].dt.date <= hasta)]
        dfc = dfc[dfc["nombre_almacen"] != EJECOM_NOM]
        if alm_sel:
            dfc = dfc[dfc["nombre_almacen"].isin(alm_sel)]
        if not dfc.empty:
            tabla_cons = dfc[["pago_nro", "fecha_recibo", "nombre_almacen", "nombre_cliente",
                              "pr", "vr_aplicado", "diferencia", "n_facturas", "n_aplicaciones"]].copy()
            tabla_cons["descuento_liq"] = tabla_cons["diferencia"] * TASA_DESCUENTO_CONSOLIDADO
            fila_total_cons = pd.DataFrame([{
                "pago_nro": "Total", "fecha_recibo": pd.NaT, "nombre_almacen": "",
                "nombre_cliente": f"{len(tabla_cons)} pagos observados",
                "pr": tabla_cons["pr"].sum(), "vr_aplicado": tabla_cons["vr_aplicado"].sum(),
                "diferencia": tabla_cons["diferencia"].sum(),
                "descuento_liq": tabla_cons["descuento_liq"].sum(),
                "n_facturas": None, "n_aplicaciones": None}])

# ============================================================
# CÁLCULO: EJECUTIVOS COMERCIALES
# ============================================================
ventas_ej = cargar_ventas_ejecom_mes(desde, hasta)
ventas_ej_hist = cargar_ventas_ejecom_historico()

pagos_ej = df_pagos_all[
    (df_pagos_all["estado_comision"] == "Pago Aplicado en el Mes")
    & (df_pagos_all["nombre_almacen"] == EJECOM_NOM)
    & (df_pagos_all["fecha_aplicacion"].dt.date >= desde)
    & (df_pagos_all["fecha_aplicacion"].dt.date <= hasta)
]
if "liq_sin_bono" not in pagos_ej.columns:
    st.error("La base de pagos no incluye liq_sin_bono. Verifique la versión de src/conexion_db.py.")
    st.stop()

# Pool EJECOM sin ponderar: anticipos (liquidación base) y descuento por conciliación consolidada.
ant_base_pool = 0.0
if not df_ant_all.empty:
    a = df_ant_all[(df_ant_all["nombre_almacen"] == EJECOM_NOM)
                   & (df_ant_all["fecha_recibo_pago"].dt.date >= desde)
                   & (df_ant_all["fecha_recibo_pago"].dt.date <= hasta)]
    mv = a["monto_validado"].where(a["monto_validado"] > 0, 0.0)
    ant_base_pool = float((mv / FACTOR_IVA * FACTOR_BASE * TASA_ANTICIPO).sum())

tabla_cons_ej = pd.DataFrame()
fila_total_cons_ej = pd.DataFrame()
desc_pool = 0.0
if not df_cons_all.empty:
    dfc_ej = df_cons_all[(df_cons_all["fecha_recibo"].dt.date >= desde)
                         & (df_cons_all["fecha_recibo"].dt.date <= hasta)
                         & (df_cons_all["nombre_almacen"] == EJECOM_NOM)]
    if not dfc_ej.empty:
        tabla_cons_ej = dfc_ej[["pago_nro", "fecha_recibo", "nombre_almacen", "nombre_cliente",
                                "pr", "vr_aplicado", "diferencia", "n_facturas", "n_aplicaciones"]].copy()
        tabla_cons_ej["descuento_liq"] = tabla_cons_ej["diferencia"] * TASA_DESCUENTO_CONSOLIDADO
        desc_pool = float(tabla_cons_ej["descuento_liq"].sum())
        fila_total_cons_ej = pd.DataFrame([{
            "pago_nro": "Total", "fecha_recibo": pd.NaT, "nombre_almacen": "",
            "nombre_cliente": f"{len(tabla_cons_ej)} pagos observados",
            "pr": tabla_cons_ej["pr"].sum(), "vr_aplicado": tabla_cons_ej["vr_aplicado"].sum(),
            "diferencia": tabla_cons_ej["diferencia"].sum(), "descuento_liq": desc_pool,
            "n_facturas": None, "n_aplicaciones": None}])

meta_adic_ej = ({"base": float(periodo_general["meta_adic_base"]),
                 "bloque": float(periodo_general["meta_adic_bloque"]),
                 "pct": float(periodo_general["meta_adic_pct"])}
                if APLICA_META_ADICIONAL_EJECOM else None)

df_ej, pool_ej = liquidar_ejecutivos(
    ventas_mes=ventas_ej, ventas_hist=ventas_ej_hist,
    pagos=pagos_ej[["nom_empleado", "fecha_factura", "liq_sin_bono"]],
    ant_base_pool=ant_base_pool, desc_pool=desc_pool,
    tramos=tramos_ej, escala=escala_ej, resolver_tramos=resolver_tramos_ej,
    exentas_auditadas=DEC_EJ, factor_desc_exentas=factor_desc_exentas, meta_adicional=meta_adic_ej)


# ============================================================
# TABLAS DE PRESENTACIÓN (las usan la pantalla y el informe Excel)
# ============================================================
def _fila_total(nombre, **vals):
    return pd.DataFrame([{"Ejecutivo": nombre, **vals}])


def tablas_ejecutivos(df: pd.DataFrame, pool: dict) -> dict:
    if df.empty:
        return {}
    n = int(df["Meta"].notna().sum())          # ejecutivos evaluados (sin la fila Sin Vendedor)
    pvp, costo, rent = (float(df[c].sum()) for c in ("PVP", "Costo", "Rentabilidad"))
    meta_tot = float(df["Meta"].sum())
    pvp_con_meta = float(df.loc[df["Meta"].notna(), "PVP"].sum())

    # 1. Resumen y cumplimiento
    t1 = df[["Ejecutivo", "Cedula", "PVP", "Meta", "Pct_Cumplimiento", "Pct_Aplica",
             "PVP_Faltante", "Costo", "Rentabilidad", "Pct_Rent_Costo"]].copy()
    tot1 = _fila_total(f"Total ({n})", Cedula="", PVP=pvp, Meta=meta_tot,
                       Pct_Cumplimiento=(pvp_con_meta / meta_tot) if meta_tot else np.nan, Pct_Aplica=np.nan,
                       PVP_Faltante=float(df["PVP_Faltante"].sum()), Costo=costo, Rentabilidad=rent,
                       Pct_Rent_Costo=(rent / costo) if costo else np.nan)
    t1 = x100(pd.concat([t1, tot1], ignore_index=True), ["Pct_Cumplimiento", "Pct_Aplica", "Pct_Rent_Costo"]).rename(columns={
        "Cedula": "Cédula", "Pct_Cumplimiento": "% Cumplimiento", "Pct_Aplica": "Aplica Bono del",
        "PVP_Faltante": "PVP faltante para meta", "Costo": "Suma de Costo total",
        "Rentabilidad": "Suma de Rentabilidad", "Pct_Rent_Costo": "% Rent (Costo)", "PVP": "Precio Total PVP"})

    # 2. Exentas
    t2 = df[["Ejecutivo", "Exentas_Calc", "Exentas_Aud"]].copy()
    t2["Diferencia"] = t2["Exentas_Aud"] - t2["Exentas_Calc"]
    tot2 = _fila_total(f"Total ({n})", Exentas_Calc=float(t2["Exentas_Calc"].sum()),
                       Exentas_Aud=float(t2["Exentas_Aud"].sum()), Diferencia=float(t2["Diferencia"].sum()))
    t2 = pd.concat([t2, tot2], ignore_index=True).rename(columns={
        "Exentas_Calc": "Exentas calculadas (SAP)", "Exentas_Aud": "Exentas auditadas (Contabilidad)"})

    # 3. Liquidación
    t3e = df[["Ejecutivo", "Pct_Exentas", "Desc_Exentas", "Valor_Bono", "Pct_Bono", "Bono_Total",
              "Pct_Meta_Adicional", "Bono_Final", "Bono_Si_Cumpliera"]].copy()
    ex_aud = float(df["Exentas_Aud"].sum())
    vb, bt, bf = (float(df[c].sum()) for c in ("Valor_Bono", "Bono_Total", "Bono_Final"))
    tot3 = _fila_total(f"Total ({n})", Pct_Exentas=(ex_aud / pvp) if pvp else 0.0,
                       Desc_Exentas=((ex_aud / pvp) if pvp else 0.0) * factor_desc_exentas,
                       Valor_Bono=vb, Pct_Bono=(bt / vb) if vb else np.nan, Bono_Total=bt,
                       Pct_Meta_Adicional=(float((df["Pct_Meta_Adicional"] * df["Valor_Bono"]).sum()) / vb) if vb else 0.0,
                       Bono_Final=bf, Bono_Si_Cumpliera=float(df["Bono_Si_Cumpliera"].sum()))
    t3e = x100(pd.concat([t3e, tot3], ignore_index=True),
               ["Pct_Exentas", "Desc_Exentas", "Pct_Bono", "Pct_Meta_Adicional"]).rename(columns={
        "Pct_Exentas": "% Exentas", "Desc_Exentas": "DESC EXENTAS", "Valor_Bono": "Valor Bono",
        "Pct_Bono": "% Bono", "Bono_Total": "BONO TOTAL", "Pct_Meta_Adicional": "% Meta Adicional",
        "Bono_Final": "BONO FINAL", "Bono_Si_Cumpliera": "Bono si cumpliera meta"})

    # 4. Descomposición del Valor Bono
    t4 = df[["Ejecutivo", "Liq_Pagos_Base", "Liq_Pagos", "Liq_Anticipos", "Desc_Consol", "Valor_Bono"]].copy()
    t4["Pct_Pagos"] = np.where(t4["Liq_Pagos_Base"] > 0,
                               t4["Liq_Pagos"] / t4["Liq_Pagos_Base"].where(t4["Liq_Pagos_Base"] > 0, 1.0), np.nan)
    base_t = float(t4["Liq_Pagos_Base"].sum())
    tot4 = _fila_total(f"Total ({n})", Liq_Pagos_Base=base_t, Liq_Pagos=float(t4["Liq_Pagos"].sum()),
                       Liq_Anticipos=float(t4["Liq_Anticipos"].sum()), Desc_Consol=float(t4["Desc_Consol"].sum()),
                       Valor_Bono=float(t4["Valor_Bono"].sum()),
                       Pct_Pagos=(float(t4["Liq_Pagos"].sum()) / base_t) if base_t else np.nan)
    t4 = x100(pd.concat([t4, tot4], ignore_index=True), ["Pct_Pagos"])
    t4 = t4[["Ejecutivo", "Liq_Pagos_Base", "Pct_Pagos", "Liq_Pagos", "Liq_Anticipos", "Desc_Consol", "Valor_Bono"]].rename(columns={
        "Liq_Pagos_Base": "Liq. Pagos base", "Pct_Pagos": "% Bono aplicado a pagos", "Liq_Pagos": "Liq. Pagos",
        "Liq_Anticipos": "Liq. Anticipos (pool)", "Desc_Consol": "Desc. Consolidados (pool)", "Valor_Bono": "Valor Bono"})

    # Conciliación del pool
    pool_df = pd.DataFrame([
        {"Concepto": "Liquidación base de pagos aplicados", "Monto": pool["pagos_base"]},
        {"Concepto": "Liquidación de pagos con % individual", "Monto": pool["pagos_ponderados"]},
        {"Concepto": "Liquidación de anticipos del pool", "Monto": pool["anticipos_ponderados"]},
        {"Concepto": "Descuentos por conciliación consolidada", "Monto": -pool["descuentos"]},
        {"Concepto": "Valor Bono del pool", "Monto": pool["valor_bono_pool"]},
        {"Concepto": "Atribuido a ejecutivos", "Monto": pool["atribuido"]},
        {"Concepto": "Pendiente de atribución", "Monto": pool["pendiente_atribucion"]},
    ])
    return {"resumen": t1, "exentas": t2, "liquidacion": t3e, "descomposicion": t4, "pool": pool_df}


CFG_EJ = {
    "resumen": {"Precio Total PVP": cop(), "Meta": cop(), "% Cumplimiento": pct(), "Aplica Bono del": pct(),
                "PVP faltante para meta": cop(), "Suma de Costo total": cop(), "Suma de Rentabilidad": cop(),
                "% Rent (Costo)": pct(), "Cédula": st.column_config.TextColumn(width="small")},
    "exentas": {"Exentas calculadas (SAP)": cop(), "Exentas auditadas (Contabilidad)": cop(), "Diferencia": cop()},
    "liquidacion": {"% Exentas": pct(), "DESC EXENTAS": pct(), "Valor Bono": cop(), "% Bono": pct(),
                    "BONO TOTAL": cop(), "% Meta Adicional": pct(), "BONO FINAL": cop(),
                    "Bono si cumpliera meta": cop()},
    "descomposicion": {"Liq. Pagos base": cop(), "% Bono aplicado a pagos": pct(), "Liq. Pagos": cop(),
                       "Liq. Anticipos (pool)": cop(), "Desc. Consolidados (pool)": cop(), "Valor Bono": cop()},
}
REN_CONS = {"pago_nro": "Pago Nro", "fecha_recibo": "Fecha Recibo", "nombre_almacen": "Almacén",
            "nombre_cliente": "Cliente", "pr": "Total Recibo (PR)", "vr_aplicado": "VR Aplicado",
            "diferencia": "Diferencia", "descuento_liq": "Descuento Liquidación",
            "n_facturas": "Facturas", "n_aplicaciones": "Aplicaciones"}
CFG_CONS = {"Fecha Recibo": st.column_config.DateColumn(format="DD/MM/YYYY"),
            "Almacén": st.column_config.TextColumn(width="medium"),
            "Cliente": st.column_config.TextColumn(width="large"),
            "Total Recibo (PR)": cop(), "VR Aplicado": cop(), "Diferencia": cop(),
            "Descuento Liquidación": cop()}

TAB_EJ = tablas_ejecutivos(df_ej, pool_ej)

REN1 = {"nom_almacen": "Almacén", "Precio_Total_PVP": "Precio Total PVP", "Aplica_Bono_Del": "Aplica Bono del",
        "Suma_Costo_Total": "Suma de Costo total", "Suma_Rentabilidad": "Suma de Rentabilidad",
        "Pct_Rent_Costo": "% Rent (Costo)"}
REN2 = {"nom_almacen": "Almacén", "Suma_Total_Anticipo": "Suma de Total Anticipo",
        "Suma_Precio_Total_Exentas": "Suma de Precio total exentas", "Total_Exentas": "Total Exentas"}
REN3 = {"nom_almacen": "Almacén", "Pct_Exentas": "% Exentas", "Desc_Exentas": "DESC EXENTAS",
        "Valor_Bono": "Valor Bono", "Pct_Bono": "% Bono", "Bono_Total": "BONO TOTAL",
        "Pct_Meta_Adicional": "% Meta Adicional", "Bono_Final": "BONO FINAL"}
REN4 = {"nom_almacen": "Almacén", "pagos_liq": "Liq. Pagos", "ant_liq": "Liq. Anticipos",
        "desc_liq": "Desc. Consolidados", "Valor_Bono": "Valor Bono"}
COLS3 = ["Almacén", "% Exentas", "DESC EXENTAS", "Valor Bono", "% Bono", "BONO TOTAL", "% Meta Adicional", "BONO FINAL"]


def frames_almacenes() -> dict:
    if dff.empty:
        return {}
    t3_show = x100(pd.concat([t3, fila_total3], ignore_index=True),
                   ["Pct_Exentas", "Desc_Exentas", "Pct_Bono", "Pct_Meta_Adicional"])
    return {
        "resumen": pd.concat([tabla1, fila_total1], ignore_index=True).rename(columns=REN1),
        "exentas": pd.concat([tabla2, fila_total2], ignore_index=True).rename(columns=REN2),
        "liquidacion": t3_show.rename(columns=REN3)[COLS3],
        "descomposicion": descomp.rename(columns=REN4),
        "consolidados": (pd.concat([tabla_cons, fila_total_cons], ignore_index=True).rename(columns=REN_CONS)
                         if not tabla_cons.empty else pd.DataFrame()),
    }


FR_ALM = frames_almacenes()

CFG_ALM = {
    "resumen": {"Precio Total PVP": cop(), "Aplica Bono del": pct(), "Suma de Costo total": cop(),
                "Suma de Rentabilidad": cop(), "% Rent (Costo)": pct()},
    "exentas": {"Suma de Total Anticipo": cop(), "Suma de Precio total exentas": cop(), "Total Exentas": cop()},
    "liquidacion": {"% Exentas": pct(), "DESC EXENTAS": pct(), "Valor Bono": cop(), "% Bono": pct(),
                    "BONO TOTAL": cop(), "% Meta Adicional": pct(), "BONO FINAL": cop()},
    "descomposicion": {"Liq. Pagos": cop(), "Liq. Anticipos": cop(), "Desc. Consolidados": cop(), "Valor Bono": cop()},
}


# ============================================================
# INFORME COMPLETO (Excel)
# ============================================================
def _hoja(xw, df, nombre, vacio):
    (df if df is not None and not df.empty else pd.DataFrame({"Nota": [vacio]})).to_excel(
        xw, index=False, sheet_name=nombre)


def construir_informe_excel() -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        _hoja(xw, FR_ALM.get("resumen"), "1. Resumen Almacén", "Sin datos de almacenes en el mes")
        _hoja(xw, FR_ALM.get("exentas"), "2. Anticipos y Exentas", "Sin datos")
        _hoja(xw, FR_ALM.get("liquidacion"), "3. Liquidación Almacén", "Sin datos")
        _hoja(xw, FR_ALM.get("descomposicion"), "4. Descomposición Almacén", "Sin datos")
        _hoja(xw, FR_ALM.get("consolidados"), "5. Consolidados Almacén", "Sin pagos consolidados observados")
        _hoja(xw, TAB_EJ.get("resumen"), "6. Resumen Ejecutivos", "Sin ventas de ejecutivos en el mes")
        _hoja(xw, TAB_EJ.get("exentas"), "7. Exentas Ejecutivos", "Sin datos")
        _hoja(xw, TAB_EJ.get("liquidacion"), "8. Liquidación Ejecutivos", "Sin datos")
        _hoja(xw, TAB_EJ.get("descomposicion"), "9. Descomp. Ejecutivos", "Sin datos")
        _hoja(xw, TAB_EJ.get("pool"), "10. Pool Ejecutivos", "Sin datos")
        _hoja(xw, (pd.concat([tabla_cons_ej, fila_total_cons_ej], ignore_index=True).rename(columns=REN_CONS)
                   if not tabla_cons_ej.empty else None),
              "11. Consolidados Ejecutivos", "Sin pagos consolidados observados")
    return buf.getvalue()


# ============================================================
# ENCABEZADO
# ============================================================
st.title("Conciliación Bono")
h1, h2 = st.columns([5, 1])
with h1:
    st.caption(md(
        f"Período evaluado: {fmt_mes(mes_sel)}  |  Metas almacenes: {periodo_metas['nombre']}  |  "
        f"Escala almacenes: {periodo_escala['nombre']}  |  Metas ejecutivos: {fuente_meta_ej}  |  "
        f"Escala ejecutivos: {fuente_escala_ej}  |  Factor DESC exentas: {factor_desc_exentas * 100:.0f}%"))
with h2:
    st.download_button("Descargar informe (Excel)", data=construir_informe_excel(),
                       file_name=f"informe_conciliacion_bono_{periodo_mes_str}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       use_container_width=True)

tab_alm, tab_ej = st.tabs(["Almacenes", "Ejecutivos Comerciales"])

# ------------------------------------------------------------
# PESTAÑA: ALMACENES
# ------------------------------------------------------------
with tab_alm:
    if not FR_ALM:
        st.info("Sin datos de almacenes en el mes y filtros seleccionados.")
    else:
        n_alm = len(t3)
        n_con_bono = int((t3["aplica"] != 0).sum())
        tarjetas([
            ("PVP del mes", fmt_cop(tp), f"{n_alm} almacenes"),
            ("Almacenes con bono", f"{n_con_bono} de {n_alm}", "Meta de ventas alcanzada"),
            ("Valor Bono", fmt_cop(tv), "Pagos + anticipos - consolidados"),
            ("BONO TOTAL", fmt_cop(bt_sum), "Tras escala de rentabilidad"),
            ("BONO FINAL", fmt_cop(bf_sum), "Tras meta de ventas y adicional"),
            ("% Exentas", fmt_pct(te / tp if tp else 0.0, 2), f"DESC EXENTAS {fmt_pct((te / tp if tp else 0.0) * factor_desc_exentas, 2)}"),
        ])

        st.subheader("1. Resumen por almacén (tramos de meta)")
        tabla(FR_ALM["resumen"], CFG_ALM["resumen"])

        st.subheader("2. Anticipos y exentas por almacén")
        tabla(FR_ALM["exentas"], CFG_ALM["exentas"])

        st.subheader("3. Liquidación del bono por almacén")
        st.caption("La fila Total suma las filas: los tramos de meta se evalúan por almacén.")
        tabla(FR_ALM["liquidacion"], CFG_ALM["liquidacion"])

        st.subheader("4. Descomposición del Valor Bono")
        tabla(FR_ALM["descomposicion"], CFG_ALM["descomposicion"])

        st.subheader("5. Control: pagos en conciliación consolidada")
        st.caption(md("Recibos cuya suma aplicada supera el valor del recibo (conciliaciones con N pagos y M facturas). "
                      "Regla: diferencia mayor a $100. Respeta los filtros de mes y almacén."))
        if FR_ALM["consolidados"].empty:
            st.info("Sin pagos consolidados observados en el mes y almacenes seleccionados.")
        else:
            tabla(FR_ALM["consolidados"], CFG_CONS)

# ------------------------------------------------------------
# PESTAÑA: EJECUTIVOS COMERCIALES
# ------------------------------------------------------------
with tab_ej:
    if df_ej.empty:
        st.info("Sin ventas ni pagos de ejecutivos comerciales en el mes seleccionado.")
    else:
        n_ej = int(df_ej["Meta"].notna().sum())     # excluye la fila Sin Vendedor
        n_cumplen = int(df_ej["Cumple"].sum())
        pvp_ej = float(df_ej["PVP"].sum())
        vb_ej = float(df_ej["Valor_Bono"].sum())
        bt_ej = float(df_ej["Bono_Total"].sum())
        bf_ej = float(df_ej["Bono_Final"].sum())
        meta_ej = float(pool_ej["meta"])
        sin_derecho = float(df_ej.loc[~df_ej["Cumple"] & df_ej["Meta"].notna(), "Bono_Si_Cumpliera"].sum())

        tarjetas([
            ("PVP del mes", fmt_cop(pvp_ej), f"{n_ej} ejecutivos"),
            ("Cumplen meta", f"{n_cumplen} de {n_ej}", f"Meta individual {fmt_cop(meta_ej)}"),
            ("Valor Bono", fmt_cop(vb_ej), "Pagos + anticipos - consolidados"),
            ("BONO TOTAL", fmt_cop(bt_ej), "Tras escala de rentabilidad"),
            ("BONO FINAL", fmt_cop(bf_ej), "Ejecutivos que cumplen meta"),
            ("Sin derecho por meta", fmt_cop(sin_derecho), "Bono que habrían recibido al cumplir"),
        ])

        if abs(pool_ej["pendiente_atribucion"]) > TOL_CUADRE:
            st.warning(
                md(f"Pendiente de atribución: {fmt_cop(pool_ej['pendiente_atribucion'])}. Existen anticipos o descuentos "
                   "consolidados de EJECOM sin pagos aplicados en el mes que permitan repartirlos entre ejecutivos."))
        if (df_ej["Ejecutivo"] == "Sin Vendedor").any():
            st.warning("Existen pagos o ventas sin vendedor asignado en SAP. Revise la fila Sin Vendedor.")

        st.subheader("1. Resumen por ejecutivo (meta individual)")
        st.caption(md(f"Meta individual vigente: {fmt_cop(meta_ej)} de PVP mensual. "
                      f"Cumplen: {n_cumplen} de {n_ej}."))
        tabla(TAB_EJ["resumen"], CFG_EJ["resumen"])

        st.subheader("2. Exentas por ejecutivo")
        st.caption("Valor auditado por Contabilidad cuando existe corrección; en otro caso, el valor calculado desde SAP.")
        tabla(TAB_EJ["exentas"], CFG_EJ["exentas"])

        st.subheader("3. Liquidación del bono por ejecutivo")
        st.caption("Mismo esquema que almacenes. BONO FINAL es cero cuando el ejecutivo no alcanza la meta del mes; "
                   "la última columna muestra el bono que habría recibido al cumplirla.")
        tabla(TAB_EJ["liquidacion"], CFG_EJ["liquidacion"])

        st.subheader("4. Descomposición del Valor Bono")
        tabla(TAB_EJ["descomposicion"], CFG_EJ["descomposicion"])
        with st.expander("Conciliación del pool EJECOM"):
            tabla(TAB_EJ["pool"], {"Monto": cop()})
            if abs(pool_ej["pendiente_atribucion"]) <= TOL_CUADRE:
                st.caption("El Valor Bono del pool está atribuido en su totalidad a ejecutivos.")

        st.subheader("5. Control: pagos en conciliación consolidada")
        st.caption(md("Recibos de series EJECOM cuya suma aplicada supera el valor del recibo. Regla: diferencia mayor a $100."))
        if tabla_cons_ej.empty:
            st.info("Sin pagos consolidados observados para EJECOM en el mes.")
        else:
            tabla(pd.concat([tabla_cons_ej, fila_total_cons_ej], ignore_index=True).rename(columns=REN_CONS), CFG_CONS)

        with st.expander("Criterios de cálculo"):
            st.markdown(md(
                f"""
- **Meta individual.** Se toma de los tramos de metas del ámbito Ejecutivos en Variables del Bono
  ({fuente_meta_ej}). Hoy: 0 % por debajo de {fmt_cop(meta_ej)} y 100 % desde esa cifra.
- **Valor Bono.** Cada pago aplicado en el mes liquida sobre su base y se pondera con el % del tramo que el
  ejecutivo alcanzó con su PVP en el mes de la factura, igual que los almacenes con su propio mes de factura.
- **Anticipos y descuentos consolidados.** El recibo no identifica vendedor. Se tratan como un pool y se reparten
  en proporción a la liquidación ponderada de pagos de cada ejecutivo, lo que equivale a aplicar a cada uno su propio
  % de bono. Si ningún pago pondera, el descuento se reparte según la liquidación base.
- **% Bono.** Escala de rentabilidad ({fuente_escala_ej}) sobre la rentabilidad propia del ejecutivo en el mes.
- **BONO FINAL.** Solo si el PVP del mes alcanza la meta individual. Meta adicional por ventas:
  {"aplicada" if APLICA_META_ADICIONAL_EJECOM else "no aplicada a ejecutivos"}.
- **Exentas.** % Exentas y DESC EXENTAS se informan con el mismo criterio que en almacenes; no modifican BONO TOTAL.
- **Cruce de nombres.** Ventas y pagos se cruzan por nombre de vendedor sin distinguir mayúsculas ni espacios
  repetidos. Se reemplaza por el código SAP cuando el vínculo con Talento Humano esté disponible.
"""))

st.caption("Los porcentajes se guardan como fracción y se multiplican por 100 solo para mostrarlos.")
#st.markdown("---")
#st.caption("Los porcentajes se guardan como fracción y se convierten a porcentaje solo para mostrar. "
#           "EJECOM queda fuera de las tablas de almacenes: su auditoría completa vive en la pestaña de ejecutivos "
#           "y en la hoja 6 del informe descargable.")








# Los porcentajes se guardan como fracción; convertir a porcentaje para mostrar
# (se aplica sobre la copia mostrada, no altera el cálculo)