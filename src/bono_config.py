"""
src/bono_config.py
Utilidades de normalización de tipos (numpy -> nativos de Python) y validación
de tramos de metas y escalas de rentabilidad.
"""
import math
from datetime import date, datetime
import numpy as np
import pandas as pd

# ---------------- tipos nativos ----------------

def entero(v):
    """Convierte a int nativo. Falla si el valor es vacío/NaN."""
    if v is None:
        raise ValueError("entero() no acepta valores vacíos")
    try:
        if pd.isna(v):
            raise ValueError("entero() no acepta valores vacíos")
    except (TypeError, ValueError):
        pass
    if isinstance(v, float) and math.isnan(v):
        raise ValueError("entero() no acepta valores vacíos")
    return int(v)

def py(v):
    """Convierte tipos de numpy (float64, bool_, int64) y NaN/NaT a tipos nativos de Python."""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, (np.floating,)):
        return float(v)
    if isinstance(v, (np.bool_,)):
        return bool(v)
    if isinstance(v, (np.integer,)):
        return int(v)
    return v

def fecha(v):
    """Normaliza distintos tipos de fecha a datetime.date nativo."""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
        
    if isinstance(v, pd.Timestamp):
        return v.date()
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, np.datetime64):
        return pd.Timestamp(v).date()
    if isinstance(v, str):
        return pd.to_datetime(v).date()
    
    # Fallback
    try:
        return pd.to_datetime(v).date()
    except Exception:
        return None

# ---------------- editor -> tuplas ----------------

def tuplas_metas(df):
    """Convierte el DataFrame del editor a lista de tuplas para la BD."""
    out = []
    if df is None or df.empty:
        return out
    for _, r in df.iterrows():
        if pd.isna(r.get("umbral_min")):
            continue
        umbral_min = float(r["umbral_min"])
        min_inclusivo = bool(r.get("min_inclusivo", True)) if pd.notna(r.get("min_inclusivo")) else True
        umbral_max = float(r["umbral_max"]) if pd.notna(r.get("umbral_max")) else None
        max_inclusivo = bool(r.get("max_inclusivo", False)) if pd.notna(r.get("max_inclusivo")) else False
        pct_aplica = float(r["pct_aplica"]) if pd.notna(r.get("pct_aplica")) else 0.0
        out.append((umbral_min, min_inclusivo, umbral_max, max_inclusivo, pct_aplica))
    return out

def tuplas_escala(df):
    """Convierte el DataFrame del editor a lista de tuplas para la BD."""
    out = []
    if df is None or df.empty:
        return out
    for _, r in df.iterrows():
        cond_min = float(r["cond_min"]) if pd.notna(r.get("cond_min")) else None
        cond_max = float(r["cond_max"]) if pd.notna(r.get("cond_max")) else None
        pct_bono = float(r["pct_bono"]) if pd.notna(r.get("pct_bono")) else None
        
        # Omitir filas completamente vacías
        if cond_min is None and cond_max is None and pct_bono is None:
            continue
            
        min_inclusivo = bool(r.get("min_inclusivo", True)) if pd.notna(r.get("min_inclusivo")) else True
        max_inclusivo = bool(r.get("max_inclusivo", False)) if pd.notna(r.get("max_inclusivo")) else False
        out.append((cond_min, min_inclusivo, cond_max, max_inclusivo, pct_bono))
    return out

# ---------------- evaluación ----------------

def evaluar(valor, tramos, default):
    """Evalúa un valor contra una lista de tramos. Gana la primera coincidencia."""
    for t in tramos:
        mn, mi, mx, mxi, pct = t
        ok_min = True if mn is None else (valor >= mn if mi else valor > mn)
        ok_max = True if mx is None else (valor <= mx if mxi else valor < mx)
        if ok_min and ok_max:
            return pct
    return default

# ---------------- validación ----------------

def validar_metas(metas):
    """Valida la estructura y lógica de los tramos de meta."""
    errores = []
    avisos = []
    if not metas:
        errores.append("La tabla de metas está vacía.")
        return errores, avisos
        
    for i, t in enumerate(metas):
        mn, mi, mx, mxi, pct = t
        if mn is not None and mx is not None:
            if mn > mx:
                errores.append(f"Fila {i+1}: intervalo vacío (mínimo > máximo).")
            elif mn == mx and not (mi and mxi):
                errores.append(f"Fila {i+1}: intervalo vacío (los extremos no son inclusivos).")
        if pct is None:
            errores.append(f"Fila {i+1}: falta el porcentaje.")
        elif not (0.0 <= pct <= 1.0):
            errores.append(f"Fila {i+1}: porcentaje fuera de rango (debe estar entre 0 y 1).")
            
    # Avisos de huecos
    for i in range(len(metas) - 1):
        _, _, mx1, mxi1, _ = metas[i]
        mn2, mi2, _, _, _ = metas[i+1]
        if mx1 is not None and mn2 is not None:
            if mx1 < mn2:
                avisos.append(f"Hay un hueco sin fila entre {mx1} y {mn2}.")
                
    # Avisos de filas que nunca se aplican (sombras)
    for i in range(1, len(metas)):
        mn_i, _, _, _, _ = metas[i]
        for j in range(i):
            mn_j, _, mx_j, _, _ = metas[j]
            if mx_j is None and mn_j <= mn_i:
                avisos.append(f"Fila {i+1} nunca se aplica porque la Fila {j+1} la cubre completamente.")
                break
                
    return errores, avisos

def validar_escala(escala):
    """Valida la estructura y lógica de la escala de rentabilidad."""
    errores = []
    avisos = []
    if not escala:
        errores.append("La tabla de escala está vacía.")
        return errores, avisos
        
    for i, t in enumerate(escala):
        mn, mi, mx, mxi, pct = t
        if mn is not None and mx is not None:
            if mn > mx:
                errores.append(f"Fila {i+1}: intervalo vacío.")
            elif mn == mx and not (mi and mxi):
                errores.append(f"Fila {i+1}: intervalo vacío.")
        if pct is None:
            errores.append(f"Fila {i+1}: falta el porcentaje.")
        elif not (-2.0 <= pct <= 2.0):
            errores.append(f"Fila {i+1}: porcentaje fuera de rango.")
            
    return errores, avisos
