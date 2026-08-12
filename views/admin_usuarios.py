import streamlit as st
import pandas as pd
from sqlalchemy import text
from src.conexion_db import conn
from werkzeug.security import generate_password_hash

st.set_page_config(page_title="Gestión de Usuarios", layout="wide")
st.title("👥 Gestión Avanzada de Usuarios")
st.markdown("---")

# Validar seguridad de acceso
if st.session_state.get('rol_actual') != "admin":
    st.error("⛔ Acceso denegado. Solo el Administrador de Sistemas puede ver esto.")
    st.stop()

# =====================================================================
# 1. OBTENER DATOS MAESTROS DIRECTO DE LAS TABLAS RAW
# =====================================================================
try:
    df_almacenes = conn.query('SELECT DISTINCT "Almacen" FROM sap_raw.remisiones WHERE "Almacen" IS NOT NULL ORDER BY "Almacen"', ttl=0)
    lista_almacenes = ["Seleccione un almacén..."] + df_almacenes['Almacen'].tolist() if not df_almacenes.empty else ["No hay almacenes en la base de datos"]
    
    df_colab = conn.query('SELECT "Codigo", "Nombre" || \' \' || "Apellido" AS "Colaborador" FROM sap_raw.colaboradores WHERE "Nombre" IS NOT NULL ORDER BY "Colaborador"', ttl=0)
    if not df_colab.empty:
        lista_comerciales = ["Seleccione un colaborador..."] + df_colab['Colaborador'].tolist()
        dict_comerciales = dict(zip(df_colab['Colaborador'], df_colab['Codigo']))
    else:
        lista_comerciales = ["No hay colaboradores en la base de datos"]
        dict_comerciales = {}
except Exception as e:
    st.error(f"⚠️ Error al conectar con las tablas maestras de SAP: {e}")
    lista_almacenes = ["Error al cargar"]
    lista_comerciales = ["Error al cargar"]
    dict_comerciales = {}

# =====================================================================
# 2. INTERFAZ EN PESTAÑAS
# =====================================================================
tab_crear, tab_editar, tab_reset = st.tabs([" Crear Usuario", "✏️ Consultar, Editar y Eliminar", "🔐 Resetear Claves"])

# ----------------- PESTAÑA 1: CREAR USUARIO -----------------
with tab_crear:
    st.subheader("Registrar Nuevo Miembro en Plataforma")
    
    col1, col2 = st.columns(2)
    with col1:
        nuevo_usuario = st.text_input("Usuario de Acceso (Ej: juan.perez)").strip().lower()
        nombre_visible = st.text_input("Nombre Completo (Para mostrar en pantalla)")
        rol_seleccionado = st.selectbox("Rol de Seguridad", ["comercial", "admin_punto", "gerente_comercial", "gerente", "admin"])
    
    with col2:
        depto_seleccionado = st.selectbox("Departamento", ["VENTAS", "COMPRAS", "CONTABILIDAD", "GERENCIA", "SISTEMAS"])
        
        sap_branch = None
        sap_owner = None
        
        if depto_seleccionado == "VENTAS":
            almacen_sel = st.selectbox("🏢 Asignar Almacén / Sede:", lista_almacenes)
            
            if rol_seleccionado == "admin_punto":
                if almacen_sel != "Seleccione un almacén...":
                    # ✅ AHORA GUARDAMOS EL NOMBRE COMPLETO DEL ALMACÉN, NO EL CÓDIGO CORTO
                    sap_branch = almacen_sel  # Guardamos "EJECUTIVOS COMERCIALES" tal cual
                    st.info(f"💡 Se asignará el almacén: **{sap_branch}**")
                    
            elif rol_seleccionado == "comercial":
                colab_sel = st.selectbox("👤 Vincular con Vendedor SAP:", lista_comerciales)
                if colab_sel != "Seleccione un colaborador...":
                    sap_owner = int(dict_comerciales[colab_sel])
                    
    st.caption("🔑 Contraseña provisional por defecto: **Sistemas2026***")
    
    if st.button("🚀 Crear Usuario", type="primary"):
        if not nuevo_usuario or not nombre_visible:
            st.error("️ El usuario y el nombre son obligatorios.")
        elif depto_seleccionado == "VENTAS" and rol_seleccionado == "comercial" and sap_owner is None:
            st.error("️ Para el rol comercial de ventas debes seleccionar su equivalente de la lista de SAP.")
        elif depto_seleccionado == "VENTAS" and rol_seleccionado == "admin_punto" and sap_branch is None:
            st.error("⚠️ Para el rol admin_punto debes seleccionar un almacén.")
        else:
            try:
                with conn.session as session:
                    session.execute(text("""
                        INSERT INTO app.usuarios_portal (usuario, clave, nombre_completo, rol, departamento, sap_owner_code, sap_branch_code)
                        VALUES (:usr, :cla, :nom, :rol, :dep, :owner, :branch);
                    """), {
                        "usr": nuevo_usuario, 
                        "cla": generate_password_hash('Sistemas2026*'), 
                        "nom": nombre_visible, 
                        "rol": rol_seleccionado,
                        "dep": depto_seleccionado, 
                        "owner": sap_owner, 
                        "branch": sap_branch  # Ahora guarda el nombre completo
                    })
                    session.commit()
                st.success(f"✅ ¡Usuario '{nuevo_usuario}' creado exitosamente!")
                st.rerun()
            except Exception as e:
                st.error(f"El usuario ya existe o hubo un problema en PostgreSQL: {e}")

# ----------------- PESTAÑA 2: CONSULTAR, EDITAR Y ELIMINAR -----------------
with tab_editar:
    st.subheader("Usuarios Registrados en el Sistema")
    df_usuarios = conn.query("SELECT id, usuario, nombre_completo, rol, departamento, sap_branch_code, sap_owner_code FROM app.usuarios_portal ORDER BY usuario ASC", ttl=0)
    
    st.dataframe(df_usuarios[['usuario', 'nombre_completo', 'departamento', 'rol', 'sap_branch_code', 'sap_owner_code']], use_container_width=True, hide_index=True)
    
    st.markdown("---")
    st.subheader("✏️ Modificar Parámetros de un Usuario")
    
    usuario_a_editar = st.selectbox("Seleccione el usuario que desea gestionar:", [""] + df_usuarios['usuario'].tolist())
    
    if usuario_a_editar:
        datos_usr = df_usuarios[df_usuarios['usuario'] == usuario_a_editar].iloc[0]
        
        # --- FORMULARIO DE EDICIÓN ---
        with st.form("form_editar_usuario"):
            e_col1, e_col2 = st.columns(2)
            with e_col1:
                e_nombre = st.text_input("Nombre Completo", value=datos_usr['nombre_completo'])
                lista_roles = ["comercial", "admin_punto", "gerente_comercial", "gerente", "admin"]
                e_rol = st.selectbox("Rol", lista_roles, index=lista_roles.index(datos_usr['rol']) if datos_usr['rol'] in lista_roles else 0)
            
            with e_col2:
                lista_deptos = ["VENTAS", "COMPRAS", "CONTABILIDAD", "GERENCIA", "SISTEMAS"]
                e_depto = st.selectbox("Departamento", lista_deptos, index=lista_deptos.index(datos_usr['departamento']) if datos_usr['departamento'] in lista_deptos else 0)
                
                # ✅ Ahora mostramos el nombre completo del almacén
                val_b = str(datos_usr['sap_branch_code']) if pd.notna(datos_usr['sap_branch_code']) else ""
                e_branch = st.text_input("Almacén / Sede (ej: EJECUTIVOS COMERCIALES, CENTRO 1)", value=val_b).strip()
                
                val_o = int(datos_usr['sap_owner_code']) if pd.notna(datos_usr['sap_owner_code']) else 0
                e_owner = st.number_input("Código Comercial SAP (Owner Code)", value=val_o)
            
            if st.form_submit_button("💾 Guardar Cambios Realizados"):
                final_owner = e_owner if e_owner != 0 else None
                final_branch = e_branch if e_branch != "" else None
                
                with conn.session as session:
                    session.execute(text("""
                        UPDATE app.usuarios_portal 
                        SET nombre_completo = :nom, rol = :rol, departamento = :dep, sap_owner_code = :owner, sap_branch_code = :branch
                        WHERE usuario = :usr
                    """), {
                        "nom": e_nombre, 
                        "rol": e_rol, 
                        "dep": e_depto, 
                        "owner": final_owner, 
                        "branch": final_branch,
                        "usr": usuario_a_editar
                    })
                    session.commit()
                st.success("✅ ¡Usuario modificado correctamente!")
                st.rerun()

        # ==========================================
        # ✅ NUEVA SECCIÓN: ELIMINAR USUARIO
        # ==========================================
        st.markdown("---")
        st.subheader(f"🗑️ Eliminar Usuario: **{usuario_a_editar}**")
        st.warning("⚠️ **ADVERTENCIA CRÍTICA:** Esta acción es **irreversible**. El usuario perderá todo acceso al sistema inmediatamente.")
        
        col_del1, col_del2 = st.columns([3, 1])
        with col_del1:
            confirm_delete = st.checkbox("✅ Confirmo que deseo eliminar este usuario permanentemente.", key=f"chk_del_{usuario_a_editar}")
        
        with col_del2:
            if st.button("🚨 ELIMINAR", type="primary", key=f"btn_del_{usuario_a_editar}"):
                if confirm_delete:
                    try:
                        with conn.session as session:
                            session.execute(text("DELETE FROM app.usuarios_portal WHERE usuario = :usr"), {"usr": usuario_a_editar})
                            session.commit()
                        st.success(f"✅ El usuario '{usuario_a_editar}' ha sido eliminado exitosamente.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ Error al eliminar el usuario: {e}")
                else:
                    st.warning("⚠️ Debes marcar la casilla de confirmación para poder eliminar.")

# ----------------- PESTAÑA 3: RESETEAR CONTRASEÑAS -----------------
with tab_reset:
    st.subheader("🔐 Restablecer Credenciales Olvidadas")
    r_usr = st.selectbox("Seleccione la cuenta a restablecer:", df_usuarios['usuario'].tolist())
    
    if st.button("🚨 Resetear a Contraseña de Fábrica", type="primary"):
        with conn.session as session:
            session.execute(text("UPDATE app.usuarios_portal SET clave = :nueva WHERE usuario = :usr"), 
                            {"nueva": generate_password_hash('Sistemas2026*'), "usr": r_usr})
            session.commit()
        st.success(f"La contraseña de **{r_usr}** ha vuelto a ser 'Sistemas2026*'.")