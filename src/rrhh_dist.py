"""
MÓDULO DE DISTRIBUCIÓN DEL BONO (lógica de negocio desacoplada de la interfaz).

Modelo de Distribución por Bolsa de Cargo (Pool por Cargo) y Períodos de Vigencia:
---------------------------------------------------------------------------------
1. PERÍODOS DE VIGENCIA POR ALMACÉN (Estilo Variables del Bono):
   - Cada almacén define períodos con `fecha_inicio` y `fecha_fin` (NULL = Abierto).
   - Un período abierto rige para todos los meses a partir de `fecha_inicio` hasta que
     se defina una nueva vigencia.
2. BOLSA / POOL POR CARGO:
   - Para cada período y almacén, la Gerencia define el porcentaje del bono asignado
     a cada CARGO (ej: Gerente Punto 22%, Vendedor 2 48%, Cajera 15%, Bodega 1 15% -> Total 100%).
   - La Gerencia NO define cupos individuales: el sistema cruza con `rrhh.asignaciones`
     (alimentado por Talento Humano).
3. REPARTO EQUITATIVO Y PRORRATEO POR DÍAS ENTRE CARGOS REPETIDOS:
   - Si un cargo tiene N personas activas en el mes, el porcentaje del cargo se reparte
     entre ellas proporcionalmente a sus días efectivos trabajados en el mes.
   - Si hay 3 Vendedores 2 mes completo, cada uno recibe 48% / 3 = 16%.
   - Si hay 2 Vendedores 2 mes completo, cada uno recibe 48% / 2 = 24%.
   - Si entran o salen a mitad de mes, se prorratea por días-hombre trabajados.
4. INVARIANTE PRESUPUESTAL:
   - El total pagado en el almacén NUNCA puede exceder el 100%.
   - Si un cargo no tiene a nadie asignado en el mes (0 personas), ese porcentaje queda
     como VACANTE / NO EJECUTADO (ahorro de la empresa).
5. PERÍODO DE PRUEBA:
   - Si un colaborador está en período de prueba durante el mes, computa sus días en prueba
     y su porcentaje se aísla sin afectar la proporción de los demás.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_EVEN
from fractions import Fraction
from typing import Any

MODO_PORCENTAJE = "PORCENTAJE"
MODO_INDIVIDUAL = "INDIVIDUAL"

TOL = 1e-4

CARGOS_BONO: tuple[str, ...] = (
    "Gerente Punto",
    "Subgerente",
    "Vendedor 1",
    "Vendedor 2",
    "Vendedor 3",
    "Cajera",
    "Bodega 1",
    "Bodega 2",
    "Conductor",
    "Auxiliar",
)


# =====================================================================
# MATEMÁTICA EXACTA Y REPARTO MONETARIO (LARGEST REMAINDER METHOD)
# =====================================================================

def _fr(x) -> Fraction:
    """Convierte float, int, str o Decimal a Fraction exacta sin errores de coma flotante."""
    return Fraction(str(x) if isinstance(x, (float, Decimal)) else x)


def monto_por_porcentaje(bono, pcts: dict, decimales: int = 0) -> dict:
    """
    Distribución monetaria con Largest Remainder Method (Hare-Niemeyer).
    Garantiza que la suma de los montos asignados sea IDÉNTICA al bono total al peso exacto.
    """
    total_unidades = int(round(float(bono) * (10 ** decimales)))
    if total_unidades <= 0 or not pcts:
        return {k: Decimal(0) for k in pcts}

    factor = Decimal(10) ** (-decimales)
    cuotas = {k: (_fr(p) * _fr(total_unidades)) / _fr(100) for k, p in pcts.items()}
    enteros = {k: int(v) for k, v in cuotas.items()}
    resto = total_unidades - sum(enteros.values())

    fracciones = sorted(
        ((cuotas[k] - enteros[k], k) for k in cuotas),
        key=lambda x: (x[0], x[1]),
        reverse=True
    )
    for i in range(max(0, min(resto, len(fracciones)))):
        enteros[fracciones[i][1]] += 1

    return {k: (Decimal(enteros[k]) * factor) for k in pcts}


# =====================================================================
# HELPERS DE FECHAS
# =====================================================================

def a_fecha(v) -> date | None:
    if v is None:
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, str):
        v = v.strip()
        if not v or v.lower() in ("none", "null", "nat"):
            return None
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
            try:
                return datetime.strptime(v[:10], fmt).date()
            except ValueError:
                continue
    return None


def inicio_mes(d: date) -> date:
    return date(d.year, d.month, 1)


def fin_mes(d: date) -> date:
    sig = date(d.year + (1 if d.month == 12 else 0), 1 if d.month == 12 else d.month + 1, 1)
    return date.fromordinal(sig.toordinal() - 1)


def sumar_meses(d: date, n: int) -> date:
    total_m = (d.year * 12 + (d.month - 1)) + n
    return date(total_m // 12, (total_m % 12) + 1, 1)


def lista_meses(desde: date, hasta: date) -> list[date]:
    cur = inicio_mes(desde)
    lim = inicio_mes(hasta)
    res = []
    while cur <= lim:
        res.append(cur)
        cur = sumar_meses(cur, 1)
    return res

# Alias para compatibilidad hacia atrás
generar_rango_meses = lista_meses


def etiqueta_mes(d: date) -> str:
    nombres = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
    return f"{nombres[d.month - 1]} {d.year}"


def dias_solape(a_desde: date, a_hasta: date, b_desde: date, b_hasta: date) -> int:
    """Calcula los días calendario inclusivos de intersección entre dos rangos de fechas."""
    ini = max(a_desde, b_desde)
    fin = min(a_hasta, b_hasta)
    if ini > fin:
        return 0
    return (fin - ini).days + 1


def resolver_periodo(periodos: list[dict], mes: date) -> dict | None:
    """
    Encuentra el período que aplica para un mes determinado.
    Maneja períodos con fecha_fin y períodos abiertos (fecha_fin IS NULL).
    Si hay solape de períodos abiertos, toma el de fecha_inicio más reciente.
    """
    m_ini = inicio_mes(mes)
    m_fin = fin_mes(mes)
    candidatos = []
    for p in periodos:
        fi = a_fecha(p.get("fecha_inicio"))
        ff = a_fecha(p.get("fecha_fin"))
        if not fi:
            continue
        # Debe haber iniciado antes o durante el mes
        if fi <= m_fin:
            # Y no haber terminado antes del inicio del mes
            if ff is None or ff >= m_ini:
                candidatos.append((fi, p))
    if not candidatos:
        return None
    # Mayor fecha_inicio rige
    candidatos.sort(key=lambda c: c[0], reverse=True)
    return candidatos[0][1]


def fin_efectivo(a: dict) -> date | None:
    """Calcula el último día activo de una asignación considerando inactividad del colaborador."""
    h = a_fecha(a.get("hasta"))
    inact = a_fecha(a.get("fecha_inactividad"))
    if not a.get("activo", True) and inact is not None:
        if h is None:
            return inact
        return min(h, inact)
    return h


# =====================================================================
# CÁLCULO PRINCIPAL DE REPARTO DEL BONO POR MES Y ALMACÉN
# =====================================================================

def calcular_reparto_mes(
    almacen: str,
    mes: date,
    periodos: list[dict],
    cargos_config: list[dict],
    asignaciones: list[dict],
    modo: str = MODO_PORCENTAJE,
) -> dict:
    """
    Calcula el reparto del bono para un almacén en un mes específico.
    Cruza la política de cargos vigente con las asignaciones de talento humano.
    """
    m_ini = inicio_mes(mes)
    m_fin = fin_mes(mes)
    dias_del_mes = (m_fin - m_ini).days + 1

    alertas: list[dict[str, str]] = []

    # 1. Caso almacén individual (EJECOM)
    if modo == MODO_INDIVIDUAL:
        # En modo individual la meta se liquida por vendedor, no por bolsa
        return {
            "almacen": almacen,
            "mes": m_ini,
            "periodo_aplicado": None,
            "modo": MODO_INDIVIDUAL,
            "cargos": [],
            "personas": [],
            "totales": {
                "asignado_politica": 0.0,
                "repartido": 0.0,
                "en_prueba": 0.0,
                "vacante": 0.0,
                "personas": 0,
            },
            "alertas": [{
                "nivel": "info",
                "mensaje": f"El almacén {almacen} opera bajo modalidad INDIVIDUAL (EJECOM). No aplica bolsa porcentual."
            }]
        }

    # 2. Resolver el período de distribución aplicable
    periodos_alm = [p for p in periodos if p["almacen"] == almacen]
    periodo_vigente = resolver_periodo(periodos_alm, m_ini)

    if not periodo_vigente:
        alertas.append({
            "nivel": "error",
            "mensaje": f"No hay una política de distribución configurada o vigente para {almacen} en {etiqueta_mes(m_ini)}."
        })
        return {
            "almacen": almacen,
            "mes": m_ini,
            "periodo_aplicado": None,
            "modo": MODO_PORCENTAJE,
            "cargos": [],
            "personas": [],
            "totales": {"asignado_politica": 0.0, "repartido": 0.0, "en_prueba": 0.0, "vacante": 0.0, "personas": 0},
            "alertas": alertas,
        }

    pid = periodo_vigente["id"]
    cargos_map = {
        c["cargo"]: float(c["pct"])
        for c in cargos_config
        if c["periodo_id"] == pid
    }
    suma_politica = sum(cargos_map.values())
    if abs(suma_politica - 100.0) > TOL:
        alertas.append({
            "nivel": "warning",
            "mensaje": f"La política «{periodo_vigente['nombre']}» suma {suma_politica:.2f}% (difiere del 100%)."
        })

    # 3. Filtrar asignaciones activas en este almacén durante el mes
    asigs_mes: list[dict] = []
    for a in asignaciones:
        if a["almacen"] != almacen:
            continue
        d = a_fecha(a.get("desde"))
        if not d or d > m_fin:
            continue
        h = fin_efectivo(a)
        if h and h < m_ini:
            continue
        # Está en este mes
        asigs_mes.append(a)

    # 4. Agrupar colaboradores por cargo y calcular días trabajados
    # Una persona puede tener más de un registro si cambió de cargo o contrato en el mismo mes
    por_cargo: dict[str, list[dict]] = {}
    for a in asigs_mes:
        cargo = a["cargo"]
        d = max(a_fecha(a["desde"]), m_ini)
        h_ef = fin_efectivo(a)
        h = min(h_ef, m_fin) if h_ef else m_fin
        dias_en_alm = max(0, (h - d).days + 1)
        if dias_en_alm <= 0:
            continue

        # Periodo de prueba
        dias_prueba = 0
        if a.get("en_prueba"):
            p_ini = a_fecha(a.get("prueba_inicio")) or d
            p_fin = a_fecha(a.get("prueba_fin")) or h
            dias_prueba = dias_solape(d, h, p_ini, p_fin)
        dias_participa = max(0, dias_en_alm - dias_prueba)

        colab_entry = {
            "colaborador_id": a["colaborador_id"],
            "cedula": a.get("cedula", ""),
            "nombre": a.get("nombre", ""),
            "cargo": cargo,
            "desde_en_mes": d,
            "hasta_en_mes": h,
            "dias": dias_en_alm,
            "dias_participa": dias_participa,
            "dias_prueba": dias_prueba,
            "fraccion_mes": dias_en_alm / dias_del_mes,
        }
        por_cargo.setdefault(cargo, []).append(colab_entry)

    # 5. Distribuir el porcentaje de cada cargo entre sus ocupantes
    cargos_resumen: list[dict] = []
    personas_resumen: list[dict] = []

    total_repartido = 0.0
    total_en_prueba = 0.0
    total_vacante = 0.0

    # Todos los cargos configurados en la política + cargos que tengan personas pero 0% configurado
    todos_cargos = sorted(set(list(cargos_map.keys()) + list(por_cargo.keys())))

    for cargo in todos_cargos:
        pct_cargo = cargos_map.get(cargo, 0.0)
        ocupantes = por_cargo.get(cargo, [])

        if not ocupantes:
            # Cargo vacante: nadie estuvo en el mes ocupando este cargo
            total_vacante += pct_cargo
            cargos_resumen.append({
                "cargo": cargo,
                "pct_bolsa": pct_cargo,
                "personas": 0,
                "dias_totales": 0,
                "repartido": 0.0,
                "en_prueba": 0.0,
                "vacante": pct_cargo,
                "estado": "Vacante (0 asignados)"
            })
            continue

        # Hay ocupantes: calcular días totales hombre (dias_participa + dias_prueba)
        tot_dias_part = sum(o["dias_participa"] for o in ocupantes)
        tot_dias_prue = sum(o["dias_prueba"] for o in ocupantes)
        tot_dias_hombre = tot_dias_part + tot_dias_prue

        if pct_cargo <= 0:
            alertas.append({
                "nivel": "warning",
                "mensaje": f"Hay {len(ocupantes)} persona(s) con cargo «{cargo}» en {almacen}, pero la política asigna 0.0% a este cargo."
            })

        # Reparto por peso de días trabajados
        repartido_cargo = 0.0
        prueba_cargo = 0.0

        # Base para prorrateo: si todos están mes completo, el peso se reparte equitativamente
        for o in ocupantes:
            if tot_dias_hombre > 0 and pct_cargo > 0:
                pct_persona_bruto = (pct_cargo * o["dias"]) / tot_dias_hombre
                pct_persona_efectivo = (pct_cargo * o["dias_participa"]) / tot_dias_hombre
                pct_persona_prueba = (pct_cargo * o["dias_prueba"]) / tot_dias_hombre
            else:
                pct_persona_bruto = 0.0
                pct_persona_efectivo = 0.0
                pct_persona_prueba = 0.0

            repartido_cargo += pct_persona_efectivo
            prueba_cargo += pct_persona_prueba

            personas_resumen.append({
                **o,
                "pct_bolsa_cargo": pct_cargo,
                "pct_bruto": round(pct_persona_bruto, 4),
                "pct_efectivo": round(pct_persona_efectivo, 4),
                "pct_en_prueba": round(pct_persona_prueba, 4),
            })

        # Si el cupo estuvo desocupado parte del mes respecto a la capacidad esperada
        vacante_cargo = max(0.0, pct_cargo - (repartido_cargo + prueba_cargo))
        total_repartido += repartido_cargo
        total_en_prueba += prueba_cargo
        total_vacante += vacante_cargo

        cargos_resumen.append({
            "cargo": cargo,
            "pct_bolsa": pct_cargo,
            "personas": len(ocupantes),
            "dias_totales": tot_dias_hombre,
            "repartido": round(repartido_cargo, 4),
            "en_prueba": round(prueba_cargo, 4),
            "vacante": round(vacante_cargo, 4),
            "estado": f"{len(ocupantes)} activo(s)"
        })

    # Invariante de seguridad
    if (total_repartido + total_en_prueba) > 100.0 + TOL:
        alertas.append({
            "nivel": "error",
            "mensaje": f"CRÍTICO: El total repartido ({total_repartido + total_en_prueba:.2f}%) supera el 100% de la bolsa."
        })

    return {
        "almacen": almacen,
        "mes": m_ini,
        "periodo_aplicado": periodo_vigente,
        "modo": MODO_PORCENTAJE,
        "cargos": cargos_resumen,
        "personas": personas_resumen,
        "totales": {
            "asignado_politica": round(suma_politica, 4),
            "repartido": round(total_repartido, 4),
            "en_prueba": round(total_en_prueba, 4),
            "vacante": round(total_vacante, 4),
            "personas": len(personas_resumen),
        },
        "alertas": alertas,
    }


# =====================================================================
# MIGRACIÓN Y MODELO DE BASE DE DATOS IDEMPOTENTE
# =====================================================================

def migrar(con) -> list[str]:
    """
    Garantiza la creación y coexistencia de las tablas de distribución por período.
    Idempotente y compatible hacia atrás.
    """
    from sqlalchemy import text

    avisos = []

    # 1. Tabla de períodos de distribución por almacén
    con.execute(text("""
        CREATE TABLE IF NOT EXISTS rrhh.distribucion_periodos (
            id SERIAL PRIMARY KEY,
            almacen VARCHAR(30) NOT NULL REFERENCES app.almacenes(codigo) ON UPDATE CASCADE,
            nombre VARCHAR(120) NOT NULL,
            fecha_inicio DATE NOT NULL,
            fecha_fin DATE,
            activo BOOLEAN NOT NULL DEFAULT TRUE,
            creado_en TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
            creado_por VARCHAR(80),
            CONSTRAINT chk_dist_fechas CHECK (fecha_fin IS NULL OR fecha_fin >= fecha_inicio)
        );
        CREATE INDEX IF NOT EXISTS idx_dist_periodos_alm_fechas
            ON rrhh.distribucion_periodos (almacen, fecha_inicio, fecha_fin);
    """))

    # 2. Tabla de porcentajes asignados a cada cargo dentro del período
    con.execute(text("""
        CREATE TABLE IF NOT EXISTS rrhh.distribucion_cargos (
            id SERIAL PRIMARY KEY,
            periodo_id INT NOT NULL REFERENCES rrhh.distribucion_periodos(id) ON DELETE CASCADE,
            cargo VARCHAR(80) NOT NULL,
            pct NUMERIC(6, 3) NOT NULL CHECK (pct >= 0 AND pct <= 100),
            CONSTRAINT uq_dist_periodo_cargo UNIQUE (periodo_id, cargo)
        );
        CREATE INDEX IF NOT EXISTS idx_dist_cargos_pid
            ON rrhh.distribucion_cargos (periodo_id);
    """))

    # 3. Si la tabla clásica distribucion_puestos tenía datos, migrar hacia distribucion_periodos
    tiene_puestos = con.execute(text("""
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'rrhh' AND table_name = 'distribucion_puestos'
        )
    """)).scalar()

    if tiene_puestos:
        # Migrar almacenes con puestos existentes que no tengan período aún
        con.execute(text("""
            DO $$
            DECLARE
                r RECORD;
                new_pid INT;
            BEGIN
                FOR r IN
                    SELECT almacen, MIN(vigente_desde) as f_ini, MAX(vigente_hasta) as f_fin
                    FROM rrhh.distribucion_puestos
                    WHERE almacen NOT IN (SELECT DISTINCT almacen FROM rrhh.distribucion_periodos)
                    GROUP BY almacen
                LOOP
                    INSERT INTO rrhh.distribucion_periodos (almacen, nombre, fecha_inicio, fecha_fin, creado_por)
                    VALUES (r.almacen, 'Migración Inicial ' || r.almacen, COALESCE(r.f_ini, '2024-01-01'::DATE), r.f_fin, 'sistema')
                    RETURNING id INTO new_pid;

                    INSERT INTO rrhh.distribucion_cargos (periodo_id, cargo, pct)
                    SELECT new_pid, cargo, SUM(pct * cupos)
                    FROM rrhh.distribucion_puestos
                    WHERE almacen = r.almacen
                    GROUP BY cargo;
                END LOOP;
            END $$;
        """))

    return avisos


# =====================================================================
# OPERACIONES DE CONSULTA Y PERSISTENCIA (CRUD + AUDITORÍA)
# =====================================================================

def cargar_almacenes(conn) -> list[dict]:
    df = conn.query("SELECT codigo, COALESCE(nombre_serie, codigo) AS nombre, modo_bono "
                    "FROM app.almacenes WHERE activo ORDER BY codigo", ttl=0)
    return df.to_dict("records")


def cargar_periodos(conn, almacen: str | None = None) -> list[dict]:
    sql = ("SELECT id, almacen, nombre, fecha_inicio, fecha_fin, activo, creado_en, creado_por "
           "FROM rrhh.distribucion_periodos {w} ORDER BY almacen, fecha_inicio DESC")
    if almacen:
        df = conn.query(sql.format(w="WHERE almacen = :a AND activo = TRUE"), params={"a": almacen}, ttl=0)
    else:
        df = conn.query(sql.format(w="WHERE activo = TRUE"), ttl=0)
    filas = df.to_dict("records")
    for f in filas:
        f["fecha_inicio"] = a_fecha(f["fecha_inicio"])
        f["fecha_fin"] = a_fecha(f["fecha_fin"])
    return filas


def cargar_cargos_config(conn, periodo_id: int | None = None) -> list[dict]:
    sql = ("SELECT dc.id, dc.periodo_id, dc.cargo, dc.pct, dp.almacen "
           "FROM rrhh.distribucion_cargos dc "
           "JOIN rrhh.distribucion_periodos dp ON dp.id = dc.periodo_id "
           "{w} ORDER BY dc.periodo_id, dc.cargo")
    if periodo_id:
        df = conn.query(sql.format(w="WHERE dc.periodo_id = :p"), params={"p": periodo_id}, ttl=0)
    else:
        df = conn.query(sql.format(w=""), ttl=0)
    filas = df.to_dict("records")
    for f in filas:
        f["pct"] = float(f["pct"])
    return filas


def cargar_asignaciones(conn) -> list[dict]:
    df = conn.query("""
        SELECT a.id AS asig_id, a.colaborador_id, c.cedula, c.nombre, a.almacen, a.cargo,
               a.fecha_desde AS desde, a.fecha_hasta AS hasta,
               c.activo, c.fecha_inactividad, c.en_prueba, c.prueba_inicio, c.prueba_fin
        FROM rrhh.asignaciones a JOIN rrhh.colaboradores c ON c.id = a.colaborador_id
        ORDER BY c.nombre, a.fecha_desde
    """, ttl=0)
    filas = df.to_dict("records")
    for f in filas:
        for k in ("desde", "hasta", "fecha_inactividad", "prueba_inicio", "prueba_fin"):
            f[k] = a_fecha(f[k])
        f["activo"] = bool(f.get("activo", True))
        f["en_prueba"] = bool(f.get("en_prueba", False))
    return filas


def guardar_periodo_con_cargos(
    con,
    *,
    almacen: str,
    periodo_id: int | None,
    nombre: str,
    fecha_inicio: date,
    fecha_fin: date | None,
    cargos_pct: dict[str, float],
    usuario: str,
    rol: str,
    motivo: str | None = None,
    nombre_usuario: str | None = None
) -> int:
    """
    Crea o actualiza un período y su matriz de porcentajes de cargos de manera atómica.
    Registra auditoría en rrhh.rrhh_auditoria dentro de la misma transacción.
    """
    from sqlalchemy import text

    total = sum(cargos_pct.values())
    if total > 100.0 + TOL:
        raise ValueError(f"La suma de los porcentajes de los cargos ({total:.3f}%) supera el 100%.")

    if periodo_id:
        # Actualización de período existente
        con.execute(text("""
            UPDATE rrhh.distribucion_periodos
            SET nombre = :n, fecha_inicio = :fi, fecha_fin = :ff
            WHERE id = :id AND almacen = :a
        """), {"n": nombre, "fi": fecha_inicio, "ff": fecha_fin, "id": periodo_id, "a": almacen})
        pid = periodo_id
        accion = "ACTUALIZAR_PERIODO_DISTRIBUCION"
    else:
        # Creación de nuevo período
        pid = con.execute(text("""
            INSERT INTO rrhh.distribucion_periodos (almacen, nombre, fecha_inicio, fecha_fin, creado_por)
            VALUES (:a, :n, :fi, :ff, :u)
            RETURNING id
        """), {"a": almacen, "n": nombre, "fi": fecha_inicio, "ff": fecha_fin, "u": usuario}).scalar()
        accion = "CREAR_PERIODO_DISTRIBUCION"

    # Reemplazar cargos del período
    con.execute(text("DELETE FROM rrhh.distribucion_cargos WHERE periodo_id = :pid"), {"pid": pid})
    for c, p in cargos_pct.items():
        if p > 0:
            con.execute(text("""
                INSERT INTO rrhh.distribucion_cargos (periodo_id, cargo, pct)
                VALUES (:pid, :c, :p)
            """), {"pid": pid, "c": c, "p": round(p, 3)})

    # Auditoría
    from src.rrhh import auditar_rrhh
    payload_despues = {
        "almacen": almacen,
        "periodo_id": pid,
        "nombre": nombre,
        "fecha_inicio": str(fecha_inicio),
        "fecha_fin": str(fecha_fin) if fecha_fin else "Abierto",
        "cargos": cargos_pct,
        "total": round(total, 3)
    }
    auditar_rrhh(
        tabla="distribucion_periodos",
        registro_id=f"{almacen}:periodo:{pid}",
        accion=accion,
        antes=None,
        despues=payload_despues,
        con=con,
        motivo=motivo
    )

    return pid


def eliminar_periodo(con, *, periodo_id: int, almacen: str, usuario: str, rol: str, motivo: str) -> None:
    """Elimina un período y sus cargos, auditando la acción."""
    from sqlalchemy import text
    from src.rrhh import auditar_rrhh

    per = con.execute(text("SELECT nombre, fecha_inicio, fecha_fin FROM rrhh.distribucion_periodos WHERE id = :id"),
                      {"id": periodo_id}).mappings().first()
    if not per:
        return

    con.execute(text("DELETE FROM rrhh.distribucion_periodos WHERE id = :id"), {"id": periodo_id})

    auditar_rrhh(
        tabla="distribucion_periodos",
        registro_id=f"{almacen}:periodo:{periodo_id}",
        accion="ELIMINAR_PERIODO_DISTRIBUCION",
        antes={"nombre": per["nombre"], "fecha_inicio": str(per["fecha_inicio"]), "fecha_fin": str(per["fecha_fin"])},
        despues=None,
        con=con,
        motivo=motivo
    )