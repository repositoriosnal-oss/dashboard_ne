from __future__ import annotations

import json
import logging

import streamlit as st
from sqlalchemy import text

from src.conexion_db import conn
from src.rrhh_dist import TOL as TOL_PCT   # misma tolerancia que el motor de distribución

log = logging.getLogger(__name__)

# Roles con acceso al módulo (centralizado aquí; app.py y vistas importan esto).
ROLES_RRHH_ADMIN = ["admin", "gerente"]                        # ven TODO
ROLES_RRHH_CARGUE = ["admin", "gerente", "talento_humano"]     # pueden cargar novedades

# Convención del proyecto: fecha_hasta es el ÚLTIMO DÍA trabajado (inclusivo). Por eso la restricción
# de no-solape usa rangos cerrados '[]' y las consultas usan <= / >=.
_FIN_ABIERTO = "DATE '2100-12-31'"


def _asegurar_no_solape(con) -> str | None:
    """
    Garantiza la restricción EXCLUDE de no-solape con rangos inclusivos. Cada paso corre en un savepoint:
    un fallo (permisos, datos existentes que se solapan) deshace solo ese paso y NO aborta la transacción
    del bootstrap. Devuelve None si quedó bien, o un aviso para mostrar al usuario.
    """
    try:
        with con.begin_nested():
            con.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
    except Exception as e:  # noqa: BLE001
        return f"No se pudo habilitar btree_gist (¿permisos?): {e}. La no-solape de asignaciones queda solo en la app."
    try:
        with con.begin_nested():
            definicion = con.execute(text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'asignaciones_no_solape' AND conrelid = 'rrhh.asignaciones'::regclass")).scalar()
            if definicion and "'[]'" in definicion:
                return None                       # ya es la versión inclusiva
            con.execute(text("ALTER TABLE rrhh.asignaciones DROP CONSTRAINT IF EXISTS asignaciones_no_solape"))
            con.execute(text(
                "ALTER TABLE rrhh.asignaciones ADD CONSTRAINT asignaciones_no_solape "
                f"EXCLUDE USING gist (colaborador_id WITH =, "
                f"daterange(fecha_desde, COALESCE(fecha_hasta, {_FIN_ABIERTO}), '[]') WITH &&)"))
    except Exception as e:  # noqa: BLE001
        return ("No se pudo activar la restricción de no-solape con fechas inclusivas (se conserva la anterior). "
                "Probablemente hay asignaciones que comparten el último día: revísalas con "
                f"diagnosticar_solapes(). Detalle: {e}")
    return None


def bootstrap_rrhh() -> list:
    """Crea esquema rrhh + tablas maestras. Idempotente. Devuelve avisos no críticos."""
    avisos = []
    with conn.session as con:
        try:
            # Evita que dos procesos de Streamlit hagan DDL a la vez (bloqueos y deadlocks).
            con.execute(text("SELECT pg_advisory_xact_lock(hashtext('bootstrap_rrhh'))"))
            con.execute(text("CREATE SCHEMA IF NOT EXISTS rrhh"))

            # ---- Tabla canónica de almacenes ----
            con.execute(text("""
                CREATE TABLE IF NOT EXISTS app.almacenes (
                    codigo       TEXT PRIMARY KEY,          -- ALM170, Q6...
                    nombre_serie TEXT UNIQUE,               -- PUNTO 170
                    nombre_whs   TEXT UNIQUE,               -- nombre bodega SAP (WhsName)
                    activo       BOOLEAN DEFAULT TRUE
                )
            """))

            # ---- Maestro de colaboradores (clave universal = cédula) ----
            con.execute(text("""
                CREATE TABLE IF NOT EXISTS rrhh.colaboradores (
                    id             SERIAL PRIMARY KEY,
                    cedula         TEXT UNIQUE NOT NULL,
                    nombre         TEXT NOT NULL,
                    slp_code       INT,
                    owner_code     INT,
                    usuario_portal TEXT,
                    fecha_ingreso  DATE NOT NULL,
                    periodo_prueba_meses INT DEFAULT 2,
                    activo         BOOLEAN DEFAULT TRUE
                )
            """))
            # Columnas que usa el motor de distribución (idempotente; no toca las que ya existen).
            con.execute(text("ALTER TABLE rrhh.colaboradores ADD COLUMN IF NOT EXISTS fecha_inactividad DATE"))
            con.execute(text("ALTER TABLE rrhh.colaboradores ADD COLUMN IF NOT EXISTS "
                             "en_prueba BOOLEAN NOT NULL DEFAULT FALSE"))
            con.execute(text("ALTER TABLE rrhh.colaboradores ADD COLUMN IF NOT EXISTS prueba_inicio DATE"))
            con.execute(text("ALTER TABLE rrhh.colaboradores ADD COLUMN IF NOT EXISTS prueba_fin DATE"))

            # ---- Plantilla de distribución por almacén/cargo (suma = 100%) ----
            con.execute(text("""
                CREATE TABLE IF NOT EXISTS rrhh.distribucion_puestos (
                    id            SERIAL PRIMARY KEY,
                    almacen       TEXT NOT NULL REFERENCES app.almacenes(codigo),
                    cargo         TEXT NOT NULL,
                    pct           NUMERIC(6,3) NOT NULL CHECK (pct > 0 AND pct <= 100),  -- % POR PERSONA
                    cupos         INT NOT NULL DEFAULT 1 CHECK (cupos >= 1),             -- personas en el cargo
                    vigente_desde DATE NOT NULL,
                    vigente_hasta DATE,
                    UNIQUE (almacen, cargo, vigente_desde)
                )
            """))

            # ---- Asignaciones persona↔puesto con historial ----
            con.execute(text("""
                CREATE TABLE IF NOT EXISTS rrhh.asignaciones (
                    id             SERIAL PRIMARY KEY,
                    colaborador_id INT NOT NULL REFERENCES rrhh.colaboradores(id),
                    almacen        TEXT NOT NULL REFERENCES app.almacenes(codigo),
                    cargo          TEXT NOT NULL,
                    fecha_desde    DATE NOT NULL,
                    fecha_hasta    DATE,
                    CHECK (fecha_hasta IS NULL OR fecha_hasta >= fecha_desde)
                )
            """))

            # ---- Catálogo maestro de tipos de falta (grados 1/2/3 configurables) ----
            con.execute(text("""
                CREATE TABLE IF NOT EXISTS rrhh.tipos_falta (
                    id     SERIAL PRIMARY KEY,
                    nombre TEXT UNIQUE NOT NULL,
                    activo BOOLEAN DEFAULT TRUE
                )
            """))

            # ---- Novedades cargadas por talento humano (rangos de fecha crudos) ----
            con.execute(text("""
                CREATE TABLE IF NOT EXISTS rrhh.novedades (
                    id             SERIAL PRIMARY KEY,
                    colaborador_id INT NOT NULL REFERENCES rrhh.colaboradores(id),
                    tipo           TEXT NOT NULL CHECK (tipo IN
                                    ('FALTA','INCAPACIDAD','MEMORANDO','VACACIONES','PERIODO_PRUEBA')),
                    tipo_falta_id  INT REFERENCES rrhh.tipos_falta(id),
                    fecha_inicio   DATE NOT NULL,
                    fecha_fin      DATE NOT NULL,
                    soporte        TEXT,
                    cargado_por    TEXT,
                    creado_en      TIMESTAMPTZ DEFAULT now(),
                    CHECK (fecha_fin >= fecha_inicio),
                    CHECK ((tipo = 'FALTA') = (tipo_falta_id IS NOT NULL))
                )
            """))

            # ---- Auditoría propia de rrhh ----
            con.execute(text("""
                CREATE TABLE IF NOT EXISTS rrhh.rrhh_auditoria (
                    id          SERIAL PRIMARY KEY,
                    tabla       TEXT NOT NULL,
                    registro_id TEXT,
                    accion      TEXT NOT NULL,
                    antes       JSONB,
                    despues     JSONB,
                    usuario     TEXT,
                    fecha       TIMESTAMPTZ DEFAULT now()
                )
            """))

            # ---- Seed mínimo del catálogo (los 3 grados) ----
            for nombre in ("Falta Tipo 1", "Falta Tipo 2", "Falta Tipo 3"):
                con.execute(text("""
                    INSERT INTO rrhh.tipos_falta (nombre)
                    VALUES (:n) ON CONFLICT (nombre) DO NOTHING
                """), {"n": nombre})

            # ---- No-solape de asignaciones (rangos inclusivos; no crítico si falla, pero se avisa) ----
            aviso = _asegurar_no_solape(con)
            if aviso:
                avisos.append(aviso)

            # ---- Auditoría con nombre y rol del autor (migración idempotente) ----
            con.execute(text("ALTER TABLE rrhh.rrhh_auditoria ADD COLUMN IF NOT EXISTS rol TEXT"))
            con.execute(text("ALTER TABLE rrhh.rrhh_auditoria ADD COLUMN IF NOT EXISTS nombre_usuario TEXT"))

            # ---- Novedades: dominical, observación y tipos ampliados ----
            con.execute(text("ALTER TABLE rrhh.novedades ADD COLUMN IF NOT EXISTS "
                             "incluye_dominical BOOLEAN NOT NULL DEFAULT FALSE"))
            con.execute(text("ALTER TABLE rrhh.novedades ADD COLUMN IF NOT EXISTS observacion TEXT"))
            con.execute(text("ALTER TABLE rrhh.novedades ADD COLUMN IF NOT EXISTS creado_en TIMESTAMPTZ DEFAULT now()"))

            con.execute(text("ALTER TABLE rrhh.novedades DROP CONSTRAINT IF EXISTS novedades_tipo_check"))
            con.execute(text("""
                ALTER TABLE rrhh.novedades ADD CONSTRAINT novedades_tipo_check CHECK (tipo IN
                    ('FALTA','AUSENCIA','INCAPACIDAD','MEMORANDO','VACACIONES','PERMISO','PERIODO_PRUEBA'))
            """))
            # ---- % de descuento por grado de falta, con vigencias (versiones por periodo) ----
            con.execute(text("""
                CREATE TABLE IF NOT EXISTS rrhh.faltas_config (
                    id            SERIAL PRIMARY KEY,
                    tipo_falta_id INT NOT NULL REFERENCES rrhh.tipos_falta(id),
                    pct_descuento NUMERIC NOT NULL CHECK (pct_descuento >= 0 AND pct_descuento <= 100),
                    vigente_desde DATE NOT NULL,
                    vigente_hasta DATE,
                    UNIQUE (tipo_falta_id, vigente_desde)
                )
            """))
            con.execute(text("""
                INSERT INTO rrhh.faltas_config (tipo_falta_id, pct_descuento, vigente_desde)
                SELECT tf.id,
                       CASE tf.nombre WHEN 'Falta Tipo 1' THEN 15.0
                                      WHEN 'Falta Tipo 2' THEN 30.0
                                      WHEN 'Falta Tipo 3' THEN 45.0 END,
                       DATE '2025-01-01'
                FROM rrhh.tipos_falta tf
                WHERE tf.nombre IN ('Falta Tipo 1','Falta Tipo 2','Falta Tipo 3')
                ON CONFLICT (tipo_falta_id, vigente_desde) DO NOTHING
            """))

            con.commit()
        except Exception:
            con.rollback()
            raise
    for a in avisos:
        log.warning(a)
    return avisos


def auditar_rrhh(tabla, registro_id, accion, antes, despues, con=None, motivo=None) -> bool:
    """
    Auditoría sobre rrhh.rrhh_auditoria. Registra login, nombre completo y rol de quien hace el cambio.

    * Con `con` (sesión de la operación): el registro va en LA MISMA transacción del cambio; si falla,
      la excepción se propaga y el cambio no se guarda. Es la forma recomendada para todo cambio que
      afecte el bono.
    * Sin `con` (comportamiento anterior): usa una sesión propia, no rompe la operación y avisa en
      pantalla si falla. Devuelve False en ese caso.
    """
    usuario = st.session_state.get("usuario_actual", "bootstrap")
    rol = st.session_state.get("rol_actual", "bootstrap")
    nombre = st.session_state.get("nombre_completo", usuario)
    pay = dict(despues, motivo=motivo) if (motivo and isinstance(despues, dict)) else despues
    sql = text("""
        INSERT INTO rrhh.rrhh_auditoria
            (tabla, registro_id, accion, antes, despues, usuario, rol, nombre_usuario)
        VALUES (:t, :r, :a, CAST(:b AS jsonb), CAST(:d AS jsonb), :u, :rol, :nom)
    """)
    params = {
        "t": tabla, "r": str(registro_id), "a": accion, "u": usuario, "rol": rol, "nom": nombre,
        "b": json.dumps(antes, default=str) if antes is not None else None,
        "d": json.dumps(pay, default=str) if pay is not None else None,
    }
    if con is not None:
        con.execute(sql, params)
        return True
    try:
        with conn.session as s:
            s.execute(sql, params)
            s.commit()
        return True
    except Exception as e:  # noqa: BLE001
        log.error("No se pudo registrar la auditoría: %s", e)
        try:
            st.warning(f"⚠️ No se pudo registrar la auditoría: {e}")
        except Exception:  # noqa: BLE001
            pass
        return False


def validar_suma_100(df_plantilla):
    """Recibe DataFrame [almacen, cargo, pct, vigente_desde] (+ cupos opcional).
    Regla: POR ALMACÉN Y VIGENCIA, la suma de pct × cupos debe ser 100 (tolerancia TOL_PCT)."""
    errores = []
    df_plantilla = df_plantilla.copy()
    cupos = df_plantilla["cupos"].fillna(1) if "cupos" in df_plantilla.columns else 1
    df_plantilla["pct"] = df_plantilla["pct"].astype(float) * cupos
    grp = df_plantilla.groupby(["almacen", "vigente_desde"])["pct"].sum()
    for (alm, vig), total in grp.items():
        if abs(float(total) - 100.0) > TOL_PCT:
            errores.append(f"⚠️ {alm} (vigencia {vig}): suma = {total:.3f}% ≠ 100%")
    return errores


def validar_solape_asignacion(colaborador_id, fecha_desde, fecha_hasta=None, excluir_id=None):
    """Devuelve lista de conflictos (strings) si la asignación [fecha_desde, fecha_hasta] (ambos días
    INCLUSIVOS) se solapa con otra del mismo colaborador. `excluir_id` ignora la asignación que se edita.
    Fallback de la restricción EXCLUDE de la base de datos."""
    from datetime import date
    fh = fecha_hasta or date(2100, 12, 31)
    df = conn.query(f"""
        SELECT id, almacen, cargo, fecha_desde, fecha_hasta
        FROM rrhh.asignaciones
        WHERE colaborador_id = :c
          AND fecha_desde <= :fh
          AND COALESCE(fecha_hasta, {_FIN_ABIERTO}) >= :fd
          AND (CAST(:ex AS INT) IS NULL OR id <> CAST(:ex AS INT))
    """, params={"c": int(colaborador_id), "fd": fecha_desde, "fh": fh,
                 "ex": None if excluir_id is None else int(excluir_id)}, ttl=0)
    conflictos = []
    for _, r in df.iterrows():
        fin = "en curso" if r["fecha_hasta"] is None or str(r["fecha_hasta"]) in ("NaT", "None") else r["fecha_hasta"]
        conflictos.append(f"Se solapa con {r['almacen']} / {r['cargo']} ({r['fecha_desde']} → {fin})")
    return conflictos


def diagnosticar_solapes():
    """DataFrame con las asignaciones de una misma persona que se solapan (días inclusivos).
    Úsalo para limpiar datos antes de activar la restricción inclusiva de la base de datos."""
    return conn.query(f"""
        SELECT c.cedula, c.nombre,
               a.id AS asig_a, a.almacen AS almacen_a, a.cargo AS cargo_a, a.fecha_desde AS desde_a, a.fecha_hasta AS hasta_a,
               b.id AS asig_b, b.almacen AS almacen_b, b.cargo AS cargo_b, b.fecha_desde AS desde_b, b.fecha_hasta AS hasta_b
        FROM rrhh.asignaciones a
        JOIN rrhh.asignaciones b ON b.colaborador_id = a.colaborador_id AND a.id < b.id
        JOIN rrhh.colaboradores c ON c.id = a.colaborador_id
        WHERE a.fecha_desde <= COALESCE(b.fecha_hasta, {_FIN_ABIERTO})
          AND b.fecha_desde <= COALESCE(a.fecha_hasta, {_FIN_ABIERTO})
        ORDER BY c.nombre, a.fecha_desde
    """, ttl=0)


def seed_almacenes_desde_maps():
    """Carga inicial de app.almacenes desde SHORT2FULL de conexion_db. Idempotente."""
    from src import conexion_db as cdb
    short2full = getattr(cdb, "SHORT2FULL", {})
    with conn.session as con:
        for cod, serie in short2full.items():
            con.execute(text("""
                INSERT INTO app.almacenes (codigo, nombre_serie)
                VALUES (:c, :s)
                ON CONFLICT (codigo) DO UPDATE
                SET nombre_serie = EXCLUDED.nombre_serie
            """), {"c": cod, "s": serie})
        con.commit()


# Bootstrap al importar (igual que bono_variables). Si falla, avisa sin romper la app.
try:
    _avisos_bootstrap = bootstrap_rrhh()
    seed_almacenes_desde_maps()
    for _aviso in _avisos_bootstrap:
        try:
            st.warning(f"⚠️ {_aviso}")
        except Exception:  # noqa: BLE001
            pass
except Exception as e:  # noqa: BLE001
    try:
        st.warning(f"⚠️ Bootstrap RRHH no ejecutado: {e}")
    except Exception:  # noqa: BLE001
        pass