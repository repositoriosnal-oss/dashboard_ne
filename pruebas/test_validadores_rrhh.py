# -*- coding: utf-8 -*-
"""Prueba OFFLINE de los validadores de Fase 2 (sin base de datos).
Uso:  python pruebas/test_validadores_rrhh.py"""
import sys, types, datetime
import pandas as pd

class _FakeConn:
    def query(self, *a, **k): raise RuntimeError("no hay BD en esta prueba")
    def begin(self): raise RuntimeError("no hay BD en esta prueba")
st = types.ModuleType("streamlit"); st.warning = print; st.set_page_config = lambda **k: None
sqla = types.ModuleType("sqlalchemy"); sqla.text = lambda s: s
cdx = types.ModuleType("src.conexion_db"); cdx.conn = _FakeConn()
sys.modules.update({"streamlit": st, "sqlalchemy": sqla, "src.conexion_db": cdx})
sys.path.insert(0, ".")
from src.rrhh import validar_suma_100

V = datetime.date(2026, 10, 1)
casos = [
    ("Q6 ejemplo del gerente (suma 100)",
     pd.DataFrame({"almacen": ["Q6"]*8, "cargo": ["G","V1","V2","V3","Cajera","Bod","Bod2","Cond"],
                   "pct": [22,16,11,11,8,8,5,8], "vigente_desde": [V]*8}), 0),
    ("Excede 100 (100.5)",
     pd.DataFrame({"almacen": ["A"]*2, "cargo": ["x","y"], "pct": [60.5,40], "vigente_desde": [V]*2}), 1),
    ("Falta (99.5)",
     pd.DataFrame({"almacen": ["A"], "cargo": ["x"], "pct": [99.5], "vigente_desde": [V]}), 1),
    ("Dos almacenes independientes correctos",
     pd.DataFrame({"almacen": ["A","A","B","B"], "cargo": ["x","y","x","y"],
                   "pct": [50,50,70,30], "vigente_desde": [V,V,V,V]}), 0),
    ("Una vigencia buena y otra mala",
     pd.DataFrame({"almacen": ["A","A","A"], "cargo": ["x","y","z"],
                   "pct": [50,50,10], "vigente_desde": [V,V,V]}), 1),
]
fallaron = []
for nombre, df, esperado in casos:
    errs = validar_suma_100(df)
    ok = len(errs) == esperado
    print(f"{'✅' if ok else '❌'} {nombre}: {len(errs)} errores (esperado {esperado})")
    if not ok: fallaron.append(nombre)
print("\nRESULTADO:", "TODO OK" if not fallaron else f"FALLARON: {fallaron}")
sys.exit(0 if not fallaron else 1)