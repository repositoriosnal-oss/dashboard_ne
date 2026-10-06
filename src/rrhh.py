import streamlit as st
from sqlalchemy import text
from src.conexion_db import conn

# Roles con acceso al módulo (centralizado aquí; app.py y vistas importan esto).
ROLES_RRHH_ADMIN = ["admin", "gerente"]                        # ven TODO
ROLES_RRHH_CARGUE = ["admin", "gerente", "talento_humano"]     # pueden cargar novedades

def bootstrap_rrhh():
    """Crea esquema rrhh + tablas maestras. No hace nada si ya existen."""
    with conn.session as con:
        try:
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
            
            # ---- Plantilla de distribución por almacén/cargo (suma = 100%) ----
            con.execute(text("""
                CREATE TABLE IF NOT EXISTS rrhh.distribucion_puestos (
                    id            SERIAL PRIMARY KEY,
                    almacen       TEXT NOT NULL REFERENCES app.almacenes(codigo),
                    cargo         TEXT NOT NULL,
                    pct           NUMERIC(6,3) NOT NULL CHECK (pct > 0 AND pct <= 100),
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
                    fecha_hasta    DATE
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
                
            # Intentar crear extensión para constraint de no-solape (puede fallar por permisos, no es crítico)
            try:
                con.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
                con.execute(text(
                    "ALTER TABLE rrhh.asignaciones DROP CONSTRAINT IF EXISTS asignaciones_no_solape"))
                con.execute(text(
                    "ALTER TABLE rrhh.asignaciones ADD CONSTRAINT asignaciones_no_solape "
                    "EXCLUDE USING gist (colaborador_id WITH =, "
                    "daterange(fecha_desde, COALESCE(fecha_hasta, DATE '2100-12-31'), '[)') WITH &&)"))
            except Exception:
                pass
                
            con.commit()
        except Exception as e:
            con.rollback()
            raise e

def auditar_rrhh(tabla, registro_id, accion, antes, despues):
    """Auditoría estilo _auditar_bono, sobre rrhh.rrhh_auditoria."""
    import json
    try:
        usuario = st.session_state.get("usuario_actual", "bootstrap")
        with conn.session as con:
            con.execute(text("""
                INSERT INTO rrhh.rrhh_auditoria (tabla, registro_id, accion, antes, despues, usuario)
                VALUES (:t, :r, :a, :b::jsonb, :d::jsonb, :u)
            """), {
                "t": tabla, "r": str(registro_id), "a": accion, "u": usuario,
                "b": json.dumps(antes) if antes is not None else None,
                "d": json.dumps(despues) if despues is not None else None,
            })
            con.commit()
    except Exception:
        pass  # la auditoría nunca debe romper la operación principal

def validar_suma_100(df_plantilla):
    """Recibe DataFrame [almacen, cargo, pct, vigente_desde].
    Regla: POR ALMACÉN Y VIGENCIA, la suma de pct debe ser EXACTAMENTE 100."""
    errores = []
    grp = df_plantilla.groupby(["almacen", "vigente_desde"])["pct"].sum()
    for (alm, vig), total in grp.items():
        if abs(float(total) - 100.0) > 0.001:
            errores.append(f"⚠️ {alm} (vigencia {vig}): suma = {total:.3f}% ≠ 100%")
    return errores

def validar_solape_asignacion(colaborador_id, fecha_desde, fecha_hasta=None):
    """Devuelve lista de conflictos (strings) si la nueva asignación se solapa
    con una existente del mismo colaborador. Fallback de la constraint EXCLUDE."""
    from datetime import date
    fh = fecha_hasta or date(2100, 12, 31)
    df = conn.query("""
        SELECT id, almacen, cargo, fecha_desde, COALESCE(fecha_hasta, DATE '2100-12-31') AS fecha_hasta
        FROM rrhh.asignaciones
        WHERE colaborador_id = :c
        AND fecha_desde < :fh
        AND COALESCE(fecha_hasta, DATE '2100-12-31') > :fd
    """, {"c": int(colaborador_id), "fd": fecha_desde, "fh": fh}, ttl=0)
    conflictos = []
    for _, r in df.iterrows():
        conflictos.append(f"Se solapa con {r['almacen']} / {r['cargo']} "
                          f"({r['fecha_desde']} → {r['fecha_hasta']})")
    return conflictos

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
    bootstrap_rrhh()
except Exception as e:
    try:
        st.warning(f"⚠️ Bootstrap RRHH no ejecutado: {e}")
    except Exception:
        pass