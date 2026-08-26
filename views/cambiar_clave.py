import streamlit as st
from sqlalchemy import text
from src.conexion_db import conn
from werkzeug.security import check_password_hash, generate_password_hash

# ============================================================
# CONFIGURACIÓN DE PÁGINA
# ============================================================
st.set_page_config(
    page_title="Cambio Obligatorio de Contraseña",
    layout="centered",
    page_icon=":material/lock:"
)

# ============================================================
# VALIDACIÓN DE SEGURIDAD
# ============================================================
# Si el usuario no está autenticado o no tiene cambio_forzado, redirigir
if not st.session_state.get("autenticado", False):
    st.error("⛔ Debes iniciar sesión primero.")
    st.stop()

if not st.session_state.get("cambio_forzado", False):
    st.info("✅ Tu contraseña está al día. Serás redirigido al inicio.")
    st.stop()

# ============================================================
# INTERFAZ DE CAMBIO OBLIGATORIO
# ============================================================
st.markdown("### 🔐 Cambio Obligatorio de Contraseña")
st.markdown("---")
st.warning(
    f"**Hola {st.session_state.get('nombre_completo', 'Usuario')},**\n\n"
    "Por seguridad, debes cambiar tu contraseña antes de acceder al sistema. "
    "Esta es una medida de protección para tu cuenta."
)
st.markdown("---")

# ============================================================
# FORMULARIO DE CAMBIO DE CONTRASEÑA
# ============================================================
with st.form("form_cambio_obligatorio", clear_on_submit=False):
    clave_actual = st.text_input("🔑 Contraseña Actual", type="password")
    
    col1, col2 = st.columns(2)
    with col1:
        clave_nueva = st.text_input("🆕 Nueva Contraseña", type="password")
    with col2:
        clave_nueva_conf = st.text_input("✅ Confirmar Nueva Contraseña", type="password")
    
    # Requisitos de seguridad
    st.markdown("**Requisitos de la nueva contraseña:**")
    st.markdown("- Mínimo 6 caracteres")
    st.markdown("- No puede ser igual a la actual")
    
    submitted = st.form_submit_button("🔄 Cambiar Contraseña", type="primary", use_container_width=True)
    
    if submitted:
        usr_actual = st.session_state['usuario_actual']
        
        # Validación 1: Verificar contraseña actual
        res = conn.query(
            "SELECT clave FROM app.usuarios_portal WHERE usuario = :usr", 
            params={"usr": usr_actual}
        )
        
        if res.empty or not check_password_hash(res.iloc[0]['clave'], clave_actual):
            st.error("❌ La contraseña actual es incorrecta.")
        elif clave_nueva != clave_nueva_conf:
            st.warning("⚠️ Las nuevas contraseñas no coinciden.")
        elif len(clave_nueva) < 6:
            st.warning("️ La nueva contraseña debe tener al menos 6 caracteres.")
        elif clave_actual == clave_nueva:
            st.warning("⚠️ La nueva contraseña no puede ser igual a la actual.")
        else:
            # ✅ TODO CORRECTO: Actualizar contraseña y marcar cambio_forzado = FALSE
            try:
                with conn.session as session:
                    session.execute(
                        text("""
                            UPDATE app.usuarios_portal 
                            SET clave = :nueva, cambio_forzado = FALSE 
                            WHERE usuario = :usr
                        """),
                        {"nueva": generate_password_hash(clave_nueva), "usr": usr_actual}
                    )
                    session.commit()
                
                # Actualizar session_state para liberar el acceso
                st.session_state["cambio_forzado"] = False
                
                st.success("✅ ¡Contraseña actualizada con éxito! Serás redirigido al inicio.")
                
                # Recargar la página principal para que tome el nuevo estado
                st.cache_data.clear()
                st.rerun()
                
            except Exception as e:
                st.error(f"❌ Error al actualizar la contraseña: {e}")

# ============================================================
# CIERRE DE SESIÓN (opcional, por si el usuario quiere salir)
# ============================================================
st.markdown("---")
if st.button(" Cerrar Sesión", icon=":material/logout:", use_container_width=True):
    st.session_state.clear()
    st.rerun()