import streamlit as st
from pathlib import Path
from src.conexion_db import ejecutar_sincronizacion_desde_sap
from werkzeug.security import check_password_hash

st.set_page_config(
    page_title="Portal Analítico SAP",
    layout="wide",
    page_icon=":material/business:"
)

# ============================================================
# RUTA DEL LOGO DE LA EMPRESA
# ============================================================
RUTA_LOGO = Path(__file__).parent / "logo_empresa.png"

# ============================================================
# 1. CONTROL DE AUTENTICACIÓN
# ============================================================
if "autenticado" not in st.session_state:
    st.session_state["autenticado"] = False

if not st.session_state["autenticado"]:
    if RUTA_LOGO.exists():
        col_izq, col_centro, col_der = st.columns([1, 1, 1])
        with col_centro:
            st.image(str(RUTA_LOGO), width=250)

    st.title("Acceso Portal Corporativo")

    with st.form("login_form"):
        usuario_input = st.text_input("Usuario").strip().lower()
        clave_input = st.text_input("Contraseña", type="password")

        if st.form_submit_button("Iniciar sesión", icon=":material/login:"):
            conn_db = st.connection("postgresql", type="sql", url=st.secrets["postgres"]["url"])
            user_df = conn_db.query(
                "SELECT * FROM app.usuarios_portal WHERE usuario = :usr AND activo = TRUE",
                params={"usr": usuario_input}
            )

            if not user_df.empty and check_password_hash(user_df.iloc[0]["clave"], clave_input):
                st.session_state["autenticado"] = True
                st.session_state["usuario_actual"] = user_df.iloc[0]["usuario"]
                st.session_state["nombre_completo"] = user_df.iloc[0]["nombre_completo"]
                st.session_state["rol_actual"] = user_df.iloc[0]["rol"]
                st.session_state["sap_owner_code"] = user_df.iloc[0]["sap_owner_code"]
                st.session_state["sap_branch_code"] = user_df.iloc[0]["sap_branch_code"]
                st.session_state["departamento"] = user_df.iloc[0]["departamento"]
                st.session_state["cambio_forzado"] = bool(user_df.iloc[0].get("cambio_forzado", False))
                st.rerun()
            else:
                st.error("❌ Credenciales inválidas o usuario bloqueado. Intenta de nuevo.")
    st.stop()

# ============================================================
# 🔐 BLOQUEO DE ACCESO: cambio de clave obligatorio
# ============================================================
if st.session_state.get("cambio_forzado", False):
    pag_cambiar_clave = st.Page(
        "views/cambiar_clave.py",
        title="Cambio Obligatorio de Contraseña",
        icon=":material/lock:",
        default=True
    )
    navegacion = st.navigation([pag_cambiar_clave])
    navegacion.run()
    st.stop()

# ============================================================
# 2. DECLARACIÓN DE PÁGINAS
# ============================================================
pag_inicio = st.Page("views/inicio.py", title="Inicio", icon=":material/home:", default=True)
pag_ventas_meta = st.Page("views/ventas.py", title="Ventas vs Metas", icon=":material/trending_up:")
pag_remisiones = st.Page("views/ventas_remisiones.py", title="Remisiones Abiertas", icon=":material/local_shipping:")
pag_cotizaciones = st.Page("views/cotizaciones_abiertas.py", title="Cotizaciones Abiertas", icon=":material/request_quote:")
pag_ordenes = st.Page("views/ordenes_venta_abiertas.py", title="Órdenes de Venta", icon=":material/shopping_cart:")
pag_compras = st.Page("views/solicitudes_compras.py", title="Solicitudes de Compra", icon=":material/inventory:")
pag_traslados = st.Page("views/solicitudes_traslados.py", title="Solicitudes de Traslado", icon=":material/swap_horiz:")
pag_facturas = st.Page("views/facturas_reserva.py", title="Facturas de Reserva", icon=":material/receipt_long:")
pag_notas = st.Page("views/notas_credito.py", title="Notas Crédito Abiertas", icon=":material/credit_score:")
pag_usuarios = st.Page("views/admin_usuarios.py", title="Gestión de Usuarios", icon=":material/group:")
pag_rotacion = st.Page("views/rotacion_articulos.py", title="Rotación Artículos", icon=":material/inventory_2:")

# 🎁 Módulo Bonificaciones
pag_bono_detalle = st.Page("views/bonificaciones_detalle.py", title="Detalle Bono", icon=":material/redeem:")
pag_bono_conciliacion = st.Page("views/bonificaciones_conciliacion.py", title="Conciliación Bono", icon=":material/fact_check:")
pag_bono_variables = st.Page("views/bono_variables.py", title="Variables del Bono", icon=":material/tune:")
pag_contabilidad = st.Page("views/contabilidad_exentas.py", title="Auditoría Exentas", icon=":material/account_balance:")

# ============================================================
# 3. MENÚ DINÁMICO POR DEPARTAMENTO Y ROL
# ============================================================
depto_usuario = st.session_state["departamento"]

estructura_menu = {"": [pag_inicio]}

if depto_usuario in ["VENTAS", "SISTEMAS", "GERENCIA"]:
    estructura_menu["Ventas"] = [pag_ventas_meta]
    estructura_menu["Partidas Abiertas"] = [
        pag_remisiones,
        pag_cotizaciones,
        pag_ordenes,
        pag_traslados,
        pag_facturas,
        pag_notas,
        pag_compras,
    ]

# 🔐 Sección "Compras" gateada por ROL
ROLES_CON_ACCESO_COMPRAS = ["compras", "gerente", "admin"]
if st.session_state["rol_actual"] in ROLES_CON_ACCESO_COMPRAS:
    estructura_menu["Compras"] = [pag_rotacion]

# 🎁 Sección Bonificaciones:
# - Detalle y Conciliación: admin, gerente, gerente_comercial
# - Variables del Bono: SOLO admin y gerente (gerente_comercial no la ve)
ROLES_BONIFICACIONES = ["admin", "gerente", "gerente_comercial"]
ROLES_EDIT_BONO = ["admin", "gerente"]
if st.session_state["rol_actual"] in ROLES_BONIFICACIONES:
    paginas_bono = [pag_bono_detalle, pag_bono_conciliacion]
    if st.session_state["rol_actual"] in ROLES_EDIT_BONO:
        paginas_bono.append(pag_bono_variables)
    estructura_menu["Bonificaciones"] = paginas_bono

# 🏦 Sección Contabilidad: SOLO admin, gerente y contabilidad
ROLES_CONTABILIDAD = ["admin", "gerente", "contabilidad"]
if st.session_state["rol_actual"] in ROLES_CONTABILIDAD:
    estructura_menu["Contabilidad"] = [pag_contabilidad]

if st.session_state["rol_actual"] == "admin":
    estructura_menu["Administración"] = [pag_usuarios]

navegacion = st.navigation(estructura_menu)

# ============================================================
# 4. EJECUCIÓN DE LA PÁGINA ACTUAL
# ============================================================
navegacion.run()

# ============================================================
# 5. SIDEBAR: Info Usuario, Sincronización y Cierre de Sesión
# ============================================================
with st.sidebar:
    st.markdown("---")
    st.write(f"👤 {st.session_state['nombre_completo']}")
    st.caption(f"Depto: {st.session_state['departamento']} | Rol: {st.session_state['rol_actual'].upper()}")

    if st.session_state.get("cambio_forzado", False):
        st.warning("⚠️ Debes cambiar tu contraseña")

    st.markdown("---")

    if st.session_state["rol_actual"] in ["admin", "gerente"]:
        st.markdown("### 🛠️ Sincronización")
        if st.button(
            "Forzar Sincronización SAP",
            icon=":material/sync:",
            type="primary",
            use_container_width=True
        ):
            with st.spinner("Sincronizando con SAP..."):
                if ejecutar_sincronizacion_desde_sap():
                    st.cache_data.clear()
                    st.sidebar.success("¡Base de datos actualizada!")
                    st.rerun()
        st.markdown("---")

    if st.button("🚪 Cerrar Sesión", icon=":material/logout:", use_container_width=True):
        st.session_state.clear()
        st.rerun()