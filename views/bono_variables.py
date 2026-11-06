"""
VARIABLES DEL BONO
Parámetros por período y por ámbito.
Parámetros generales (compartidos): factor de descuento por exentas y meta adicional.
Ámbito Almacenes:  tramos de meta sobre la venta del punto y escala de rentabilidad.
Ámbito Ejecutivos: tramos de meta sobre la venta individual y escala de rentabilidad.
Los identificadores que salen de un DataFrame se convierten con entero() antes de ir a la base
de datos: psycopg2 no adapta numpy.int64.
"""
import io                    # [P2.1] para exportar Excel
import inspect               # [P2.1] para inspeccionar firmas
from datetime import date
from html import escape

import pandas as pd
import streamlit as st

from src.conexion_db import (
    bootstrap_bono_variables,
    _cargar_config_bono,
    _cargar_periodos_metas,
    _cargar_periodos_escala,
    actualizar_periodo,
    actualizar_periodo_metas,
    actualizar_periodo_escala,
    crear_periodo,
    crear_periodo_metas,
    crear_periodo_escala,
    eliminar_periodo_metas,
    eliminar_periodo_escala,
    reemplazar_metas,
    reemplazar_escala,
    listar_auditoria_bono,
    ESCALA_RENT_DEFAULT,
    METAS_2026,
    METAS_EJECOM_DEFAULT,    # [P2.1] una sola vez (quitado el duplicado)
)
from src.bono_config import (
    entero,
    evaluar,
    tuplas_escala,
    tuplas_metas,
    validar_escala,
    validar_metas,
)

st.set_page_config(page_title="Variables del Bono", layout="wide")

ROLES_EDIT_BONO = ["admin", "gerente"]
if st.session_state.get("rol_actual") not in ROLES_EDIT_BONO:
    st.error("Acceso denegado. Solo Administrador y Gerencia pueden editar las variables del bono.")
    st.stop()

AMBITOS = ["ALMACEN", "EJECOM"]
NOM_AMBITO = {"ALMACEN": "Almacenes", "EJECOM": "Ejecutivos Comerciales"}
ROTULO_ESCALA = {"ALMACEN": "Escala estándar de almacenes", "EJECOM": "Escala estándar de ejecutivos"}

# ============================================================
# ESTILO Y FORMATO
# ============================================================
st.markdown("""
<style>
.kpi-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
            gap: 10px; margin: 4px 0 16px 0; }
.kpi { border: 1px solid rgba(128,128,128,.35); border-radius: 6px; padding: 10px 12px; min-width: 0; }
.kpi-l { font-size: .70rem; letter-spacing: .04em; text-transform: uppercase; opacity: .72; line-height: 1.25; }
.kpi-v { font-size: clamp(1rem, 1.5vw, 1.28rem); font-weight: 650; line-height: 1.25; margin-top: 5px;
         font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.kpi-s { font-size: .72rem; opacity: .62; margin-top: 3px; line-height: 1.25; overflow-wrap: anywhere; }
</style>
""", unsafe_allow_html=True)


def tarjetas(items):
    html = ['<div class="kpi-grid">']
    for it in items:
        lab, val = it[0], it[1]
        sub = it[2] if len(it) > 2 else " "
        s = f'<div class="kpi-s">{escape(sub)}</div>' if sub else " "
        html.append(f'<div class="kpi"><div class="kpi-l">{escape(lab)}</div>'
                    f'<div class="kpi-v">{escape(val)}</div>{s}</div>')
    html.append("</div>")
    st.markdown(" ".join(html).replace("$ ", "$"), unsafe_allow_html=True)


def md(s: str) -> str:
    return s.replace("$", r"$")


def fmt_cop(v) -> str:
    return "-" if v is None or pd.isna(v) else f"${v:,.0f}"


def fecha_txt(v) -> str:
    return "Abierto" if v is None or pd.isna(v) else f"{pd.Timestamp(v):%d/%m/%Y}"


# ============================================================
# MENSAJES Y EJECUCIÓN SEGURA
# ============================================================
def flash(texto: str, tipo: str = "success"):
    st.session_state["_vb_flash"] = (tipo, texto)


def mostrar_flash():
    pendiente = st.session_state.pop("_vb_flash", None)
    if pendiente:
        getattr(st, pendiente[0])(pendiente[1])


def ejecutar(accion, mensaje_ok: str) -> bool:
    """Ejecuta la acción; si sale bien, deja el aviso para después del rerun y recarga la pantalla."""
    try:
        accion()
    except ValueError as ve:
        st.error(f"No se guardó: {ve}")
        return False
    except Exception as e:
        st.error(f"Error al guardar: {e}")
        return False
    flash(mensaje_ok)
    st.session_state["_vb_ver"] = st.session_state.get("_vb_ver", 0) + 1
    st.cache_data.clear()
    st.rerun()
    return True


# ============================================================
# CARGA
# ============================================================
st.title("Variables del Bono")
st.caption(
    "Parámetros por período y por ámbito. Los parámetros generales (factor de exentas y meta adicional) "
    "son compartidos por Almacenes y Ejecutivos Comerciales; los tramos de meta y la escala de rentabilidad "
    "se definen por ámbito."
)
mostrar_flash()

try:
    bootstrap_bono_variables()
except Exception as e:
    st.error(f"Error al inicializar las tablas de configuración: {e}")
    st.stop()

# [P2.2] Guardia de dependencia: si conexion_db.py no tiene el parche,
# crear_periodo_metas/escala fallarán con TypeError al pasar `ambito=`.
_sin_ambito = [n for n, f in (("crear_periodo_metas", crear_periodo_metas),
                              ("crear_periodo_escala", crear_periodo_escala))
               if "ambito" not in inspect.signature(f).parameters]
if _sin_ambito:
    st.error("src/conexion_db.py aún no tiene el parche de períodos: falta el parámetro `ambito` en "
             + ", ".join(_sin_ambito) + ". Aplica parche_conexion_db_periodos.py antes de crear períodos por ámbito.")
    st.stop()

per, met, esc = _cargar_config_bono()
per_metas = _cargar_periodos_metas()
per_escala = _cargar_periodos_escala()

if per.empty:
    st.warning("No hay períodos configurados. Contacte al administrador.")
    st.stop()

usuario = st.session_state.get("usuario_actual", "desconocido")
ver = st.session_state.get("_vb_ver", 0)      # cambia tras cada guardado: reinicia los editores de tablas


def por_ambito(df, ambito):
    if df.empty or "ambito" not in df.columns:
        return df.iloc[0:0]
    return df[df["ambito"] == ambito]


def vigente(df, f):
    """Primera fila cuyo rango de fechas cubre f (fin vacío = abierto)."""
    if df is None or df.empty:
        return None
    ini = pd.to_datetime(df["fecha_inicio"], errors="coerce")
    fin = pd.to_datetime(df["fecha_fin"], errors="coerce")
    sel = df[(ini <= pd.Timestamp(f)) & (fin.isna() | (fin >= pd.Timestamp(f)))]
    return None if sel.empty else sel.iloc[0]


def meta_plena(tramos):
    """Menor umbral que ya paga el 100 %."""
    if tramos is None or tramos.empty:
        return None
    full = tramos[tramos["pct_aplica"] >= 1.0]
    return float(full["umbral_min"].min()) if not full.empty else None


def etiquetas(df) -> dict:
    """{id: texto} para los selectores. El id es siempre un int nativo."""
    out = {}
    for _, r in df.sort_values("fecha_inicio").iterrows():
        out[entero(r["id"])] = f"{r['nombre']}   ({fecha_txt(r['fecha_inicio'])} a {fecha_txt(r['fecha_fin'])})"
    return out


def tramos_de(pid):
    return met[met["periodo_id"].astype(int) == int(pid)].sort_values("orden")


def rangos_de(pid):
    return esc[esc["periodo_id"].astype(int) == int(pid)].sort_values("orden")


# [P2.3] Exportar configuración a Excel (3 hojas)
def _exportar_configuracion() -> bytes:
    """Libro de 3 hojas con la configuración vigente.
    Todas las fechas salen como texto (fecha_txt) y solo se incluyen las columnas
    que se muestran: así ningún TIMESTAMPTZ con zona horaria llega a openpyxl."""
    buf = io.BytesIO()

    pm = per_metas.copy()
    pe = per_escala.copy()
    if "ambito" not in pm.columns:
        pm["ambito"] = "ALMACEN"
    if "ambito" not in pe.columns:
        pe["ambito"] = "ALMACEN"

    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        # ---- Hoja 1: parámetros generales (sin creado_en / actualizado_en) ----
        g = pd.DataFrame({
            "Período": per["nombre"].astype(str),
            "Inicio": per["fecha_inicio"].map(fecha_txt),
            "Fin": per["fecha_fin"].map(fecha_txt),
            "Factor DESC exentas (%)": per["factor_desc_exentas"].astype(float) * 100,
            "Base meta adicional": per["meta_adic_base"].astype(float),
            "Bloque": per["meta_adic_bloque"].astype(float),
            "% por bloque": per["meta_adic_pct"].astype(float) * 100,
        })
        g.to_excel(xw, index=False, sheet_name="1. Parámetros generales")

        # ---- Hoja 2: tramos de meta por ámbito ----
        filas = []
        for _, r in pm.sort_values(["ambito", "fecha_inicio"]).iterrows():
            for _, t in tramos_de(r["id"]).iterrows():
                filas.append({
                    "Ámbito": r["ambito"], "Período": r["nombre"],
                    "Inicio": fecha_txt(r["fecha_inicio"]), "Fin": fecha_txt(r["fecha_fin"]),
                    "Orden": int(t["orden"]),
                    "Mínimo": float(t["umbral_min"]) if pd.notna(t["umbral_min"]) else None,
                    "Máximo": float(t["umbral_max"]) if pd.notna(t["umbral_max"]) else None,
                    "% aplica": float(t["pct_aplica"]) * 100,
                })
        (pd.DataFrame(filas) if filas else
         pd.DataFrame(columns=["Ámbito", "Período", "Inicio", "Fin", "Orden",
                               "Mínimo", "Máximo", "% aplica"])
         ).to_excel(xw, index=False, sheet_name="2. Tramos de meta")

        # ---- Hoja 3: escala de rentabilidad por ámbito ----
        filas = []
        for _, r in pe.sort_values(["ambito", "fecha_inicio"]).iterrows():
            for _, t in rangos_de(r["id"]).iterrows():
                filas.append({
                    "Ámbito": r["ambito"], "Período": r["nombre"],
                    "Inicio": fecha_txt(r["fecha_inicio"]), "Fin": fecha_txt(r["fecha_fin"]),
                    "Orden": int(t["orden"]),
                    "Mínimo": float(t["cond_min"]) if pd.notna(t["cond_min"]) else None,
                    "Máximo": float(t["cond_max"]) if pd.notna(t["cond_max"]) else None,
                    "% bono": float(t["pct_bono"]) * 100,
                })
        (pd.DataFrame(filas) if filas else
         pd.DataFrame(columns=["Ámbito", "Período", "Inicio", "Fin", "Orden",
                               "Mínimo", "Máximo", "% bono"])
         ).to_excel(xw, index=False, sheet_name="3. Escala de rentabilidad")
    return buf.getvalue()


tab_res, tab_new, tab_edit, tab_hist = st.tabs(["Resumen", "Crear período", "Editar período", "Historial"])

# ------------------------------------------------------------
# 1. RESUMEN
# ------------------------------------------------------------
with tab_res:
    hoy = date.today()

    # [P2.3] Encabezado con botón de exportación
    c_t, c_x = st.columns([4, 1])
    c_t.subheader("Configuración vigente")
    c_x.download_button("Exportar configuración (Excel)", _exportar_configuracion(),
                        file_name="variables_bono_config.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        use_container_width=True)

    g = vigente(per, hoy)
    m_alm = vigente(por_ambito(per_metas, "ALMACEN"), hoy)
    m_eje = vigente(por_ambito(per_metas, "EJECOM"), hoy)

    tarjetas([
        ("Factor DESC exentas",
         f"{float(g['factor_desc_exentas']) * 100:.0f}%" if g is not None else "Sin período",
         g["nombre"] if g is not None else "No hay período general vigente hoy"),
        ("Base meta adicional",
         fmt_cop(float(g["meta_adic_base"])) if g is not None else "-",
         f"Bloque {fmt_cop(float(g['meta_adic_bloque']))} al {float(g['meta_adic_pct']) * 100:.0f}%"
         if g is not None else " "),
        ("Meta plena almacenes",
         fmt_cop(meta_plena(tramos_de(entero(m_alm["id"])))) if m_alm is not None else "Sin período",
         m_alm["nombre"] if m_alm is not None else "No hay tramos vigentes hoy"),
        ("Meta individual ejecutivos",
         fmt_cop(meta_plena(tramos_de(entero(m_eje["id"])))) if m_eje is not None else "Sin período",
         m_eje["nombre"] if m_eje is not None else "No hay tramos vigentes hoy"),
    ])

    st.subheader("Parámetros generales (compartidos)")
    vig_g = vigente(per, hoy)
    per_display = per[["id", "nombre", "fecha_inicio", "fecha_fin", "factor_desc_exentas",
                       "meta_adic_base", "meta_adic_bloque", "meta_adic_pct"]].copy()
    per_display["Vigente"] = [("Si" if vig_g is not None and entero(i) == entero(vig_g["id"]) else "")
                              for i in per_display["id"]]
    per_display["fecha_fin"] = per_display["fecha_fin"].map(fecha_txt)
    per_display["fecha_inicio"] = pd.to_datetime(per_display["fecha_inicio"])
    per_display["factor_desc_exentas"] = per_display["factor_desc_exentas"].astype(float) * 100
    per_display["meta_adic_pct"] = per_display["meta_adic_pct"].astype(float) * 100
    st.dataframe(
        per_display.drop(columns=["id"]).rename(columns={
            "nombre": "Período", "fecha_inicio": "Inicio", "fecha_fin": "Fin",
            "factor_desc_exentas": "Factor DESC Exentas", "meta_adic_base": "Base Meta Adicional",
            "meta_adic_bloque": "Bloque", "meta_adic_pct": "% por Bloque"}),
        use_container_width=True, hide_index=True,
        column_config={
            "Inicio": st.column_config.DateColumn(format="DD/MM/YYYY"),
            "Fin": st.column_config.TextColumn(width="small"),
            "Vigente": st.column_config.TextColumn(width="small"),
            "Factor DESC Exentas": st.column_config.NumberColumn(format="%.0f%%"),
            "Base Meta Adicional": st.column_config.NumberColumn(format="$%,.0f"),
            "Bloque": st.column_config.NumberColumn(format="$%,.0f"),
            "% por Bloque": st.column_config.NumberColumn(format="%.0f%%")})

    st.markdown("---")
    c1, c2 = st.columns(2)
    for col, amb in zip((c1, c2), AMBITOS):
        with col:
            st.subheader(f"Variables: {NOM_AMBITO[amb]}")
            pm = por_ambito(per_metas, amb)
            vig_m = vigente(pm, hoy)
            if pm.empty:
                st.info("Sin períodos de tramos para este ámbito.")
            else:
                filas = [{
                    "Período": r["nombre"], "Inicio": pd.Timestamp(r["fecha_inicio"]),
                    "Fin": fecha_txt(r["fecha_fin"]), "Tramos": len(tramos_de(r["id"])),
                    "Meta plena ($)": meta_plena(tramos_de(r["id"])),
                    "Vigente": "Si" if vig_m is not None and entero(r["id"]) == entero(vig_m["id"]) else ""}
                    for _, r in pm.sort_values("fecha_inicio").iterrows()]
                st.dataframe(pd.DataFrame(filas), use_container_width=True, hide_index=True, column_config={
                    "Inicio": st.column_config.DateColumn(format="DD/MM/YYYY"),
                    "Fin": st.column_config.TextColumn(width="small"),
                    "Vigente": st.column_config.TextColumn(width="small"),
                    "Meta plena ($)": st.column_config.NumberColumn(format="$%,.0f")})

            pe = por_ambito(per_escala, amb)
            vig_e = vigente(pe, hoy)
            if pe.empty:
                st.info("Sin períodos de escala para este ámbito.")
            else:
                filas_e = [{
                    "Período": r["nombre"], "Inicio": pd.Timestamp(r["fecha_inicio"]),
                    "Fin": fecha_txt(r["fecha_fin"]), "Rangos": len(rangos_de(r["id"])),
                    "Vigente": "Si" if vig_e is not None and entero(r["id"]) == entero(vig_e["id"]) else ""}
                    for _, r in pe.sort_values("fecha_inicio").iterrows()]
                st.dataframe(pd.DataFrame(filas_e), use_container_width=True, hide_index=True, column_config={
                    "Inicio": st.column_config.DateColumn(format="DD/MM/YYYY"),
                    "Fin": st.column_config.TextColumn(width="small"),
                    "Vigente": st.column_config.TextColumn(width="small")})

    # ---- Verificaciones de integridad de la configuración
    hallazgos = []
    for tabla_p, hijos, nom, ent in ((per_metas, met, "tramos", "tramos"), (per_escala, esc, "escala", "rangos")):
        for _, r in tabla_p.iterrows():
            if hijos[hijos["periodo_id"].astype(int) == entero(r["id"])].empty:
                hallazgos.append(f"El período de {nom} '{r['nombre']}' ({NOM_AMBITO.get(r.get('ambito'), '')}) no tiene {ent}.")
    for tabla_p, nom in ((per_metas, "tramos"), (per_escala, "escala")):
        for amb in AMBITOS:
            sub = por_ambito(tabla_p, amb).sort_values("fecha_inicio")
            filas_s = list(sub.itertuples())
            for a, b in zip(filas_s, filas_s[1:]):
                fin_a = pd.Timestamp(a.fecha_fin) if pd.notna(a.fecha_fin) else pd.Timestamp("2262-01-01")
                if pd.Timestamp(b.fecha_inicio) <= fin_a:
                    hallazgos.append(f"Los períodos de {nom} '{a.nombre}' y '{b.nombre}' ({NOM_AMBITO[amb]}) se solapan.")
    ids_generales = {entero(i) for i in per["id"]}
    for tabla_p, nom in ((per_metas, "tramos"), (per_escala, "escala")):
        for _, r in por_ambito(tabla_p, "EJECOM").iterrows():
            if entero(r["id"]) in ids_generales:
                hallazgos.append(
                    f"El id {entero(r['id'])} existe como período general y como período de {nom} de Ejecutivos "
                    f"('{r['nombre']}'). Ambos leen las mismas filas de la tabla de {nom}: revise que no se mezclen.")

    # [P2.5] Hallazgo nuevo: alerta si EJECOM no tiene período vigente
    if m_eje is None:
        hallazgos.append(
            "⚠️ **EJECOM no tiene período de tramos vigente hoy**. Los ejecutivos comerciales no recibirán bono "
            "hasta que se configure uno (pestaña *Crear período* → *Ejecutivos Comerciales*)."
        )

    with st.expander(f"Verificaciones de la configuración ({len(hallazgos)} hallazgos)", expanded=bool(hallazgos)):
        if hallazgos:
            for h in hallazgos:
                st.warning(h)
        else:
            st.caption("Sin hallazgos: todos los períodos tienen filas, no hay solapes dentro de un mismo ámbito "
                       "y no se detectaron identificadores compartidos.")


# ------------------------------------------------------------
# 2. CREAR PERÍODO
# ------------------------------------------------------------
with tab_new:
    with st.expander("Nuevo período de parámetros generales"):
        with st.form("form_nuevo_general"):
            col1, col2, col3 = st.columns(3)
            nuevo_nombre = col1.text_input("Nombre del período", placeholder="Ej: 2027")
            nueva_fecha_ini = col2.date_input("Fecha inicio", value=None)
            nueva_fecha_fin = col3.date_input("Fecha fin (vacío = abierto)", value=None)
            col4, col5, col6 = st.columns(3)
            nuevo_factor = col4.number_input("Factor DESC Exentas", min_value=0.0, max_value=1.0, value=0.50, step=0.01)
            nueva_base = col5.number_input("Base Meta Adicional", min_value=0.0, value=2_580_000_000.0, step=100_000_000.0)
            nuevo_bloque = col6.number_input("Bloque", min_value=1.0, value=100_000_000.0, step=50_000_000.0)
            nuevo_pct_meta = st.number_input("% por Bloque", min_value=0.0, max_value=1.0, value=0.06, step=0.01)
            st.caption("Los tramos de meta y la escala de rentabilidad no se crean aquí: se definen por ámbito "
                       "en el formulario siguiente.")
            enviar_gen = st.form_submit_button("Crear período general", type="primary")
            if enviar_gen:
                if not nuevo_nombre.strip() or nueva_fecha_ini is None:
                    st.error("Nombre y fecha de inicio son obligatorios.")
                else:
                    ejecutar(lambda: crear_periodo(
                        nombre=nuevo_nombre, f_ini=nueva_fecha_ini, f_fin=nueva_fecha_fin, factor=nuevo_factor,
                        base=nueva_base, bloque=nuevo_bloque, pct=nuevo_pct_meta, metas=[], escala=[], usuario=usuario),
                        f"Período general '{nuevo_nombre.strip()}' creado.")

    with st.expander("Nuevo período de tramos y escala por ámbito", expanded=True):
        amb_new = st.radio("Ámbito", AMBITOS, format_func=lambda a: NOM_AMBITO[a], horizontal=True, key="new_ambito")
        pm_amb, pe_amb = por_ambito(per_metas, amb_new), por_ambito(per_escala, amb_new)
        lab_m, lab_e = etiquetas(pm_amb), etiquetas(pe_amb)
        n1, n2, n3 = st.columns(3)
        nom_new = n1.text_input("Nombre", placeholder=f"Ej: {'Ejecutivos' if amb_new == 'EJECOM' else 'Red'} 2027",
                                key=f"nom_new_{amb_new}")
        fi_new = n2.date_input("Fecha inicio", value=None, key=f"new_fi_{amb_new}")
        ff_new = n3.date_input("Fecha fin (vacío = abierto)", value=None, key=f"new_ff_{amb_new}")
        crear_tramos = st.checkbox("Crear período de tramos de meta", value=True, key=f"chk_t_{amb_new}")
        origen_m = st.selectbox("Tramos iniciales", options=[0] + list(lab_m),
                                format_func=lambda i: "Estándar del ámbito" if i == 0 else f"Copiar de: {lab_m[i]}",
                                key=f"org_m_{amb_new}", disabled=not crear_tramos)
        crear_escala = st.checkbox("Crear período de escala de rentabilidad", value=True, key=f"chk_e_{amb_new}")
        origen_e = st.selectbox("Escala inicial", options=[0] + list(lab_e),
                                format_func=lambda i: ROTULO_ESCALA[amb_new] if i == 0 else f"Copiar de: {lab_e[i]}",
                                key=f"org_e_{amb_new}", disabled=not crear_escala)
        st.caption("Los tramos y la escala se pueden ajustar después en la pestaña Editar período.")
        if st.button("Crear período(s) del ámbito", type="primary", key=f"btn_new_amb_{amb_new}"):
            if not nom_new.strip() or fi_new is None:
                st.error("Nombre y fecha de inicio son obligatorios.")
            elif not (crear_tramos or crear_escala):
                st.error("Marque al menos una de las dos casillas.")
            else:
                tr_ini = (tuplas_metas(tramos_de(origen_m)) if origen_m
                          else (METAS_EJECOM_DEFAULT if amb_new == "EJECOM" else METAS_2026))
                es_ini = tuplas_escala(rangos_de(origen_e)) if origen_e else ESCALA_RENT_DEFAULT

                def _crear_ambito():
                    pid_m = pid_e = None
                    try:
                        if crear_tramos:
                            pid_m = crear_periodo_metas(nom_new, fi_new, ff_new, usuario, ambito=amb_new)
                            reemplazar_metas(pid_m, tr_ini, usuario)
                        if crear_escala:
                            pid_e = crear_periodo_escala(nom_new, fi_new, ff_new, usuario, ambito=amb_new)
                            reemplazar_escala(pid_e, es_ini, usuario)
                    except Exception:
                        # no dejar períodos vacíos si falla un paso intermedio
                        for fn, p in ((eliminar_periodo_metas, pid_m), (eliminar_periodo_escala, pid_e)):
                            if p:
                                try:
                                    fn(p, usuario)
                                except Exception:
                                    pass
                        raise

                ejecutar(_crear_ambito, f"Período '{nom_new.strip()}' creado para {NOM_AMBITO[amb_new]}.")


# ------------------------------------------------------------
# 3. EDITAR PERÍODO
# ------------------------------------------------------------
with tab_edit:
    st.subheader("Parámetros generales")
    lab_g = etiquetas(per)
    ids_g = list(lab_g)
    por_defecto_g = entero(vigente(per, date.today())["id"]) if vigente(per, date.today()) is not None else ids_g[-1]
    periodo_id = st.selectbox("Período general a editar", ids_g, index=ids_g.index(por_defecto_g),
                              format_func=lab_g.get, key="sel_gen")
    periodo_row = per[per["id"].astype(int) == periodo_id].iloc[0]

    with st.form(f"form_params_{periodo_id}_{ver}"):
        col1, col2, col3 = st.columns(3)
        e_nombre = col1.text_input("Nombre", value=str(periodo_row["nombre"]))
        e_fini = col2.date_input("Fecha inicio", value=pd.to_datetime(periodo_row["fecha_inicio"]).date())
        e_ffin = col3.date_input("Fecha fin (vacío = abierto)",
                                 value=pd.to_datetime(periodo_row["fecha_fin"]).date()
                                 if pd.notna(periodo_row["fecha_fin"]) else None)
        col4, col5, col6 = st.columns(3)
        e_factor = col4.number_input("Factor DESC Exentas", min_value=0.0, max_value=1.0,
                                     value=float(periodo_row["factor_desc_exentas"]), step=0.01)
        e_base = col5.number_input("Base Meta Adicional", min_value=0.0,
                                   value=float(periodo_row["meta_adic_base"]), step=100_000_000.0)
        e_bloque = col6.number_input("Bloque", min_value=1.0,
                                     value=float(periodo_row["meta_adic_bloque"]), step=50_000_000.0)
        e_pct = st.number_input("% por Bloque", min_value=0.0, max_value=1.0,
                                value=float(periodo_row["meta_adic_pct"]), step=0.01)
        guardar_gen = st.form_submit_button("Guardar parámetros generales", type="primary")
        if guardar_gen:
            ejecutar(lambda: actualizar_periodo(periodo_id, {
                "nombre": e_nombre, "fecha_inicio": e_fini, "fecha_fin": e_ffin,
                "factor_desc_exentas": e_factor, "meta_adic_base": e_base,
                "meta_adic_bloque": e_bloque, "meta_adic_pct": e_pct}, usuario),
                "Parámetros generales actualizados.")

    st.markdown("---")
    ambito_edit = st.radio("Ámbito a editar", AMBITOS, format_func=lambda a: NOM_AMBITO[a],
                           horizontal=True, key="edit_ambito")

    # ---------------- Tramos de meta ----------------
    st.subheader("Tramos de meta de ventas")
    st.caption("Se aplica la primera fila que coincida, en el orden de la tabla. "
               "Una venta que no coincide con ninguna fila recibe 0 %.")
    # [P2.4] Caption que explica la meta individual de EJECOM
    if ambito_edit == "EJECOM":
        st.info(
            "**EJECOM usa una meta única** (por defecto $430 M): si el PVP del ejecutivo la alcanza "
            "aplica el 100 % del bono; si no, su bono es $0. Los tramos intermedios no aplican aquí, "
            "pero puede crearlos para escenarios futuros. El umbral de meta plena mostrado en las "
            "tarjetas del Resumen refleja esta regla."
        )

    pm_edit = por_ambito(per_metas, ambito_edit)
    if pm_edit.empty:
        st.info(f"Sin períodos de tramos para {NOM_AMBITO[ambito_edit]}. Créelo en la pestaña Crear período.")
    else:
        lab_pm = etiquetas(pm_edit)
        ids_pm = list(lab_pm)
        vig_pm = vigente(pm_edit, date.today())
        pid_m = st.selectbox("Período de tramos", ids_pm,
                             index=ids_pm.index(entero(vig_pm["id"])) if vig_pm is not None else len(ids_pm) - 1,
                             format_func=lab_pm.get, key=f"sel_m_{ambito_edit}")
        row_m = pm_edit[pm_edit["id"].astype(int) == pid_m].iloc[0]
        with st.form(f"form_per_metas_{ambito_edit}_{pid_m}_{ver}"):
            c1, c2, c3 = st.columns(3)
            nom_m = c1.text_input("Nombre", value=str(row_m["nombre"]))
            fini_m = c2.date_input("Fecha inicio", value=pd.to_datetime(row_m["fecha_inicio"]).date())
            ffin_m = c3.date_input("Fecha fin (vacío = abierto)",
                                   value=pd.to_datetime(row_m["fecha_fin"]).date() if pd.notna(row_m["fecha_fin"]) else None)
            guardar_dm = st.form_submit_button("Guardar datos del período")
        if guardar_dm:
            ejecutar(lambda: actualizar_periodo_metas(pid_m, {"nombre": nom_m, "fecha_inicio": fini_m,
                                                              "fecha_fin": ffin_m}, usuario),
                     "Datos del período de tramos actualizados.")

        cols_m = ["umbral_min", "umbral_max", "pct_aplica", "min_inclusivo", "max_inclusivo"]
        metas_edit = tramos_de(pid_m).reset_index(drop=True)
        metas_df = metas_edit[cols_m].copy() if not metas_edit.empty else pd.DataFrame(columns=cols_m)
        edited_m = st.data_editor(
            metas_df, num_rows="dynamic", use_container_width=True, key=f"editor_metas_{ambito_edit}_{pid_m}_{ver}",
            column_config={
                "umbral_min": st.column_config.NumberColumn("Mínimo ($)", format="$%,.0f", required=True),
                "umbral_max": st.column_config.NumberColumn("Máximo ($)", format="$%,.0f"),
                "pct_aplica": st.column_config.NumberColumn("% Aplica", format="%.2f", min_value=0.0, max_value=1.0, required=True),
                "min_inclusivo": st.column_config.CheckboxColumn("Min Inclusivo", default=True),
                "max_inclusivo": st.column_config.CheckboxColumn("Max Inclusivo", default=False)})

        tup_m = tuplas_metas(edited_m)
        err_m, av_m = validar_metas(tup_m)
        for e in err_m:
            st.error(e)
        for a in av_m:
            st.warning(a)

        with st.expander("Probar un valor de ventas con estos tramos (sin guardar)"):
            ref = meta_plena(edited_m) or 0.0
            venta_prueba = st.number_input("Ventas del mes (PVP)", min_value=0.0, value=float(ref), step=10_000_000.0,
                                           key=f"prueba_m_{ambito_edit}_{pid_m}_{ver}")
            st.write(md(f"Con {fmt_cop(venta_prueba)} se aplica el {evaluar(venta_prueba, tup_m, 0.0) * 100:.0f}% del bono."))

        b1, b2 = st.columns(2)
        if b1.button("Guardar tramos", type="primary", key=f"btn_metas_{ambito_edit}_{pid_m}", disabled=bool(err_m)):
            ejecutar(lambda: reemplazar_metas(pid_m, tup_m, usuario), f"{len(tup_m)} tramos guardados.")
        conf_rm = b2.checkbox("Confirmo restaurar los tramos estándar", key=f"conf_rest_m_{ambito_edit}_{pid_m}")
        if b2.button("Restaurar tramos estándar del ámbito", key=f"btn_rest_m_{ambito_edit}_{pid_m}", disabled=not conf_rm):
            ejecutar(lambda: reemplazar_metas(pid_m, METAS_EJECOM_DEFAULT if ambito_edit == "EJECOM" else METAS_2026,
                                              usuario), "Tramos estándar restaurados.")
        with st.expander("Eliminar este período de tramos"):
            st.warning(f"Al eliminar '{row_m['nombre']}' se borran sus tramos; los meses que cubría quedarán sin regla (0 %).")
            conf_m = st.checkbox(f"Confirmo eliminar '{row_m['nombre']}'", key=f"conf_del_m_{ambito_edit}_{pid_m}")
            if st.button("Eliminar período de tramos", type="primary", disabled=not conf_m,
                         key=f"btn_del_m_{ambito_edit}_{pid_m}"):
                ejecutar(lambda: eliminar_periodo_metas(pid_m, usuario), "Período de tramos eliminado y registrado en el historial.")

    # ---------------- Escala de rentabilidad ----------------
    st.markdown("---")
    st.subheader("Escala de rentabilidad")
    st.caption("Se aplica la primera fila que coincida, en el orden de la tabla. Una rentabilidad que no coincide "
               "con ninguna fila recibe -100 %. La rentabilidad se expresa como fracción (0,10 = 10 %).")
    pe_edit = por_ambito(per_escala, ambito_edit)
    if pe_edit.empty:
        st.info(f"Sin períodos de escala para {NOM_AMBITO[ambito_edit]}. Créelo en la pestaña Crear período.")
    else:
        lab_pe = etiquetas(pe_edit)
        ids_pe = list(lab_pe)
        vig_pe = vigente(pe_edit, date.today())
        pid_e = st.selectbox("Período de escala", ids_pe,
                             index=ids_pe.index(entero(vig_pe["id"])) if vig_pe is not None else len(ids_pe) - 1,
                             format_func=lab_pe.get, key=f"sel_e_{ambito_edit}")
        row_e = pe_edit[pe_edit["id"].astype(int) == pid_e].iloc[0]
        with st.form(f"form_per_esc_{ambito_edit}_{pid_e}_{ver}"):
            c1, c2, c3 = st.columns(3)
            nom_e = c1.text_input("Nombre", value=str(row_e["nombre"]))
            fini_e = c2.date_input("Fecha inicio", value=pd.to_datetime(row_e["fecha_inicio"]).date())
            ffin_e = c3.date_input("Fecha fin (vacío = abierto)",
                                   value=pd.to_datetime(row_e["fecha_fin"]).date() if pd.notna(row_e["fecha_fin"]) else None)
            guardar_de = st.form_submit_button("Guardar datos del período")
        if guardar_de:
            ejecutar(lambda: actualizar_periodo_escala(pid_e, {"nombre": nom_e, "fecha_inicio": fini_e,
                                                               "fecha_fin": ffin_e}, usuario),
                     "Datos del período de escala actualizados.")

        cols_e = ["cond_min", "cond_max", "pct_bono", "min_inclusivo", "max_inclusivo"]
        esc_edit = rangos_de(pid_e).reset_index(drop=True)
        esc_df = esc_edit[cols_e].copy() if not esc_edit.empty else pd.DataFrame(columns=cols_e)
        edited_e = st.data_editor(
            esc_df, num_rows="dynamic", use_container_width=True, key=f"editor_esc_{ambito_edit}_{pid_e}_{ver}",
            column_config={
                "cond_min": st.column_config.NumberColumn("Mínimo", format="%.4f"),
                "cond_max": st.column_config.NumberColumn("Máximo", format="%.4f"),
                "pct_bono": st.column_config.NumberColumn("% Bono", format="%.2f", required=True),
                "min_inclusivo": st.column_config.CheckboxColumn("Min Inclusivo", default=True),
                "max_inclusivo": st.column_config.CheckboxColumn("Max Inclusivo", default=False)})

        tup_e = tuplas_escala(edited_e)
        err_e, av_e = validar_escala(tup_e)
        for e in err_e:
            st.error(e)
        for a in av_e:
            st.warning(a)

        with st.expander("Probar una rentabilidad con esta escala (sin guardar)"):
            rent_prueba = st.number_input("Rentabilidad sobre costo (%)", value=12.0, step=0.5,
                                          key=f"prueba_e_{ambito_edit}_{pid_e}_{ver}")
            st.write(md(f"Con una rentabilidad de {rent_prueba:.2f}% el % de bono es "
                        f"{evaluar(rent_prueba / 100.0, tup_e, -1.0) * 100:.0f}%."))

        b1, b2 = st.columns(2)
        if b1.button("Guardar escala", type="primary", key=f"btn_esc_{ambito_edit}_{pid_e}", disabled=bool(err_e)):
            ejecutar(lambda: reemplazar_escala(pid_e, tup_e, usuario), f"Escala guardada ({len(tup_e)} rangos).")
        conf_re = b2.checkbox("Confirmo restaurar la escala estándar", key=f"conf_rest_e_{ambito_edit}_{pid_e}")
        if b2.button("Restaurar escala estándar", key=f"btn_rest_e_{ambito_edit}_{pid_e}", disabled=not conf_re):
            ejecutar(lambda: reemplazar_escala(pid_e, ESCALA_RENT_DEFAULT, usuario), "Escala estándar restaurada.")
        with st.expander("Eliminar este período de escala"):
            st.warning(f"Al eliminar '{row_e['nombre']}' se borran sus rangos; los meses cubiertos quedarán sin escala (-100 %).")
            conf_e = st.checkbox(f"Confirmo eliminar '{row_e['nombre']}'", key=f"conf_del_e_{ambito_edit}_{pid_e}")
            if st.button("Eliminar período de escala", type="primary", disabled=not conf_e,
                         key=f"btn_del_e_{ambito_edit}_{pid_e}"):
                ejecutar(lambda: eliminar_periodo_escala(pid_e, usuario), "Período de escala eliminado y registrado en el historial.")


# ------------------------------------------------------------
# 4. HISTORIAL
# ------------------------------------------------------------
with tab_hist:
    st.subheader("Historial de cambios")
    auditoria = listar_auditoria_bono(limite=300)
    if auditoria.empty:
        st.info("No hay cambios registrados todavía.")
    else:
        auditoria = auditoria.copy()
        auditoria["ambito"] = auditoria["valores_nuevos"].apply(
            lambda v: NOM_AMBITO.get(v.get("ambito", ""), "") if isinstance(v, dict) else "")
        f1, f2 = st.columns(2)
        sel_tabla = f1.selectbox("Tabla", ["Todas"] + sorted(auditoria["tabla"].unique().tolist()), key="aud_tabla")
        sel_accion = f2.selectbox("Acción", ["Todas"] + sorted(auditoria["accion"].unique().tolist()), key="aud_accion")
        vista = auditoria
        if sel_tabla != "Todas":
            vista = vista[vista["tabla"] == sel_tabla]
        if sel_accion != "Todas":
            vista = vista[vista["accion"] == sel_accion]
        st.dataframe(
            vista[["id", "momento", "usuario", "tabla", "accion", "ambito"]].rename(columns={
                "id": "ID", "momento": "Fecha/Hora", "usuario": "Usuario", "tabla": "Tabla",
                "accion": "Acción", "ambito": "Ámbito"}),
            use_container_width=True, hide_index=True,
            column_config={"Fecha/Hora": st.column_config.DatetimeColumn(format="DD/MM/YYYY HH:mm")})

        if not vista.empty:
            with st.expander("Ver detalle de un cambio"):
                ids_aud = [entero(i) for i in vista["id"]]
                aud_id = st.selectbox("ID del cambio", ids_aud, key="aud_id")
                cambio = vista[vista["id"].astype(int) == aud_id].iloc[0]
                antes, despues = cambio["valores_anteriores"], cambio["valores_nuevos"]
                if isinstance(antes, dict) and isinstance(despues, dict):
                    difs = [{"Campo": k, "Antes": str(antes.get(k, "")), "Después": str(despues[k])}
                            for k in despues if str(antes.get(k, "")) != str(despues[k])]
                    if difs:
                        st.markdown("**Campos modificados**")
                        st.dataframe(pd.DataFrame(difs), use_container_width=True, hide_index=True)
                c1, c2 = st.columns(2)
                c1.markdown("**Antes**")
                c1.json(antes if antes is not None else {})
                c2.markdown("**Después**")
                c2.json(despues if despues is not None else {})