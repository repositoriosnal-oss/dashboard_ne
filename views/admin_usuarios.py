import streamlit as st
import pandas as pd
from sqlalchemy import text
from src.conexion_db import conn
from werkzeug.security import generate_password_hash
import io

st.set_page_config(page_title="Gestión de Usuarios", layout="wide")
st.title("👥 Gestión Avanzada de Usuarios")
st.markdown("---")

# ==========================================
# VALIDAR SEGURIDAD DE ACCESO
# ==========================================
if st.session_state.get('rol_actual') != "admin":
    st.error("⛔ Acceso denegado. Solo el Administrador de Sistemas puede ver esto.")
    st.stop()

# ==========================================
# 1. OBTENER DATOS MAESTROS (Almacenes)
# ==========================================
try:
    df_almacenes = conn.query('SELECT DISTINCT "Almacen" FROM sap_raw.remisiones WHERE "Almacen" IS NOT NULL ORDER BY "Almacen"', ttl=0)
    lista_almacenes = ["Seleccione un almacén..."] + df_almacenes['Almacen'].tolist() if not df_almacenes.empty else ["No hay almacenes"]
except Exception as e:
    st.error(f"⚠️ Error al conectar con las tablas maestras: {e}")
    lista_almacenes = ["Error al cargar"]

# ==========================================
# 2. INTERFAZ EN PESTAÑAS
# ==========================================
tab_crear, tab_masiva, tab_editar, tab_reset = st.tabs([
    "🆕 Crear Usuario",
    "📥 Carga Masiva (Excel)",
    "✏️ Consultar, Editar y Bloquear",
    "🔐 Resetear Claves"
])

# --------------------------------------------------
# PESTAÑA 1: CREAR USUARIO (LÓGICA DINÁMICA MEJORADA)
# --------------------------------------------------
with tab_crear:
    st.subheader("Registrar Nuevo Miembro en Plataforma")
    col1, col2 = st.columns(2)
    
    with col1:
        nuevo_usuario = st.text_input("Usuario de Acceso (Ej: juan.perez)").strip().lower()
        nombre_visible = st.text_input("Nombre Completo")
        rol_seleccionado = st.selectbox("Rol de Seguridad", ["comercial", "admin_punto", "gerente_comercial", "gerente", "admin"])
        
    with col2:
        depto_seleccionado = st.selectbox("Departamento", ["VENTAS", "COMPRAS", "CONTABILIDAD", "GERENCIA", "SISTEMAS"])

    sap_branch, sap_owner = None, None

    if depto_seleccionado == "VENTAS":
        almacen_sel = st.selectbox("🏢 Asignar Almacén / Sede:", lista_almacenes)
        
        if rol_seleccionado == "admin_punto" and almacen_sel != "Seleccione un almacén...":
            sap_branch = almacen_sel
            st.info(f"💡 Se asignará el almacén: **{sap_branch}**")
            
        elif rol_seleccionado == "comercial" and almacen_sel != "Seleccione un almacén...":
            # ✅ MEJORA EXPERTA: Carga dinámica de colaboradores SOLO del almacén seleccionado
            with st.spinner("Cargando colaboradores activos en este almacén..."):
                query_colab = """
                    SELECT DISTINCT "Colaborador", "Empleado_Ventas" AS "Codigo"
                    FROM sap_raw.remisiones
                    WHERE "Sede_Codigo" = :almacen
                      AND "Colaborador" IS NOT NULL
                      AND TRIM("Colaborador") != ''
                    ORDER BY "Colaborador"
                """
                df_colab_filtrado = conn.query(query_colab, params={"almacen": almacen_sel}, ttl=10)
            
            if not df_colab_filtrado.empty:
                lista_comerciales_dinamica = ["Seleccione un colaborador..."] + df_colab_filtrado['Colaborador'].tolist()
                dict_comerciales_dinamico = dict(zip(df_colab_filtrado['Colaborador'], df_colab_filtrado['Codigo']))
            else:
                lista_comerciales_dinamica = ["⚠️ No hay colaboradores con registros en este almacén"]
                dict_comerciales_dinamico = {}

            colab_sel = st.selectbox("👤 Vincular con Vendedor SAP:", lista_comerciales_dinamica)
            
            if colab_sel != "Seleccione un colaborador..." and "⚠️" not in colab_sel:
                sap_owner = int(dict_comerciales_dinamico[colab_sel])
                st.success(f"✅ Vinculado correctamente con código SAP: **{sap_owner}**")

    # Obtener contraseña por defecto desde secrets (o fallback si no está configurado)
    default_pwd = st.secrets.get("default_password", "Sistemas2026*")
    st.caption(f"🔑 Contraseña provisional por defecto: **{default_pwd}**")

    if st.button("🚀 Crear Usuario", type="primary"):
        if not nuevo_usuario or not nombre_visible:
            st.error("⚠️ El usuario y el nombre son obligatorios.")
        elif depto_seleccionado == "VENTAS" and rol_seleccionado == "comercial" and sap_owner is None:
            st.error("⚠️ Para el rol comercial debes seleccionar un colaborador válido de SAP.")
        elif depto_seleccionado == "VENTAS" and rol_seleccionado == "admin_punto" and sap_branch is None:
            st.error("⚠️ Para el rol admin_punto debes seleccionar un almacén.")
        else:
            try:
                with conn.session as session:
                    session.execute(text("""
                        INSERT INTO app.usuarios_portal (usuario, clave, nombre_completo, rol, departamento, sap_owner_code, sap_branch_code, activo)
                        VALUES (:usr, :cla, :nom, :rol, :dep, :owner, :branch, TRUE);
                    """), {
                        "usr": nuevo_usuario, 
                        "cla": generate_password_hash(default_pwd), 
                        "nom": nombre_visible, 
                        "rol": rol_seleccionado, 
                        "dep": depto_seleccionado, 
                        "owner": sap_owner, 
                        "branch": sap_branch
                    })
                    session.commit()
                st.success(f"✅ ¡Usuario '{nuevo_usuario}' creado exitosamente!")
                st.rerun()
            except Exception as e:
                st.error(f"❌ El usuario ya existe o hubo un error de base de datos: {e}")

# --------------------------------------------------
# PESTAÑA 2: CARGA MASIVA DESDE EXCEL
# --------------------------------------------------
with tab_masiva:
    st.subheader("📥 Cargar Usuarios desde Archivo Excel")
    st.info(f"💡 **Instrucciones:** El archivo Excel debe tener las columnas: `usuario`, `nombre_completo`, `rol`, `departamento`, `sap_branch_code`, `sap_owner_code`. Contraseña asignada: **{default_pwd}**")
    
    plantilla = pd.DataFrame(columns=["usuario", "nombre_completo", "rol", "departamento", "sap_branch_code", "sap_owner_code"])
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="xlsxwriter") as writer:
        plantilla.to_excel(writer, index=False, sheet_name="Plantilla")
        
    st.download_button(
        label="📥 Descargar Plantilla Excel",
        data=buffer.getvalue(),
        file_name="plantilla_usuarios.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    
    uploaded_file = st.file_uploader("Subir archivo Excel (.xlsx)", type=["xlsx"])
    if uploaded_file is not None:
        try:
            df_carga = pd.read_excel(uploaded_file)
            st.write("Vista previa de los datos a cargar:")
            st.dataframe(df_carga.head(), use_container_width=True)
            
            columnas_requeridas = ["usuario", "nombre_completo", "rol", "departamento"]
            if not all(col in df_carga.columns for col in columnas_requeridas):
                st.error(f"❌ El archivo debe contener al menos las columnas: {', '.join(columnas_requeridas)}")
            else:
                if st.button("🚀 Procesar Carga Masiva", type="primary"):
                    exitos, errores = 0, 0
                    with conn.session as session:
                        for index, row in df_carga.iterrows():
                            usuario = str(row['usuario']).strip().lower()
                            if pd.isna(usuario) or usuario == "":
                                continue
                            try:
                                branch = str(row.get('sap_branch_code', '')).strip() if pd.notna(row.get('sap_branch_code')) else None
                                owner = int(row.get('sap_owner_code')) if pd.notna(row.get('sap_owner_code')) and str(row.get('sap_owner_code')).replace('.', '', 1).isdigit() else None
                                
                                session.execute(text("""
                                    INSERT INTO app.usuarios_portal (usuario, clave, nombre_completo, rol, departamento, sap_owner_code, sap_branch_code, activo)
                                    VALUES (:usr, :cla, :nom, :rol, :dep, :owner, :branch, TRUE)
                                    ON CONFLICT (usuario) DO NOTHING;
                                """), {
                                    "usr": usuario,
                                    "cla": generate_password_hash(default_pwd),
                                    "nom": str(row['nombre_completo']).strip(),
                                    "rol": str(row.get('rol', 'comercial')).strip().lower(),
                                    "dep": str(row.get('departamento', 'VENTAS')).strip().upper(),
                                    "owner": owner,
                                    "branch": branch
                                })
                                exitos += 1
                            except Exception:
                                errores += 1
                                continue
                        session.commit()
                    st.success(f"✅ Proceso finalizado. **{exitos}** usuarios procesados. **{errores}** omitidos (posiblemente duplicados o datos inválidos).")
                    st.rerun()
        except Exception as e:
            st.error(f"❌ Error al leer el archivo: {e}")

# --------------------------------------------------
# PESTAÑA 3: CONSULTAR, EDITAR Y BLOQUEAR
# --------------------------------------------------
with tab_editar:
    st.subheader("Usuarios Registrados en el Sistema")
    df_usuarios = conn.query("SELECT id, usuario, nombre_completo, rol, departamento, sap_branch_code, sap_owner_code, activo FROM app.usuarios_portal ORDER BY usuario ASC", ttl=0)
    
    df_display = df_usuarios.copy()
    df_display['Estado'] = df_display['activo'].apply(lambda x: "🟢 Activo" if x else "🔴 Bloqueado")
    st.dataframe(df_display[['usuario', 'nombre_completo', 'departamento', 'rol', 'sap_branch_code', 'Estado']], use_container_width=True, hide_index=True)
    
    st.markdown("---")
    st.subheader("✏️ Gestionar Usuario")
    usuario_a_gestionar = st.selectbox("Seleccione el usuario:", [""] + df_usuarios['usuario'].tolist())
    
    if usuario_a_gestionar:
        datos_usr = df_usuarios[df_usuarios['usuario'] == usuario_a_gestionar].iloc[0]
        col_accion1, col_accion2 = st.columns(2)
        
        with col_accion1:
            st.markdown("#### Modificar Datos")
            with st.form("form_editar_usuario"):
                e_nombre = st.text_input("Nombre Completo", value=datos_usr['nombre_completo'])
                lista_roles = ["comercial", "admin_punto", "gerente_comercial", "gerente", "admin"]
                e_rol = st.selectbox("Rol", lista_roles, index=lista_roles.index(datos_usr['rol']) if datos_usr['rol'] in lista_roles else 0)
                lista_deptos = ["VENTAS", "COMPRAS", "CONTABILIDAD", "GERENCIA", "SISTEMAS"]
                e_depto = st.selectbox("Departamento", lista_deptos, index=lista_deptos.index(datos_usr['departamento']) if datos_usr['departamento'] in lista_deptos else 0)
                
                val_b = str(datos_usr['sap_branch_code']) if pd.notna(datos_usr['sap_branch_code']) else ""
                e_branch = st.text_input("Almacén / Sede (Opcional)", value=val_b).strip()
                
                val_o = int(datos_usr['sap_owner_code']) if pd.notna(datos_usr['sap_owner_code']) else 0
                e_owner = st.number_input("Código Comercial SAP (Opcional)", value=val_o)
                
                if st.form_submit_button("💾 Guardar Cambios"):
                    with conn.session as session:
                        session.execute(text("""
                            UPDATE app.usuarios_portal 
                            SET nombre_completo = :nom, rol = :rol, departamento = :dep, sap_owner_code = :owner, sap_branch_code = :branch
                            WHERE usuario = :usr
                        """), {
                            "nom": e_nombre, "rol": e_rol, "dep": e_depto, 
                            "owner": e_owner if e_owner != 0 else None, 
                            "branch": e_branch if e_branch != "" else None,
                            "usr": usuario_a_gestionar
                        })
                        session.commit()
                    st.success("✅ ¡Usuario modificado correctamente!")
                    st.rerun()
                    
        with col_accion2:
            st.markdown("#### Estado del Usuario")
            estado_actual = "🟢 Activo" if datos_usr['activo'] else "🔴 Bloqueado"
            st.info(f"Estado actual: **{estado_actual}**")
            
            nuevo_estado = not datos_usr['activo']
            texto_boton = "🔓 Desbloquear Usuario" if not datos_usr['activo'] else "🚫 Bloquear Usuario"
            tipo_boton = "primary" if not datos_usr['activo'] else "secondary"
            
            if st.button(texto_boton, type=tipo_boton):
                with conn.session as session:
                    session.execute(text("UPDATE app.usuarios_portal SET activo = :activo WHERE usuario = :usr"), 
                                    {"activo": nuevo_estado, "usr": usuario_a_gestionar})
                    session.commit()
                st.success(f"✅ Usuario {'desbloqueado' if nuevo_estado else 'bloqueado'} exitosamente.")
                st.rerun()
                
            st.markdown("---")
            st.warning("⚠️ **Eliminar es irreversible.**")
            confirm_delete = st.checkbox("Confirmo que deseo ELIMINAR permanentemente este usuario.")
            
            if st.button("🗑️ ELIMINAR USUARIO", type="primary", disabled=not confirm_delete):
                try:
                    with conn.session as session:
                        session.execute(text("DELETE FROM app.usuarios_portal WHERE usuario = :usr"), {"usr": usuario_a_gestionar})
                        session.commit()
                    st.success(f"✅ Usuario '{usuario_a_gestionar}' eliminado.")
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ Error al eliminar: {e}")

# --------------------------------------------------
# PESTAÑA 4: RESETEAR CONTRASEÑAS
# --------------------------------------------------
with tab_reset:
    st.subheader("🔐 Restablecer Credenciales Olvidadas")
    r_usr = st.selectbox("Seleccione la cuenta a restablecer:", df_usuarios['usuario'].tolist())
    
    if st.button("🚨 Resetear a Contraseña de Fábrica", type="primary"):
        with conn.session as session:
            session.execute(text("UPDATE app.usuarios_portal SET clave = :nueva WHERE usuario = :usr"), 
                            {"nueva": generate_password_hash(default_pwd), "usr": r_usr})
            session.commit()
        st.success(f"✅ La contraseña de **{r_usr}** ha vuelto a ser '{default_pwd}'.")