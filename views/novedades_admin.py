"""
BONIFICACIONES — Novedades Administrativas y Gerenciales en Porcentaje (%).
Acceso exclusivo: Gerencia y Administración.
Pestañas:
  1. Resumen y Reporte
  2. Crear y Cargar (con filtro por almacén y carga masiva Excel)
  3. Editar Periodo
  4. Historial y Auditoría (visualización limpia y legible para Gerencia)
"""

import io
from datetime import date
import pandas as pd
import streamlit as st
from openpyxl.worksheet.datavalidation import DataValidation
from sqlalchemy import text

from src import rrhh_dist as dist
from src.conexion_db import conn
from src.novedades_admin import (
    ROLES_NOVEDADES_ADMIN,
    CONCEPTOS_ADMIN,
    bootstrap_novedades_admin,
    cargar_almacenes_activos,
    cargar_colaboradores_disponibles,
    cargar_novedades_admin_periodo,
    insertar_novedad_admin,
    actualizar_novedad_admin,
    eliminar_novedad_admin,
    cargar_historial_auditoria_admin,
)

# ============================================================
# CONFIGURACIÓN DE PÁGINA Y CONTROL DE ACCESO
# ============================================================
st.set_page_config(
    page_title="Novedades Administrativas",
    page_icon=":material/admin_panel_settings:",
    layout="wide"
)

st.title("Novedades Administrativas — Bono")
st.markdown("---")

rol = st.session_state.get("rol_actual", "desconocido")
usuario = st.session_state.get("usuario_actual", "desconocido")
nombre_usr = st.session_state.get("nombre_completo", usuario)

# 🔐 Control estricto: Solo Gerencia y Administración
if rol not in ROLES_NOVEDADES_ADMIN:
    st.error("⛔ Acceso denegado. Este módulo está restringido exclusivamente para Gerencia y Administración.")
    st.stop()

# Inicialización automática de tablas en PostgreSQL
try:
    bootstrap_novedades_admin()
except Exception as e:
    st.error(f"Error preparando tablas de novedades administrativas en la base de datos: {e}")
    st.stop()

# ============================================================
# CATÁLOGOS Y HELPERS DE PERIODOS
# ============================================================
MES_ACTUAL = dist.inicio_mes(date.today())
MESES = list(reversed(dist.lista_meses(dist.sumar_meses(MES_ACTUAL, -24), dist.sumar_meses(MES_ACTUAL, 3))))

def selector_mes(key, opciones=None):
    ops = opciones or MESES
    idx = ops.index(MES_ACTUAL) if MES_ACTUAL in ops else 0
    return st.selectbox("Periodo a consultar / aplicar", ops, index=idx, format_func=dist.etiqueta_mes, key=key)

# Catálogos base
df_almacenes = cargar_almacenes_activos()
MAPA_ALM_NOMBRE = dict(zip(df_almacenes["codigo"], df_almacenes["nombre"]))
LISTA_COD_ALM = df_almacenes["codigo"].tolist()

# Colaboradores convertidos a dicts nativos para máxima velocidad
df_todos_cols = cargar_colaboradores_disponibles()
MAPA_COL_INFO = {int(r["id"]): r for r in df_todos_cols.to_dict("records")}
MAPA_CEDULA_COL = {str(r["cedula"]).strip(): r for r in df_todos_cols.to_dict("records")}

tab_res, tab_new, tab_edit, tab_hist = st.tabs([
    "Resumen y Reporte",
    "Crear y Cargar",
    "Editar Periodo",
    "Historial y Auditoría"
])

# ------------------------------------------------------------
# 1. RESUMEN Y REPORTE
# ------------------------------------------------------------
with tab_res:
    c1, c2, c3 = st.columns([2, 2, 3])
    with c1:
        mes_res = selector_mes("res_mes_adm")

    # Cargar datos del periodo
    df_nov_mes = cargar_novedades_admin_periodo(mes_res)

    alms_en_periodo = sorted(df_nov_mes["almacen_nombre"].dropna().unique()) if not df_nov_mes.empty else []
    with c2:
        f_alm = st.multiselect("Filtrar por Almacén", alms_en_periodo, key="res_alm_filter_adm")
    with c3:
        f_q = st.text_input("Buscar colaborador (nombre o cédula)", key="res_q_filter_adm")

    df_filtrado = df_nov_mes.copy()
    if not df_filtrado.empty:
        if f_alm:
            df_filtrado = df_filtrado[df_filtrado["almacen_nombre"].isin(f_alm)]
        if f_q.strip():
            filtro_q = (
                df_filtrado["colaborador_nombre"].str.contains(f_q, case=False, na=False) |
                df_filtrado["cedula"].str.contains(f_q, na=False)
            )
            df_filtrado = df_filtrado[filtro_q]

    # KPIs porcentuales
    k1, k2, k3, k4 = st.columns(4)
    tot_personas = df_filtrado["colaborador_id"].nunique() if not df_filtrado.empty else 0
    prom_pct = df_filtrado.groupby("colaborador_id")["porcentaje_descuento"].sum().mean() if not df_filtrado.empty else 0.0
    max_pct = df_filtrado.groupby("colaborador_id")["porcentaje_descuento"].sum().max() if not df_filtrado.empty else 0.0
    cant_registros = len(df_filtrado)

    k1.metric("Personas Afectadas", tot_personas)
    k2.metric("% Descuento Promedio", f"{prom_pct:.2f}%")
    k3.metric("% Descuento Máximo", f"{max_pct:.2f}%")
    k4.metric("Total Novedades Registradas", cant_registros)

    st.markdown("---")

    if df_filtrado.empty:
        st.info(f"Sin novedades administrativas registradas para el periodo {dist.etiqueta_mes(mes_res)}.")
    else:
        # Tabla consolidada por colaborador
        st.subheader("Consolidado de Descuentos por Colaborador")
        df_cons = df_filtrado.groupby(
            ["almacen_nombre", "cedula", "colaborador_nombre", "cargo"], as_index=False
        ).agg(
            total_porcentaje=("porcentaje_descuento", "sum"),
            cantidad_novedades=("id", "count")
        ).rename(columns={
            "almacen_nombre": "Almacén",
            "cedula": "Cédula",
            "colaborador_nombre": "Colaborador",
            "cargo": "Cargo",
            "total_porcentaje": "% Total Descuento",
            "cantidad_novedades": "Novedades"
        })

        # Marcador de tope si supera 100%
        df_cons["Estado Tope"] = df_cons["% Total Descuento"].apply(
            lambda p: "⚠️ Supera 100%" if p > 100 else ("100% (Total)" if p == 100 else "Válido")
        )

        st.dataframe(
            df_cons,
            use_container_width=True,
            hide_index=True,
            column_config={
                "% Total Descuento": st.column_config.NumberColumn(format="%.2f%%")
            }
        )

        with st.expander("Ver detalle de cada novedad individual"):
            st.dataframe(
                df_filtrado[[
                    "id", "almacen_nombre", "cedula", "colaborador_nombre", "cargo",
                    "porcentaje_descuento", "concepto", "observacion", "documento_soporte", "cargado_por", "creado_en"
                ]].rename(columns={
                    "id": "ID",
                    "almacen_nombre": "Almacén",
                    "cedula": "Cédula",
                    "colaborador_nombre": "Colaborador",
                    "cargo": "Cargo",
                    "porcentaje_descuento": "% Descuento",
                    "concepto": "Concepto",
                    "observacion": "Observación",
                    "documento_soporte": "Doc. Soporte",
                    "cargado_por": "Registrado Por",
                    "creado_en": "Fecha Registro"
                }),
                use_container_width=True,
                hide_index=True,
                column_config={
                    "% Descuento": st.column_config.NumberColumn(format="%.2f%%"),
                    "Fecha Registro": st.column_config.DatetimeColumn(format="DD/MM/YYYY HH:mm")
                }
            )

        # Botón de exportación
        csv_data = df_filtrado.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "📥 Descargar Reporte en CSV",
            data=csv_data,
            file_name=f"novedades_administrativas_{mes_res:%Y_%m}.csv",
            mime="text/csv"
        )

# ------------------------------------------------------------
# 2. CREAR Y CARGAR
# ------------------------------------------------------------
with tab_new:
    st.subheader("1) Registro Directo (con Filtro por Almacén)")
    st.caption("Selecciona el periodo y almacén. La tabla mostrará los colaboradores correspondientes para aplicar el porcentaje de descuento.")

    col_cfg1, col_cfg2 = st.columns(2)
    with col_cfg1:
        mes_nuevo = selector_mes("new_mes_adm")
    with col_cfg2:
        opciones_filtro_alm = ["-- Todos los Almacenes --"] + [
            f"{r['codigo']} — {r['nombre']}" for _, r in df_almacenes.iterrows()
        ]
        alm_seleccionado_txt = st.selectbox("Filtrar colaboradores por almacén", opciones_filtro_alm, key="new_alm_filter_adm")

    codigo_alm_filtro = None
    if alm_seleccionado_txt != "-- Todos los Almacenes --":
        codigo_alm_filtro = alm_seleccionado_txt.split(" — ")[0]

    # Cargar colaboradores según filtro
    df_cols_filtrados = cargar_colaboradores_disponibles(codigo_alm_filtro)
    if df_cols_filtrados.empty:
        st.warning("No se encontraron colaboradores activos asociados a este almacén.")
        opciones_colaborador = {}
    else:
        opciones_colaborador = {
            f"{r['nombre']} — CC {r['cedula']} ({r['almacen_nombre']} - {r['cargo']})": int(r["id"])
            for r in df_cols_filtrados.to_dict("records")
        }

    save_key_counter = st.session_state.get("nov_adm_save_counter", 0)

    st.markdown("##### Ingrese las novedades administrativas a registrar:")
    ed_novedades = st.data_editor(
        pd.DataFrame(columns=[
            "Colaborador",
            "% Descuento",
            "Concepto",
            "Observación / Justificación",
            "Doc. Soporte / Memorando"
        ]),
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        key=f"ed_nov_adm_{save_key_counter}",
        column_config={
            "Colaborador": st.column_config.SelectboxColumn(
                "Colaborador",
                options=list(opciones_colaborador.keys()),
                required=True,
                width="large"
            ),
            "% Descuento": st.column_config.NumberColumn(
                "% Descuento",
                min_value=0.5,
                max_value=100.0,
                step=0.5,
                format="%.2f%%",
                required=True,
                width="medium"
            ),
            "Concepto": st.column_config.SelectboxColumn(
                "Concepto",
                options=CONCEPTOS_ADMIN,
                required=True,
                default="Sanción administrativa",
                width="medium"
            ),
            "Observación / Justificación": st.column_config.TextColumn(
                "Observación / Justificación",
                required=True,
                width="large"
            ),
            "Doc. Soporte / Memorando": st.column_config.TextColumn(
                "Memorando / Acta / Soporte (opcional)",
                width="medium"
            )
        }
    )

    # Validación y previsualización del plan
    plan_guardar, errores_plan = [], []
    novedades_previas = cargar_novedades_admin_periodo(mes_nuevo)
    previos_por_colab = novedades_previas.groupby("colaborador_id")["porcentaje_descuento"].sum().to_dict() if not novedades_previas.empty else {}

    for i, r in ed_novedades.iterrows():
        colab_str = str(r["Colaborador"]).strip() if pd.notna(r["Colaborador"]) else ""
        if not colab_str or colab_str not in opciones_colaborador:
            continue

        cid = opciones_colaborador[colab_str]
        col_info = MAPA_COL_INFO.get(cid)
        if col_info is None:
            errores_plan.append(f"Fila {i + 1}: Colaborador no encontrado.")
            continue

        pct_raw = r["% Descuento"]
        try:
            pct_val = float(pct_raw)
            if pct_val <= 0 or pct_val > 100:
                errores_plan.append(f"Fila {i + 1} ({col_info['nombre']}): El porcentaje debe estar entre 0.1% y 100%.")
        except (ValueError, TypeError):
            errores_plan.append(f"Fila {i + 1} ({col_info['nombre']}): Porcentaje inválido.")
            pct_val = 0.0

        concepto_val = str(r["Concepto"]).strip() if pd.notna(r["Concepto"]) else ""
        if not concepto_val:
            errores_plan.append(f"Fila {i + 1} ({col_info['nombre']}): Seleccione un concepto.")

        obs_val = str(r["Observación / Justificación"]).strip() if pd.notna(r["Observación / Justificación"]) else ""
        if not obs_val:
            errores_plan.append(f"Fila {i + 1} ({col_info['nombre']}): Ingrese la justificación.")

        doc_soporte = str(r["Doc. Soporte / Memorando"]).strip() if pd.notna(r["Doc. Soporte / Memorando"]) else None
        alm_cod_destino = codigo_alm_filtro or col_info.get("almacen_codigo") or "ALM170"

        plan_guardar.append({
            "colaborador_id": cid,
            "nombre": col_info["nombre"],
            "cedula": col_info["cedula"],
            "almacen_codigo": alm_cod_destino,
            "almacen_nombre": MAPA_ALM_NOMBRE.get(alm_cod_destino, alm_cod_destino),
            "porcentaje": pct_val,
            "concepto": concepto_val,
            "observacion": obs_val,
            "documento_soporte": doc_soporte,
            "acumulado_previo": previos_por_colab.get(cid, 0.0)
        })

    if plan_guardar:
        st.markdown("##### Vista Previa de lo que se Registrará:")
        df_prev = pd.DataFrame(plan_guardar)
        st.dataframe(
            df_prev[["almacen_nombre", "cedula", "nombre", "porcentaje", "concepto", "observacion", "documento_soporte"]].rename(columns={
                "almacen_nombre": "Almacén",
                "cedula": "Cédula",
                "nombre": "Colaborador",
                "porcentaje": "% Descuento",
                "concepto": "Concepto",
                "observacion": "Observación",
                "documento_soporte": "Doc. Soporte"
            }),
            use_container_width=True,
            hide_index=True,
            column_config={
                "% Descuento": st.column_config.NumberColumn(format="%.2f%%")
            }
        )

        for p in plan_guardar:
            nuevo_total = p["acumulado_previo"] + p["porcentaje"]
            if p["acumulado_previo"] > 0:
                st.warning(f"⚠️ **{p['nombre']}** ya tenía {p['acumulado_previo']:.2f}% de descuento previo en {dist.etiqueta_mes(mes_nuevo)}. Con este registro acumulará **{nuevo_total:.2f}%**.")
            if nuevo_total > 100:
                st.error(f"🛑 **{p['nombre']}** superaría el 100% de descuento del bono ({nuevo_total:.2f}%). El tope máximo aplicable es 100%.")

    if errores_plan:
        st.error("Por favor corrija los siguientes errores antes de guardar:\n- " + "\n- ".join(errores_plan))

    if st.button("💾 Confirmar y Guardar Novedades", type="primary", disabled=(not plan_guardar or bool(errores_plan)), key="btn_guardar_nov_adm"):
        with conn.session as con:
            for item in plan_guardar:
                insertar_novedad_admin(
                    con=con,
                    periodo_mes=mes_nuevo,
                    colaborador_id=item["colaborador_id"],
                    almacen_codigo=item["almacen_codigo"],
                    porcentaje=item["porcentaje"],
                    concepto=item["concepto"],
                    observacion=item["observacion"],
                    documento_soporte=item["documento_soporte"],
                    usuario=usuario,
                    rol=rol,
                    nombre_usuario=nombre_usr
                )
            con.commit()

        st.session_state["nov_adm_save_counter"] = save_key_counter + 1
        st.success(f"✅ Se registraron y auditaron {len(plan_guardar)} novedad(es) administrativa(s) exitosamente.")
        st.rerun()

    # ------------------------------------------------------------
    # CARGA MASIVA MEDIANTE EXCEL
    # ------------------------------------------------------------
    st.markdown("---")
    st.subheader("2) Carga Masiva desde Excel (.xlsx)")
    st.caption("Plantilla para aplicar deducciones porcentuales múltiples simultáneamente.")

    buf_excel = io.BytesIO()
    with pd.ExcelWriter(buf_excel, engine="openpyxl") as xw:
        cols_plantilla = ["cedula", "codigo_almacen", "porcentaje_descuento", "concepto", "observacion", "documento_soporte"]
        pd.DataFrame(columns=cols_plantilla).to_excel(xw, index=False, sheet_name="novedades_admin")
        ws = xw.sheets["novedades_admin"]

        dv_concepto = DataValidation(type="list", formula1='"' + ",".join(CONCEPTOS_ADMIN) + '"', allow_blank=False)
        ws.add_data_validation(dv_concepto)
        dv_concepto.add("D2:D1000")

        df_ayuda_alms = df_almacenes[["codigo", "nombre"]].rename(columns={"codigo": "codigo_almacen", "nombre": "nombre_almacen"})
        df_ayuda_alms.to_excel(xw, index=False, sheet_name="almacenes_validos")

        df_instrucciones = pd.DataFrame({
            "Columna": cols_plantilla,
            "Descripción": [
                "Cédula numérica del colaborador activo",
                "Código del almacén (ej: ALM170, NORTE128, CHIA). Ver hoja almacenes_validos.",
                "Valor porcentual numérico (ej: 15 para 15%, 50 para 50%). Entre 0.1 y 100.",
                f"Desplegable con valores: {', '.join(CONCEPTOS_ADMIN)}",
                "Justificación y detalle de la decisión de Gerencia (obligatorio)",
                "Número de memorando o acta disciplinaria (opcional)"
            ]
        })
        df_instrucciones.to_excel(xw, index=False, sheet_name="instrucciones")

    st.download_button(
        "📄 Descargar Plantilla Excel de Novedades (.xlsx)",
        data=buf_excel.getvalue(),
        file_name="plantilla_novedades_administrativas.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    archivo_subido = st.file_uploader("Cargar archivo Excel diligenciado", type=["xlsx"], key="upl_excel_nov_adm")
    if archivo_subido:
        try:
            df_excel = pd.read_excel(archivo_subido, sheet_name="novedades_admin")
            df_excel.columns = [str(c).strip().lower() for c in df_excel.columns]

            plan_excel, errores_excel = [], []
            for idx, fila in df_excel.iterrows():
                ced_str = str(fila.get("cedula", "")).strip().split(".")[0]
                if not ced_str or ced_str == "nan":
                    continue

                col_info = MAPA_CEDULA_COL.get(ced_str)
                if col_info is None:
                    errores_excel.append(f"Fila {idx + 2}: Cédula {ced_str} no encontrada o inactiva en RRHH.")
                    continue

                alm_cod_in = str(fila.get("codigo_almacen", "")).strip().upper()
                if alm_cod_in not in LISTA_COD_ALM:
                    alm_cod_in = col_info.get("almacen_codigo") or "ALM170"

                try:
                    pct_in = float(fila.get("porcentaje_descuento", 0))
                    if pct_in <= 0 or pct_in > 100:
                        errores_excel.append(f"Fila {idx + 2} ({col_info['nombre']}): El porcentaje debe ser entre 0.1% y 100%.")
                except Exception:
                    errores_excel.append(f"Fila {idx + 2} ({col_info['nombre']}): Porcentaje inválido.")
                    pct_in = 0.0

                concepto_in = str(fila.get("concepto", "")).strip()
                if not concepto_in or concepto_in == "nan":
                    concepto_in = "Sanción administrativa"

                obs_in = str(fila.get("observacion", "")).strip()
                if not obs_in or obs_in == "nan":
                    errores_excel.append(f"Fila {idx + 2} ({col_info['nombre']}): Requiere observación.")

                doc_in = str(fila.get("documento_soporte", "")).strip() if pd.notna(fila.get("documento_soporte")) else None

                plan_excel.append({
                    "colaborador_id": col_info["id"],
                    "nombre": col_info["nombre"],
                    "cedula": col_info["cedula"],
                    "almacen_codigo": alm_cod_in,
                    "porcentaje": pct_in,
                    "concepto": concepto_in,
                    "observacion": obs_in,
                    "documento_soporte": doc_in
                })

            if plan_excel:
                st.markdown("##### Previsualización del archivo Excel:")
                st.dataframe(pd.DataFrame(plan_excel), use_container_width=True, hide_index=True)

            if errores_excel:
                st.error("No se puede importar el archivo debido a los siguientes errores:\n- " + "\n- ".join(errores_excel))
            elif plan_excel:
                if st.button(f"📥 Confirmar Importación Masiva ({len(plan_excel)} registros)", type="primary", key="btn_confirm_excel_adm"):
                    with conn.session as con:
                        for item in plan_excel:
                            insertar_novedad_admin(
                                con=con,
                                periodo_mes=mes_nuevo,
                                colaborador_id=item["colaborador_id"],
                                almacen_codigo=item["almacen_codigo"],
                                porcentaje=item["porcentaje"],
                                concepto=item["concepto"],
                                observacion=item["observacion"],
                                documento_soporte=item["documento_soporte"],
                                usuario=usuario,
                                rol=rol,
                                nombre_usuario=nombre_usr
                            )
                        con.commit()
                    st.success(f"✅ Se importaron y auditaron {len(plan_excel)} novedades correctamente.")
                    st.rerun()

        except Exception as ex_excel:
            st.error(f"Error procesando el archivo Excel: {ex_excel}")

# ------------------------------------------------------------
# 3. EDITAR PERIODO
# ------------------------------------------------------------
with tab_edit:
    st.subheader("Intervenir o Anular Novedades Registradas")
    mes_edit = selector_mes("edit_mes_adm")

    df_edit = cargar_novedades_admin_periodo(mes_edit)
    if df_edit.empty:
        st.info(f"No hay novedades registradas en el periodo {dist.etiqueta_mes(mes_edit)} para editar.")
    else:
        def format_etiqueta_nov(r):
            return f"#{r['id']} · {r['colaborador_nombre']} (CC {r['cedula']}) · {r['almacen_nombre']} · {r['porcentaje_descuento']:.1f}% · {r['concepto']}"

        opciones_edit = {format_etiqueta_nov(r): r["id"] for _, r in df_edit.iterrows()}
        nov_sel_txt = st.selectbox("Seleccione la novedad a modificar o anular", list(opciones_edit.keys()), key="sel_nov_edit_adm")
        id_sel = opciones_edit[nov_sel_txt]
        registro_actual = df_edit[df_edit["id"] == id_sel].iloc[0]

        st.markdown(f"**Editando:** #{registro_actual['id']} — {registro_actual['colaborador_nombre']} ({registro_actual['almacen_nombre']})")

        with st.form("form_editar_novedad_adm"):
            e1, e2 = st.columns(2)
            with e1:
                e_pct = st.number_input(
                    "% Descuento corregido",
                    value=float(registro_actual["porcentaje_descuento"]),
                    min_value=0.5,
                    max_value=100.0,
                    step=0.5,
                    format="%.2f"
                )
            with e2:
                idx_con = CONCEPTOS_ADMIN.index(registro_actual["concepto"]) if registro_actual["concepto"] in CONCEPTOS_ADMIN else 0
                e_concepto = st.selectbox("Concepto", CONCEPTOS_ADMIN, index=idx_con)

            e_obs = st.text_area("Observación / Justificación", value=str(registro_actual["observacion"] or ""))
            e_doc = st.text_input("Documento Soporte / Memorando", value=str(registro_actual["documento_soporte"] or ""))

            if st.form_submit_button("💾 Guardar Cambios"):
                if not e_obs.strip():
                    st.error("La observación no puede quedar vacía.")
                else:
                    try:
                        with conn.session as con:
                            actualizar_novedad_admin(
                                con=con,
                                novedad_id=id_sel,
                                porcentaje=e_pct,
                                concepto=e_concepto,
                                observacion=e_obs,
                                documento_soporte=e_doc,
                                usuario=usuario,
                                rol=rol,
                                nombre_usuario=nombre_usr
                            )
                            con.commit()
                        st.success("Novedad actualizada y auditada correctamente.")
                        st.rerun()
                    except Exception as ex_up:
                        st.error(f"Error al actualizar: {ex_up}")

        # Anulación
        st.markdown("---")
        with st.expander("🗑️ Anular / Eliminar esta novedad"):
            st.warning("Esta acción retirará el descuento del cálculo del bono, manteniendo constancia en la auditoría.")
            motivo_anulacion = st.text_input("Motivo de anulación", key=f"motivo_anula_adm_{id_sel}")
            if st.button("Confirmar Anulación", type="secondary", key=f"btn_anular_adm_{id_sel}"):
                if not motivo_anulacion.strip():
                    st.error("Debe ingresar un motivo para justificar la anulación.")
                else:
                    try:
                        with conn.session as con:
                            eliminar_novedad_admin(
                                con=con,
                                novedad_id=id_sel,
                                motivo_anulacion=motivo_anulacion,
                                usuario=usuario,
                                rol=rol,
                                nombre_usuario=nombre_usr
                            )
                            con.commit()
                        st.success("Novedad anulada exitosamente.")
                        st.rerun()
                    except Exception as ex_del:
                        st.error(f"Error al anular: {ex_del}")

# ------------------------------------------------------------
# 4. HISTORIAL Y AUDITORÍA (Visualización limpia, sin JSON)
# ------------------------------------------------------------
with tab_hist:
    st.subheader("Auditoría de Movimientos y Decisiones Gerenciales")
    st.caption("Registro de quién creó, modificó o anuló novedades de bonificación.")

    c_h1, _ = st.columns([2, 2])
    with c_h1:
        filtro_aud_mes = st.selectbox(
            "Filtrar por Periodo",
            ["Todos los periodos"] + [dist.etiqueta_mes(m) for m in MESES],
            key="aud_filtro_periodo_adm"
        )

    mes_aud = None
    if filtro_aud_mes != "Todos los periodos":
        for m in MESES:
            if dist.etiqueta_mes(m) == filtro_aud_mes:
                mes_aud = m
                break

    df_aud = cargar_historial_auditoria_admin(mes_aud)

    if df_aud.empty:
        st.info("No se registran movimientos auditados para el filtro seleccionado.")
    else:
        # Construcción de tabla ejecutiva limpia para Gerencia
        filas_auditadas = []
        for _, fila in df_aud.iterrows():
            antes = fila["antes"] if isinstance(fila["antes"], dict) else {}
            despues = fila["despues"] if isinstance(fila["despues"], dict) else {}

            pct_antes = f"{float(antes['porcentaje_descuento']):.1f}%" if "porcentaje_descuento" in antes else "—"
            pct_nuevo = f"{float(despues['porcentaje_descuento']):.1f}%" if "porcentaje_descuento" in despues else "—"

            concepto_show = despues.get("concepto") or antes.get("concepto") or "—"
            detalle_show = despues.get("observacion") or despues.get("motivo_anulacion") or antes.get("observacion") or "—"
            alm_show = despues.get("almacen_codigo") or antes.get("almacen_codigo") or "—"

            filas_auditadas.append({
                "Fecha y Hora": fila["fecha"],
                "Usuario": fila["usuario"],
                "Nombre": fila["nombre_usuario"],
                "Rol": fila["rol"],
                "Acción": fila["accion"],
                "Novedad ID": fila["novedad_id"],
                "Almacén": MAPA_ALM_NOMBRE.get(alm_show, alm_show),
                "% Anterior": pct_antes,
                "% Nuevo": pct_nuevo,
                "Concepto": concepto_show,
                "Observación / Motivo": detalle_show
            })

        df_audit_limpia = pd.DataFrame(filas_auditadas)

        st.dataframe(
            df_audit_limpia,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Fecha y Hora": st.column_config.DatetimeColumn(format="DD/MM/YYYY HH:mm:ss"),
                "Acción": st.column_config.TextColumn(width="small"),
                "% Anterior": st.column_config.TextColumn(width="small"),
                "% Nuevo": st.column_config.TextColumn(width="small"),
                "Observación / Motivo": st.column_config.TextColumn(width="large")
            }
        )