"""
VISTA: DISTRIBUCIÓN DEL BONO POR ALMACÉN
Configuración de políticas por período (estilo Variables del Bono) y reparto automático
del pool por cargo entre los colaboradores registrados por Talento Humano.
"""

from __future__ import annotations

import importlib
from datetime import date
import pandas as pd
import streamlit as st

from src import rrhh_dist as dist
# Forzar recarga en caliente para que cambios en src/rrhh_dist.py surtan efecto sin reiniciar
try:
    importlib.reload(dist)
except Exception:
    pass

from src.conexion_db import conn
from src.rrhh import ROLES_RRHH_ADMIN

# ============================================================
# CONFIGURACIÓN Y CONTROL DE ACCESO
# ============================================================
st.set_page_config(page_title="Distribución del Bono", page_icon=":material/percent:", layout="wide")
st.title("Distribución del Bono por Almacén")

rol = st.session_state.get("rol_actual")
usuario = st.session_state.get("usuario_actual", "admin")
nombre_usuario = st.session_state.get("nombre_completo", usuario)

if rol not in ROLES_RRHH_ADMIN:
    st.error("⛔ Acceso denegado. La distribución del bono solo es accesible por Gerencia o Administración.")
    st.stop()

st.caption(f"Sesión: **{nombre_usuario}** · Rol: **{rol}**. Cada cambio queda auditado en el sistema.")

if "dist_flash" in st.session_state:
    _tipo, _msg = st.session_state.pop("dist_flash")
    getattr(st, _tipo)(_msg)


# ============================================================
# INICIALIZACIÓN / MIGRACIÓN IDEMPOTENTE
# ============================================================
@st.cache_resource(show_spinner=False)
def _inicializar_tablas():
    with conn.session as con:
        dist.migrar(con)
        con.commit()
    return True

try:
    _inicializar_tablas()
except Exception as e:  # noqa: BLE001
    st.error(f"No se pudo inicializar la estructura de distribución: {e}")
    st.stop()


def flash(tipo, msg):
    st.session_state["dist_flash"] = (tipo, msg)
    st.rerun()


def _transaccion(fn, **kwargs):
    """Ejecuta fn(con, **kwargs) en una transacción atómica: commit si tiene éxito, rollback si falla."""
    with conn.session as con:
        try:
            resultado = fn(con, **kwargs)
            con.commit()
            return resultado
        except Exception:
            con.rollback()
            raise


# ============================================================
# CARGA DE CATÁLOGOS Y SELECTOR PRINCIPAL
# ============================================================
ALMACENES = dist.cargar_almacenes(conn)
if not ALMACENES:
    st.warning("No hay almacenes activos configurados en app.almacenes.")
    st.stop()

NOM_ALM = {a["codigo"]: a["nombre"] for a in ALMACENES}
MODO_ALM = {a["codigo"]: a["modo_bono"] for a in ALMACENES}

# Selector superior de almacén
c_alm, c_info = st.columns([2, 3])
almacen_sel = c_alm.selectbox(
    "Selecciona el Almacén a gestionar",
    [a["codigo"] for a in ALMACENES],
    format_func=lambda c: f"{c} — {NOM_ALM.get(c, c)}",
    key="sb_dist_alm"
)

modo_bono = MODO_ALM.get(almacen_sel, dist.MODO_PORCENTAJE)
c_info.caption(
    f"Almacén: **{NOM_ALM.get(almacen_sel, almacen_sel)}** (`{almacen_sel}`) · "
    f"Modalidad: **{modo_bono}** "
    f"({'Bolsa por porcentaje por cargo' if modo_bono == dist.MODO_PORCENTAJE else 'Meta de venta individual'})"
)

# Carga de datos del almacén y globales
PERIODOS_TODOS = dist.cargar_periodos(conn)
PERIODOS_ALM = [p for p in PERIODOS_TODOS if p["almacen"] == almacen_sel]
CARGOS_CONFIG = dist.cargar_cargos_config(conn)
ASIGNACIONES = dist.cargar_asignaciones(conn)

# Generación de lista de meses (12 meses atrás hasta 3 meses adelante)
hoy = date.today()
mes_actual = dist.inicio_mes(hoy)
mes_min = dist.sumar_meses(mes_actual, -12)
mes_max = dist.sumar_meses(mes_actual, 3)
MESES_OPCIONES = list(reversed(dist.lista_meses(mes_min, mes_max)))


# ============================================================
# PESTAÑAS PRINCIPALES
# ============================================================
tab_reparto, tab_config, tab_matriz, tab_auditoria = st.tabs([
    "📊 Reparto del Periodo",
    "⚙️ Configurar Períodos y Porcentajes",
    "🗺️ Matriz General de Almacenes",
    "📜 Auditoría e Historial"
])

# ------------------------------------------------------------
# 1. PESTAÑA: REPARTO DEL PERIODO (LIQUIDACIÓN Y SIMULACIÓN)
# ------------------------------------------------------------
with tab_reparto:
    st.subheader(f"Reparto del Bono · {almacen_sel} — {NOM_ALM.get(almacen_sel, almacen_sel)}")
    
    col_per, col_esp = st.columns([1, 2])
    mes_consultado = col_per.selectbox(
        "Mes a liquidar / consultar",
        MESES_OPCIONES,
        index=MESES_OPCIONES.index(mes_actual),
        format_func=dist.etiqueta_mes,
        key="sb_mes_reparto"
    )

    resultado = dist.calcular_reparto_mes(
        almacen=almacen_sel,
        mes=mes_consultado,
        periodos=PERIODOS_ALM,
        cargos_config=CARGOS_CONFIG,
        asignaciones=ASIGNACIONES,
        modo=modo_bono
    )

    per_ap = resultado["periodo_aplicado"]
    if per_ap:
        f_fin_txt = per_ap['fecha_fin'].strftime('%d/%m/%Y') if per_ap['fecha_fin'] else "Abierto (vigente)"
        st.info(
            f"📌 **Política de distribución aplicada:** «{per_ap['nombre']}» "
            f"· Vigencia: **{per_ap['fecha_inicio'].strftime('%d/%m/%Y')}** hasta **{f_fin_txt}**."
        )

    # Mostrar alertas del cálculo
    if resultado["alertas"]:
        for al in resultado["alertas"]:
            if al["nivel"] == "error":
                st.error(al["mensaje"])
            elif al["nivel"] == "warning":
                st.warning(al["mensaje"])
            else:
                st.info(al["mensaje"])

    tot = resultado["totales"]
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Bolsa Tienda", f"{tot['asignado_politica']:.2f}%")
    m2.metric("Repartido a Personas", f"{tot['repartido']:.2f}%")
    m3.metric("En Prueba (no participa)", f"{tot['en_prueba']:.2f}%")
    m4.metric("Vacante (cargos libres)", f"{tot['vacante']:.2f}%",
              delta=f"-{tot['vacante']:.2f}%" if tot['vacante'] > 0 else None, delta_color="inverse")
    m5.metric("Colaboradores en Mes", tot["personas"])

    st.markdown("---")
    
    # 1.1 Resumen por Cargo
    if modo_bono == dist.MODO_PORCENTAJE and resultado["cargos"]:
        st.markdown("##### Resumen de Bolsas por Cargo")
        df_cargos = pd.DataFrame(resultado["cargos"]).rename(columns={
            "cargo": "Cargo",
            "pct_bolsa": "% Bolsa Asignada",
            "personas": "Personas Activas",
            "dias_totales": "Días-Hombre",
            "repartido": "% Efectivo Repartido",
            "en_prueba": "% En Prueba",
            "vacante": "% Vacante",
            "estado": "Estado"
        })
        st.dataframe(
            df_cargos,
            use_container_width=True,
            hide_index=True,
            column_config={
                "% Bolsa Asignada": st.column_config.NumberColumn(format="%.3f%%"),
                "% Efectivo Repartido": st.column_config.NumberColumn(format="%.3f%%"),
                "% En Prueba": st.column_config.NumberColumn(format="%.3f%%"),
                "% Vacante": st.column_config.NumberColumn(format="%.3f%%"),
                "Personas Activas": st.column_config.NumberColumn(format="%d"),
                "Días-Hombre": st.column_config.NumberColumn(format="%d"),
            }
        )

    # 1.2 Detalle por Persona
    st.markdown("##### Detalle Individual de Colaboradores")
    st.caption("Colaboradores asignados al almacén según Talento Humano durante el mes consultado.")
    personas = resultado["personas"]
    if not personas:
        st.info("No hay colaboradores registrados en este almacén durante el mes consultado.")
    else:
        df_per = pd.DataFrame(personas)
        cols_mostrar = ["cedula", "nombre", "cargo", "desde_en_mes", "hasta_en_mes",
                        "dias", "dias_participa", "dias_prueba"]
        if modo_bono == dist.MODO_PORCENTAJE:
            cols_mostrar += ["pct_bolsa_cargo", "pct_efectivo", "pct_en_prueba"]

        df_display = df_per[cols_mostrar].rename(columns={
            "cedula": "Cédula",
            "nombre": "Nombre Colaborador",
            "cargo": "Cargo",
            "desde_en_mes": "Desde",
            "hasta_en_mes": "Hasta",
            "dias": "Días en Almacén",
            "dias_participa": "Días Participa",
            "dias_prueba": "Días Prueba",
            "pct_bolsa_cargo": "% Bolsa Cargo",
            "pct_efectivo": "% Bono Asignado",
            "pct_en_prueba": "% En Prueba"
        })
        st.dataframe(
            df_display,
            use_container_width=True,
            hide_index=True,
            column_config={
                "% Bolsa Cargo": st.column_config.NumberColumn(format="%.3f%%"),
                "% Bono Asignado": st.column_config.NumberColumn(format="%.3f%%"),
                "% En Prueba": st.column_config.NumberColumn(format="%.3f%%"),
                "Desde": st.column_config.DateColumn(format="DD/MM/YYYY"),
                "Hasta": st.column_config.DateColumn(format="DD/MM/YYYY"),
            }
        )

        # Botón de descarga
        csv_data = df_display.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "📥 Descargar Liquidación del Periodo (CSV)",
            data=csv_data,
            file_name=f"liquidacion_bono_{almacen_sel}_{mes_consultado.strftime('%Y_%m')}.csv",
            mime="text/csv"
        )


# ------------------------------------------------------------
# 2. PESTAÑA: CONFIGURAR PERÍODOS Y PORCENTAJES (ESTILO VARIABLES BONO)
# ------------------------------------------------------------
with tab_config:
    if modo_bono == dist.MODO_INDIVIDUAL:
        st.info(
            f"El almacén **{almacen_sel}** está configurado en modalidad **INDIVIDUAL** (EJECOM). "
            "No requiere configuración de bolsa de porcentajes por cargo."
        )
    else:
        st.subheader(f"Configuración de Períodos y Porcentajes · {almacen_sel}")
        st.caption(
            "Crea y administra los períodos de vigencia para este almacén y define el porcentaje "
            "asignado a cada cargo. La suma de los cargos debe ser exactamente **100%**."
        )

        NUEVO_PERIODO = "➕ Crear nuevo período para este almacén..."
        opciones_periodos = [NUEVO_PERIODO] + [
            f"{p['nombre']} (Desde {p['fecha_inicio'].strftime('%d/%m/%Y')} → "
            f"{p['fecha_fin'].strftime('%d/%m/%Y') if p['fecha_fin'] else 'Abierto'})"
            for p in PERIODOS_ALM
        ]
        
        map_periodos = {
            f"{p['nombre']} (Desde {p['fecha_inicio'].strftime('%d/%m/%Y')} → "
            f"{p['fecha_fin'].strftime('%d/%m/%Y') if p['fecha_fin'] else 'Abierto'})": p
            for p in PERIODOS_ALM
        }

        sel_per_nombre = st.selectbox(
            "Selecciona el período a consultar o editar:",
            opciones_periodos,
            index=1 if len(opciones_periodos) > 1 else 0,
            key=f"sb_per_edit_{almacen_sel}"
        )

        es_nuevo = (sel_per_nombre == NUEVO_PERIODO)
        per_seleccionado = None if es_nuevo else map_periodos[sel_per_nombre]

        # Cargar los cargos actuales para el período seleccionado
        cargos_actuales = {}
        if per_seleccionado:
            pid = per_seleccionado["id"]
            cargos_actuales = {c["cargo"]: c["pct"] for c in CARGOS_CONFIG if c["periodo_id"] == pid}

        # Formulario de parámetros del período
        st.markdown("#### Datos del Período")
        c1, c2, c3 = st.columns([2, 1, 1])
        
        val_nombre = "" if es_nuevo else per_seleccionado["nombre"]
        nombre_in = c1.text_input("Nombre del Período", value=val_nombre,
                                  placeholder="Ej: Política 2026, Ajuste II Semestre...",
                                  key=f"inp_nom_per_{almacen_sel}")

        val_fini = date.today() if es_nuevo else per_seleccionado["fecha_inicio"]
        fecha_ini_in = c2.date_input("Fecha Inicio", value=val_fini, key=f"inp_fini_{almacen_sel}")

        es_abierto = True if es_nuevo else (per_seleccionado["fecha_fin"] is None)
        chk_abierto = c3.checkbox("Período Abierto (sin fecha fin)", value=es_abierto,
                                  key=f"chk_abierto_{almacen_sel}")

        fecha_fin_in = None
        if not chk_abierto:
            val_ffin = per_seleccionado["fecha_fin"] if (per_seleccionado and per_seleccionado["fecha_fin"]) else date.today()
            fecha_fin_in = c3.date_input("Fecha Fin", value=val_ffin, key=f"inp_ffin_{almacen_sel}")

        # Acciones Rápidas (Copiar de otro almacén)
        st.markdown("#### Acciones Rápidas")
        col_acc1, col_acc2 = st.columns(2)
        
        otros_almacenes = [a["codigo"] for a in ALMACENES if a["codigo"] != almacen_sel and a["modo_bono"] == dist.MODO_PORCENTAJE]
        alm_copiar = col_acc1.selectbox("Copiar estructura desde otro almacén:", ["— Seleccionar —"] + otros_almacenes,
                                        format_func=lambda c: f"{c} — {NOM_ALM.get(c, c)}" if c != "— Seleccionar —" else c,
                                        key=f"sb_cp_alm_{almacen_sel}")
        if col_acc1.button("📋 Cargar cargos de ese almacén", key=f"btn_cp_alm_{almacen_sel}"):
            if alm_copiar != "— Seleccionar —":
                pers_otro = [p for p in PERIODOS_TODOS if p["almacen"] == alm_copiar]
                vig_otro = dist.resolver_periodo(pers_otro, hoy)
                if vig_otro:
                    cargos_otro = {c["cargo"]: c["pct"] for c in CARGOS_CONFIG if c["periodo_id"] == vig_otro["id"]}
                    st.session_state[f"editor_cargos_mem_{almacen_sel}"] = cargos_otro
                    st.success(f"Cargados los cargos de {alm_copiar} ({vig_otro['nombre']}).")
                    st.rerun()
                else:
                    st.warning(f"El almacén {alm_copiar} no tiene un período vigente.")

        # Armar DataFrame para el editor interactivo de cargos en tabla
        cargos_base = list(dist.CARGOS_BONO)
        mem_cargos = st.session_state.get(f"editor_cargos_mem_{almacen_sel}")
        fuente_cargos = mem_cargos if mem_cargos is not None else cargos_actuales

        filas_editor = []
        for c in cargos_base:
            filas_editor.append({
                "Cargo": c,
                "% Asignado": float(fuente_cargos.get(c, 0.0))
            })
        for c, p in fuente_cargos.items():
            if c not in cargos_base:
                filas_editor.append({"Cargo": c, "% Asignado": float(p)})

        df_editor_in = pd.DataFrame(filas_editor)

        st.markdown("#### Matriz de Porcentajes por Cargo (Edición en Tabla)")
        st.caption("Edita directamente los porcentajes en la tabla. Los cargos que no apliquen en esta tienda pueden dejarse en 0.0%.")

        df_editado = st.data_editor(
            df_editor_in,
            num_rows="dynamic",
            use_container_width=True,
            key=f"data_editor_cargos_{almacen_sel}_{sel_per_nombre}",
            column_config={
                "Cargo": st.column_config.SelectboxColumn(
                    "Cargo",
                    options=dist.CARGOS_BONO,
                    required=True,
                    width="medium"
                ),
                "% Asignado": st.column_config.NumberColumn(
                    "% Asignado del Bono",
                    format="%.3f%%",
                    min_value=0.0,
                    max_value=100.0,
                    step=0.5,
                    required=True,
                    width="medium"
                )
            }
        )

        # Cálculo dinámico del total y validación del 100%
        total_suma = float(df_editado["% Asignado"].sum())
        dif_100 = round(100.0 - total_suma, 3)

        k1, k2, k3 = st.columns(3)
        k1.metric("Suma Total de Cargos", f"{total_suma:.3f}%")
        k2.metric("Diferencia vs 100%", f"{dif_100:+.3f}%")

        puede_guardar = False
        if abs(dif_100) <= dist.TOL:
            k3.success("✅ Plantilla equilibrada (100.000%)")
            puede_guardar = True
        elif total_suma > 100.0 + dist.TOL:
            k3.error(f"❌ Excede por {total_suma - 100.0:.3f}% (Máximo 100%)")
        else:
            k3.warning(f"⚠️ Faltan {dif_100:.3f}% por asignar")
            puede_guardar = True

        motivo_cambio = st.text_input(
            "Motivo o justificación del cambio / creación:",
            placeholder="Ej: Actualización de política salarial 2026...",
            key=f"motivo_per_{almacen_sel}"
        )

        # Botón único para guardar en bloque
        if st.button("💾 Guardar Configuración del Período", type="primary", disabled=not puede_guardar,
                     key=f"btn_save_per_{almacen_sel}"):
            if not nombre_in.strip():
                st.error("El nombre del período es obligatorio.")
            elif not fecha_ini_in:
                st.error("La fecha de inicio es obligatoria.")
            elif fecha_fin_in and fecha_fin_in < fecha_ini_in:
                st.error("La fecha de fin no puede ser anterior a la fecha de inicio.")
            else:
                try:
                    cargos_dict = {
                        str(r["Cargo"]).strip(): float(r["% Asignado"])
                        for _, r in df_editado.iterrows()
                        if pd.notna(r["Cargo"]) and float(r.get("% Asignado", 0)) > 0
                    }
                    _transaccion(
                        dist.guardar_periodo_con_cargos,
                        almacen=almacen_sel,
                        periodo_id=per_seleccionado["id"] if per_seleccionado else None,
                        nombre=nombre_in.strip(),
                        fecha_inicio=fecha_ini_in,
                        fecha_fin=fecha_fin_in,
                        cargos_pct=cargos_dict,
                        usuario=usuario,
                        rol=rol,
                        motivo=motivo_cambio.strip() or None,
                        nombre_usuario=nombre_usuario
                    )
                    st.session_state.pop(f"editor_cargos_mem_{almacen_sel}", None)
                    flash("success", f"✅ Período «{nombre_in.strip()}» guardado exitosamente con {len(cargos_dict)} cargos.")
                except Exception as ex:  # noqa: BLE001
                    st.error(f"Error al guardar: {ex}")

        # Expander para eliminar período
        if per_seleccionado:
            st.markdown("---")
            with st.expander("⚠️ Opciones Avanzadas: Eliminar Período"):
                st.warning(
                    f"Al eliminar el período «{per_seleccionado['nombre']}», se eliminarán sus porcentajes asociados. "
                    "Esta acción quedará registrada en la auditoría del sistema."
                )
                motivo_del = st.text_input("Motivo de la eliminación (obligatorio):", key=f"mot_del_{almacen_sel}")
                chk_del = st.checkbox("Confirmo que deseo eliminar este período", key=f"chk_del_{almacen_sel}")
                if st.button("🗑️ Eliminar Período Definitivamente", type="secondary", disabled=not chk_del,
                             key=f"btn_del_per_{almacen_sel}"):
                    if len(motivo_del.strip()) < 5:
                        st.error("Debes indicar un motivo de al menos 5 caracteres.")
                    else:
                        try:
                            _transaccion(
                                dist.eliminar_periodo,
                                periodo_id=per_seleccionado["id"],
                                almacen=almacen_sel,
                                usuario=usuario,
                                rol=rol,
                                motivo=motivo_del.strip()
                            )
                            flash("success", f"✅ Período «{per_seleccionado['nombre']}» eliminado.")
                        except Exception as ex:  # noqa: BLE001
                            st.error(f"Error al eliminar: {ex}")


# ------------------------------------------------------------
# 3. PESTAÑA: MATRIZ GENERAL DE TODA LA RED DE ALMACENES
# ------------------------------------------------------------
with tab_matriz:
    st.subheader("Matriz General de la Red Comercial")
    st.caption("Visión panorámica de la política de distribución vigente en cada almacén.")

    matriz_filas = []
    cargos_col = ["Gerente Punto", "Vendedor 1", "Vendedor 2", "Vendedor 3", "Cajera", "Bodega 1", "Bodega 2", "Conductor"]

    for alm in ALMACENES:
        cod = alm["codigo"]
        nom = alm["nombre"]
        modo = alm["modo_bono"]

        if modo == dist.MODO_INDIVIDUAL:
            fila = {
                "Almacén": cod, "Nombre": nom, "Modalidad": "INDIVIDUAL",
                "Período Vigente": "Meta Individual", "Total %": "—", "Estado": "Individual"
            }
            for c in cargos_col:
                fila[c] = "—"
            matriz_filas.append(fila)
            continue

        pers_alm = [p for p in PERIODOS_TODOS if p["almacen"] == cod]
        vig = dist.resolver_periodo(pers_alm, hoy)

        if not vig:
            fila = {
                "Almacén": cod, "Nombre": nom, "Modalidad": "PORCENTAJE",
                "Período Vigente": "❌ Sin período", "Total %": "0.000%", "Estado": "Sin configurar"
            }
            for c in cargos_col:
                fila[c] = "0.000%"
            matriz_filas.append(fila)
            continue

        cargos_vig = {c["cargo"]: c["pct"] for c in CARGOS_CONFIG if c["periodo_id"] == vig["id"]}
        tot_alm = sum(cargos_vig.values())
        estado_txt = "✅ 100%" if abs(tot_alm - 100.0) <= dist.TOL else f"⚠️ {tot_alm:.1f}%"

        fila = {
            "Almacén": cod,
            "Nombre": nom,
            "Modalidad": "PORCENTAJE",
            "Período Vigente": vig["nombre"],
            "Total %": f"{tot_alm:.3f}%",
            "Estado": estado_txt
        }
        for c in cargos_col:
            val_c = cargos_vig.get(c, 0.0)
            fila[c] = f"{val_c:.2f}%" if val_c > 0 else "—"

        matriz_filas.append(fila)

    df_matriz = pd.DataFrame(matriz_filas)
    st.dataframe(df_matriz, use_container_width=True, hide_index=True)

    csv_matriz = df_matriz.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "📥 Descargar Matriz General (CSV)",
        data=csv_matriz,
        file_name="matriz_general_distribucion_almacenes.csv",
        mime="text/csv"
    )


# ------------------------------------------------------------
# 4. PESTAÑA: HISTORIAL Y AUDITORÍA
# ------------------------------------------------------------
with tab_auditoria:
    st.subheader(f"Auditoría de Cambios · {almacen_sel}")
    st.caption("Registro inmutable de creación, edición y eliminación de períodos y cargos.")

    df_aud = conn.query("""
        SELECT fecha, usuario, COALESCE(nombre_usuario, usuario) AS nombre, rol,
               accion, registro_id, despues
        FROM rrhh.rrhh_auditoria
        WHERE tabla IN ('distribucion_periodos', 'distribucion_puestos')
          AND (registro_id LIKE :p OR registro_id = :a)
        ORDER BY fecha DESC LIMIT 100
    """, params={"p": f"{almacen_sel}:%", "a": almacen_sel}, ttl=0)

    if df_aud.empty:
        st.info("No hay registros de auditoría para este almacén.")
    else:
        st.dataframe(
            df_aud[["fecha", "nombre", "usuario", "rol", "accion", "registro_id"]].rename(columns={
                "fecha": "Fecha", "nombre": "Nombre", "usuario": "Usuario",
                "rol": "Rol", "accion": "Acción", "registro_id": "Registro"
            }),
            use_container_width=True,
            hide_index=True
        )

        with st.expander("🔍 Ver Detalles y Motivo de un Cambio"):
            sel_idx = st.selectbox(
                "Selecciona el registro a inspeccionar:",
                list(range(len(df_aud))),
                format_func=lambda i: f"{df_aud.iloc[i]['fecha']} · {df_aud.iloc[i]['accion']} · {df_aud.iloc[i]['nombre']}"
            )
            reg = df_aud.iloc[sel_idx]
            st.json(reg["despues"] if reg["despues"] is not None else {})