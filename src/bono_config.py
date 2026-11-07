"""
src/bono_config.py
Utilidades de tipos, evaluación de tramos y validación de configuración del bono.
"""
import math
from datetime import date, datetime
import numpy as np
import pandas as pd

# ============================================================
# CONVERSIONES DE TIPOS (para evitar errores con psycopg2 y numpy)
# ============================================================

def entero(v):
    """Convierte a int nativo. Falla con ValueError si es None o NaN."""
    if v is None:
        raise ValueError("Valor vacío.")
    try:
        if pd.isna(v):
            raise ValueError("Valor NaN o NaT.")
    except Exception:
        pass
    return int(v)

def py(v):
    """Convierte tipos de numpy a tipos nativos de Python. Retorna None para NaN/NaT."""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except Exception:
        pass
        
    if isinstance(v, (np.float32, np.float64, float)):
        return float(v)
    if isinstance(v, (np.bool_, bool)):
        return bool(v)
    if isinstance(v, (np.int32, np.int64, int)):
        return int(v)
    return v

def fecha(v):
    """Normaliza cualquier tipo de fecha a datetime.date. Retorna None para nulos."""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except Exception:
        pass
    
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, pd.Timestamp):
        return v.date()
    if isinstance(v, np.datetime64):
        return pd.Timestamp(v).date()
    if isinstance(v, str):
        try:
            return pd.to_datetime(v).date()
        except Exception:
            return None
    return None

# ============================================================
# CONVERSIÓN DE DATAFRAMES A TUPLAS
# ============================================================

def tuplas_metas(df):
    """Convierte un DataFrame de tramos de meta a lista de tuplas, omitiendo filas sin mínimo."""
    out = []
    if df is None or df.empty:
        return out
    for _, r in df.iterrows():
        mn = r.get("umbral_min")
        if pd.isna(mn):
            continue
        mn = float(mn)
        mx = r.get("umbral_max")
        mx = None if pd.isna(mx) else float(mx)
        pct = float(r.get("pct_aplica", 0.0)) if not pd.isna(r.get("pct_aplica")) else 0.0
        
        mi_val = r.get("min_inclusivo")
        mi = bool(mi_val) if not pd.isna(mi_val) else True
        
        mxi_val = r.get("max_inclusivo")
        mxi = bool(mxi_val) if not pd.isna(mxi_val) else False
        
        out.append((mn, mi, mx, mxi, pct))
    return out

def tuplas_escala(df):
    """Convierte un DataFrame de escala de rentabilidad a lista de tuplas, omitiendo filas vacías."""
    out = []
    if df is None or df.empty:
        return out
    for _, r in df.iterrows():
        mn = r.get("cond_min")
        mx = r.get("cond_max")
        pct = r.get("pct_bono")
        
        if pd.isna(mn) and pd.isna(mx) and pd.isna(pct):
            continue
            
        mn = None if pd.isna(mn) else float(mn)
        mx = None if pd.isna(mx) else float(mx)
        pct = float(pct) if not pd.isna(pct) else 0.0
        
        mi_val = r.get("min_inclusivo")
        mi = bool(mi_val) if not pd.isna(mi_val) else True
        
        mxi_val = r.get("max_inclusivo")
        mxi = bool(mxi_val) if not pd.isna(mxi_val) else False
        
        out.append((mn, mi, mx, mxi, pct))
    return out

# ============================================================
# EVALUACIÓN DE TRAMOS
# ============================================================

def evaluar(valor, tramos, default):
    """Retorna el porcentaje de la primera fila que coincida con el valor, o default si ninguna coincide."""
    for (mn, mi, mx, mxi, pct) in tramos:
        ok_min = True if mn is None else (valor >= mn if mi else valor > mn)
        ok_max = True if mx is None else (valor <= mx if mxi else valor < mx)
        if ok_min and ok_max:
            return pct
    return default

# ============================================================
# VALIDACIÓN DE REGLAS DE NEGOCIO
# ============================================================

def validar_metas(metas):
    """
    Valida la integridad de los tramos de meta.
    Retorna (errores, avisos).
    """
    errores = []
    avisos = []
    if not metas:
        return ["La tabla de tramos está vacía."], []
        
    for i, (mn, mi, mx, mxi, pct) in enumerate(metas, 1):
        if pct is None:
            errores.append(f"Fila {i}: falta el porcentaje a aplicar.")
        elif not (0.0 <= pct <= 1.0):
            errores.append(f"Fila {i}: porcentaje fuera de rango (debe estar entre 0 y 1).")
            
        if mn is not None and mx is not None:
            if mn > mx:
                errores.append(f"Fila {i}: intervalo vacío (mínimo mayor que máximo).")
            elif mn == mx and not (mi and mxi):
                errores.append(f"Fila {i}: intervalo vacío (los extremos son iguales pero no ambos inclusivos).")

    # Ordenar por umbral_min para analizar continuidad y sombras
    indexed_metas = sorted(enumerate(metas, 1), key=lambda x: (x[1][0] is None, x[1][0] or 0))
    
    for idx in range(len(indexed_metas) - 1):
        i, (mn1, mi1, mx1, mxi1, pct1) = indexed_metas[idx]
        j, (mn2, mi2, mx2, mxi2, pct2) = indexed_metas[idx+1]
        
        if mx1 is None:
            avisos.append(f"Fila {j}: nunca se aplica porque la Fila {i} no tiene límite superior.")
        else:
            if mn2 > mx1:
                avisos.append(f"Hueco entre Fila {i} y Fila {j}: sin fila que cubra valores entre {mx1} y {mn2}.")
            elif mn2 == mx1:
                if not mxi1 and not mi2:
                    avisos.append(f"Hueco entre Fila {i} y Fila {j}: sin fila que cubra el valor exacto {mx1}.")

    # =========================================================
    # AVISO: Techo finito (valores altos sin cubrir)
    # =========================================================
    # Si TODOS los tramos tienen un umbral_max definido (ninguno es None),
    # significa que existe un tope máximo y las ventas superiores no tendrán bono.
    if all(mx is not None for _, _, mx, _, _ in metas):
        max_cubierto = max(mx for _, _, mx, _, _ in metas)
        avisos.append(
            f"Los tramos solo cubren hasta {max_cubierto:,.0f}. "
            "Las ventas superiores a este valor no aplicarán a ningún tramo (quedarán en 0%)."
        )


    return errores, avisos

def validar_escala(escala):
    """
    Valida la integridad de la escala de rentabilidad.
    Retorna (errores, avisos).
    """
    errores = []
    avisos = []
    if not escala:
        return ["La tabla de escala está vacía."], []
        
    for i, (mn, mi, mx, mxi, pct) in enumerate(escala, 1):
        if pct is None:
            errores.append(f"Fila {i}: falta el porcentaje de bono.")
        elif not (-1.0 <= pct <= 2.0):
            errores.append(f"Fila {i}: porcentaje fuera de rango (debe estar entre -1.0 y 2.0).")
            
        if mn is not None and mx is not None:
            if mn > mx:
                errores.append(f"Fila {i}: intervalo vacío (mínimo mayor que máximo).")
            elif mn == mx and not (mi and mxi):
                errores.append(f"Fila {i}: intervalo vacío (los extremos son iguales pero no ambos inclusivos).")
                
    return errores, avisos