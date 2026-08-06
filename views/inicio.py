import streamlit as st
from sqlalchemy import text
from src.conexion_db import conn
from werkzeug.security import check_password_hash, generate_password_hash

st.title(f"🏢 ¡Bienvenido al Portal Corporativo, {st.session_state.get('nombre_completo')}!")
st.markdown("---")

col_info, col_clave = st.columns([2, 1])

with col_info:
    st.write(f"Has ingresado con el departamento: **{st.session_state.get('departamento')}**")
    st.write(f"Tu Perfil de seguridad es: **{st.session_state.get('rol_actual').upper()}**")
    st.info("Utilice el menú de la izquierda para navegar entre los módulos autorizados para su perfil.")

with col_clave:
    st.markdown("### 🔐 Cambiar mi Contraseña")
    with st.form("form_cambio_clave", clear_on_submit=True):
        clave_actual = st.text_input("Contraseña Actual", type="password")
        clave_nueva = st.text_input("Nueva Contraseña", type="password")
        clave_nueva_conf = st.text_input("Confirmar Nueva Contraseña", type="password")
        
        if st.form_submit_button("Actualizar Credenciales"):
            usr_actual = st.session_state['usuario_actual']
            res = conn.query("SELECT clave FROM app.usuarios_portal WHERE usuario = :usr", params={"usr": usr_actual})
            
            if res.empty or not check_password_hash(res.iloc[0]['clave'], clave_actual):
                st.error("❌ La contraseña actual es incorrecta.")
            elif clave_nueva != clave_nueva_conf:
                st.warning("⚠️ Las nuevas contraseñas no coinciden.")
            elif len(clave_nueva) < 4:
                st.warning("⚠️ La nueva contraseña debe tener al menos 4 caracteres.")
            else:
                # Guardar el NUEVO hash
                with conn.session as session:
                    session.execute(text("UPDATE app.usuarios_portal SET clave = :nueva WHERE usuario = :usr"),
                                    {"nueva": generate_password_hash(clave_nueva), "usr": usr_actual})
                    session.commit()
                st.success("¡Contraseña actualizada con éxito!")