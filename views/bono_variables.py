import streamlit as st
import pandas as pd
import numpy as np
from src.conexion_db import (
    bootstrap_bono_variables,
    _cargar_config_bono,
    _cargar_periodos_metas,
    _cargar_periodos_escala,
    actualizar_periodo_metas,
    actualizar_periodo_escala,
    eliminar_periodo_metas,
    eliminar_periodo_escala,
    crear_periodo,
    crear_periodo_metas,
    crear_periodo_escala,
    actualizar_periodo,
    reemplazar_metas,
    reemplazar_escala,
    listar_auditoria_bono,
    ESCALA_RENT_DEFAULT,
    METAS_2025,
    METAS_2026,
)

# ============================================================
# CONFIGURACIÓN DE PÁGINA
# ============================================================
st.set_page_config(
    page_title="Variables del Bono",
    page_icon=":material/tune:",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ============================================================
# CONTROL DE ACCESO
# ============================================================
ROLES_EDIT_BONO = ["admin", "gerente"]
if st.session_state.get("rol_actual") not in ROLES_EDIT_BONO:
    st.error("⛔ Acceso denegado. Solo Administrador y Gerencia pueden editar las variables del bono.")
    st.stop()

st.title("Variables del Bono")
st.caption(
    "Configuración de parámetros por período: metas de venta, factor de descuento de exentas, "
    "meta adicional y escala de rentabilidad. Cada período tiene sus propias reglas y se aplican "
    "automáticamente según la fecha del informe."
)

# ============================================================
# BOOTSTRAP (idempotente: solo crea tablas la primera vez)
# ============================================================
try:
    bootstrap_bono_variables()
except Exception as e:
    st.error(f"Error al inicializar tablas de configuración: {e}")
    st.stop()

# ============================================================
# CARGA DE DATOS
# ============================================================
per, met, esc = _cargar_config_bono()

if per.empty:
    st.warning("No hay períodos configurados. Contacta al administrador.")
    st.stop()

usuario = st.session_state.get("usuario_actual", "desconocido")

# ============================================================
# TABLA RESUMEN DE PERÍODOS (con formatos)
# ============================================================
st.subheader("Períodos vigentes")
st.caption("Resumen de todos los períodos configurados con sus parámetros principales.")

per_display = per[["nombre", "fecha_inicio", "fecha_fin", "factor_desc_exentas", 
                    "meta_adic_base", "meta_adic_bloque", "meta_adic_pct"]].copy()
per_display["fecha_fin"] = per_display["fecha_fin"].fillna("Abierto")
per_display = per_display.rename(columns={
    "nombre": "Período",
    "fecha_inicio": "Inicio",
    "fecha_fin": "Fin",
    "factor_desc_exentas": "Factor DESC Exentas",
    "meta_adic_base": "Base Meta Adicional",
    "meta_adic_bloque": "Bloque",
    "meta_adic_pct": "% por Bloque",
})

st.dataframe(
    per_display,
    use_container_width=True,
    hide_index=True,
    column_config={
        "Período": st.column_config.TextColumn("Período", width="medium"),
        "Inicio": st.column_config.DateColumn("Inicio", format="DD/MM/YYYY"),
        "Fin": st.column_config.TextColumn("Fin", width="small"),
        "Factor DESC Exentas": st.column_config.NumberColumn(
            "Factor DESC Exentas",
            format="%.0f%%",
            help="Factor aplicado al % de exentas (se muestra como porcentaje)"
        ),
        "Base Meta Adicional": st.column_config.NumberColumn(
            "Base Meta Adicional",
            format="$%,.0f",
            help="Valor base para calcular % Meta Adicional"
        ),
        "Bloque": st.column_config.NumberColumn(
            "Bloque",
            format="$%,.0f",
            help="Tamaño del bloque para incrementos"
        ),
        "% por Bloque": st.column_config.NumberColumn(
            "% por Bloque",
            format="%.0f%%",
            help="Porcentaje aplicado por cada bloque superado"
        ),
    }
)

# ============================================================
# CREAR NUEVO PERÍODO
# ============================================================
st.markdown("---")
st.subheader("Crear nuevo período")
st.caption("Agrega un nuevo período con sus propias reglas de cálculo.")

with st.expander("Agregar nuevo período", expanded=False):
    with st.form("form_nuevo_periodo"):
        col1, col2, col3 = st.columns(3)
        
        with col1:
            nuevo_nombre = st.text_input("Nombre del período", placeholder="Ej: 2027")
        
        with col2:
            nueva_fecha_ini = st.date_input("Fecha inicio", value=None)
        
        with col3:
            nueva_fecha_fin = st.date_input(
                "Fecha fin (dejar vacío para abierto)",
                value=None,
            )
        
        st.markdown("---")
        col4, col5, col6 = st.columns(3)
        
        with col4:
            nuevo_factor = st.number_input(
                "Factor DESC Exentas",
                min_value=0.0, max_value=1.0,
                value=0.50,
                step=0.01,
                help="Factor aplicado al porcentaje de exentas (ej: 0.50 = 50%, 0.75 = 75%)"
            )
        
        with col5:
            nueva_base = st.number_input(
                "Base Meta Adicional",
                min_value=0.0,
                value=2_580_000_000.0,
                step=100_000_000.0,
                help="Valor base para calcular % Meta Adicional"
            )
        
        with col6:
            nuevo_bloque = st.number_input(
                "Bloque",
                min_value=1.0,
                value=100_000_000.0,
                step=50_000_000.0,
                help="Tamaño del bloque para incrementos"
            )
        
        nuevo_pct_meta = st.number_input(
            "% por Bloque",
            min_value=0.0, max_value=1.0,
            value=0.06,
            step=0.01,
            help="Porcentaje aplicado por cada bloque superado"
        )
        
        st.markdown("---")
        st.markdown("**Copiar configuración de período existente:**")
        
        # Selector para copiar metas y escala
        periodo_copiar = st.selectbox(
            "Copiar tramos de meta y escala de rentabilidad de:",
            options=["Ninguno (usar valores por defecto)"] + per["nombre"].tolist(),
            help="Si seleccionas un período existente, se copiarán sus tramos de meta y escala de rentabilidad"
        )
        
        submitted_nuevo = st.form_submit_button("Crear período", type="primary")
        
        if submitted_nuevo:
            if not nuevo_nombre:
                st.error("El nombre del período es obligatorio")
            elif nueva_fecha_ini is None:
                st.error("La fecha de inicio es obligatoria")
            else:
                try:
                    # Determinar qué metas y escala usar
                    if periodo_copiar != "Ninguno (usar valores por defecto)":
                        periodo_origen_id = per[per["nombre"] == periodo_copiar]["id"].iloc[0]
                        metas_copiar = met[met["periodo_id"] == periodo_origen_id].sort_values("orden")
                        escala_copiar = esc[esc["periodo_id"] == periodo_origen_id].sort_values("orden")
                        
                        metas_tuples = [
                            (float(r["umbral_min"]), bool(r["min_inclusivo"]),
                             float(r["umbral_max"]) if pd.notna(r["umbral_max"]) else None,
                             bool(r["max_inclusivo"]), float(r["pct_aplica"]))
                            for _, r in metas_copiar.iterrows()
                        ]
                        escala_tuples = [
                            (float(r["cond_min"]) if pd.notna(r["cond_min"]) else None,
                             bool(r["min_inclusivo"]),
                             float(r["cond_max"]) if pd.notna(r["cond_max"]) else None,
                             bool(r["max_inclusivo"]), float(r["pct_bono"]))
                            for _, r in escala_copiar.iterrows()
                        ]
                    else:
                        # Usar valores por defecto (METAS_2026 y ESCALA_RENT_DEFAULT)
                        metas_tuples = METAS_2026
                        escala_tuples = ESCALA_RENT_DEFAULT
                    
                    nuevo_id = crear_periodo(
                        nombre=nuevo_nombre,
                        f_ini=nueva_fecha_ini,
                        f_fin=nueva_fecha_fin,
                        factor=nuevo_factor,
                        base=nueva_base,
                        bloque=nuevo_bloque,
                        pct=nuevo_pct_meta,
                        metas=metas_tuples,
                        escala=escala_tuples,
                        usuario=usuario
                    )
                    
                    st.success(f"✅ Período '{nuevo_nombre}' creado correctamente con ID {nuevo_id}")
                    st.cache_data.clear()
                    st.rerun()
                except ValueError as ve:
                    st.error(f"Error de validación: {ve}")
                except Exception as e:
                    st.error(f"Error al crear período: {e}")

# ============================================================
# EDITAR PERÍODO EXISTENTE
# ============================================================
st.markdown("---")
st.subheader("Editar período")

periodo_options = per["nombre"].tolist()
periodo_sel = st.selectbox("Selecciona el período a editar:", periodo_options, key="sel_editar")
periodo_id = per[per["nombre"] == periodo_sel]["id"].iloc[0]
periodo_row = per[per["id"] == periodo_id].iloc[0]

# ============================================================
# FORMULARIO: PARÁMETROS GENERALES
# ============================================================
with st.form("form_params"):
    st.markdown("### Parámetros generales")
    col1, col2, col3 = st.columns(3)
    
    with col1:
        nuevo_nombre = st.text_input("Nombre del período", value=periodo_row["nombre"])
    
    with col2:
        fecha_ini = pd.to_datetime(periodo_row["fecha_inicio"]).date()
        nueva_fecha_ini = st.date_input("Fecha inicio", value=fecha_ini)
    
    with col3:
        fecha_fin_val = periodo_row["fecha_fin"]
        nueva_fecha_fin = st.date_input(
            "Fecha fin (dejar vacío para abierto)",
            value=pd.to_datetime(fecha_fin_val).date() if pd.notna(fecha_fin_val) else None,
        )
    
    st.markdown("---")
    col4, col5, col6 = st.columns(3)
    
    with col4:
        nuevo_factor = st.number_input(
            "Factor DESC Exentas",
            min_value=0.0, max_value=1.0,
            value=float(periodo_row["factor_desc_exentas"]),
            step=0.01,
            help="Factor aplicado al porcentaje de exentas (ej: 0.50 = 50%, 0.75 = 75%)"
        )
    
    with col5:
        nueva_base = st.number_input(
            "Base Meta Adicional",
            min_value=0.0,
            value=float(periodo_row["meta_adic_base"]),
            step=100_000_000.0,
            help="Valor base para calcular % Meta Adicional"
        )
    
    with col6:
        nuevo_bloque = st.number_input(
            "Bloque",
            min_value=1.0,
            value=float(periodo_row["meta_adic_bloque"]),
            step=50_000_000.0,
            help="Tamaño del bloque para incrementos"
        )
    
    nuevo_pct_meta = st.number_input(
        "% por Bloque",
        min_value=0.0, max_value=1.0,
        value=float(periodo_row["meta_adic_pct"]),
        step=0.01,
        help="Porcentaje aplicado por cada bloque superado"
    )
    
    submitted_params = st.form_submit_button("Guardar parámetros generales", type="primary")
    
    if submitted_params:
        try:
            campos = {
                "nombre": nuevo_nombre,
                "fecha_inicio": nueva_fecha_ini,
                "fecha_fin": nueva_fecha_fin if nueva_fecha_fin else None,
                "factor_desc_exentas": nuevo_factor,
                "meta_adic_base": nueva_base,
                "meta_adic_bloque": nuevo_bloque,
                "meta_adic_pct": nuevo_pct_meta,
            }
            actualizar_periodo(periodo_id, campos, usuario)
            st.success("✅ Parámetros actualizados correctamente")
            st.cache_data.clear()
            st.rerun()
        except Exception as e:
            st.error(f"Error al actualizar: {e}")

# ============================================================
# TRAMOS DE META DE VENTAS (períodos INDEPENDIENTES)
# ============================================================
st.markdown("---")
st.subheader("Tramos de meta de ventas")
st.caption("Cada período de tramos es independiente y tiene sus propias fechas de vigencia.")

per_metas = _cargar_periodos_metas()
met_all = met  # ya cargado arriba

tab_res, tab_edit, tab_new = st.tabs(["Resumen", "Editar tramos", "Crear nuevo período"])

# ---------- TAB 1: RESUMEN ----------
with tab_res:
    if per_metas.empty:
        st.info("No hay períodos de tramos creados.")
    else:
        sel_res = st.selectbox("Ver período de tramos:", per_metas["nombre"].tolist(), key="sel_res_metas")
        pid_res = per_metas[per_metas["nombre"] == sel_res]["id"].iloc[0]
        tramos_res = met_all[met_all["periodo_id"] == pid_res].sort_values("orden")
        per_res_row = per_metas[per_metas["id"] == pid_res].iloc[0]
        st.caption(f"Vigencia: {per_res_row['fecha_inicio']} → {per_res_row['fecha_fin'] if pd.notna(per_res_row['fecha_fin']) else 'Abierto'}")
        st.dataframe(
            tramos_res[["orden", "umbral_min", "umbral_max", "pct_aplica"]].rename(columns={
                "orden": "#", "umbral_min": "Mínimo", "umbral_max": "Máximo", "pct_aplica": "% Aplica"}),
            use_container_width=True, hide_index=True,
            column_config={
                "Mínimo": st.column_config.NumberColumn(format="$%,.0f"),
                "Máximo": st.column_config.NumberColumn(format="$%,.0f"),
                "% Aplica": st.column_config.NumberColumn(format="%.0f%%"),
            })

# ---------- TAB 2: EDITAR ----------
with tab_edit:
    if per_metas.empty:
        st.info("Crea primero un período de tramos en la pestaña 'Crear nuevo período'.")
    else:
        sel_edit = st.selectbox("Editar período de tramos:", per_metas["nombre"].tolist(), key="sel_edit_metas")
        pid_edit = per_metas[per_metas["nombre"] == sel_edit]["id"].iloc[0]
        row_edit = per_metas[per_metas["id"] == pid_edit].iloc[0]

        # --- Editar nombre y fechas de vigencia ---
        with st.form(f"form_per_metas_{pid_edit}"):
            st.markdown("#### Datos del período")
            c1, c2, c3 = st.columns(3)
            nom_m = c1.text_input("Nombre", value=row_edit["nombre"], key=f"nom_m_{pid_edit}")
            fini_m = c2.date_input("Fecha inicio", value=pd.to_datetime(row_edit["fecha_inicio"]).date(), key=f"fini_m_{pid_edit}")
            ffin_val_m = row_edit["fecha_fin"]
            ffin_m = c3.date_input("Fecha fin (vacío = abierto)",
                                   value=pd.to_datetime(ffin_val_m).date() if pd.notna(ffin_val_m) else None,
                                   key=f"ffin_m_{pid_edit}")
            if st.form_submit_button("Guardar datos del período", type="primary"):
                try:
                    actualizar_periodo_metas(pid_edit, {"nombre": nom_m, "fecha_inicio": fini_m, "fecha_fin": ffin_m}, usuario)
                    st.success("✅ Datos del período actualizados")
                    st.cache_data.clear()
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")

        st.markdown("#### Tramos del período")
        metas_edit = met_all[met_all["periodo_id"] == pid_edit].sort_values("orden").reset_index(drop=True)
        if metas_edit.empty:
            metas_df = pd.DataFrame(columns=["umbral_min", "umbral_max", "pct_aplica", "min_inclusivo", "max_inclusivo"])
        else:
            metas_df = metas_edit[["umbral_min", "umbral_max", "pct_aplica", "min_inclusivo", "max_inclusivo"]].copy()

        edited = st.data_editor(
            metas_df, num_rows="dynamic", use_container_width=True,
            key=f"editor_metas_{pid_edit}",
            column_config={
                "umbral_min": st.column_config.NumberColumn("Mínimo ($)", format="$%,.0f", required=True),
                "umbral_max": st.column_config.NumberColumn("Máximo ($)", format="$%,.0f"),
                "pct_aplica": st.column_config.NumberColumn("% Aplica", format="%.2f", min_value=0.0, max_value=1.0, required=True),
                "min_inclusivo": st.column_config.CheckboxColumn("Min Inclusivo", default=True),
                "max_inclusivo": st.column_config.CheckboxColumn("Max Inclusivo", default=False),
            })

        if st.button("Guardar tramos", type="primary", key=f"btn_metas_{pid_edit}"):
            tuplas = [(float(r["umbral_min"]), bool(r.get("min_inclusivo", True)),
                       float(r["umbral_max"]) if pd.notna(r["umbral_max"]) else None,
                       bool(r.get("max_inclusivo", False)), float(r["pct_aplica"]))
                      for _, r in edited.iterrows() if pd.notna(r["umbral_min"])]
            if tuplas:
                reemplazar_metas(pid_edit, tuplas, usuario)
                st.success(f"✅ {len(tuplas)} tramos guardados para '{sel_edit}'")
                st.cache_data.clear()
                st.rerun()
            else:
                st.warning("Agrega al menos un tramo con valor mínimo.")

        # --- Eliminar período ---
        with st.expander("⚠️ Eliminar este período"):
            st.warning(
                f"Al eliminar '{sel_edit}' se borran también sus tramos. "
                "Los meses que cubría este período quedarán sin regla (pct_bono = 0). "
                "La acción queda registrada en el historial con el detalle completo."
            )
            confirmar_m = st.checkbox(f"Confirmo que deseo eliminar '{sel_edit}'", key=f"conf_del_m_{pid_edit}")
            if st.button("Eliminar período de tramos", type="primary",
                         disabled=not confirmar_m, key=f"btn_del_m_{pid_edit}"):
                try:
                    eliminar_periodo_metas(pid_edit, usuario)
                    st.success("✅ Período eliminado y registrado en el historial")
                    st.cache_data.clear()
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")

# ---------- TAB 3: CREAR NUEVO PERÍODO ----------
with tab_new:
    with st.form("form_nuevo_per_metas"):
        c1, c2, c3 = st.columns(3)
        nom_m = c1.text_input("Nombre del período de tramos", placeholder="Ej: 2027 Metas")
        ini_m = c2.date_input("Fecha inicio", value=None)
        fin_m = c3.date_input("Fecha fin (vacío = abierto)", value=None)
        crear_m = st.form_submit_button("Crear período de tramos", type="primary")
        if crear_m:
            if not nom_m or ini_m is None:
                st.error("Nombre y fecha de inicio son obligatorios.")
            else:
                try:
                    nuevo_pid = crear_periodo_metas(nom_m, ini_m, fin_m, usuario)
                    st.success(f"✅ Período de tramos '{nom_m}' creado (ID {nuevo_pid}). Ahora edítalo en la pestaña 'Editar tramos'.")
                    st.cache_data.clear()
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")

# ============================================================
# ESCALA DE RENTABILIDAD (períodos INDEPENDIENTES)
# ============================================================
st.markdown("---")
st.subheader("Escala de rentabilidad")
st.caption("Cada período de escala es independiente y tiene sus propias fechas de vigencia.")

per_escala = _cargar_periodos_escala()
esc_all = esc

tab_res_e, tab_edit_e, tab_new_e = st.tabs(["Resumen", "Editar escala", "Crear nuevo período"])

with tab_res_e:
    if per_escala.empty:
        st.info("No hay períodos de escala creados.")
    else:
        sel_res_e = st.selectbox("Ver período de escala:", per_escala["nombre"].tolist(), key="sel_res_esc")
        pid_res_e = per_escala[per_escala["nombre"] == sel_res_e]["id"].iloc[0]
        esc_res = esc_all[esc_all["periodo_id"] == pid_res_e].sort_values("orden")
        per_res_e_row = per_escala[per_escala["id"] == pid_res_e].iloc[0]
        st.caption(f"Vigencia: {per_res_e_row['fecha_inicio']} → {per_res_e_row['fecha_fin'] if pd.notna(per_res_e_row['fecha_fin']) else 'Abierto'}")
        st.dataframe(
            esc_res[["orden", "cond_min", "cond_max", "pct_bono"]].rename(columns={
                "orden": "#", "cond_min": "Mínimo", "cond_max": "Máximo", "pct_bono": "% Bono"}),
            use_container_width=True, hide_index=True,
            column_config={
                "Mínimo": st.column_config.NumberColumn(format="%.4f"),
                "Máximo": st.column_config.NumberColumn(format="%.4f"),
                "% Bono": st.column_config.NumberColumn(format="%.0f%%"),
            })

with tab_edit_e:
    if per_escala.empty:
        st.info("Crea primero un período de escala en la pestaña 'Crear nuevo período'.")
    else:
        sel_edit_e = st.selectbox("Editar período de escala:", per_escala["nombre"].tolist(), key="sel_edit_esc")
        pid_edit_e = per_escala[per_escala["nombre"] == sel_edit_e]["id"].iloc[0]
        row_edit_e = per_escala[per_escala["id"] == pid_edit_e].iloc[0]

        # --- Editar nombre y fechas de vigencia ---
        with st.form(f"form_per_esc_{pid_edit_e}"):
            st.markdown("#### Datos del período")
            c1, c2, c3 = st.columns(3)
            nom_e = c1.text_input("Nombre", value=row_edit_e["nombre"], key=f"nom_e_{pid_edit_e}")
            fini_e = c2.date_input("Fecha inicio", value=pd.to_datetime(row_edit_e["fecha_inicio"]).date(), key=f"fini_e_{pid_edit_e}")
            ffin_val_e = row_edit_e["fecha_fin"]
            ffin_e = c3.date_input("Fecha fin (vacío = abierto)",
                                   value=pd.to_datetime(ffin_val_e).date() if pd.notna(ffin_val_e) else None,
                                   key=f"ffin_e_{pid_edit_e}")
            if st.form_submit_button("Guardar datos del período", type="primary"):
                try:
                    actualizar_periodo_escala(pid_edit_e, {"nombre": nom_e, "fecha_inicio": fini_e, "fecha_fin": ffin_e}, usuario)
                    st.success("✅ Datos del período actualizados")
                    st.cache_data.clear()
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")

        st.markdown("#### Rangos de escala del período")
        esc_edit = esc_all[esc_all["periodo_id"] == pid_edit_e].sort_values("orden").reset_index(drop=True)
        if esc_edit.empty:
            esc_df = pd.DataFrame(columns=["cond_min", "cond_max", "pct_bono", "min_inclusivo", "max_inclusivo"])
        else:
            esc_df = esc_edit[["cond_min", "cond_max", "pct_bono", "min_inclusivo", "max_inclusivo"]].copy()

        edited_e = st.data_editor(
            esc_df, num_rows="dynamic", use_container_width=True,
            key=f"editor_esc_{pid_edit_e}",
            column_config={
                "cond_min": st.column_config.NumberColumn("Mínimo", format="%.4f"),
                "cond_max": st.column_config.NumberColumn("Máximo", format="%.4f"),
                "pct_bono": st.column_config.NumberColumn("% Bono", format="%.2f", required=True),
                "min_inclusivo": st.column_config.CheckboxColumn("Min Inclusivo", default=True),
                "max_inclusivo": st.column_config.CheckboxColumn("Max Inclusivo", default=False),
            })

        col_b1, col_b2 = st.columns(2)
        with col_b1:
            if st.button("Guardar escala", type="primary", key=f"btn_esc_{pid_edit_e}"):
                tuplas_e = [(float(r["cond_min"]) if pd.notna(r["cond_min"]) else None,
                             bool(r.get("min_inclusivo", True)),
                             float(r["cond_max"]) if pd.notna(r["cond_max"]) else None,
                             bool(r.get("max_inclusivo", False)), float(r["pct_bono"]))
                            for _, r in edited_e.iterrows()]
                reemplazar_escala(pid_edit_e, tuplas_e, usuario)
                st.success(f"✅ Escala guardada para '{sel_edit_e}'")
                st.cache_data.clear()
                st.rerun()
        with col_b2:
            if st.button("Restaurar escala estándar", key=f"btn_rest_{pid_edit_e}"):
                reemplazar_escala(pid_edit_e, ESCALA_RENT_DEFAULT, usuario)
                st.success("✅ Escala estándar restaurada")
                st.cache_data.clear()
                st.rerun()

        # --- Eliminar período ---
        with st.expander("⚠️ Eliminar este período"):
            st.warning(
                f"Al eliminar '{sel_edit_e}' se borran también sus rangos. "
                "Los meses que cubría este período quedarán sin escala (pct_bono = -1). "
                "La acción queda registrada en el historial con el detalle completo."
            )
            confirmar_e = st.checkbox(f"Confirmo que deseo eliminar '{sel_edit_e}'", key=f"conf_del_e_{pid_edit_e}")
            if st.button("Eliminar período de escala", type="primary",
                         disabled=not confirmar_e, key=f"btn_del_e_{pid_edit_e}"):
                try:
                    eliminar_periodo_escala(pid_edit_e, usuario)
                    st.success("✅ Período eliminado y registrado en el historial")
                    st.cache_data.clear()
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")

with tab_new_e:
    with st.form("form_nuevo_per_escala"):
        c1, c2, c3 = st.columns(3)
        nom_e = c1.text_input("Nombre del período de escala", placeholder="Ej: 2027 Escala")
        ini_e = c2.date_input("Fecha inicio", value=None)
        fin_e = c3.date_input("Fecha fin (vacío = abierto)", value=None)
        crear_e = st.form_submit_button("Crear período de escala", type="primary")
        if crear_e:
            if not nom_e or ini_e is None:
                st.error("Nombre y fecha de inicio son obligatorios.")
            else:
                try:
                    nuevo_pid_e = crear_periodo_escala(nom_e, ini_e, fin_e, usuario)
                    st.success(f"✅ Período de escala '{nom_e}' creado (ID {nuevo_pid_e}).")
                    st.cache_data.clear()
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")

# ============================================================
# AUDITORÍA DE CAMBIOS
# ============================================================
st.markdown("---")
st.subheader("Historial de cambios")

auditoria = listar_auditoria_bono(limite=50)
if auditoria.empty:
    st.info("No hay cambios registrados todavía.")
else:
    aud_display = auditoria[["id", "momento", "usuario", "tabla", "accion"]].copy()
    aud_display = aud_display.rename(columns={
        "id": "ID",
        "momento": "Fecha/Hora",
        "usuario": "Usuario",
        "tabla": "Tabla",
        "accion": "Acción",
    })
    st.dataframe(aud_display, use_container_width=True, hide_index=True)
    
    with st.expander("Ver detalle de un cambio"):
        aud_id = st.selectbox("Selecciona ID de cambio:", auditoria["id"].tolist())
        if aud_id:
            cambio = auditoria[auditoria["id"] == aud_id].iloc[0]
            st.json({
                "valores_anteriores": cambio["valores_anteriores"],
                "valores_nuevos": cambio["valores_nuevos"],
            })