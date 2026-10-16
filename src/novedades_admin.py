"""
MÓDULO BONIFICACIONES — Novedades Administrativas / Descuentos Gerenciales en Porcentaje (%).
Esquema / Tabla: app.novedades_administrativas, app.novedades_administrativas_auditoria
Acceso exclusivo: Gerencia y Administración.
"""

from __future__ import annotations
import io
import json
from datetime import date, datetime
import pandas as pd
from sqlalchemy import text
from src.conexion_db import conn

# Roles autorizados: Solo Gerencia y Administración
ROLES_NOVEDADES_ADMIN = ["admin", "gerente"]

# Conceptos estándar de descuento administrativo / gerencial
CONCEPTOS_ADMIN = [
    "Sanción administrativa",
    "Incumplimiento de procesos / políticas",
    "Llamado de atención / Memorando",
    "Ajuste disciplinario gerencial",
    "Decisión de Gerencia",
    "Otro (especificar en observación)"
]

def bootstrap_novedades_admin():
    """
    Crea las tablas app.novedades_administrativas y app.novedades_administrativas_auditoria
    de forma transparente e idempotente.
    """
    with conn.session as con:
        try:
            # Tabla principal de novedades en porcentaje
            con.execute(text("""
                CREATE TABLE IF NOT EXISTS app.novedades_administrativas (
                    id                   SERIAL PRIMARY KEY,
                    periodo_mes          DATE NOT NULL,
                    colaborador_id       INT NOT NULL REFERENCES rrhh.colaboradores(id),
                    almacen_codigo       TEXT NOT NULL,
                    porcentaje_descuento NUMERIC(6,2) NOT NULL CHECK (porcentaje_descuento > 0 AND porcentaje_descuento <= 100),
                    concepto             TEXT NOT NULL,
                    observacion          TEXT NOT NULL,
                    documento_soporte    TEXT,
                    cargado_por          TEXT NOT NULL,
                    creado_en            TIMESTAMPTZ DEFAULT now(),
                    actualizado_en       TIMESTAMPTZ DEFAULT now()
                )
            """))

            # Tabla de auditoría
            con.execute(text("""
                CREATE TABLE IF NOT EXISTS app.novedades_administrativas_auditoria (
                    id             SERIAL PRIMARY KEY,
                    novedad_id     INT,
                    accion         TEXT NOT NULL,
                    antes          JSONB,
                    despues        JSONB,
                    usuario        TEXT NOT NULL,
                    rol            TEXT,
                    nombre_usuario TEXT,
                    fecha          TIMESTAMPTZ DEFAULT now()
                )
            """))

            # Índices de optimización
            con.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_nov_adm_periodo 
                ON app.novedades_administrativas(periodo_mes)
            """))
            con.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_nov_adm_colab 
                ON app.novedades_administrativas(colaborador_id)
            """))
            con.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_nov_adm_almacen 
                ON app.novedades_administrativas(almacen_codigo)
            """))

            con.commit()
            return True
        except Exception as e:
            con.rollback()
            raise e


def auditar_novedad_admin(con, novedad_id, accion, antes, despues, usuario, rol, nombre_usuario):
    """Inserta registro de trazabilidad en app.novedades_administrativas_auditoria."""
    con.execute(text("""
        INSERT INTO app.novedades_administrativas_auditoria
            (novedad_id, accion, antes, despues, usuario, rol, nombre_usuario)
        VALUES (:nid, :a, CAST(:b AS jsonb), CAST(:d AS jsonb), :u, :rol, :nom)
    """), {
        "nid": novedad_id,
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


def cargar_novedades_admin_periodo(periodo_mes, almacen_codigo=None):
    """
    Carga todas las novedades gerenciales registradas para un periodo mensual.
    """
    q = """
        SELECT n.id, n.periodo_mes, n.colaborador_id,
               c.cedula, c.nombre AS colaborador_nombre,
               COALESCE(c.cargo_actual, 'Sin cargo') AS cargo,
               n.almacen_codigo,
               COALESCE(alm.nombre_serie, alm.nombre_whs, n.almacen_codigo) AS almacen_nombre,
               n.porcentaje_descuento, n.concepto, n.observacion, n.documento_soporte,
               n.cargado_por, n.creado_en, n.actualizado_en
        FROM app.novedades_administrativas n
        JOIN rrhh.colaboradores c ON c.id = n.colaborador_id
        LEFT JOIN app.almacenes alm ON alm.codigo = n.almacen_codigo
        WHERE n.periodo_mes = :mes
    """
    params = {"mes": periodo_mes}
    if almacen_codigo:
        q += " AND n.almacen_codigo = :alm"
        params["alm"] = almacen_codigo

    q += " ORDER BY c.nombre, n.id"
    df = conn.query(q, params=params, ttl=0)
    if not df.empty:
        df["porcentaje_descuento"] = df["porcentaje_descuento"].astype(float)
    return df


def insertar_novedad_admin(con, periodo_mes, colaborador_id, almacen_codigo, porcentaje, concepto, observacion, documento_soporte, usuario, rol, nombre_usuario):
    """Inserta una novedad en porcentaje y registra la auditoría."""
    nid = con.execute(text("""
        INSERT INTO app.novedades_administrativas
            (periodo_mes, colaborador_id, almacen_codigo, porcentaje_descuento, concepto, observacion, documento_soporte, cargado_por)
        VALUES
            (:mes, :cid, :alm, :pct, :con, :obs, :doc, :usr)
        RETURNING id
    """), {
        "mes": periodo_mes,
        "cid": int(colaborador_id),
        "alm": str(almacen_codigo),
        "pct": float(porcentaje),
        "con": str(concepto).strip(),
        "obs": str(observacion).strip(),
        "doc": str(documento_soporte).strip() if documento_soporte else None,
        "usr": usuario
    }).scalar()

    auditar_novedad_admin(
        con=con,
        novedad_id=nid,
        accion="CREATE",
        antes=None,
        despues={
            "periodo_mes": str(periodo_mes),
            "colaborador_id": colaborador_id,
            "almacen_codigo": almacen_codigo,
            "porcentaje_descuento": float(porcentaje),
            "concepto": concepto,
            "observacion": observacion,
            "documento_soporte": documento_soporte
        },
        usuario=usuario,
        rol=rol,
        nombre_usuario=nombre_usuario
    )
    return nid


def actualizar_novedad_admin(con, novedad_id, porcentaje, concepto, observacion, documento_soporte, usuario, rol, nombre_usuario):
    """Actualiza una novedad administrativa y audita el cambio."""
    row = con.execute(text("""
        SELECT * FROM app.novedades_administrativas WHERE id = :id
    """), {"id": int(novedad_id)}).mappings().first()

    if not row:
        raise ValueError(f"No existe la novedad con ID #{novedad_id}")

    antes = dict(row)

    con.execute(text("""
        UPDATE app.novedades_administrativas
        SET porcentaje_descuento = :pct,
            concepto = :con,
            observacion = :obs,
            documento_soporte = :doc,
            actualizado_en = now()
        WHERE id = :id
    """), {
        "id": int(novedad_id),
        "pct": float(porcentaje),
        "con": str(concepto).strip(),
        "obs": str(observacion).strip(),
        "doc": str(documento_soporte).strip() if documento_soporte else None
    })

    despues = {
        "id": novedad_id,
        "periodo_mes": str(antes["periodo_mes"]),
        "colaborador_id": antes["colaborador_id"],
        "almacen_codigo": antes["almacen_codigo"],
        "porcentaje_descuento": float(porcentaje),
        "concepto": concepto,
        "observacion": observacion,
        "documento_soporte": documento_soporte
    }

    auditar_novedad_admin(
        con=con,
        novedad_id=novedad_id,
        accion="UPDATE",
        antes=antes,
        despues=despues,
        usuario=usuario,
        rol=rol,
        nombre_usuario=nombre_usuario
    )


def eliminar_novedad_admin(con, novedad_id, motivo_anulacion, usuario, rol, nombre_usuario):
    """Anula/elimina una novedad registrando el motivo en auditoría."""
    row = con.execute(text("""
        SELECT * FROM app.novedades_administrativas WHERE id = :id
    """), {"id": int(novedad_id)}).mappings().first()

    if not row:
        raise ValueError(f"No existe la novedad con ID #{novedad_id}")

    antes = dict(row)

    con.execute(text("""
        DELETE FROM app.novedades_administrativas WHERE id = :id
    """), {"id": int(novedad_id)})

    auditar_novedad_admin(
        con=con,
        novedad_id=novedad_id,
        accion="DELETE",
        antes=antes,
        despues={"motivo_anulacion": motivo_anulacion},
        usuario=usuario,
        rol=rol,
        nombre_usuario=nombre_usuario
    )


def cargar_historial_auditoria_admin(periodo_mes=None):
    """
    Consulta la auditoría con formato estructurado.
    """
    q = """
        SELECT a.id, a.fecha, a.usuario,
               COALESCE(a.nombre_usuario, a.usuario) AS nombre_usuario,
               COALESCE(a.rol, '—') AS rol,
               a.accion, a.novedad_id,
               a.antes, a.despues
        FROM app.novedades_administrativas_auditoria a
    """
    params = {}
    if periodo_mes:
        q += " WHERE (a.despues->>'periodo_mes' = :mes OR a.antes->>'periodo_mes' = :mes)"
        params["mes"] = str(periodo_mes)

    q += " ORDER BY a.fecha DESC LIMIT 300"
    return conn.query(q, params=params, ttl=0)