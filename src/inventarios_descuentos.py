"""
MÓDULO INVENTARIOS — Gestión y cálculo de deducciones por inventarios sobre bonificaciones.
Esquema: inventarios
Tablas: inventarios.descuentos_bono, inventarios.auditoria
Integración directa con rrhh.colaboradores y app.almacenes.
"""

from __future__ import annotations
import io
import json
from datetime import date, datetime
import pandas as pd
from sqlalchemy import text
from src.conexion_db import conn

# Roles autorizados para ingresar y operar en el módulo
ROLES_INVENTARIOS_CARGUE = ["admin", "gerente", "inventarios", "compras", "admin_punto"]
ROLES_INVENTARIOS_ADMIN = ["admin", "gerente"]

# Conceptos estándar de descuento por inventarios
CONCEPTOS_DESCUENTO = [
    "Faltante de inventario",
    "Pérdida de material",
    "Avería / Daño",
    "Hurto / Robo",
    "Descuadre en conteo físico",
    "Ajuste operativo",
    "Otro"
]

def bootstrap_inventarios():
    """
    Crea el esquema inventarios y las tablas maestras si no existen.
    Es una función idempotente y segura para ejecutar al iniciar la vista.
    """
    with conn.session as con:
        try:
            con.execute(text("CREATE SCHEMA IF NOT EXISTS inventarios"))
            
            # Tabla de deducciones aplicadas a colaboradores
            con.execute(text("""
                CREATE TABLE IF NOT EXISTS inventarios.descuentos_bono (
                    id                SERIAL PRIMARY KEY,
                    periodo_mes       DATE NOT NULL,
                    colaborador_id    INT NOT NULL REFERENCES rrhh.colaboradores(id),
                    almacen_codigo    TEXT NOT NULL,
                    monto             NUMERIC(14,2) NOT NULL CHECK (monto > 0),
                    concepto          TEXT NOT NULL,
                    observacion       TEXT NOT NULL,
                    documento_soporte TEXT,
                    cargado_por       TEXT NOT NULL,
                    creado_en         TIMESTAMPTZ DEFAULT now(),
                    actualizado_en    TIMESTAMPTZ DEFAULT now()
                )
            """))

            # Tabla de auditoría detallada
            con.execute(text("""
                CREATE TABLE IF NOT EXISTS inventarios.auditoria (
                    id             SERIAL PRIMARY KEY,
                    descuento_id   INT,
                    accion         TEXT NOT NULL,
                    antes          JSONB,
                    despues        JSONB,
                    usuario        TEXT NOT NULL,
                    rol            TEXT,
                    nombre_usuario TEXT,
                    fecha          TIMESTAMPTZ DEFAULT now()
                )
            """))

            # Índices para optimizar reportes y búsquedas
            con.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_inv_desc_periodo 
                ON inventarios.descuentos_bono(periodo_mes)
            """))
            con.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_inv_desc_colab 
                ON inventarios.descuentos_bono(colaborador_id)
            """))
            con.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_inv_desc_almacen 
                ON inventarios.descuentos_bono(almacen_codigo)
            """))
            
            con.commit()
            return True
        except Exception as e:
            con.rollback()
            raise e


def auditar_inventario(con, descuento_id, accion, antes, despues, usuario, rol, nombre_usuario):
    """Inserta registro en inventarios.auditoria con trazabilidad completa."""
    con.execute(text("""
        INSERT INTO inventarios.auditoria
            (descuento_id, accion, antes, despues, usuario, rol, nombre_usuario)
        VALUES (:did, :a, CAST(:b AS jsonb), CAST(:d AS jsonb), :u, :rol, :nom)
    """), {
        "did": descuento_id,
        "a": accion,
        "b": json.dumps(antes, default=str) if antes is not None else None,
        "d": json.dumps(despues, default=str) if despues is not None else None,
        "u": usuario,
        "rol": rol,
        "nom": nombre_usuario
    })


def cargar_almacenes_activos():
    """Retorna DataFrame de almacenes activos registrados."""
    try:
        df = conn.query("""
            SELECT codigo, COALESCE(nombre_serie, nombre_whs, codigo) AS nombre
            FROM app.almacenes
            WHERE activo = TRUE
            ORDER BY nombre
        """, ttl=10)
        if not df.empty:
            return df
    except Exception:
        pass

    # Catálogo fallback canónico si aún no existe la tabla
    alms = [
        ("ALM128", "NORTE128"), ("ALM134", "PUNTO 134"), ("ALM170", "PUNTO170"),
        ("7AGOS", "7 DE AGOSTO"), ("AV19", "AVENIDA 19"), ("ARME", "ARMENIA"),
        ("CHIA", "CHIA"), ("EJECOM", "EJECUTIVOS COMERCIALES"),
        ("Q1", "CENTRO 1"), ("Q3", "CENTRO 3"), ("Q5", "CENTRO 5"), ("Q6", "CENTRO 6"),
        ("VILL", "VILLAVICENCIO"), ("GIRAR", "GIRARDOT"),
    ]
    return pd.DataFrame(alms, columns=["codigo", "nombre"])


def cargar_colaboradores_disponibles(almacen_codigo=None):
    """
    Carga colaboradores activos de rrhh.colaboradores con su almacén y cargo actual.
    Si se proporciona almacen_codigo, filtra por ese almacén.
    """
    q = """
        SELECT c.id, c.cedula, c.nombre,
               COALESCE(c.almacen_actual, a.almacen) AS almacen_codigo,
               COALESCE(alm.nombre_serie, alm.nombre_whs, c.almacen_actual, a.almacen, 'Sin asignar') AS almacen_nombre,
               COALESCE(c.cargo_actual, a.cargo, 'Sin cargo') AS cargo
        FROM rrhh.colaboradores c
        LEFT JOIN LATERAL (
            SELECT almacen, cargo 
            FROM rrhh.asignaciones
            WHERE colaborador_id = c.id AND fecha_hasta IS NULL
            ORDER BY fecha_desde DESC LIMIT 1
        ) a ON TRUE
        LEFT JOIN app.almacenes alm ON alm.codigo = COALESCE(c.almacen_actual, a.almacen)
        WHERE c.activo = TRUE
    """
    params = {}
    if almacen_codigo:
        q += " AND (c.almacen_actual = :alm OR a.almacen = :alm)"
        params["alm"] = almacen_codigo

    q += " ORDER BY c.nombre"
    return conn.query(q, params=params, ttl=0)


def cargar_descuentos_periodo(periodo_mes, almacen_codigo=None):
    """
    Carga todos los descuentos registrados para un periodo mensual.
    Opcionalmente filtra por código de almacén.
    """
    q = """
        SELECT d.id, d.periodo_mes, d.colaborador_id,
               c.cedula, c.nombre AS colaborador_nombre,
               COALESCE(c.cargo_actual, 'Sin cargo') AS cargo,
               d.almacen_codigo,
               COALESCE(alm.nombre_serie, alm.nombre_whs, d.almacen_codigo) AS almacen_nombre,
               d.monto, d.concepto, d.observacion, d.documento_soporte,
               d.cargado_por, d.creado_en, d.actualizado_en
        FROM inventarios.descuentos_bono d
        JOIN rrhh.colaboradores c ON c.id = d.colaborador_id
        LEFT JOIN app.almacenes alm ON alm.codigo = d.almacen_codigo
        WHERE d.periodo_mes = :mes
    """
    params = {"mes": periodo_mes}
    if almacen_codigo:
        q += " AND d.almacen_codigo = :alm"
        params["alm"] = almacen_codigo

    q += " ORDER BY c.nombre, d.id"
    df = conn.query(q, params=params, ttl=0)
    if not df.empty:
        df["monto"] = df["monto"].astype(float)
    return df


def insertar_descuento_individual(con, periodo_mes, colaborador_id, almacen_codigo, monto, concepto, observacion, documento_soporte, usuario, rol, nombre_usuario):
    """Inserta una deducción y registra la auditoría correspondiente."""
    nid = con.execute(text("""
        INSERT INTO inventarios.descuentos_bono
            (periodo_mes, colaborador_id, almacen_codigo, monto, concepto, observacion, documento_soporte, cargado_por)
        VALUES
            (:mes, :cid, :alm, :mon, :con, :obs, :doc, :usr)
        RETURNING id
    """), {
        "mes": periodo_mes,
        "cid": int(colaborador_id),
        "alm": str(almacen_codigo),
        "mon": float(monto),
        "con": str(concepto).strip(),
        "obs": str(observacion).strip(),
        "doc": str(documento_soporte).strip() if documento_soporte else None,
        "usr": usuario
    }).scalar()

    auditar_inventario(
        con=con,
        descuento_id=nid,
        accion="CREATE",
        antes=None,
        despues={
            "periodo_mes": str(periodo_mes),
            "colaborador_id": colaborador_id,
            "almacen_codigo": almacen_codigo,
            "monto": float(monto),
            "concepto": concepto,
            "observacion": observacion,
            "documento_soporte": documento_soporte
        },
        usuario=usuario,
        rol=rol,
        nombre_usuario=nombre_usuario
    )
    return nid


def actualizar_descuento_individual(con, descuento_id, monto, concepto, observacion, documento_soporte, usuario, rol, nombre_usuario):
    """Actualiza monto, concepto u observaciones y genera registro de auditoría."""
    row = con.execute(text("""
        SELECT * FROM inventarios.descuentos_bono WHERE id = :id
    """), {"id": int(descuento_id)}).mappings().first()

    if not row:
        raise ValueError(f"No existe el descuento con ID #{descuento_id}")

    antes = dict(row)

    con.execute(text("""
        UPDATE inventarios.descuentos_bono
        SET monto = :mon,
            concepto = :con,
            observacion = :obs,
            documento_soporte = :doc,
            actualizado_en = now()
        WHERE id = :id
    """), {
        "id": int(descuento_id),
        "mon": float(monto),
        "con": str(concepto).strip(),
        "obs": str(observacion).strip(),
        "doc": str(documento_soporte).strip() if documento_soporte else None
    })

    despues = {
        "id": descuento_id,
        "periodo_mes": str(antes["periodo_mes"]),
        "colaborador_id": antes["colaborador_id"],
        "almacen_codigo": antes["almacen_codigo"],
        "monto": float(monto),
        "concepto": concepto,
        "observacion": observacion,
        "documento_soporte": documento_soporte
    }

    auditar_inventario(
        con=con,
        descuento_id=descuento_id,
        accion="UPDATE",
        antes=antes,
        despues=despues,
        usuario=usuario,
        rol=rol,
        nombre_usuario=nombre_usuario
    )


def eliminar_descuento_individual(con, descuento_id, motivo_anulacion, usuario, rol, nombre_usuario):
    """Anula/elimina un descuento registrando en la auditoría el motivo."""
    row = con.execute(text("""
        SELECT * FROM inventarios.descuentos_bono WHERE id = :id
    """), {"id": int(descuento_id)}).mappings().first()

    if not row:
        raise ValueError(f"No existe el descuento con ID #{descuento_id}")

    antes = dict(row)

    con.execute(text("""
        DELETE FROM inventarios.descuentos_bono WHERE id = :id
    """), {"id": int(descuento_id)})

    auditar_inventario(
        con=con,
        descuento_id=descuento_id,
        accion="DELETE",
        antes=antes,
        despues={"motivo_anulacion": motivo_anulacion},
        usuario=usuario,
        rol=rol,
        nombre_usuario=nombre_usuario
    )


def cargar_historial_auditoria(periodo_mes=None):
    """Consulta la tabla de auditoría con detalles del usuario."""
    q = """
        SELECT a.id, a.fecha, a.usuario,
               COALESCE(a.nombre_usuario, a.usuario) AS nombre_usuario,
               COALESCE(a.rol, '—') AS rol,
               a.accion, a.descuento_id, a.antes, a.despues
        FROM inventarios.auditoria a
    """
    params = {}
    if periodo_mes:
        q += " WHERE (a.despues->>'periodo_mes' = :mes OR a.antes->>'periodo_mes' = :mes)"
        params["mes"] = str(periodo_mes)

    q += " ORDER BY a.fecha DESC LIMIT 300"
    return conn.query(q, params=params, ttl=0)