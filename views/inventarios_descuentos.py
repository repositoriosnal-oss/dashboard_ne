"""
INVENTARIOS — Descuentos por pérdidas, averías y descuadres para el cálculo del bono.
Pestañas:
  1. Resumen y Reporte
  2. Crear y Cargar (con filtro previo por almacén y carga masiva Excel)
  3. Editar Periodo
  4. Historial y Auditoría
"""

import io
from datetime import date
import pandas as pd
import streamlit as st
from openpyxl.worksheet.datavalidation import DataValidation
from sqlalchemy import text

from src import rrhh_dist as dist
from src.conexion_db import conn
from src.inventarios_descuentos import (
    ROLES_INVENTARIOS_CARGUE,
    ROLES_INVENTARIOS_ADMIN,
    CONCEPTOS_DESCUENTO,
    bootstrap_inventarios,
    cargar_almacenes_activos,
    cargar_colaboradores_disponibles,
    cargar_descuentos_periodo,
    insertar_descuento_individual,
    actualizar_descuento_individual,
    eliminar_descuento_individual,
    cargar_historial_auditoria,
)

# ============================================================
# CONFIGURACIÓN DE PÁGINA Y CONTROL DE ACCESO
# ============================================================
st.set_page_config(
    page_title="Descuentos Inventarios",
    page_icon=":material/inventory_2:",
    layout="wide"
)

st.title("Descuentos por Inventarios — Bono")
st.markdown("---")

rol = st.session_state.get("rol_actual", "desconocido")
usuario = st.session_state.get("usuario_actual", "desconocido")
nombre_usr = st.session_state.get("nombre_completo", usuario)

if rol not in ROLES_INVENTARIOS_CARGUE:
    st.error("⛔ Acceso denegado. Este módulo está disponible para Inventarios, Compras, Gerencia y Administración.")
    st.stop()

# Inicialización de esquema y tablas de forma transparente e idempotente
try:
    bootstrap_inventarios()
except Exception as e:
    st.error(f"Error preparando tablas de inventarios en la base de datos: {e}")
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

# Cargamos colaboradores y convertimos a diccionarios nativos para evitar ambigüedad de Series
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
        mes_res = selector_mes("res_mes")
    
    # Cargar datos del periodo
    df_desc_mes = cargar_descuentos_periodo(mes_res)

    alms_en_periodo = sorted(df_desc_mes["almacen_nombre"].dropna().unique()) if not df_desc_mes.empty else []
    with c2:
        f_alm = st.multiselect("Filtrar por Almacén", alms_en_periodo, key="res_alm_filter")
    with c3:
        f_q = st.text_input("Buscar colaborador (nombre o cédula)", key="res_q_filter")

    df_filtrado = df_desc_mes.copy()
    if not df_filtrado.empty:
        if f_alm:
            df_filtrado = df_filtrado[df_filtrado["almacen_nombre"].isin(f_alm)]
        if f_q.strip():
            filtro_q = (
                df_filtrado["colaborador_nombre"].str.contains(f_q, case=False, na=False) |
                df_filtrado["cedula"].str.contains(f_q, na=False)
            )
            df_filtrado = df_filtrado[filtro_q]

    # KPIs
    k1, k2, k3, k4 = st.columns(4)
    tot_monto = df_filtrado["monto"].sum() if not df_filtrado.empty else 0.0
    tot_personas = df_filtrado["colaborador_id"].nunique() if not df_filtrado.empty else 0
    prom_persona = (tot_monto / tot_personas) if tot_personas > 0 else 0.0
    cant_registros = len(df_filtrado)

    k1.metric("Total Descuentos ($)", f"${tot_monto:,.0f}")
    k2.metric("Personas Afectadas", tot_personas)
    k3.metric("Promedio por Persona", f"${prom_persona:,.0f}")
    k4.metric("Total Novedades / Registros", cant_registros)

    st.markdown("---")

    if df_filtrado.empty:
        st.info(f"Sin descuentos de inventarios registrados para el periodo {dist.etiqueta_mes(mes_res)}.")
    else:
        # Tabla consolidada por colaborador
        st.subheader("Consolidado por Colaborador")
        df_cons = df_filtrado.groupby(
            ["almacen_nombre", "cedula", "colaborador_nombre", "cargo"], as_index=False
        ).agg(
            total_descuento=("monto", "sum"),
            cantidad_descuentos=("id", "count")
        ).rename(columns={
            "almacen_nombre": "Almacén",
            "cedula": "Cédula",
            "colaborador_nombre": "Colaborador",
            "cargo": "Cargo",
            "total_descuento": "Total a Descontar ($)",
            "cantidad_descuentos": "Novedades"
        })

        st.dataframe(
            df_cons,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Total a Descontar ($)": st.column_config.NumberColumn(format="$%d")
            }
        )

        with st.expander("Ver detalle de cada registro individual"):
            st.dataframe(
                df_filtrado[[
                    "id", "almacen_nombre", "cedula", "colaborador_nombre", "cargo",
                    "monto", "concepto", "observacion", "documento_soporte", "cargado_por", "creado_en"
                ]].rename(columns={
                    "id": "ID",
                    "almacen_nombre": "Almacén",
                    "cedula": "Cédula",
                    "colaborador_nombre": "Colaborador",
                    "cargo": "Cargo",
                    "monto": "Monto ($)",
                    "concepto": "Concepto",
                    "observacion": "Observación",
                    "documento_soporte": "Doc. Soporte",
                    "cargado_por": "Registrado Por",
                    "creado_en": "Fecha Registro"
                }),
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Monto ($)": st.column_config.NumberColumn(format="$%d"),
                    "Fecha Registro": st.column_config.DatetimeColumn(format="DD/MM/YYYY HH:mm")
                }
            )

        # Botón de exportación
        csv_data = df_filtrado.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "📥 Descargar Detalle en CSV",
            data=csv_data,
            file_name=f"descuentos_inventarios_{mes_res:%Y_%m}.csv",
            mime="text/csv"
        )

# ------------------------------------------------------------
# 2. CREAR Y CARGAR
# ------------------------------------------------------------
with tab_new:
    st.subheader("1) Registro Directo (con Filtro por Almacén)")
    st.caption("Selecciona el periodo y almacén. La tabla mostrará y sugerirá automáticamente los colaboradores de ese almacén.")

    col_cfg1, col_cfg2 = st.columns(2)
    with col_cfg1:
        mes_nuevo = selector_mes("new_mes")
    with col_cfg2:
        # Selector de almacén para filtrar el catálogo de personas
        opciones_filtro_alm = ["-- Todos los Almacenes --"] + [
            f"{r['codigo']} — {r['nombre']}" for _, r in df_almacenes.iterrows()
        ]
        alm_seleccionado_txt = st.selectbox("Almacén donde se detectó el descuadre", opciones_filtro_alm, key="new_alm_filter")

    codigo_alm_filtro = None
    if alm_seleccionado_txt != "-- Todos los Almacenes --":
        codigo_alm_filtro = alm_seleccionado_txt.split(" — ")[0]

    # Cargar colaboradores según filtro de almacén
    df_cols_filtrados = cargar_colaboradores_disponibles(codigo_alm_filtro)
    if df_cols_filtrados.empty:
        st.warning("No se encontraron colaboradores activos asociados a este almacén.")
        opciones_colaborador = {}
    else:
        opciones_colaborador = {
            f"{r['nombre']} — CC {r['cedula']} ({r['almacen_nombre']} - {r['cargo']})": int(r["id"])
            for r in df_cols_filtrados.to_dict("records")
        }

    save_key_counter = st.session_state.get("inv_save_counter", 0)

    # Editor interactivo
    st.markdown("##### Ingrese los descuentos a registrar:")
    ed_descuentos = st.data_editor(
        pd.DataFrame(columns=[
            "Colaborador",
            "Monto a descontar ($)",
            "Concepto",
            "Observación",
            "Doc. Soporte / Acta"
        ]),
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        key=f"ed_inv_desc_{save_key_counter}",
        column_config={
            "Colaborador": st.column_config.SelectboxColumn(
                "Colaborador",
                options=list(opciones_colaborador.keys()),
                required=True,
                width="large"
            ),
            "Monto a descontar ($)": st.column_config.NumberColumn(
                "Monto a descontar ($)",
                min_value=1.0,
                step=1000.0,
                format="$%d",
                required=True,
                width="medium"
            ),
            "Concepto": st.column_config.SelectboxColumn(
                "Concepto",
                options=CONCEPTOS_DESCUENTO,
                required=True,
                default="Faltante de inventario",
                width="medium"
            ),
            "Observación": st.column_config.TextColumn(
                "Observación / Justificación",
                required=True,
                width="large"
            ),
            "Doc. Soporte / Acta": st.column_config.TextColumn(
                "Nro. Acta / Ajuste (opcional)",
                width="medium"
            )
        }
    )

    # Validación y previsualización del plan de guardado
    plan_guardar, errores_plan = [], []
    colabs_en_periodo_prev = cargar_descuentos_periodo(mes_nuevo)
    previos_por_colab = colabs_en_periodo_prev.groupby("colaborador_id")["monto"].sum().to_dict() if not colabs_en_periodo_prev.empty else {}

    for i, r in ed_descuentos.iterrows():
        colab_str = str(r["Colaborador"]).strip() if pd.notna(r["Colaborador"]) else ""
        if not colab_str or colab_str not in opciones_colaborador:
            continue

        cid = opciones_colaborador[colab_str]
        col_info = MAPA_COL_INFO.get(cid)
        if col_info is None:
            errores_plan.append(f"Fila {i + 1}: Colaborador no encontrado en el maestro.")
            continue

        monto_raw = r["Monto a descontar ($)"]
        try:
            monto_val = float(monto_raw)
            if monto_val <= 0:
                errores_plan.append(f"Fila {i + 1} ({col_info['nombre']}): El monto debe ser mayor a 0.")
        except (ValueError, TypeError):
            errores_plan.append(f"Fila {i + 1} ({col_info['nombre']}): Monto inválido.")
            monto_val = 0.0

        concepto_val = str(r["Concepto"]).strip() if pd.notna(r["Concepto"]) else ""
        if not concepto_val:
            errores_plan.append(f"Fila {i + 1} ({col_info['nombre']}): Debe seleccionar un concepto.")

        obs_val = str(r["Observación"]).strip() if pd.notna(r["Observación"]) else ""
        if not obs_val:
            errores_plan.append(f"Fila {i + 1} ({col_info['nombre']}): Ingrese una observación o justificación.")

        doc_soporte = str(r["Doc. Soporte / Acta"]).strip() if pd.notna(r["Doc. Soporte / Acta"]) else None

        # Almacén al que se asigna el descuento: preferir el del filtro, o el almacén actual de la persona
        alm_cod_destino = codigo_alm_filtro or col_info.get("almacen_codigo") or "ALM170"

        plan_guardar.append({
            "colaborador_id": cid,
            "nombre": col_info["nombre"],
            "cedula": col_info["cedula"],
            "almacen_codigo": alm_cod_destino,
            "almacen_nombre": MAPA_ALM_NOMBRE.get(alm_cod_destino, alm_cod_destino),
            "monto": monto_val,
            "concepto": concepto_val,
            "observacion": obs_val,
            "documento_soporte": doc_soporte,
            "acumulado_previo": previos_por_colab.get(cid, 0.0)
        })

    if plan_guardar:
        st.markdown("##### Vista Previa de lo que se Registrará:")
        df_prev = pd.DataFrame(plan_guardar)
        st.dataframe(
            df_prev[["almacen_nombre", "cedula", "nombre", "monto", "concepto", "observacion", "documento_soporte"]].rename(columns={
                "almacen_nombre": "Almacén",
                "cedula": "Cédula",
                "nombre": "Colaborador",
                "monto": "Monto ($)",
                "concepto": "Concepto",
                "observacion": "Observación",
                "documento_soporte": "Doc. Soporte"
            }),
            use_container_width=True,
            hide_index=True,
            column_config={
                "Monto ($)": st.column_config.NumberColumn(format="$%d")
            }
        )

        total_a_guardar = sum(p["monto"] for p in plan_guardar)
        st.info(f"💰 Total a descontar en este lote: **${total_a_guardar:,.0f}** en {len(plan_guardar)} registro(s).")

        # Alertas si ya tenían descuentos previos en el mes
        for p in plan_guardar:
            if p["acumulado_previo"] > 0:
                st.warning(f"⚠️ **{p['nombre']}** ya cuenta con ${p['acumulado_previo']:,.0f} en descuentos registrados para {dist.etiqueta_mes(mes_nuevo)}. Este nuevo monto se sumará a su total.")

    if errores_plan:
        st.error("Por favor corrija los siguientes errores antes de guardar:\n- " + "\n- ".join(errores_plan))

    if st.button("💾 Confirmar y Guardar Descuentos", type="primary", disabled=(not plan_guardar or bool(errores_plan)), key="btn_guardar_inv"):
        with conn.session as con:
            for item in plan_guardar:
                insertar_descuento_individual(
                    con=con,
                    periodo_mes=mes_nuevo,
                    colaborador_id=item["colaborador_id"],
                    almacen_codigo=item["almacen_codigo"],
                    monto=item["monto"],
                    concepto=item["concepto"],
                    observacion=item["observacion"],
                    documento_soporte=item["documento_soporte"],
                    usuario=usuario,
                    rol=rol,
                    nombre_usuario=nombre_usr
                )
            con.commit()

        st.session_state["inv_save_counter"] = save_key_counter + 1
        st.success(f"✅ Se registraron y auditaron {len(plan_guardar)} descuento(s) exitosamente.")
        st.rerun()

    # ------------------------------------------------------------
    # CARGA MASIVA MEDIANTE EXCEL
    # ------------------------------------------------------------
    st.markdown("---")
    st.subheader("2) Carga Masiva desde Excel (.xlsx)")
    st.caption("Ideal para importar actas de inventario completas con múltiples colaboradores.")

    # Generación de plantilla Excel con hojas y validaciones
    buf_excel = io.BytesIO()
    with pd.ExcelWriter(buf_excel, engine="openpyxl") as xw:
        cols_plantilla = ["cedula", "codigo_almacen", "monto", "concepto", "observacion", "documento_soporte"]
        pd.DataFrame(columns=cols_plantilla).to_excel(xw, index=False, sheet_name="descuentos")
        ws = xw.sheets["descuentos"]

        # Validación de datos para desplegable de conceptos
        dv_concepto = DataValidation(type="list", formula1='"' + ",".join(CONCEPTOS_DESCUENTO) + '"', allow_blank=False)
        ws.add_data_validation(dv_concepto)
        dv_concepto.add("D2:D1000")

        # Hoja de códigos válidos de almacén
        df_ayuda_alms = df_almacenes[["codigo", "nombre"]].rename(columns={"codigo": "codigo_almacen", "nombre": "nombre_almacen"})
        df_ayuda_alms.to_excel(xw, index=False, sheet_name="almacenes_validos")

        df_instrucciones = pd.DataFrame({
            "Columna": cols_plantilla,
            "Descripción": [
                "Cédula numérica del colaborador activo",
                "Código del almacén (ej: ALM170, NORTE128, CHIA). Ver hoja almacenes_validos.",
                "Valor numérico en pesos sin signos ni puntos (ej: 150000)",
                f"Desplegable con valores: {', '.join(CONCEPTOS_DESCUENTO)}",
                "Justificación y detalle de la pérdida o descuadre (obligatorio)",
                "Número de acta, orden o remisión SAP (opcional)"
            ]
        })
        df_instrucciones.to_excel(xw, index=False, sheet_name="instrucciones")

    st.download_button(
        "📄 Descargar Plantilla Excel de Descuentos (.xlsx)",
        data=buf_excel.getvalue(),
        file_name="plantilla_descuentos_inventarios.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    archivo_subido = st.file_uploader("Cargar archivo Excel diligenciado", type=["xlsx"], key="upl_excel_inv")
    if archivo_subido:
        try:
            df_excel = pd.read_excel(archivo_subido, sheet_name="descuentos")
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
                    monto_in = float(fila.get("monto", 0))
                    if monto_in <= 0:
                        errores_excel.append(f"Fila {idx + 2} ({col_info['nombre']}): El monto debe ser superior a 0.")
                except Exception:
                    errores_excel.append(f"Fila {idx + 2} ({col_info['nombre']}): Monto inválido.")
                    monto_in = 0.0

                concepto_in = str(fila.get("concepto", "")).strip()
                if not concepto_in or concepto_in == "nan":
                    concepto_in = "Faltante de inventario"

                obs_in = str(fila.get("observacion", "")).strip()
                if not obs_in or obs_in == "nan":
                    errores_excel.append(f"Fila {idx + 2} ({col_info['nombre']}): Requiere observación.")

                doc_in = str(fila.get("documento_soporte", "")).strip() if pd.notna(fila.get("documento_soporte")) else None

                plan_excel.append({
                    "colaborador_id": col_info["id"],
                    "nombre": col_info["nombre"],
                    "cedula": col_info["cedula"],
                    "almacen_codigo": alm_cod_in,
                    "monto": monto_in,
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
                if st.button(f"📥 Confirmar Importación Masiva ({len(plan_excel)} registros)", type="primary", key="btn_confirm_excel"):
                    with conn.session as con:
                        for item in plan_excel:
                            insertar_descuento_individual(
                                con=con,
                                periodo_mes=mes_nuevo,
                                colaborador_id=item["colaborador_id"],
                                almacen_codigo=item["almacen_codigo"],
                                monto=item["monto"],
                                concepto=item["concepto"],
                                observacion=item["observacion"],
                                documento_soporte=item["documento_soporte"],
                                usuario=usuario,
                                rol=rol,
                                nombre_usuario=nombre_usr
                            )
                        con.commit()
                    st.success(f"✅ Se importaron y auditaron {len(plan_excel)} descuentos correctamente.")
                    st.rerun()

        except Exception as ex_excel:
            st.error(f"Error procesando el archivo Excel: {ex_excel}")

# ------------------------------------------------------------
# 3. EDITAR PERIODO
# ------------------------------------------------------------
with tab_edit:
    st.subheader("Intervenir o Anular Descuentos Registrados")
    mes_edit = selector_mes("edit_mes")

    df_edit = cargar_descuentos_periodo(mes_edit)
    if df_edit.empty:
        st.info(f"No hay descuentos registrados en el periodo {dist.etiqueta_mes(mes_edit)} para editar.")
    else:
        def format_etiqueta_descuento(r):
            return f"#{r['id']} · {r['colaborador_nombre']} (CC {r['cedula']}) · {r['almacen_nombre']} · ${r['monto']:,.0f} · {r['concepto']}"

        opciones_edit = {format_etiqueta_descuento(r): r["id"] for _, r in df_edit.iterrows()}
        descuento_sel_txt = st.selectbox("Seleccione el registro a modificar o anular", list(opciones_edit.keys()), key="sel_desc_edit")
        id_sel = opciones_edit[descuento_sel_txt]
        registro_actual = df_edit[df_edit["id"] == id_sel].iloc[0]

        st.markdown(f"**Editando:** #{registro_actual['id']} — {registro_actual['colaborador_nombre']} ({registro_actual['almacen_nombre']})")

        with st.form("form_editar_descuento"):
            e1, e2 = st.columns(2)
            with e1:
                e_monto = st.number_input(
                    "Monto corregido ($)",
                    value=float(registro_actual["monto"]),
                    min_value=1.0,
                    step=1000.0,
                    format="%.0f"
                )
            with e2:
                idx_con = CONCEPTOS_DESCUENTO.index(registro_actual["concepto"]) if registro_actual["concepto"] in CONCEPTOS_DESCUENTO else 0
                e_concepto = st.selectbox("Concepto", CONCEPTOS_DESCUENTO, index=idx_con)

            e_obs = st.text_area("Observación / Justificación", value=str(registro_actual["observacion"] or ""))
            e_doc = st.text_input("Documento Soporte / Acta", value=str(registro_actual["documento_soporte"] or ""))

            if st.form_submit_button("💾 Guardar Cambios"):
                if not e_obs.strip():
                    st.error("La observación no puede quedar vacía.")
                else:
                    try:
                        with conn.session as con:
                            actualizar_descuento_individual(
                                con=con,
                                descuento_id=id_sel,
                                monto=e_monto,
                                concepto=e_concepto,
                                observacion=e_obs,
                                documento_soporte=e_doc,
                                usuario=usuario,
                                rol=rol,
                                nombre_usuario=nombre_usr
                            )
                            con.commit()
                        st.success("Descuento actualizado y auditado correctamente.")
                        st.rerun()
                    except Exception as ex_up:
                        st.error(f"Error al actualizar: {ex_up}")

        # Sección de anulación
        st.markdown("---")
        with st.expander("🗑️ Anular / Eliminar este descuento"):
            st.warning("Esta acción eliminará el descuento del cálculo del bono pero dejará registro en la auditoría con el motivo.")
            motivo_anulacion = st.text_input("Motivo de anulación", key=f"motivo_anula_{id_sel}")
            if st.button("Confirmar Anulación", type="secondary", key=f"btn_anular_{id_sel}"):
                if not motivo_anulacion.strip():
                    st.error("Debe ingresar un motivo para justificar la anulación.")
                else:
                    try:
                        with conn.session as con:
                            eliminar_descuento_individual(
                                con=con,
                                descuento_id=id_sel,
                                motivo_anulacion=motivo_anulacion,
                                usuario=usuario,
                                rol=rol,
                                nombre_usuario=nombre_usr
                            )
                            con.commit()
                        st.success("Descuento anulado exitosamente.")
                        st.rerun()
                    except Exception as ex_del:
                        st.error(f"Error al anular: {ex_del}")

# ------------------------------------------------------------
# 4. HISTORIAL Y AUDITORÍA
# ------------------------------------------------------------
with tab_hist:
    st.subheader("Auditoría de Movimientos y Trazabilidad")
    st.caption("Registro inmutable de quién creó, modificó o anuló descuentos de inventarios.")

    c_h1, _ = st.columns([2, 2])
    with c_h1:
        filtro_aud_mes = st.selectbox(
            "Filtrar por Periodo",
            ["Todos los periodos"] + [dist.etiqueta_mes(m) for m in MESES],
            key="aud_filtro_periodo"
        )

    mes_aud = None
    if filtro_aud_mes != "Todos los periodos":
        for m in MESES:
            if dist.etiqueta_mes(m) == filtro_aud_mes:
                mes_aud = m
                break

    df_auditoria = cargar_historial_auditoria(mes_aud)

    if df_auditoria.empty:
        st.info("No se registran movimientos auditados para el filtro seleccionado.")
    else:
        st.dataframe(
            df_auditoria[["id", "fecha", "usuario", "nombre_usuario", "rol", "accion", "descuento_id"]].rename(columns={
                "id": "Log ID",
                "fecha": "Fecha y Hora",
                "usuario": "Usuario",
                "nombre_usuario": "Nombre",
                "rol": "Rol",
                "accion": "Acción",
                "descuento_id": "Descuento ID"
            }),
            use_container_width=True,
            hide_index=True,
            column_config={
                "Fecha y Hora": st.column_config.DatetimeColumn(format="DD/MM/YYYY HH:mm:ss")
            }
        )

        with st.expander("🔍 Inspeccionar detalles JSON de un movimiento (Antes vs Después)"):
            log_id_sel = st.selectbox("Seleccione Log ID", df_auditoria["id"].tolist(), format_func=lambda x: f"Movimiento #{x}")
            fila_log = df_auditoria[df_auditoria["id"] == log_id_sel].iloc[0]

            col_a, col_d = st.columns(2)
            with col_a:
                st.markdown("**Estado Anterior**")
                if fila_log["antes"]:
                    st.json(fila_log["antes"])
                else:
                    st.caption("Sin estado anterior (Registro nuevo creado).")
            with col_d:
                st.markdown("**Estado Posterior**")
                if fila_log["despues"]:
                    st.json(fila_log["despues"])
                else:
                    st.caption("Sin estado posterior.")