"""
Pruebas unitarias para la lógica de conciliación, repartición del bono y deducciones cruzadas.
Ejecutar:
    python pruebas/test_bono_reparto.py
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import bono_reparto as br

MES = date(2026, 8, 1)

def test_calculo_deducciones_persona():
    colab_id = 10
    bono_bruto = 2_000_000.0

    descuentos_inv = [
        {"colaborador_id": 10, "monto": 150_000.0, "concepto": "Faltante caja"}
    ]
    # Novedad falta tipo 2 (10% descuento)
    novedades_rrhh = [
        {"colaborador_id": 10, "tipo": "FALTA", "tipo_falta_id": 2, "fecha_inicio": MES, "fecha_fin": MES},
        # 3 días de vacaciones/ausencia = 3 * 3.3333 = 10%
        {"colaborador_id": 10, "tipo": "VACACIONES", "fecha_inicio": date(2026, 8, 10), "fecha_fin": date(2026, 8, 12), "incluye_dominical": False}
    ]
    config_faltas = [
        {"tipo_falta_id": 2, "pct_descuento": 10.0, "vigente_desde": date(2026, 1, 1), "vigente_hasta": None}
    ]
    # Novedad admin 5%
    novedades_admin = [
        {"colaborador_id": 10, "porcentaje_descuento": 5.0, "concepto": "Llegada tarde"}
    ]

    ded = br.calcular_deducciones_persona(
        colaborador_id=colab_id,
        bono_bruto=bono_bruto,
        descuentos_inv=descuentos_inv,
        novedades_rrhh=novedades_rrhh,
        config_faltas=config_faltas,
        novedades_admin=novedades_admin,
        mes=MES
    )

    assert ded["monto_inv_pesos"] == 150_000.0
    assert abs(ded["pct_faltas"] - 10.0) < 1e-2
    assert abs(ded["monto_faltas_pesos"] - 200_000.0) < 1e-2
    assert abs(ded["pct_ausencias"] - 10.0) < 1e-2
    assert abs(ded["monto_ausencias_pesos"] - 200_000.0) < 1e-2
    assert abs(ded["pct_admin"] - 5.0) < 1e-2
    assert abs(ded["monto_admin_pesos"] - 100_000.0) < 1e-2

    total_esperado = 150_000.0 + 200_000.0 + 200_000.0 + 100_000.0 # 650,000
    assert abs(ded["total_descuentos_pesos"] - total_esperado) < 1e-2
    assert abs(ded["bono_neto"] - (bono_bruto - total_esperado)) < 1e-2


def test_consolidar_reparticion_almacen_tres_vendedores():
    reparto_dist = {
        "personas": [
            {
                "colaborador_id": 1, "cedula": "101", "nombre": "Vendedor 1", "cargo": "Vendedor 2",
                "pct_bolsa_cargo": 45.0, "pct_efectivo": 15.0, "dias_participa": 31, "dias_prueba": 0
            },
            {
                "colaborador_id": 2, "cedula": "102", "nombre": "Vendedor 2", "cargo": "Vendedor 2",
                "pct_bolsa_cargo": 45.0, "pct_efectivo": 15.0, "dias_participa": 31, "dias_prueba": 0
            },
            {
                "colaborador_id": 3, "cedula": "103", "nombre": "Vendedor 3", "cargo": "Vendedor 2",
                "pct_bolsa_cargo": 45.0, "pct_efectivo": 15.0, "dias_participa": 31, "dias_prueba": 0
            },
        ]
    }

    valor_bono = 10_000_000.0
    bono_final = 12_000_000.0

    res = br.consolidar_reparticion_almacen(
        almacen_codigo="Q6",
        almacen_nombre="CENTRO 6",
        mes=MES,
        valor_bono_almacen=valor_bono,
        bono_final_almacen=bono_final,
        aplica_meta=True,
        reparto_dist=reparto_dist,
        descuentos_inv=[],
        novedades_rrhh=[],
        config_faltas=[],
        novedades_admin=[]
    )

    assert len(res) == 3
    for v in res:
        # 15% de 10,000,000 = 1,500,000
        assert abs(v["bono_base"] - 1_500_000.0) < 1e-2
        # 15% de (12,000,000 - 10,000,000) = 300,000
        assert abs(v["bono_adicional"] - 300_000.0) < 1e-2
        # 15% de 12,000,000 = 1,800,000
        assert abs(v["bono_bruto"] - 1_800_000.0) < 1e-2
        assert v["bono_neto"] == 1_800_000.0


def test_almacen_no_aplica_meta():
    reparto_dist = {
        "personas": [
            {
                "colaborador_id": 1, "cedula": "101", "nombre": "Vendedor 1", "cargo": "Vendedor 2",
                "pct_bolsa_cargo": 45.0, "pct_efectivo": 15.0, "dias_participa": 31, "dias_prueba": 0
            }
        ]
    }

    res = br.consolidar_reparticion_almacen(
        almacen_codigo="Q6",
        almacen_nombre="CENTRO 6",
        mes=MES,
        valor_bono_almacen=10_000_000.0,
        bono_final_almacen=0.0,
        aplica_meta=False,
        reparto_dist=reparto_dist,
        descuentos_inv=[],
        novedades_rrhh=[],
        config_faltas=[],
        novedades_admin=[]
    )

    assert res[0]["bono_base"] == 0.0
    assert res[0]["bono_bruto"] == 0.0
    assert res[0]["bono_neto"] == 0.0
    assert "no alcanzada" in res[0]["estado"]


def test_descuentos_superan_bono():
    colab_id = 15
    bono_bruto = 1_000_000.0

    descuentos_inv = [
        {"colaborador_id": 15, "monto": 800_000.0}
    ]
    novedades_admin = [
        {"colaborador_id": 15, "porcentaje_descuento": 30.0}
    ]

    ded = br.calcular_deducciones_persona(
        colaborador_id=colab_id,
        bono_bruto=bono_bruto,
        descuentos_inv=descuentos_inv,
        novedades_rrhh=[],
        config_faltas=[],
        novedades_admin=novedades_admin,
        mes=MES
    )

    assert ded["total_descuentos_pesos"] == 1_100_000.0
    assert ded["bono_neto"] == 0.0
    assert ded["saldo_pendiente"] == 100_000.0


def test_dias_distintos_y_suma_componentes():
    reparto_dist = {
        "personas": [
            {
                "colaborador_id": 1, "cedula": "101", "nombre": "Persona 1", "cargo": "Vendedor 1",
                "pct_bolsa_cargo": 30.0, "pct_efectivo": 20.0, "dias_participa": 30, "dias_prueba": 0
            },
            {
                "colaborador_id": 2, "cedula": "102", "nombre": "Persona 2", "cargo": "Vendedor 1",
                "pct_bolsa_cargo": 30.0, "pct_efectivo": 10.0, "dias_participa": 15, "dias_prueba": 0
            }
        ]
    }
    valor_bono = 5_000_000.0
    bono_final = 7_500_000.0

    res = br.consolidar_reparticion_almacen(
        almacen_codigo="ALM128",
        almacen_nombre="NORTE128",
        mes=MES,
        valor_bono_almacen=valor_bono,
        bono_final_almacen=bono_final,
        aplica_meta=True,
        reparto_dist=reparto_dist,
        descuentos_inv=[],
        novedades_rrhh=[],
        config_faltas=[],
        novedades_admin=[]
    )

    p1, p2 = res[0], res[1]
    assert abs(p1["bono_base"] - 1_000_000.0) < 1e-2
    assert abs(p1["bono_adicional"] - 500_000.0) < 1e-2
    assert abs(p1["bono_bruto"] - 1_500_000.0) < 1e-2

    assert abs(p2["bono_base"] - 500_000.0) < 1e-2
    assert abs(p2["bono_adicional"] - 250_000.0) < 1e-2
    assert abs(p2["bono_bruto"] - 750_000.0) < 1e-2

    assert p1["bono_base"] + p1["bono_adicional"] == p1["bono_bruto"]
    assert p2["bono_base"] + p2["bono_adicional"] == p2["bono_bruto"]


if __name__ == "__main__":
    fallos = 0
    for nombre, fn in sorted(globals().items()):
        if nombre.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"OK    {nombre}")
            except AssertionError as e:
                fallos += 1
                print(f"FALLA {nombre}: {e}")
    sys.exit(1 if fallos else 0)