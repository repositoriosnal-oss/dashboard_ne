"""
MÓDULO DE REPARTICIÓN Y CONCILIACIÓN DEL BONO POR COLABORADOR
Consolida la liquidación del bono por almacén (BONO FINAL) de Conciliación con las
políticas de distribución por cargo de RRHH y las 4 fuentes de deducciones:
1. Descuentos de Inventarios (en pesos $)
2. Faltas RRHH Tipo 1, 2, 3 (en porcentaje %)
3. Ausencias, Permisos, Vacaciones e Incapacidades (en porcentaje %, 3.3333% por día)
4. Novedades Administrativas de Gerencia (en porcentaje %)

Diseñado para ser probado de manera pura sin infraestructura externa a nivel de módulo.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from src import rrhh_dist as dist
from src import rrhh_nov as nov


def calcular_deducciones_persona(
    *,
    colaborador_id: int,
    bono_bruto: float,
    descuentos_inv: list[dict],
    novedades_rrhh: list[dict],
    config_faltas: list[dict],
    novedades_admin: list[dict],
    mes: date,
) -> dict:
    """
    Calcula de manera exacta y desglosada los 4 tipos de deducciones para un colaborador:
    1. Inventarios ($)
    2. Faltas Tipo 1, 2, 3 (% sobre Bono Bruto)
    3. Ausencias, Vacaciones, Permisos, Incapacidades (% sobre Bono Bruto)
    4. Novedades Administrativas de Gerencia (% sobre Bono Bruto)
    """
    m_ini = dist.inicio_mes(mes)

    # 1. Descuentos de Inventarios (en Pesos $)
    inv_persona = [
        d for d in descuentos_inv
        if d.get("colaborador_id") == colaborador_id
    ]
    monto_inv_pesos = sum(float(d.get("monto", 0.0)) for d in inv_persona)

    # 2 & 3. Novedades de Talento Humano
    nov_persona = [
        n for n in novedades_rrhh
        if n.get("colaborador_id") == colaborador_id
    ]
    res_nov = nov.calcular_persona_mes(nov_persona, config_faltas, m_ini)
    
    pct_faltas = float(res_nov.get("pct_faltas", 0.0))
    pct_ausencias = float(res_nov.get("pct_dias", 0.0))

    monto_faltas_pesos = (bono_bruto * (pct_faltas / 100.0)) if bono_bruto > 0 else 0.0
    monto_ausencias_pesos = (bono_bruto * (pct_ausencias / 100.0)) if bono_bruto > 0 else 0.0

    # 4. Novedades Administrativas de Gerencia (en %)
    adm_persona = [
        a for a in novedades_admin
        if a.get("colaborador_id") == colaborador_id
    ]
    pct_admin = sum(float(a.get("porcentaje_descuento", 0.0)) for a in adm_persona)
    monto_admin_pesos = (bono_bruto * (pct_admin / 100.0)) if bono_bruto > 0 else 0.0

    # Consolidado
    total_descuentos_pesos = (
        monto_inv_pesos + monto_faltas_pesos + monto_ausencias_pesos + monto_admin_pesos
    )
    bono_neto = max(0.0, bono_bruto - total_descuentos_pesos)
    saldo_pendiente = max(0.0, total_descuentos_pesos - bono_bruto)

    return {
        # Inventarios
        "monto_inv_pesos": round(monto_inv_pesos, 2),
        "items_inv": inv_persona,
        # Faltas RRHH
        "pct_faltas": round(pct_faltas, 2),
        "monto_faltas_pesos": round(monto_faltas_pesos, 2),
        "detalle_faltas": res_nov.get("faltas", []),
        # Ausencias / Permisos RRHH
        "pct_ausencias": round(pct_ausencias, 2),
        "monto_ausencias_pesos": round(monto_ausencias_pesos, 2),
        "dias_ausencias": res_nov.get("dias_total", 0),
        "detalle_ausencias": res_nov.get("detalle_dias", {}),
        # Novedades Administrativas Gerencia
        "pct_admin": round(pct_admin, 2),
        "monto_admin_pesos": round(monto_admin_pesos, 2),
        "items_admin": adm_persona,
        # Totales
        "total_descuentos_pesos": round(total_descuentos_pesos, 2),
        "bono_neto": round(bono_neto, 2),
        "saldo_pendiente": round(saldo_pendiente, 2),
    }


def consolidar_reparticion_almacen(
    *,
    almacen_codigo: str,
    almacen_nombre: str,
    mes: date,
    valor_bono_almacen: float,
    bono_final_almacen: float,
    aplica_meta: bool,
    reparto_dist: dict,
    descuentos_inv: list[dict],
    novedades_rrhh: list[dict],
    config_faltas: list[dict],
    novedades_admin: list[dict],
) -> list[dict]:
    """
    Consolida fila a fila cada colaborador de un almacén calculando su bono base,
    bono adicional, bono bruto y la cascada de deducciones.
    """
    filas_colaboradores: list[dict] = []
    personas = reparto_dist.get("personas", [])

    for p in personas:
        cid = p["colaborador_id"]
        pct_efectivo = float(p.get("pct_efectivo") or 0.0)
        pct_bolsa_cargo = float(p.get("pct_bolsa_cargo", 0.0))
        cargo = p.get("cargo", "Sin cargo")
        nombre = p.get("nombre", "")
        cedula = p.get("cedula", "")
        dias_part = p.get("dias_participa", 0)
        dias_prueba = p.get("dias_prueba", 0)

        # Repartición de Valor Bono (Base) y Bono Final
        if aplica_meta and bono_final_almacen > 0:
            bono_base = (valor_bono_almacen * pct_efectivo) / 100.0
            bono_bruto = (bono_final_almacen * pct_efectivo) / 100.0
            bono_adicional = max(0.0, bono_bruto - bono_base)
        else:
            bono_base = 0.0
            bono_bruto = 0.0
            bono_adicional = 0.0

        # Cascada de deducciones
        ded = calcular_deducciones_persona(
            colaborador_id=cid,
            bono_bruto=bono_bruto,
            descuentos_inv=descuentos_inv,
            novedades_rrhh=novedades_rrhh,
            config_faltas=config_faltas,
            novedades_admin=novedades_admin,
            mes=mes,
        )

        # Determinar estado
        if not aplica_meta:
            estado = "❌ Meta no alcanzada ($0)"
        elif dias_prueba > 0 and dias_part == 0:
            estado = "⚠️ En período de prueba"
        elif ded["bono_neto"] > 0:
            estado = "✅ A pagar"
        elif bono_bruto > 0 and ded["bono_neto"] == 0:
            estado = "⚠️ Descuentos superan bono ($0)"
        else:
            estado = "— Sin bono asignado"

        filas_colaboradores.append({
            "almacen_codigo": almacen_codigo,
            "almacen_nombre": almacen_nombre,
            "colaborador_id": cid,
            "cedula": cedula,
            "nombre": nombre,
            "cargo": cargo,
            "dias_participa": dias_part,
            "dias_prueba": dias_prueba,
            "pct_bolsa_cargo": pct_bolsa_cargo,
            "pct_efectivo": pct_efectivo,
            "bono_base": round(bono_base, 2),
            "bono_adicional": round(bono_adicional, 2),
            "bono_bruto": round(bono_bruto, 2),
            # Descuentos
            "desc_inv_pesos": ded["monto_inv_pesos"],
            "pct_faltas": ded["pct_faltas"],
            "desc_faltas_pesos": ded["monto_faltas_pesos"],
            "pct_ausencias": ded["pct_ausencias"],
            "desc_ausencias_pesos": ded["monto_ausencias_pesos"],
            "pct_admin": ded["pct_admin"],
            "desc_admin_pesos": ded["monto_admin_pesos"],
            "total_descuentos": ded["total_descuentos_pesos"],
            "bono_neto": ded["bono_neto"],
            "saldo_pendiente": ded["saldo_pendiente"],
            "estado": estado,
            "_deducciones_raw": ded,
        })

    return filas_colaboradores


# =====================================================================
# OPERACIONES DE CONSULTA CONECTADAS A BASE DE DATOS
# =====================================================================

def cargar_descuentos_inventarios_mes(conn, mes: date) -> list[dict]:
    """Carga los descuentos en pesos de inventarios registrados para el mes."""
    m_ini = dist.inicio_mes(mes)
    try:
        df = conn.query("""
            SELECT id, periodo_mes, colaborador_id, almacen_codigo, monto, concepto, observacion
            FROM inventarios.descuentos_bono
            WHERE periodo_mes = :m
        """, params={"m": m_ini}, ttl=0)
        return df.to_dict("records") if not df.empty else []
    except Exception:
        return []


def cargar_novedades_rrhh_mes(conn, mes: date) -> tuple[list[dict], list[dict]]:
    """Carga novedades de Talento Humano y la tabla de configuración de faltas."""
    m_ini = dist.inicio_mes(mes)
    m_fin = dist.fin_mes(mes)
    try:
        df_nov = conn.query("""
            SELECT n.id, n.colaborador_id, n.tipo, n.tipo_falta_id,
                   n.fecha_inicio, n.fecha_fin, n.incluye_dominical, n.observacion
            FROM rrhh.novedades n
            WHERE n.fecha_inicio <= :fin AND n.fecha_fin >= :ini
        """, params={"ini": m_ini, "fin": m_fin}, ttl=0)
        novs = df_nov.to_dict("records") if not df_nov.empty else []
    except Exception:
        novs = []

    try:
        df_cfg = conn.query("""
            SELECT id, tipo_falta_id, pct_descuento, vigente_desde, vigente_hasta
            FROM rrhh.faltas_config
        """, ttl=0)
        cfg = df_cfg.to_dict("records") if not df_cfg.empty else []
    except Exception:
        cfg = []

    return novs, cfg


def cargar_novedades_admin_mes(conn, mes: date) -> list[dict]:
    """Carga las novedades administrativas registradas por Gerencia para el mes."""
    m_ini = dist.inicio_mes(mes)
    try:
        df = conn.query("""
            SELECT id, periodo_mes, colaborador_id, almacen_codigo, porcentaje_descuento, concepto, observacion
            FROM app.novedades_administrativas
            WHERE periodo_mes = :m
        """, params={"m": m_ini}, ttl=0)
        return df.to_dict("records") if not df.empty else []
    except Exception:
        return []