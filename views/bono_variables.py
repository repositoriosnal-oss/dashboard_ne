import streamlit as st
import pandas as pd
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
    METAS_EJECOM_DEFAULT,
    METAS_EJECOM_DEFAULT, 
)

st.set_page_config(page_title="Variables del Bono", page_icon=":material/tune:", layout="wide")
ROLES_EDIT_BONO = ["admin", "gerente"]
if st.session_state.get("rol_actual") not in ROLES_EDIT_BONO:
    st.error("⛔ Acceso denegado. Solo Administrador y Gerencia pueden editar las variables del bono.")
    st.stop()

st.title("Variables del Bono")
st.caption(
    "Parámetros por período y por ámbito: **ALMACENES** (tramos sobre la venta del punto) y "
    "**EJECUTIVOS COMERCIALES** (tramos sobre la venta individual; meta 430M por defecto). "
    "Los parámetros generales (factor de exentas y meta adicional) son compartidos por ambos ámbitos."
)
try:
    bootstrap_bono_variables()
except Exception as e:
    st.error(f"Error al inicializar tablas de configuración: {e}")
    st.stop()

per, met, esc = _cargar_config_bono()
per_metas = _cargar_periodos_metas()
per_escala = _cargar_periodos_escala()
if per.empty:
    st.warning("No hay períodos configurados. Contacta al administrador.")
    st.stop()
usuario = st.session_state.get("usuario_actual", "desconocido")

AMBITOS = ["ALMACEN", "EJECOM"]
NOM_AMBITO = {"ALMACEN": "🏬 Almacenes", "EJECOM": "👤 Ejecutivos Comerciales"}


def por_ambito(df, ambito):
    if df.empty or "ambito" not in df.columns:
        return df.iloc[0:0]
    return df[df["ambito"] == ambito]


def meta_plena(tramos):
    """Menor umbral que ya paga 100% (para mostrar la meta del ámbito)."""
    if tramos is None or tramos.empty:
        return None
    full = tramos[tramos["pct_aplica"] >= 1.0]
    return float(full["umbral_min"].min()) if not full.empty else None


tab_res, tab_new, tab_edit, tab_hist = st.tabs(
    ["Resumen", "Crear período", "Editar período", "Historial"])

# ------------------------------------------------------------
# 1. RESUMEN
# ------------------------------------------------------------
with tab_res:
    st.subheader("Parámetros generales (compartidos)")
    per_display = per[["nombre", "fecha_inicio", "fecha_fin", "factor_desc_exentas",
                       "meta_adic_base", "meta_adic_bloque", "meta_adic_pct"]].copy()
    per_display["fecha_fin"] = per_display["fecha_fin"].fillna("Abierto")
    st.dataframe(
        per_display.rename(columns={
            "nombre": "Período", "fecha_inicio": "Inicio", "fecha_fin": "Fin",
            "factor_desc_exentas": "Factor DESC Exentas", "meta_adic_base": "Base Meta Adicional",
            "meta_adic_bloque": "Bloque", "meta_adic_pct": "% por Bloque"}),
        use_container_width=True, hide_index=True,
        column_config={
            "Inicio": st.column_config.DateColumn(format="DD/MM/YYYY"),
            "Fin": st.column_config.TextColumn(width="small"),
            "Factor DESC Exentas": st.column_config.NumberColumn(format="%.0f%%"),
            "Base Meta Adicional": st.column_config.NumberColumn(format="$%,.0f"),
            "Bloque": st.column_config.NumberColumn(format="$%,.0f"),
            "% por Bloque": st.column_config.NumberColumn(format="%.0f%%")})

    st.markdown("---")
    c1, c2 = st.columns(2)
    for col, amb in zip((c1, c2), AMBITOS):
        with col:
            st.subheader(f"Variables por ámbito: {NOM_AMBITO[amb]}")
            pm = por_ambito(per_metas, amb)
            if pm.empty:
                st.info("Sin períodos de tramos para este ámbito.")
            else:
                filas = []
                for _, r in pm.iterrows():
                    tr = met[met["periodo_id"] == int(r["id"])]
                    filas.append({
                        "Período": r["nombre"],
                        "Inicio": r["fecha_inicio"],
                        "Fin": r["fecha_fin"] if pd.notna(r["fecha_fin"]) else "Abierto",
                        "Tramos": len(tr),
                        "Meta plena ($)": meta_plena(tr)})
                st.dataframe(pd.DataFrame(filas), use_container_width=True, hide_index=True,
                             column_config={
                                 "Inicio": st.column_config.DateColumn(format="DD/MM/YYYY"),
                                 "Fin": st.column_config.TextColumn(width="small"),
                                 "Meta plena ($)": st.column_config.NumberColumn(format="$%,.0f")})
            pe = por_ambito(per_escala, amb)
            if pe.empty:
                st.info("Sin períodos de escala para este ámbito.")
            else:
                filas_e = []
                for _, r in pe.iterrows():
                    rg = esc[esc["periodo_id"] == int(r["id"])]
                    filas_e.append({
                        "Período": r["nombre"],
                        "Inicio": r["fecha_inicio"],
                        "Fin": r["fecha_fin"] if pd.notna(r["fecha_fin"]) else "Abierto",
                        "Rangos": len(rg)})
                st.dataframe(pd.DataFrame(filas_e), use_container_width=True, hide_index=True,
                             column_config={"Inicio": st.column_config.DateColumn(format="DD/MM/YYYY"),
                                            "Fin": st.column_config.TextColumn(width="small")})

# ------------------------------------------------------------
# 2. CREAR PERÍODO
# ------------------------------------------------------------
with tab_new:
    with st.expander("➕ Nuevo período de parámetros generales", expanded=False):
        with st.form("form_nuevo_general"):
            col1, col2, col3 = st.columns(3)
            nuevo_nombre = col1.text_input("Nombre del período", placeholder="Ej: 2027")
            nueva_fecha_ini = col2.date_input("Fecha inicio", value=None)
            nueva_fecha_fin = col3.date_input("Fecha fin (vacío = abierto)", value=None)
            col4, col5, col6 = st.columns(3)
            nuevo_factor = col4.number_input("Factor DESC Exentas", min_value=0.0, max_value=1.0,
                                             value=0.50, step=0.01)
            nueva_base = col5.number_input("Base Meta Adicional", min_value=0.0,
                                           value=2_580_000_000.0, step=100_000_000.0)
            nuevo_bloque = col6.number_input("Bloque", min_value=1.0,
                                             value=100_000_000.0, step=50_000_000.0)
            nuevo_pct_meta = st.number_input("% por Bloque", min_value=0.0, max_value=1.0,
                                             value=0.06, step=0.01)
            periodo_copiar = st.selectbox(
                "Copiar tramos y escala de:",
                options=["Ninguno (valores por defecto)"] + per["nombre"].tolist())
            if st.form_submit_button("Crear período general", type="primary"):
                if not nuevo_nombre or nueva_fecha_ini is None:
                    st.error("Nombre y fecha de inicio son obligatorios.")
                else:
                    try:
                        if periodo_copiar != "Ninguno (valores por defecto)":
                            pid_orig = int(per[per["nombre"] == periodo_copiar]["id"].iloc[0]) 
                            metas_t = [(float(r["umbral_min"]), bool(r["min_inclusivo"]),
                                        float(r["umbral_max"]) if pd.notna(r["umbral_max"]) else None,
                                        bool(r["max_inclusivo"]), float(r["pct_aplica"]))
                                       for _, r in met[met["periodo_id"] == pid_orig].sort_values("orden").iterrows()]
                            esc_t = [(float(r["cond_min"]) if pd.notna(r["cond_min"]) else None,
                                      bool(r["min_inclusivo"]),
                                      float(r["cond_max"]) if pd.notna(r["cond_max"]) else None,
                                      bool(r["max_inclusivo"]), float(r["pct_bono"]))
                                     for _, r in esc[esc["periodo_id"] == pid_orig].sort_values("orden").iterrows()]
                        else:
                            metas_t, esc_t = METAS_2026, ESCALA_RENT_DEFAULT
                        nuevo_id = crear_periodo(nombre=nuevo_nombre, f_ini=nueva_fecha_ini,
                                                 f_fin=nueva_fecha_fin, factor=nuevo_factor,
                                                 base=nueva_base, bloque=nuevo_bloque,
                                                 pct=nuevo_pct_meta, metas=metas_t, escala=esc_t,
                                                 usuario=usuario)
                        st.success(f"✅ Período general '{nuevo_nombre}' creado (ID {nuevo_id}).")
                        st.cache_data.clear(); st.rerun()
                    except ValueError as ve:
                        st.error(f"Error de validación: {ve}")
                    except Exception as e:
                        st.error(f"Error al crear período: {e}")

    with st.expander("➕ Nuevo período de tramos / escala por ámbito"):
        with st.form("form_nuevo_ambito"):
            amb_new = st.radio("Ámbito", AMBITOS, format_func=lambda a: NOM_AMBITO[a],
                               horizontal=True, key="new_ambito")
            n1, n2, n3 = st.columns(3)
            nom_new = n1.text_input("Nombre", placeholder=f"Ej: {('EJECOM' if amb_new == 'EJECOM' else 'Red')} 2027")
            fi_new = n2.date_input("Fecha inicio", value=None, key="new_fi_amb")
            ff_new = n3.date_input("Fecha fin (vacío = abierto)", value=None, key="new_ff_amb")
            tramos_ini = METAS_EJECOM_DEFAULT if amb_new == "EJECOM" else METAS_2026
            crear_tramos = st.checkbox("Crear período de tramos de meta", value=True)
            crear_escala = st.checkbox("Crear período de escala de rentabilidad", value=True)
            st.caption("Se crean con los tramos/escala estándar del ámbito; ajústalos luego en 'Editar período'. "
                       f"Tramos iniciales: meta plena ${meta_plena(pd.DataFrame(tramos_ini, columns=['umbral_min','min_inclusivo','umbral_max','max_inclusivo','pct_aplica'])):,.0f}.")
            if st.form_submit_button("Crear período(s) del ámbito", type="primary"):
                if not nom_new or fi_new is None:
                    st.error("Nombre y fecha de inicio son obligatorios.")
                elif not (crear_tramos or crear_escala):
                    st.error("Marca al menos una de las dos casillas.")
                else:
                    try:
                        msgs = []
                        if crear_tramos:
                            pid_m = crear_periodo_metas(nom_new, fi_new, ff_new, usuario, ambito=amb_new)
                            reemplazar_metas(pid_m, tramos_ini, usuario)
                            msgs.append(f"tramos (ID {pid_m})")
                        if crear_escala:
                            pid_e = crear_periodo_escala(nom_new, fi_new, ff_new, usuario, ambito=amb_new)
                            reemplazar_escala(pid_e, ESCALA_RENT_DEFAULT, usuario)
                            msgs.append(f"escala (ID {pid_e})")
                        st.success(f"✅ Creado para {NOM_AMBITO[amb_new]}: " + " y ".join(msgs) + ".")
                        st.cache_data.clear(); st.rerun()
                    except ValueError as ve:
                        st.error(f"Error de validación: {ve}")
                    except Exception as e:
                        st.error(f"Error al crear: {e}")

# ------------------------------------------------------------
# 3. EDITAR PERÍODO
# ------------------------------------------------------------
with tab_edit:
    st.markdown("### Parámetros generales")
    periodo_sel = st.selectbox("Período general a editar:", per["nombre"].tolist(), key="sel_gen")
    periodo_id = int(per[per["nombre"] == periodo_sel]["id"].iloc[0])
    periodo_row = per[per["id"] == periodo_id].iloc[0]
    with st.form("form_params"):
        col1, col2, col3 = st.columns(3)
        e_nombre = col1.text_input("Nombre", value=periodo_row["nombre"])
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
        if st.form_submit_button("Guardar parámetros generales", type="primary"):
            try:
                actualizar_periodo(periodo_id, {
                    "nombre": e_nombre, "fecha_inicio": e_fini,
                    "fecha_fin": e_ffin if e_ffin else None,
                    "factor_desc_exentas": e_factor, "meta_adic_base": e_base,
                    "meta_adic_bloque": e_bloque, "meta_adic_pct": e_pct}, usuario)
                st.success("✅ Parámetros actualizados.")
                st.cache_data.clear(); st.rerun()
            except Exception as e:
                st.error(f"Error al actualizar: {e}")

    st.markdown("---")
    ambito_edit = st.radio("Ámbito a editar", AMBITOS, format_func=lambda a: NOM_AMBITO[a],
                           horizontal=True, key="edit_ambito")

    # ---- Tramos de meta ----
    st.markdown("### Tramos de meta de ventas")
    pm_edit = por_ambito(per_metas, ambito_edit)
    if pm_edit.empty:
        st.info(f"Sin períodos de tramos para {NOM_AMBITO[ambito_edit]}. Créalo en la pestaña 'Crear período'.")
    else:
        sel_m = st.selectbox("Período de tramos:", pm_edit["nombre"].tolist(), key=f"sel_m_{ambito_edit}")
        pid_m = int(pm_edit[pm_edit["nombre"] == sel_m]["id"].iloc[0])
        row_m = pm_edit[pm_edit["id"] == pid_m].iloc[0]
        with st.form(f"form_per_metas_{ambito_edit}"):
            c1, c2, c3 = st.columns(3)
            nom_m = c1.text_input("Nombre", value=row_m["nombre"], key=f"nom_m_{ambito_edit}")
            fini_m = c2.date_input("Fecha inicio", value=pd.to_datetime(row_m["fecha_inicio"]).date(),
                                   key=f"fini_m_{ambito_edit}")
            ffin_v = row_m["fecha_fin"]
            ffin_m = c3.date_input("Fecha fin (vacío = abierto)",
                                   value=pd.to_datetime(ffin_v).date() if pd.notna(ffin_v) else None,
                                   key=f"ffin_m_{ambito_edit}")
            if st.form_submit_button("Guardar datos del período"):
                try:
                    actualizar_periodo_metas(pid_m, {"nombre": nom_m, "fecha_inicio": fini_m,
                                                     "fecha_fin": ffin_m}, usuario)
                    st.success("✅ Datos del período actualizados.")
                    st.cache_data.clear(); st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")
        metas_edit = met[met["periodo_id"] == pid_m].sort_values("orden").reset_index(drop=True)
        metas_df = (metas_edit[["umbral_min", "umbral_max", "pct_aplica", "min_inclusivo", "max_inclusivo"]].copy()
                    if not metas_edit.empty else
                    pd.DataFrame(columns=["umbral_min", "umbral_max", "pct_aplica", "min_inclusivo", "max_inclusivo"]))
        edited_m = st.data_editor(
            metas_df, num_rows="dynamic", use_container_width=True, key=f"editor_metas_{ambito_edit}_{pid_m}",
            column_config={
                "umbral_min": st.column_config.NumberColumn("Mínimo ($)", format="$%,.0f", required=True),
                "umbral_max": st.column_config.NumberColumn("Máximo ($)", format="$%,.0f"),
                "pct_aplica": st.column_config.NumberColumn("% Aplica", format="%.2f", min_value=0.0, max_value=1.0, required=True),
                "min_inclusivo": st.column_config.CheckboxColumn("Min Inclusivo", default=True),
                "max_inclusivo": st.column_config.CheckboxColumn("Max Inclusivo", default=False)})
        b1, b2 = st.columns(2)
        if b1.button("Guardar tramos", type="primary", key=f"btn_metas_{ambito_edit}"):
            tuplas = [(float(r["umbral_min"]), bool(r.get("min_inclusivo", True)),
                       float(r["umbral_max"]) if pd.notna(r["umbral_max"]) else None,
                       bool(r.get("max_inclusivo", False)), float(r["pct_aplica"]))
                      for _, r in edited_m.iterrows() if pd.notna(r["umbral_min"])]
            if tuplas:
                reemplazar_metas(pid_m, tuplas, usuario)
                st.success(f"✅ {len(tuplas)} tramos guardados para '{sel_m}'.")
                st.cache_data.clear(); st.rerun()
            else:
                st.warning("Agrega al menos un tramo con valor mínimo.")
        if b2.button("Restaurar tramos estándar del ámbito", key=f"btn_rest_m_{ambito_edit}"):
            reemplazar_metas(pid_m, METAS_EJECOM_DEFAULT if ambito_edit == "EJECOM" else METAS_2026, usuario)
            st.success("✅ Tramos estándar restaurados.")
            st.cache_data.clear(); st.rerun()
        with st.expander("⚠️ Eliminar este período de tramos"):
            st.warning(f"Al eliminar '{sel_m}' se borran sus tramos; los meses que cubría quedarán sin regla (pct 0).")
            conf_m = st.checkbox(f"Confirmo eliminar '{sel_m}'", key=f"conf_del_m_{ambito_edit}")
            if st.button("Eliminar período de tramos", type="primary", disabled=not conf_m,
                         key=f"btn_del_m_{ambito_edit}"):
                eliminar_periodo_metas(pid_m, usuario)
                st.success("✅ Período eliminado y registrado en el historial.")
                st.cache_data.clear(); st.rerun()

    # ---- Escala de rentabilidad ----
    st.markdown("### Escala de rentabilidad")
    pe_edit = por_ambito(per_escala, ambito_edit)
    if pe_edit.empty:
        st.info(f"Sin períodos de escala para {NOM_AMBITO[ambito_edit]}. Créalo en la pestaña 'Crear período'.")
    else:
        sel_e = st.selectbox("Período de escala:", pe_edit["nombre"].tolist(), key=f"sel_e_{ambito_edit}")
        pid_e = int(pe_edit[pe_edit["nombre"] == sel_e]["id"].iloc[0])
        row_e = pe_edit[pe_edit["id"] == pid_e].iloc[0]
        with st.form(f"form_per_esc_{ambito_edit}"):
            c1, c2, c3 = st.columns(3)
            nom_e = c1.text_input("Nombre", value=row_e["nombre"], key=f"nom_e_{ambito_edit}")
            fini_e = c2.date_input("Fecha inicio", value=pd.to_datetime(row_e["fecha_inicio"]).date(),
                                   key=f"fini_e_{ambito_edit}")
            ffin_ev = row_e["fecha_fin"]
            ffin_e = c3.date_input("Fecha fin (vacío = abierto)",
                                   value=pd.to_datetime(ffin_ev).date() if pd.notna(ffin_ev) else None,
                                   key=f"ffin_e_{ambito_edit}")
            if st.form_submit_button("Guardar datos del período"):
                try:
                    actualizar_periodo_escala(pid_e, {"nombre": nom_e, "fecha_inicio": fini_e,
                                                      "fecha_fin": ffin_e}, usuario)
                    st.success("✅ Datos del período actualizados.")
                    st.cache_data.clear(); st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")
        esc_edit = esc[esc["periodo_id"] == pid_e].sort_values("orden").reset_index(drop=True)
        esc_df = (esc_edit[["cond_min", "cond_max", "pct_bono", "min_inclusivo", "max_inclusivo"]].copy()
                  if not esc_edit.empty else
                  pd.DataFrame(columns=["cond_min", "cond_max", "pct_bono", "min_inclusivo", "max_inclusivo"]))
        edited_e = st.data_editor(
            esc_df, num_rows="dynamic", use_container_width=True, key=f"editor_esc_{ambito_edit}_{pid_e}",
            column_config={
                "cond_min": st.column_config.NumberColumn("Mínimo", format="%.4f"),
                "cond_max": st.column_config.NumberColumn("Máximo", format="%.4f"),
                "pct_bono": st.column_config.NumberColumn("% Bono", format="%.2f", required=True),
                "min_inclusivo": st.column_config.CheckboxColumn("Min Inclusivo", default=True),
                "max_inclusivo": st.column_config.CheckboxColumn("Max Inclusivo", default=False)})
        b1, b2 = st.columns(2)
        if b1.button("Guardar escala", type="primary", key=f"btn_esc_{ambito_edit}"):
            tuplas_e = [(float(r["cond_min"]) if pd.notna(r["cond_min"]) else None,
                         bool(r.get("min_inclusivo", True)),
                         float(r["cond_max"]) if pd.notna(r["cond_max"]) else None,
                         bool(r.get("max_inclusivo", False)), float(r["pct_bono"]))
                        for _, r in edited_e.iterrows()]
            reemplazar_escala(pid_e, tuplas_e, usuario)
            st.success(f"✅ Escala guardada para '{sel_e}'.")
            st.cache_data.clear(); st.rerun()
        if b2.button("Restaurar escala estándar", key=f"btn_rest_e_{ambito_edit}"):
            reemplazar_escala(pid_e, ESCALA_RENT_DEFAULT, usuario)
            st.success("✅ Escala estándar restaurada.")
            st.cache_data.clear(); st.rerun()
        with st.expander("⚠️ Eliminar este período de escala"):
            st.warning(f"Al eliminar '{sel_e}' se borran sus rangos; los meses cubiertos quedarán sin escala (pct -1).")
            conf_e = st.checkbox(f"Confirmo eliminar '{sel_e}'", key=f"conf_del_e_{ambito_edit}")
            if st.button("Eliminar período de escala", type="primary", disabled=not conf_e,
                         key=f"btn_del_e_{ambito_edit}"):
                eliminar_periodo_escala(pid_e, usuario)
                st.success("✅ Período eliminado y registrado en el historial.")
                st.cache_data.clear(); st.rerun()

# ------------------------------------------------------------
# 4. HISTORIAL
# ------------------------------------------------------------
with tab_hist:
    st.subheader("Historial de cambios")
    auditoria = listar_auditoria_bono(limite=100)
    if auditoria.empty:
        st.info("No hay cambios registrados todavía.")
    else:
        aud_display = auditoria[["id", "momento", "usuario", "tabla", "accion"]].copy()
        aud_display["ambito"] = auditoria["valores_nuevos"].apply(
            lambda v: v.get("ambito", "") if isinstance(v, dict) else "")
        st.dataframe(
            aud_display.rename(columns={
                "id": "ID", "momento": "Fecha/Hora", "usuario": "Usuario",
                "tabla": "Tabla", "accion": "Acción", "ambito": "Ámbito"}),
            use_container_width=True, hide_index=True,
            column_config={"Fecha/Hora": st.column_config.DatetimeColumn(format="DD/MM/YYYY HH:mm")})
        with st.expander("Ver detalle de un cambio"):
            aud_id = st.selectbox("Selecciona ID de cambio:", auditoria["id"].tolist())
            cambio = auditoria[auditoria["id"] == aud_id].iloc[0]
            c1, c2 = st.columns(2)
            c1.markdown("**Antes**"); c1.json(cambio["valores_anteriores"] if cambio["valores_anteriores"] is not None else {})
            c2.markdown("**Después**"); c2.json(cambio["valores_nuevos"] if cambio["valores_nuevos"] is not None else {})