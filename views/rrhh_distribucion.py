"""
DISTRIBUCIÓN DEL BONO POR ALMACÉN, CARGO, PERSONA Y PERIODO

Solo Gerencia y Administración (Talento Humano NO ve ni edita esta página: aporta los datos de las
personas; los porcentajes del bono los define Gerencia). Reemplaza la pestaña «Distribución por
Almacén» de rrhh_colaboradores.py.

Pestañas
  1. Reparto del periodo : quién estaba en el almacén ese mes, cuánto del bono ocupa cada cargo, qué
                           porcentaje quedó liberado, cambios del mes y alertas. Sirve para auditar meses pasados.
  2. Definir porcentajes : plantilla por almacén y versión (mes de vigencia), con acumulado y restante en
                           vivo; no deja pasar de 100 %.
  3. Evolución           : cómo cambió la ocupación mes a mes.
  4. Versiones y auditoría.

La lógica vive en src/rrhh_dist.py (probada en pruebas/test_rrhh_dist.py).
"""
from datetime import date

import pandas as pd
import streamlit as st

from src import rrhh_dist as dist
from src.conexion_db import conn
from src.rrhh import ROLES_RRHH_ADMIN

st.set_page_config(page_title="Distribución del Bono", layout="wide")
st.title("📊 Distribución del Bono por Almacén")

rol = st.session_state.get("rol_actual")
usuario = st.session_state.get("usuario_actual")
nombre_usuario = st.session_state.get("nombre_completo", usuario)
if rol not in ROLES_RRHH_ADMIN:
    st.error("⛔ Acceso denegado. Los porcentajes del bono solo los define y consulta Gerencia o Administración.")
    st.stop()
st.caption(f"Sesión: **{nombre_usuario}** · rol **{rol}**. Cada cambio queda auditado con tu usuario, rol y motivo.")

if "dist_flash" in st.session_state:
    _tipo, _msg = st.session_state.pop("dist_flash")
    getattr(st, _tipo)(_msg)


@st.cache_resource(show_spinner=False)
def _migrar():
    with conn.session as con:
        dist.migrar(con)
        con.commit()
    return True


try:
    _migrar()
except Exception as e:  # noqa: BLE001
    st.error(f"No se pudo preparar la estructura de distribución: {e}")
    st.stop()


def flash(tipo, msg):
    st.session_state["dist_flash"] = (tipo, msg)
    st.rerun()


# ============================================================
# DATOS Y SELECTORES
# ============================================================
ALMACENES = dist.cargar_almacenes(conn)
if not ALMACENES:
    st.warning("app.almacenes está vacía.")
    st.stop()
NOM = {a["codigo"]: a["nombre"] for a in ALMACENES}
MODO = {a["codigo"]: a["modo_bono"] for a in ALMACENES}

PLANT_TODAS = dist.cargar_plantilla(conn)
ASIG = dist.cargar_asignaciones(conn)

hoy = date.today()
mes_actual = dist.inicio_mes(hoy)
primeros = [a["desde"] for a in ASIG] + [dist.a_fecha(f["vigente_desde"]) for f in PLANT_TODAS]
mes_ini = dist.inicio_mes(min(primeros + [dist.sumar_meses(mes_actual, -12)]))
MESES = list(reversed(dist.lista_meses(mes_ini, dist.sumar_meses(mes_actual, 3))))

s1, s2 = st.columns([2, 1])
almacen = s1.selectbox("Almacén", [a["codigo"] for a in ALMACENES], format_func=lambda c: f"{c} — {NOM[c]}",
                       key="dist_alm")
periodo = s2.selectbox("Periodo", MESES, index=MESES.index(mes_actual), format_func=dist.etiqueta_mes,
                       key="dist_per")
modo = MODO[almacen]
plantilla = [f for f in PLANT_TODAS if f["almacen"] == almacen]
res = dist.calcular_reparto_mes(almacen, periodo, plantilla, ASIG, modo)


def pct(x):
    return "—" if x is None else f"{x:.2f}%"


def mostrar_alertas(alertas):
    if not alertas:
        st.success("Sin alertas: el 100 % del bono está ocupado por las personas del periodo.")
    for a in alertas:
        {"error": st.error, "warning": st.warning, "info": st.info}[a["nivel"]](a["mensaje"])


tab_rep, tab_def, tab_evo, tab_ver = st.tabs(
    ["📋 Reparto del periodo", "✏️ Definir porcentajes", "📈 Evolución", "🧾 Versiones y auditoría"])

# ------------------------------------------------------------
# 1. REPARTO DEL PERIODO
# ------------------------------------------------------------
with tab_rep:
    st.subheader(f"{almacen} — {NOM[almacen]} · {dist.etiqueta_mes(periodo)}")
    personas = res["personas"]

    if modo == dist.MODO_INDIVIDUAL:
        st.info("**Evaluación individual.** Este almacén no reparte porcentaje del bono: cada ejecutivo cumple "
                "su meta individual o no recibe bono. Si una persona se traslada a otro almacén, desde ese día "
                "pasa a las reglas de porcentaje del almacén destino (y viceversa).")
        st.metric("Personas en el periodo", len(personas))
    else:
        t = res["totales"]
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Plantilla asignada", pct(t["asignado"]))
        m2.metric("Ocupado por personas", pct(t["ocupado"]))
        m3.metric("Liberado (sin ocupar)", pct(t["libre"]),
                  delta=f"-{t['libre']:.2f} pts" if t["libre"] > 0.005 else None, delta_color="inverse")
        m4.metric("En prueba (no participa)", pct(t["prueba"]))
        m5.metric("Personas en el periodo", len(personas))
        st.progress(min(max(t["ocupado"] / 100.0, 0.0), 1.0), text=f"Ocupación del 100 % del bono: {t['ocupado']:.2f}%")
        if res["version"]:
            st.caption(f"Plantilla aplicada: versión vigente desde **{res['version']}**.")
    mostrar_alertas(res["alertas"])

    if modo == dist.MODO_PORCENTAJE and res["cargos"]:
        st.markdown("##### Plantilla del periodo por cargo")
        df_c = pd.DataFrame(res["cargos"]).rename(columns={
            "cargo": "Cargo", "cupos": "Cupos", "pct_persona": "% por persona", "pct_total": "% total del cargo",
            "personas": "Personas", "ocupacion": "Cupos ocupados", "cupos_libres": "Cupos libres",
            "pct_libre": "% liberado", "sobrecupo": "Sobrecupo", "pct_exceso": "% en exceso"})
        st.dataframe(df_c, use_container_width=True, hide_index=True, column_config={
            "% por persona": st.column_config.NumberColumn(format="%.3f%%"),
            "% total del cargo": st.column_config.NumberColumn(format="%.3f%%"),
            "Cupos ocupados": st.column_config.NumberColumn(format="%.2f"),
            "Cupos libres": st.column_config.NumberColumn(format="%.2f"),
            "% liberado": st.column_config.NumberColumn(format="%.3f%%"),
            "Sobrecupo": st.column_config.NumberColumn(format="%.2f"),
            "% en exceso": st.column_config.NumberColumn(format="%.3f%%")})

    st.markdown("##### Personas del periodo")
    st.caption("Solo quienes estuvieron en el almacén durante el mes (por sus fechas de ingreso, traslado, cambio de "
               "cargo e inactividad), no la plantilla actual. «Fracción» = días en el almacén ÷ días del mes.")
    if not personas:
        st.info("Nadie estuvo asignado a este almacén en el periodo.")
    else:
        df_p = pd.DataFrame(personas)[["cedula", "nombre", "cargo", "desde_en_mes", "hasta_en_mes", "dias",
                                       "dias_prueba", "fraccion", "pct_plantilla", "pct_efectivo", "pct_en_prueba"]]
        df_p = df_p.rename(columns={
            "cedula": "Cédula", "nombre": "Nombre", "cargo": "Cargo", "desde_en_mes": "Desde (en el mes)",
            "hasta_en_mes": "Hasta (en el mes)", "dias": "Días", "dias_prueba": "Días en prueba",
            "fraccion": "Fracción del mes", "pct_plantilla": "% plantilla", "pct_efectivo": "% que participa",
            "pct_en_prueba": "% en prueba"})
        if modo == dist.MODO_INDIVIDUAL:
            df_p = df_p.drop(columns=["% plantilla", "% que participa", "% en prueba"])
        st.dataframe(df_p, use_container_width=True, hide_index=True, column_config={
            "Fracción del mes": st.column_config.NumberColumn(format="%.3f"),
            "% plantilla": st.column_config.NumberColumn(format="%.3f%%"),
            "% que participa": st.column_config.NumberColumn(format="%.3f%%"),
            "% en prueba": st.column_config.NumberColumn(format="%.3f%%")})
        st.download_button("📥 Descargar personas del periodo (CSV)", df_p.to_csv(index=False).encode("utf-8-sig"),
                           file_name=f"reparto_{almacen}_{periodo:%Y_%m}.csv", mime="text/csv")

    st.markdown("##### Cambios del periodo en este almacén")
    movs = dist.movimientos_mes(almacen, periodo, ASIG)
    if movs:
        st.dataframe(pd.DataFrame(movs).rename(columns={"fecha": "Fecha", "tipo": "Movimiento", "nombre": "Nombre",
                                                        "cedula": "Cédula", "cargo": "Cargo"}),
                     use_container_width=True, hide_index=True)
    else:
        st.caption("Sin ingresos, salidas, traslados ni cambios de cargo en el periodo.")

# ------------------------------------------------------------
# 2. DEFINIR PORCENTAJES
# ------------------------------------------------------------
with tab_def:
    if modo == dist.MODO_INDIVIDUAL:
        st.info("Este almacén se evalúa por meta individual: no tiene plantilla de porcentajes.")
    else:
        st.caption("Cada **puesto** = un cargo con sus **cupos** (personas) y el **% del bono por persona**. "
                   "El total del almacén es Σ (% × cupos) y debe cerrar en 100 %. Ej.: Vendedor 2 con 2 cupos al "
                   "11 % aporta 22 %. Si alguien sale, su % queda liberado (alerta) hasta que otra persona ocupe el cupo.")
        versiones = sorted({dist.a_fecha(f["vigente_desde"]) for f in plantilla}, reverse=True)
        NUEVA = "➕ Nueva versión…"
        v_rige, _ = dist.version_vigente(plantilla, periodo)
        opciones = [NUEVA] + versiones
        idx = opciones.index(v_rige) if v_rige in opciones else 0
        resumen_v = {v["vigente_desde"]: v for v in dist.versiones(plantilla)}
        ver_sel = st.selectbox(
            "Versión de la plantilla a intervenir", opciones, index=idx, key=f"dist_ver_{almacen}",
            format_func=lambda v: v if v == NUEVA else
            f"Desde {v} — suma {resumen_v[v]['total']:.3f}% ({resumen_v[v]['estado']})")

        if ver_sel == NUEVA:
            mes_nv = st.selectbox("Rige desde el mes", MESES, index=MESES.index(mes_actual),
                                  format_func=dist.etiqueta_mes, key=f"dist_mesnv_{almacen}")
            vig = mes_nv
            existe = [f for f in plantilla if dist.inicio_mes(dist.a_fecha(f["vigente_desde"])) == mes_nv]
            if existe:
                st.warning(f"Ya existe una versión en {dist.etiqueta_mes(mes_nv)}: selecciónala arriba para editarla.")
            else:
                st.info("Una versión nueva reemplaza a la anterior **desde ese mes**; los meses anteriores conservan "
                        "la plantilla con la que se repartieron.")
                mot_cp = st.text_input("Motivo de la nueva versión", key=f"dist_motcp_{almacen}")
                if st.button("📄 Crear copiando la versión anterior", key=f"dist_cp_{almacen}"):
                    if mes_nv < mes_actual and len(mot_cp.strip()) < 5:
                        st.error("Estás creando una versión en un mes pasado: indica el motivo (mín. 5 caracteres).")
                    else:
                        try:
                            with conn.session as con:
                                try:
                                    n = dist.copiar_version(con, almacen=almacen, mes_destino=mes_nv, usuario=usuario,
                                                            rol=rol, motivo=mot_cp.strip() or None, nombre=nombre_usuario)
                                    con.commit()
                                except Exception:
                                    con.rollback()
                                    raise
                            flash("success", f"✅ Versión de {dist.etiqueta_mes(mes_nv)} creada con {n} puestos copiados.")
                        except Exception as e:  # noqa: BLE001
                            st.error(str(e))
                st.caption("O empieza en blanco agregando el primer puesto abajo.")
        else:
            vig = ver_sel
            if dist.inicio_mes(vig) < mes_actual:
                st.warning("Esta versión corresponde a un mes pasado: modificarla cambia cómo se ve el reparto "
                           "histórico. El cambio exige motivo y queda auditado.")

        filas_v = [f for f in plantilla if dist.a_fecha(f["vigente_desde"]) == vig]
        total_v = dist.total_plantilla(filas_v)
        k1, k2, k3 = st.columns(3)
        k1.metric("Acumulado asignado", pct(total_v))
        k2.metric("Restante por asignar", pct(max(100.0 - total_v, 0.0)))
        k3.metric("Estado", dist.estado_total(total_v) if filas_v else "SIN PUESTOS")
        st.progress(min(max(total_v / 100.0, 0.0), 1.0))
        if filas_v:
            st.dataframe(pd.DataFrame([{"Cargo": f["cargo"], "Cupos": f["cupos"], "% por persona": f["pct"],
                                        "% total del cargo": f["pct"] * f["cupos"]} for f in filas_v]),
                         use_container_width=True, hide_index=True, column_config={
                "% por persona": st.column_config.NumberColumn(format="%.3f%%"),
                "% total del cargo": st.column_config.NumberColumn(format="%.3f%%")})

        st.markdown("##### Agregar o ajustar un puesto")
        e1, e2, e3 = st.columns(3)
        cargo = e1.selectbox("Cargo", dist.CARGOS_BONO, index=None, placeholder="Seleccione…",
                             key=f"dist_c_{almacen}_{vig}")
        previo = next((f for f in filas_v if f["cargo"] == cargo), None)
        k_suf = f"{almacen}_{vig}_{cargo}"
        cupos = e2.number_input("Cupos (personas en este cargo)", min_value=1, max_value=20, step=1,
                                value=int(previo["cupos"]) if previo else 1, key=f"dist_k_{k_suf}")
        pct_p = e3.number_input("% del bono por persona", min_value=0.0, max_value=100.0, step=0.5, format="%.3f",
                                value=float(previo["pct"]) if previo else 0.0, key=f"dist_p_{k_suf}")
        puede_guardar = False
        if cargo and pct_p > 0:
            cap = dist.capacidad_restante(filas_v, cargo)
            aporte = pct_p * cupos
            resultado = cap["otros"] + aporte
            a1, a2, a3 = st.columns(3)
            a1.metric("Asignado en otros cargos", pct(cap["otros"]))
            a2.metric("Restante antes de este puesto", pct(cap["restante"]))
            a3.metric("Quedaría en", pct(resultado), delta=f"{resultado - 100:+.3f} vs 100%")
            if resultado > 100.0 + dist.TOL:
                st.error(f"Bloqueado: este puesto aporta {aporte:.3f}% y solo quedan {cap['restante']:.3f}% por asignar. "
                         f"Máximo permitido: {cap['restante'] / cupos:.3f}% por persona con {cupos} cupo(s).")
            else:
                puede_guardar = True
                if abs(resultado - 100) <= dist.TOL:
                    st.success("Con este puesto la plantilla queda completa en 100 %.")
                else:
                    st.warning(f"Quedarían {100 - resultado:.3f}% por asignar.")
        exige_motivo = dist.inicio_mes(vig) < mes_actual
        motivo = st.text_input("Motivo del cambio" + (" (obligatorio: mes pasado)" if exige_motivo else " (opcional)"),
                               key=f"dist_mot_{almacen}_{vig}")
        if st.button("💾 Guardar puesto", type="primary", disabled=not puede_guardar, key=f"dist_sv_{almacen}"):
            if exige_motivo and len(motivo.strip()) < 5:
                st.error("Indica el motivo del cambio (mín. 5 caracteres).")
            else:
                try:
                    with conn.session as con:
                        try:
                            r = dist.guardar_puesto(con, almacen=almacen, cargo=cargo, pct=pct_p, cupos=cupos,
                                                    vigente_desde=vig, usuario=usuario, rol=rol,
                                                    motivo=motivo.strip() or None, nombre=nombre_usuario)
                            con.commit()
                        except Exception:
                            con.rollback()
                            raise
                    flash("success", f"✅ {cargo} guardado. La versión suma {r['total']:.3f}% "
                                     f"(restante {max(r['restante'], 0):.3f}%).")
                except Exception as e:  # noqa: BLE001
                    st.error(str(e))

        if filas_v:
            with st.expander("Eliminar un puesto de esta versión"):
                ops = {f"{f['cargo']} · {f['cupos']} cupo(s) · {f['pct']:.3f}% c/u": f["id"] for f in filas_v}
                sel_del = st.selectbox("Puesto", list(ops), key=f"dist_del_{almacen}_{vig}")
                mot_del = st.text_input("Motivo (obligatorio)", key=f"dist_motdel_{almacen}_{vig}")
                ok_del = st.checkbox("Confirmo que elimino este puesto.", key=f"dist_okdel_{almacen}_{vig}")
                if st.button("Eliminar puesto", disabled=not ok_del, key=f"dist_btndel_{almacen}_{vig}"):
                    if len(mot_del.strip()) < 5:
                        st.error("Indica el motivo (mín. 5 caracteres).")
                    else:
                        try:
                            with conn.session as con:
                                try:
                                    dist.eliminar_puesto(con, puesto_id=ops[sel_del], usuario=usuario, rol=rol,
                                                         motivo=mot_del.strip(), nombre=nombre_usuario)
                                    con.commit()
                                except Exception:
                                    con.rollback()
                                    raise
                            flash("success", "✅ Puesto eliminado.")
                        except Exception as e:  # noqa: BLE001
                            st.error(str(e))

# ------------------------------------------------------------
# 3. EVOLUCIÓN
# ------------------------------------------------------------
with tab_evo:
    n_meses = st.select_slider("Meses a mostrar (terminando en el periodo elegido)", options=[3, 6, 12, 24], value=12)
    meses_ev = dist.lista_meses(dist.sumar_meses(periodo, -(n_meses - 1)), periodo)
    df_e = pd.DataFrame(dist.evolucion(almacen, meses_ev, plantilla, ASIG, modo))
    if modo == dist.MODO_INDIVIDUAL:
        st.dataframe(df_e[["mes", "personas"]].rename(columns={"mes": "Mes", "personas": "Personas"}),
                     use_container_width=True, hide_index=True)
    else:
        mostrar = df_e.rename(columns={"mes": "Mes", "personas": "Personas", "asignado": "Plantilla %",
                                       "ocupado": "Ocupado %", "libre": "Liberado %", "pagado": "Participa %",
                                       "en_prueba": "En prueba %", "alertas": "Alertas", "version": "Versión"})
        st.dataframe(mostrar, use_container_width=True, hide_index=True, column_config={
            c: st.column_config.NumberColumn(format="%.2f%%") for c in
            ("Plantilla %", "Ocupado %", "Liberado %", "Participa %", "En prueba %")})
        graf = df_e.set_index("mes")[["asignado", "ocupado", "libre"]].rename(
            columns={"asignado": "Plantilla", "ocupado": "Ocupado", "libre": "Liberado"})
        st.line_chart(graf)
        st.caption("Meses con «Liberado» > 0 tuvieron cupos sin ocupar (porcentaje sin repartir).")

# ------------------------------------------------------------
# 4. VERSIONES Y AUDITORÍA
# ------------------------------------------------------------
with tab_ver:
    st.markdown("##### Versiones de la plantilla")
    vs = dist.versiones(plantilla)
    if vs:
        st.dataframe(pd.DataFrame(vs).rename(columns={"vigente_desde": "Rige desde", "puestos": "Puestos",
                                                      "total": "Suma %", "estado": "Estado"}),
                     use_container_width=True, hide_index=True)
    else:
        st.info("Este almacén aún no tiene versiones de plantilla.")
    st.markdown("##### Auditoría de cambios de este almacén")
    aud = conn.query("""
        SELECT fecha, usuario, COALESCE(nombre_usuario, usuario) AS nombre, rol, accion, registro_id, antes, despues
        FROM rrhh.rrhh_auditoria
        WHERE tabla = 'distribucion_puestos' AND (registro_id = :a OR registro_id LIKE :p)
        ORDER BY fecha DESC LIMIT 200
    """, params={"a": almacen, "p": f"{almacen}:%"}, ttl=0)
    if aud.empty:
        st.info("Sin cambios auditados para este almacén.")
    else:
        st.dataframe(aud[["fecha", "nombre", "usuario", "rol", "accion", "registro_id"]].rename(columns={
            "fecha": "Fecha", "nombre": "Nombre", "usuario": "Usuario", "rol": "Rol", "accion": "Acción",
            "registro_id": "Registro"}), use_container_width=True, hide_index=True)
        with st.expander("Ver antes / después y motivo"):
            idx_a = st.selectbox("Fila", list(range(len(aud))),
                                 format_func=lambda i: f"{aud.iloc[i]['fecha']} · {aud.iloc[i]['accion']}")
            f = aud.iloc[idx_a]
            c1, c2 = st.columns(2)
            c1.markdown("**Antes**")
            c1.json(f["antes"] if f["antes"] is not None else {})
            c2.markdown("**Después (incluye motivo)**")
            c2.json(f["despues"] if f["despues"] is not None else {})