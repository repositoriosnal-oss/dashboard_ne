"""
MATERIALES & TRM · COMPRAS — TABLERO FINAL (TV + análisis).

MODO TV (por defecto, para el televisor: sin mouse, sin controles, rota solo con CSS)
  Ruta base:  /
  Opcionales por URL (nadie los toca en la TV):
    &paso=12          segundos por lámina del carrusel (6..60)
    &theme=light      tema claro (por defecto oscuro)
    &moneda=USD|COP   fuerza una moneda en el carrusel (por defecto alterna USD/COP)
    &reload=60        minutos entre recargas completas de página (0 = nunca)
    &pvciva=1         PVC con IVA chino incluido (por defecto SIN IVA 13 %, comparable
                      con referencias internacionales)
    &pvc_csv=URL      CSV propio fecha,usd_t que reemplaza la fuente del PVC

MODO ANÁLISIS (escritorio, con pestañas y controles)
  Ruta:  /?analisis=1

INSUMOS VALIDADOS (materia prima → contrato → unidad de bolsa)
  Tubería EMT (conduit metálico)  → Acero HRC  HRC=F  NYMEX/CRU   USD por tonelada CORTA (907,18 kg)
                                    El EMT se conforma en frío y se solda ERW a partir de
                                    bobina laminada en caliente (Wheatland / Western Tube).
  Tubería PVC (conduit plástico)  → Resina PVC V0     DCE Dalian  CNY por tonelada MÉTRICA
                                    Grado tubería = PVC suspensión SG-5/K-67. Los futuros de
                                    Dalian cotizan con IVA (13 %) incluido: por defecto se
                                    muestra sin IVA para comparar con referencias internacionales.
  Conductor eléctrico             → Cobre      HG=F   COMEX       USD por LIBRA
  Perfiles / conductor            → Aluminio   ALI=F  COMEX       USD por tonelada MÉTRICA
  Dólar                           → TRM oficial Superfinanciera (datos.gov.co)  COP por USD

REGLAS DE CÁLCULO
  COP = USD × TRM vigente EN LA MISMA FECHA del precio (merge hacia atrás; jamás se usa
  una vigencia futura para valorar hoy). 1 lb = 0,45359237 kg · 1 t corta = 907,18474 kg.
  Los futuros son referencia de tendencia y negociación, no tarifa de compra (faltan
  galvanizado, conformado, recubrimiento, flete, arancel e IVA local).
"""
from __future__ import annotations

import io
import json
import math
import os
import re
import time
from datetime import datetime, timedelta, timezone
from html import escape

import numpy as np
import pandas as pd
import requests
import streamlit as st

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    _PLT = True
except Exception:
    _PLT = False

try:
    st.set_page_config(page_title="Materiales y TRM · Compras", page_icon="\U0001F4C8",
                       layout="wide", initial_sidebar_state="collapsed")
except Exception:
    pass

# ============================================================
# CONSTANTES
# ============================================================
LB_KG, ST_KG = 0.45359237, 907.18474
IVA_CN = 1.13                       # los futuros de Dalian cotizan con IVA 13 % incluido
BOGOTA = timezone(timedelta(hours=-5))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}

# kg_u = kilos por unidad de bolsa · u = nombre de esa unidad · nat = moneda nativa
MAT = {
    "Cobre":      dict(src="yahoo", ticker="HG=F",  kg_u=LB_KG,   u="lb",      nat="USD",
                       bolsa="COMEX HG",    uso="Conductor eléctrico",
                       fred="PCOPPUSDM",    inv="Copper Futures (HG)"),
    "Aluminio":   dict(src="yahoo", ticker="ALI=F", kg_u=1000.0,  u="t",       nat="USD",
                       bolsa="COMEX ALI",   uso="Conductor / perfiles",
                       fred="PALUMUSDM",    inv="Aluminum Futures (COMEX: ALI)"),
    "Acero HRC":  dict(src="yahoo", ticker="HRC=F", kg_u=ST_KG,   u="t corta", nat="USD",
                       bolsa="NYMEX HRC (CRU)", uso="Insumo tubería EMT",
                       fred=None,           inv="US Midwest Domestic HRC Steel (CRU) Futures"),
    "Resina PVC": dict(src="sina",  ticker="V0",    kg_u=1000.0,  u="t",       nat="CNY",
                       bolsa="DCE PVC (V0)", uso="Insumo tubería PVC",
                       fred=None,           inv="Polyvinyl Chloride Futures (DPVc1)"),
}
TRM = "Dólar TRM"
SERIES = list(MAT) + [TRM]
RANGOS = {"1A": 365, "2A": 730, "5A": 1826}
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
DIAS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]

PAL = {
    "Oscuro": dict(bg="#0C0F12", glow1="#F58A47", glow2="#3FD9B4",
                   glass="rgba(255,255,255,.055)", glass2="rgba(255,255,255,.035)",
                   panel="rgba(255,255,255,.055)", panel2="rgba(255,255,255,.035)",
                   line="rgba(255,255,255,.10)", txt="#EEF3F8", mut="#93A2B0",
                   up="#FF7A7A", down="#4BE0A8", warn="#F7C566", on="#0C0F12",
                   serie={"Cobre": "#F58A47", "Aluminio": "#79C6F5", "Acero HRC": "#C3CEDA",
                          "Resina PVC": "#C4A2F7", TRM: "#3FD9B4"}),
    "Claro":  dict(bg="#EDF0F3", glow1="#C75D17", glow2="#0B8C75",
                   glass="rgba(255,255,255,.62)", glass2="rgba(255,255,255,.42)",
                   panel="rgba(255,255,255,.62)", panel2="rgba(255,255,255,.42)",
                   line="rgba(16,24,32,.10)", txt="#111A22", mut="#5C6B79",
                   up="#C62828", down="#0F7A52", warn="#A96A00", on="#FFFFFF",
                   serie={"Cobre": "#C75D17", "Aluminio": "#1D7FBF", "Acero HRC": "#5B6F84",
                          "Resina PVC": "#7B4BB7", TRM: "#0B8C75"}),
}


# ============================================================
# FORMATO es-CO (miles con punto, decimales con coma) Y UNIDADES
# ============================================================
def num(v, dec=0, signo=False) -> str:
    if v is None or pd.isna(v):
        return "\u2014"
    s = f"{v:+,.{dec}f}" if signo else f"{v:,.{dec}f}"
    return s.replace(",", "\u00a7").replace(".", ",").replace("\u00a7", ".")


def cop_corto(v) -> str:
    if v is None or pd.isna(v):
        return "\u2014"
    return f"$ {num(v / 1e6, 2)} M" if abs(v) >= 1e6 else "$ " + num(v, 0)


def rgba(hexc: str, a: float) -> str:
    h = hexc.lstrip("#")
    return f"rgba({int(h[0:2],16)},{int(h[2:4],16)},{int(h[4:6],16)},{a})"


def hoy_bogota() -> pd.Timestamp:
    return pd.Timestamp(datetime.now(BOGOTA).date())


def f_corta(ts) -> str:
    if ts is None or pd.isna(ts):
        return "\u2014"
    return f"{ts.day} {MESES[ts.month-1]}" + ("" if ts.year == hoy_bogota().year else f" {str(ts.year)[2:]}")


def qp(nombre: str, defecto: str = "") -> str:
    return str(st.query_params.get(nombre, defecto)).strip()


def md(h: str):
    st.markdown(h, unsafe_allow_html=True)


def factor(nom: str, unidad: str) -> float:
    kg = MAT[nom]["kg_u"]
    return {"bolsa": 1.0, "kg": 1.0 / kg, "t": 1000.0 / kg}[unidad]


def u_nom(nom: str, unidad: str) -> str:
    return {"bolsa": MAT[nom]["u"], "kg": "kg", "t": "t"}[unidad]


def dec(nom: str, cur: str, unidad: str) -> int:
    if nom == TRM:
        return 2
    if cur == "COP" or unidad == "t":
        return 0
    if unidad == "kg":
        return 3
    return 2 if MAT[nom]["kg_u"] >= 1 else 4


def etiqueta(nom: str, cur: str, unidad: str) -> str:
    return "COP por USD" if nom == TRM else f"{cur}/{u_nom(nom, unidad)}"


def precio(v, nom, cur, unidad) -> str:
    if v is None or pd.isna(v):
        return "\u2014"
    pref = "US$ " if (cur == "USD" and nom != TRM) else "$ "
    return pref + num(v, dec(nom, cur, unidad))


def eje(v, nom, cur, unidad) -> str:
    if v is None or pd.isna(v):
        return "\u2014"
    pref = "US$ " if (cur == "USD" and nom != TRM) else "$ "
    d = 0 if (cur == "COP" or unidad == "t" or nom == TRM) else 2
    return pref + num(v, d)


def formateadores(nom, cur, unidad):
    return (lambda v: eje(v, nom, cur, unidad)), (lambda v: precio(v, nom, cur, unidad))


# ============================================================
# FUENTES (solo lectura; un fallo NO se guarda en caché: se conserva el último dato bueno)
# ============================================================
@st.cache_resource
def _respaldo() -> dict:
    return {"datos": {}, "fallo": {}}


@st.cache_data(ttl=300, show_spinner=False)
def _yahoo(ticker: str) -> pd.DataFrame:
    err = None
    for host in ("query1", "query2"):
        try:
            r = requests.get(f"https://{host}.finance.yahoo.com/v8/finance/chart/{ticker}",
                             params={"range": "max", "interval": "1d"}, headers=UA, timeout=25)
            r.raise_for_status()
            res = r.json()["chart"]["result"][0]
            ts = res.get("timestamp") or []
            cl = res["indicators"]["quote"][0].get("close") or []
            off = int((res.get("meta") or {}).get("gmtoffset") or 0)   # fecha en la zona de la bolsa
            if not ts or len(ts) != len(cl):
                raise ValueError("serie vacía")
            f = pd.to_datetime(pd.Series(ts, dtype="int64") + off, unit="s").dt.normalize()
            df = pd.DataFrame({"fecha": f.astype("datetime64[ns]"),
                               "nat": pd.to_numeric(pd.Series(cl), errors="coerce")})
            df = df.dropna().drop_duplicates("fecha", keep="last").sort_values("fecha").reset_index(drop=True)
            if df.empty:
                raise ValueError("sin cierres")
            return df
        except Exception as e:
            err = e
    raise RuntimeError(f"Yahoo {ticker}: {err}")


@st.cache_data(ttl=900, show_spinner=False)
def _sina(simbolo: str) -> pd.DataFrame:
    """Contrato continuo principal de bolsas chinas (DCE: V0 = PVC). Acepta JSONP con filas
    {d,...,c} o [fecha, abre, máx, mín, cierre, ...]."""
    url = ("https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20" + simbolo +
           "=/InnerFuturesNewService.getDailyKLine")
    r = requests.get(url, params={"symbol": simbolo},
                     headers={**UA, "Referer": "https://finance.sina.com.cn"}, timeout=25)
    r.raise_for_status()
    txt = r.text
    filas = []
    try:
        bloque = txt[txt.find("["): txt.rfind("]") + 1]
        for it in json.loads(bloque):
            if isinstance(it, dict):
                filas.append((it.get("d") or it.get("date"), it.get("c") or it.get("close")))
            else:
                filas.append((it[0], it[4]))
    except Exception:
        filas = re.findall(r'"?d"?\s*:\s*"(\d{4}-\d{2}-\d{2})".*?"?c"?\s*:\s*"?([\d.]+)', txt)
    df = pd.DataFrame(filas, columns=["fecha", "nat"])
    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce").astype("datetime64[ns]")
    df["nat"] = pd.to_numeric(df["nat"], errors="coerce")
    df = df[df["nat"] > 0].dropna().drop_duplicates("fecha", keep="last").sort_values("fecha").reset_index(drop=True)
    if len(df) < 50:
        raise ValueError("Sina devolvió pocos datos")
    return df


@st.cache_data(ttl=900, show_spinner=False)
def _csv(url: str) -> pd.DataFrame:
    """Cotización propia opcional del PVC: CSV con columnas fecha,usd_t (USD por tonelada métrica)."""
    r = requests.get(url, headers=UA, timeout=25)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text)).iloc[:, :2]
    df.columns = ["fecha", "nat"]
    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce").astype("datetime64[ns]")
    df["nat"] = pd.to_numeric(df["nat"], errors="coerce")
    df = df[df["nat"] > 0].dropna().drop_duplicates("fecha", keep="last").sort_values("fecha").reset_index(drop=True)
    if df.empty:
        raise ValueError("CSV vacío")
    return df


@st.cache_data(ttl=1800, show_spinner=False)
def _trm() -> pd.DataFrame:
    err = None
    for rid in ("32sa-8pi3", "mcec-87by"):
        try:
            r = requests.get(f"https://www.datos.gov.co/resource/{rid}.json",
                             params={"$select": "valor,vigenciadesde",
                                     "$where": "vigenciadesde >= '1999-01-01T00:00:00.000'",
                                     "$order": "vigenciadesde DESC", "$limit": 20000},
                             headers=UA, timeout=40)
            r.raise_for_status()
            raw = pd.DataFrame(r.json())
            if raw.empty or "vigenciadesde" not in raw.columns:
                raise ValueError("respuesta vacía")
            raw["fecha"] = (pd.to_datetime(raw["vigenciadesde"], errors="coerce")
                            .dt.normalize().astype("datetime64[ns]"))
            raw["trm"] = pd.to_numeric(raw["valor"], errors="coerce")
            df = (raw[["fecha", "trm"]].dropna().drop_duplicates("fecha")
                  .sort_values("fecha").reset_index(drop=True))
            if df.empty:
                raise ValueError("sin valores")
            return df
        except Exception as e:
            err = e
    raise RuntimeError(f"TRM: {err}")


@st.cache_data(ttl=21600, show_spinner=False)
def _fred(sid: str) -> pd.DataFrame:
    r = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv", params={"id": sid}, headers=UA, timeout=30)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text)).iloc[:, :2]
    df.columns = ["fecha", "valor"]
    df["fecha"] = pd.to_datetime(df["fecha"], errors="coerce").astype("datetime64[ns]")
    df["valor"] = pd.to_numeric(df["valor"], errors="coerce")
    df = df.dropna()
    if df.empty:
        raise ValueError("vacío")
    return df


def _con_respaldo(clave: str, fn, *a):
    """(df, usando_respaldo). Tras un fallo no reintenta durante 2 min para no frenar el tablero."""
    bak = _respaldo()
    if time.time() - bak["fallo"].get(clave, 0) < 120:
        return (bak["datos"][clave], True) if clave in bak["datos"] else (pd.DataFrame(), False)
    try:
        df = fn(*a)
        bak["datos"][clave] = df
        bak["fallo"].pop(clave, None)
        return df, False
    except Exception:
        bak["fallo"][clave] = time.time()
        return (bak["datos"][clave], True) if clave in bak["datos"] else (pd.DataFrame(), False)


def _est(df, respaldo, hoy) -> dict:
    if df is None or df.empty:
        return dict(ok=False, n=0, fecha=None, dias=999, respaldo=False)
    vig = df["fecha"][df["fecha"] <= hoy]
    ult = vig.max() if len(vig) else df["fecha"].max()
    return dict(ok=True, n=len(df), fecha=ult, dias=int((hoy - ult).days), respaldo=respaldo)


def _url_pvc() -> str:
    u = qp("pvc_csv") or os.environ.get("PVC_CSV_URL", "")
    if not u:
        try:
            u = str(st.secrets.get("PVC_CSV_URL", ""))
        except Exception:
            u = ""
    return u.strip()


@st.cache_data(ttl=300, show_spinner=False)
def panel(sin_iva_pvc: bool):
    """series[nombre] = DataFrame(fecha, nat, usd, trm, cop) POR UNIDAD DE BOLSA.
    usd = nat (o nat/USDCNY, sin IVA si aplica); cop = usd × TRM de ESA misma fecha."""
    hoy = hoy_bogota()
    trm, trm_r = _con_respaldo("trm", _trm)
    estado = {TRM: _est(trm, trm_r, hoy)}
    t_ok = trm[trm["fecha"] <= hoy].sort_values("fecha") if not trm.empty else trm   # jamás vigencias futuras
    fx, fx_r = _con_respaldo("CNY=X", _yahoo, "CNY=X")
    estado["USD/CNY"] = _est(fx, fx_r, hoy)

    series = {}
    for nom, cfg in MAT.items():
        propio = (nom == "Resina PVC" and _url_pvc())
        if propio:
            raw, resp = _con_respaldo("pvc_csv", _csv, _url_pvc())
            mon = "USD"
        elif cfg["src"] == "sina":
            raw, resp = _con_respaldo("sina:" + cfg["ticker"], _sina, cfg["ticker"])
            mon = cfg["nat"]
        else:
            raw, resp = _con_respaldo(cfg["ticker"], _yahoo, cfg["ticker"])
            mon = cfg["nat"]
        estado[nom] = _est(raw, resp, hoy)
        if raw.empty:
            continue
        d = raw.sort_values("fecha").copy()
        if mon == "CNY":
            if fx.empty:
                estado[nom] = dict(estado[nom], ok=False)
                continue
            d = pd.merge_asof(d, fx.rename(columns={"nat": "fx"}).sort_values("fecha"),
                              on="fecha", direction="backward")
            d["usd"] = d["nat"] / d["fx"] / (IVA_CN if sin_iva_pvc else 1.0)
            d = d.dropna(subset=["usd"]).drop(columns="fx")
        else:
            d["usd"] = d["nat"]
        if not t_ok.empty:
            d = pd.merge_asof(d.sort_values("fecha"), t_ok, on="fecha", direction="backward")
            d["cop"] = d["usd"] * d["trm"]
        else:
            d["trm"], d["cop"] = np.nan, np.nan
        d["mon"] = mon
        series[nom] = d.reset_index(drop=True)
    return series, trm, estado, hoy


def fred_serie(sid: str) -> pd.DataFrame:
    df, _ = _con_respaldo("fred:" + sid, _fred, sid)
    return df


# ============================================================
# ANALÍTICA
# ============================================================
def vistas(series, trm, hoy, cur, unidad) -> dict:
    out = {}
    for nom in MAT:
        if nom in series:
            s = series[nom].set_index("fecha")["usd" if cur == "USD" else "cop"].dropna() * factor(nom, unidad)
            if len(s):
                out[nom] = s
    if not trm.empty:
        out[TRM] = trm[trm["fecha"] <= hoy].set_index("fecha")["trm"].dropna()
    return {n: out[n] for n in SERIES if n in out}


def var_pct(s: pd.Series, dias: int):
    s = s.dropna()
    if len(s) < 2:
        return None
    prev = s[s.index <= s.index[-1] - pd.Timedelta(days=dias)]
    return (s.iloc[-1] / prev.iloc[-1] - 1) * 100 if len(prev) else None


def kpis(s: pd.Series) -> dict:
    s = s.dropna()
    if len(s) < 2:
        return {}
    ult = float(s.iloc[-1])
    out = {"ult": ult, "fecha": s.index[-1],
           "1d": (ult / float(s.iloc[-2]) - 1) * 100 if s.iloc[-2] else None}
    for k, d in (("7d", 7), ("30d", 30), ("90d", 90), ("12m", 365)):
        out[k] = var_pct(s, d)
    y = s[s.index < pd.Timestamp(s.index[-1].year, 1, 1)]
    out["YTD"] = (ult / float(y.iloc[-1]) - 1) * 100 if len(y) else None
    w = s[s.index >= s.index[-1] - pd.Timedelta(days=365)]
    lo, hi = float(w.min()), float(w.max())
    out.update(lo=lo, hi=hi, pos=(ult - lo) / (hi - lo) if hi > lo else .5)
    return out


def ventana(s: pd.Series, dias) -> pd.Series:
    return s if (dias is None or s.empty) else s[s.index >= s.index.max() - pd.Timedelta(days=dias)]


def base100(df: pd.DataFrame, cols=None) -> pd.DataFrame:
    """Rebase robusto: primera fecha COMÚN, series sin datos descartadas."""
    if df is None or df.empty:
        return pd.DataFrame()
    presentes = [c for c in (cols or df.columns) if c in df.columns]
    if not presentes:
        return pd.DataFrame()
    sub = df[presentes].sort_index().ffill()
    primeros = {c: sub[c].first_valid_index() for c in presentes}
    ok = [c for c, p in primeros.items() if p is not None]
    if not ok:
        return pd.DataFrame()
    ini = max(primeros[c] for c in ok)
    sub = sub.loc[sub.index >= ini, ok].dropna()
    return pd.DataFrame() if sub.empty else sub.div(sub.iloc[0]) * 100.0


def unir(su: dict, dias) -> pd.DataFrame:
    wide = None
    for nom, s in su.items():
        s2 = ventana(s, dias).rename(nom).to_frame()
        wide = s2 if wide is None else wide.join(s2, how="outer")
    return wide if wide is not None else pd.DataFrame()


# ============================================================
# SVG (escalan solos con el contenedor; nitidez total en pantallas 4K)
# ============================================================
def ticks_y(lo, hi, n=5):
    if not np.isfinite(lo) or not np.isfinite(hi):
        lo, hi = 0.0, 1.0
    if hi <= lo:
        hi = lo + max(abs(lo) * .01, 1.0)
    raw = (hi - lo) / max(n - 1, 1)
    mag = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1.0
    step = next((m * mag for m in (1, 2, 2.5, 5, 10) if raw <= m * mag * 1.0001), 10 * mag)
    a, b = math.floor(lo / step) * step, math.ceil(hi / step) * step
    return [a + i * step for i in range(int(round((b - a) / step)) + 1)], step


def ticks_x(d0, d1):
    span = max((d1 - d0).days, 1)
    if span <= 400:
        t = pd.date_range(pd.Timestamp(d0.year, d0.month, 1), d1, freq="1MS")
        return [(x, f"{MESES[x.month-1]} {str(x.year)[2:]}" if (x.month == 1 or i == 0)
                 else MESES[x.month - 1]) for i, x in enumerate(t) if x >= d0]
    if span <= 2200:
        t = pd.date_range(pd.Timestamp(d0.year, ((d0.month - 1) // 3) * 3 + 1, 1), d1, freq="3MS")
        return [(x, f"{MESES[x.month-1]} {str(x.year)[2:]}" if (x.month == 1 or i == 0)
                 else MESES[x.month - 1]) for i, x in enumerate(t) if x >= d0]
    k = max(1, math.ceil(span / 365.25 / 8))
    y0 = ((d0.year + k - 1) // k) * k
    return [(x, str(x.year)) for x in pd.date_range(pd.Timestamp(y0, 1, 1), d1, freq=f"{k}YS")]


def _muestrear(s, n):
    return s if len(s) <= n else s.iloc[np.unique(np.linspace(0, len(s) - 1, n).round().astype(int))]


def _pts(xs, ys):
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys))


def _repartir(ys, gap, lo, hi):
    orden = sorted(range(len(ys)), key=lambda i: ys[i])
    pos = [ys[i] for i in orden]
    for k in range(1, len(pos)):
        pos[k] = max(pos[k], pos[k - 1] + gap)
    if pos and pos[-1] > hi:
        d = pos[-1] - hi
        pos = [p - d for p in pos]
        for k in range(len(pos) - 2, -1, -1):
            pos[k] = min(pos[k], pos[k + 1] - gap)
    res = [0.0] * len(ys)
    for k, i in enumerate(orden):
        res[i] = max(pos[k], lo)
    return res


def svg_spark(s, color, uid):
    s = _muestrear(s.dropna(), 120)
    if len(s) < 2:
        return ""
    W, H = 300, 34
    lo, hi = float(s.min()), float(s.max())
    rng = (hi - lo) or 1.0
    xs = np.linspace(2, W - 6, len(s))
    ys = (H - 4) - (s.values - lo) / rng * (H - 9)
    p = _pts(xs, ys)
    return (f'<svg viewBox="0 0 {W} {H}" class="sp" preserveAspectRatio="none">'
            f'<defs><linearGradient id="s{uid}" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset="0" stop-color="{color}" stop-opacity=".34"/>'
            f'<stop offset="1" stop-color="{color}" stop-opacity="0"/></linearGradient></defs>'
            f'<polygon points="{p} {xs[-1]:.1f},{H} {xs[0]:.1f},{H}" fill="url(#s{uid})"/>'
            f'<polyline points="{p}" fill="none" stroke="{color}" stroke-width="2.2" '
            f'stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>'
            f'<circle cx="{xs[-1]:.1f}" cy="{ys[-1]:.1f}" r="3.4" fill="{color}"/></svg>')


def _ejes(L, T, pw, ph, tk, ymin, ymax, fy, t0, t1, H, span, cero=False):
    out = []
    for v in tk:
        y = T + (ymax - v) / (ymax - ymin) * ph
        cls = "gl g0" if (cero and abs(v) < 1e-9) else "gl"
        out.append(f'<line x1="{L}" x2="{L+pw}" y1="{y:.1f}" y2="{y:.1f}" class="{cls}"/>'
                   f'<text x="{L-14}" y="{y+6:.1f}" text-anchor="end" class="ax">{fy(v)}</text>')
    for t, lab in ticks_x(t0, t1):
        x = L + (t - t0).days / span * pw
        out.append(f'<text x="{x:.1f}" y="{H-12}" text-anchor="middle" class="ax">{lab}</text>')
    return "".join(out)


def svg_comparar(b, P):
    if b.empty:
        return '<div class="vacio">Sin fechas comunes suficientes.</div>'
    W, H, L, R, T, B = 1000, 560, 82, 230, 18, 44
    pw, ph = W - L - R, H - T - B
    pct = b - 100.0
    t0, t1 = pct.index[0], pct.index[-1]
    span = max((t1 - t0).days, 1)
    tk, step = ticks_y(min(float(np.nanmin(pct.values)), 0.0), max(float(np.nanmax(pct.values)), 0.0), 6)
    ymin, ymax = tk[0], tk[-1]
    d = 0 if step >= 1 else 1
    out = [_ejes(L, T, pw, ph, tk, ymin, ymax, lambda v: num(v, d, True) + " %", t0, t1, H, span, True)]
    ends = []
    for c in pct.columns:
        s = _muestrear(pct[c].dropna(), 520)
        if len(s) < 2:
            continue
        xs = L + np.asarray((s.index - t0).days, dtype=float) / span * pw
        ys = T + (ymax - s.values) / (ymax - ymin) * ph
        col = P["serie"][c]
        out.append(f'<polyline points="{_pts(xs,ys)}" fill="none" stroke="{col}" stroke-width="3.4" '
                   f'stroke-linejoin="round" stroke-linecap="round"/>')
        ends.append((c, col, float(xs[-1]), float(ys[-1]), float(s.iloc[-1])))
    for (c, col, xe, ye, v), yl in zip(ends, _repartir([e[3] for e in ends], 66, T + 26, T + ph - 10)):
        xl = L + pw + 30
        fl, fc = ("\u25b2", P["up"]) if v > 0 else ("\u25bc", P["down"])
        out.append(f'<polyline points="{xe:.1f},{ye:.1f} {xl-10},{yl-9:.1f}" class="cn"/>'
                   f'<circle cx="{xe:.1f}" cy="{ye:.1f}" r="6.5" fill="{col}" stroke="{P["bg"]}" stroke-width="2.5"/>'
                   f'<text x="{xl}" y="{yl-12:.1f}" class="eln" fill="{col}">{escape(c)}</text>'
                   f'<text x="{xl}" y="{yl+21:.1f}" class="elv">'
                   f'<tspan fill="{fc}" class="fl">{fl}</tspan> {num(v,1,True)} %</text>')
    return f'<svg viewBox="0 0 {W} {H}" class="hs">{"".join(out)}</svg>'


def svg_serie(s, nom, cur, unidad, color, P, uid, s_cop=None):
    s = s.dropna()
    if len(s) < 3:
        return '<div class="vacio">Sin datos suficientes.</div>'
    W, H, L, R, T, B = 1000, 560, 104, 190, 18, 44
    pw, ph = W - L - R, H - T - B
    t0, t1 = s.index[0], s.index[-1]
    span = max((t1 - t0).days, 1)
    lo, hi = float(s.min()), float(s.max())
    pad = (hi - lo) * .07 or abs(hi) * .02 or 1.0
    tk, _ = ticks_y(lo - pad, hi + pad, 6)
    ymin, ymax = tk[0], tk[-1]
    X = lambda idx: L + np.asarray((pd.DatetimeIndex(idx) - t0).days, dtype=float) / span * pw
    Y = lambda v: T + (ymax - np.asarray(v, dtype=float)) / (ymax - ymin) * ph
    fy = lambda v: eje(v, nom, cur, unidad)
    out = [_ejes(L, T, pw, ph, tk, ymin, ymax, fy, t0, t1, H, span)]
    ss = _muestrear(s, 520)
    xs, ys = X(ss.index), Y(ss.values)
    p = _pts(xs, ys)
    out.append(f'<defs><linearGradient id="a{uid}" x1="0" y1="0" x2="0" y2="1">'
               f'<stop offset="0" stop-color="{color}" stop-opacity=".36"/>'
               f'<stop offset="1" stop-color="{color}" stop-opacity="0"/></linearGradient></defs>'
               f'<polygon points="{p} {xs[-1]:.1f},{T+ph} {xs[0]:.1f},{T+ph}" fill="url(#a{uid})"/>')
    if len(s) > 45:
        ma = _muestrear(s.rolling(30, min_periods=15).mean().dropna(), 520)
        if len(ma) > 2:
            out.append(f'<polyline points="{_pts(X(ma.index),Y(ma.values))}" fill="none" stroke="{P["mut"]}" '
                       f'stroke-width="1.9" stroke-dasharray="7 7"/>'
                       f'<line x1="{L+14}" x2="{L+46}" y1="{T+16}" y2="{T+16}" stroke="{P["mut"]}" '
                       f'stroke-width="1.9" stroke-dasharray="7 7"/>'
                       f'<text x="{L+54}" y="{T+22}" class="ax">Media 30 días</text>')
    out.append(f'<polyline points="{p}" fill="none" stroke="{color}" stroke-width="3.8" '
               f'stroke-linejoin="round" stroke-linecap="round"/>')

    def marca(idx, val, etq, arriba):
        x, y = float(X([idx])[0]), float(Y([val])[0])
        anc = "end" if x > L + pw * .7 else ("start" if x < L + pw * .3 else "middle")
        return (f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5.5" fill="{P["bg"]}" stroke="{color}" stroke-width="2.8"/>'
                f'<text x="{x:.1f}" y="{y+(-15 if arriba else 29):.1f}" text-anchor="{anc}" class="mk">'
                f'{etq} {fy(val)} · {f_corta(idx)}</text>')

    out.append(marca(s.idxmax(), hi, "Máx", True))
    if s.idxmin() != s.idxmax():
        out.append(marca(s.idxmin(), lo, "Mín", False))
    xe, ye = float(xs[-1]), float(ys[-1])
    yb = min(max(ye, T + 26), T + ph - 26)
    out.append(f'<circle cx="{xe:.1f}" cy="{ye:.1f}" r="12" fill="{color}" opacity=".22"/>'
               f'<circle cx="{xe:.1f}" cy="{ye:.1f}" r="6.5" fill="{color}"/>'
               f'<rect x="{xe+16:.1f}" y="{yb-21:.1f}" width="164" height="42" rx="21" fill="{color}"/>'
               f'<text x="{xe+98:.1f}" y="{yb+9:.1f}" text-anchor="middle" class="bub" fill="{P["on"]}">'
               f'{precio(float(s.iloc[-1]), nom, cur, unidad)}</text>')
    if s_cop is not None and len(s_cop):
        yb2 = yb + 50 if yb < T + ph - 80 else yb - 50
        out.append(f'<rect x="{xe+16:.1f}" y="{yb2-19:.1f}" width="164" height="38" rx="19" fill="none" '
                   f'stroke="{color}" stroke-width="2"/>'
                   f'<text x="{xe+98:.1f}" y="{yb2+8:.1f}" text-anchor="middle" class="bub2" fill="{color}">'
                   f'{precio(float(s_cop.iloc[-1]), nom, "COP", unidad)}</text>')
    return f'<svg viewBox="0 0 {W} {H}" class="hs">{"".join(out)}</svg>'


# ============================================================
# HTML DEL TABLERO
# ============================================================
def chip(v, etq=""):
    et = f'<span class="cl">{etq}</span>' if etq else ""
    if v is None or pd.isna(v):
        return f'<span class="chip mu">{et}s/d</span>'
    if abs(v) < .005:
        return f'<span class="chip mu">{et}<b>•</b> 0,00 %</span>'
    cls, fl = ("up", "\u25b2") if v > 0 else ("dn", "\u25bc")
    return f'<span class="chip {cls}">{et}<b>{fl}</b> {num(v,2,True)} %</span>'


def heat(v, P, tope=15.0):
    if v is None or pd.isna(v):
        return '<td class="mu">s/d</td>'
    if abs(v) < .005:
        return "<td>0,0</td>"
    a = .10 + min(abs(v) / tope, 1.0) * .48
    return f'<td style="background:{rgba(P["up"] if v>0 else P["down"], a)}">{num(v,1,True)}</td>'


def _flip_spans(a: str, b: str) -> str:
    return f'<div class="flip"><span class="fa">{a}</span><span class="fb">{b}</span></div>'


def flip_html(nom) -> str:
    """Línea que alterna sola: kg ↔ tonelada (USD y COP); TRM ↔ próxima vigencia."""
    series, trm = st.session_state["_panel"]
    if nom == TRM:
        hoy = hoy_bogota()
        vig = trm[trm["fecha"] <= hoy]
        if vig.empty:
            return ""
        t = float(vig["trm"].iloc[-1])
        fut = trm[trm["fecha"] > hoy].head(1)
        a = f'<b>hoy</b> · US$ 100 = $ {num(t*100,0)}'
        if len(fut):
            b = (f'<b>próxima TRM</b> · $ {num(float(fut["trm"].iloc[0]),2)} '
                 f'desde el {f_corta(fut["fecha"].iloc[0])}')
        else:
            b = f'<b>hoy</b> · US$ 1.000 = $ {num(t*1000,0)}'
        return _flip_spans(a, b)
    d = series[nom]
    u = d.iloc[-1]
    fk, ft = factor(nom, "kg"), factor(nom, "t")
    usd = float(u["usd"])
    cop = float(u["cop"]) if pd.notna(u["cop"]) else float("nan")
    a = f'<b>kg</b> · US$ {num(usd*fk,3)}' + (f' · $ {num(cop*fk,0)}' if np.isfinite(cop) else "")
    b = f'<b>t</b> · US$ {num(usd*ft,0)}' + (f' · $ {num(cop*ft,0)}' if np.isfinite(cop) else "")
    return _flip_spans(a, b)


def card(nom, s, k, P, est, uid):
    col = P["serie"][nom]
    if nom == TRM:
        big = f'$ {num(k["ult"],2)}'
        seg = "COP por USD · tasa oficial"
        tag = "Superfinanciera"
    else:
        d = st.session_state["_panel"][0][nom]
        u = d.iloc[-1]
        big = f'US$ {num(float(u["usd"]), dec(nom,"USD","bolsa"))}'
        seg = (f'$ {num(float(u["cop"]),0)}' if pd.notna(u["cop"]) else "sin TRM")
        tag = f'{MAT[nom]["bolsa"]} · por {MAT[nom]["u"]}'
    aviso = ""
    if est.get("respaldo"):
        aviso = '<div class="wr">\u26a0 último dato guardado</div>'
    elif est["dias"] > 3:
        aviso = f'<div class="wr">\u26a0 dato de hace {est["dias"]} días</div>'
    pos = max(0., min(1., k["pos"])) * 100
    f = (lambda v: f'$ {num(v,2)}') if nom == TRM else (lambda v: f'US$ {num(v, dec(nom,"USD","bolsa"))}')
    gauge = (f'<div class="gg"><div class="gt">Rango 12 m · hoy en <b>{pos:.0f} %</b></div>'
             f'<div class="gr"><i style="left:{pos:.1f}%"></i></div>'
             f'<div class="gl"><span>{f(k["lo"])}</span><span>{f(k["hi"])}</span></div></div>')
    extra = ""
    if nom == "Resina PVC" and st.session_state.get("_pvc_sin_iva"):
        extra = '<div class="mini">sin IVA chino (13 %)</div>'
    return (f'<div class="card" style="--c:{col}">'
            f'<div class="ch"><span class="cn">{escape(nom)}</span><span class="cu">{escape(tag)}</span></div>'
            f'<div class="cv">{big}</div><div class="cv2">{seg}</div>'
            f'<div class="cc">{chip(k.get("1d"),"hoy")}{chip(k.get("30d"),"30 d")}</div>'
            f'{flip_html(nom)}'
            f'{svg_spark(ventana(s,365), col, uid)}{gauge}{extra}{aviso}</div>')


def card_vacia(nom, P):
    return (f'<div class="card" style="--c:{P["serie"][nom]}"><div class="ch">'
            f'<span class="cn">{escape(nom)}</span></div><div class="cv">—</div>'
            f'<div class="cv2">fuente no disponible</div></div>')


def slide_html(i, foco, cur, unidad, rango, su, su_cop, P):
    dias = RANGOS[rango]
    if foco == "Comparar":
        b = base100(unir(su, dias))
        cuerpo = svg_comparar(b, P)
        tit = "¿Cuál subió más?"
        sub = (f"variación % en {cur} desde {b.index[0]:%d/%m/%Y} · {rango}" if not b.empty else "")
    else:
        s = ventana(su.get(foco, pd.Series(dtype=float)), dias)
        sc = ventana(su_cop.get(foco, pd.Series(dtype=float)), dias) if (cur == "USD" and foco != TRM) else None
        cuerpo = svg_serie(s, foco, cur, unidad, P["serie"][foco], P, f"h{i}", sc)
        tit = foco
        u = "COP por USD" if foco == TRM else f'{cur} por {u_nom(foco, unidad)}'
        sub = f"{u} · {rango}" + (f" · {MAT[foco]['uso']}" if foco in MAT else "")
    return (f'<div class="slide" style="--i:{i}"><div class="ph"><div class="pt">{escape(tit)}'
            f'<span class="ps">{escape(sub)}</span></div></div>{cuerpo}</div>')


def side_html(series, ks, P):
    v_trm = ks.get(TRM, {}).get("30d")
    tot = []
    for nom in MAT:
        if nom in series:
            d = series[nom].set_index("fecha")
            tot.append((nom, var_pct(d["usd"], 30), var_pct(d["cop"], 30)))
    tope = max([abs(t[2]) for t in tot if t[2] is not None] + [5.0])
    filas = []
    for nom, vm, vt in tot:
        if vt is None:
            continue
        w = min(abs(vt) / tope, 1.) * 50
        col = P["up"] if vt > 0 else P["down"]
        pos = f"left:50%;width:{w:.1f}%" if vt > 0 else f"left:{50-w:.1f}%;width:{w:.1f}%"
        filas.append(f'<div class="ef"><span class="n" style="--c:{P["serie"][nom]}">{escape(nom)}</span>'
                     f'{chip(vm)}{chip(v_trm)}{chip(vt)}'
                     f'<span class="dv"><i style="{pos};background:{col}"></i><u></u></span></div>')
    efecto = ('<div class="pn side-a"><div class="ph"><div class="pt">¿Material o dólar?'
              '<span class="ps">qué movió el costo en COP · 30 días</span></div></div>'
              '<div class="ef eh"><span></span><span>Precio USD</span><span>Dólar</span>'
              '<span>Total COP</span><span></span></div>' + "".join(filas) + '</div>')
    rows = []
    for nom in SERIES:
        k = ks.get(nom)
        if k:
            rows.append(f'<tr><td><span class="n" style="--c:{P["serie"][nom]}">{escape(nom)}</span></td>'
                        + "".join(heat(k.get(c), P) for c in ("7d", "30d", "90d", "YTD", "12m")) + "</tr>")
    tabla = ('<div class="pn side-b"><div class="ph"><div class="pt">Variaciones'
             '<span class="ps">% frente a cada fecha</span></div></div>'
             '<table class="tb"><thead><tr><th></th><th>7 d</th><th>30 d</th><th>90 d</th>'
             '<th>Año</th><th>12 m</th></tr></thead><tbody>' + "".join(rows) + '</tbody></table></div>')
    return f'<div class="side">{efecto}{tabla}</div>'


def header_html(estado, P):
    ahora = datetime.now(BOGOTA)
    fecha = f"{DIAS[ahora.weekday()]} {ahora.day} {MESES[ahora.month-1]} {ahora.year}"
    chips = []
    for nom in SERIES:
        e = estado.get(nom, {})
        if not e.get("ok"):
            col, tx = P["up"], "sin datos"
        else:
            col = P["down"] if e["dias"] <= 3 else (P["warn"] if e["dias"] <= 7 else P["up"])
            col = P["warn"] if e.get("respaldo") else col
            tx = f_corta(e["fecha"])
        chips.append(f'<span class="fu"><i style="background:{col}"></i>{escape(nom)}<em>{tx}</em></span>')
    barras = "".join(f'<rect x="0" y="{i*8}" width="40" height="6" rx="3" fill="{P["serie"][n]}"/>' for i, n in enumerate(SERIES))
    return (f'<div class="hd"><div class="hd-l"><svg viewBox="0 0 40 38" class="mark">{barras}</svg>'
            f'<div><div class="t1">MATERIALES Y TRM</div>'
            f'<div class="t2">Compras · cobre · aluminio · acero EMT · resina PVC · dólar</div></div></div>'
            f'<div class="hd-r"><div class="hora">{ahora:%H:%M}</div><div class="fecha">{fecha}</div>'
            f'<div class="fus">{"".join(chips)}</div></div></div>')


def tablero_html(series, trm, estado, hoy, ks, playlist, unidad, P, paso):
    cards = []
    for i, nom in enumerate(SERIES):
        k = ks.get(nom)
        cards.append(card(nom, ks[nom + "_s"], k, P, estado[nom], f"c{i}") if k else card_vacia(nom, P))
    slides, dots = [], []
    for i, (foco, cur, rango) in enumerate(playlist):
        su = vistas(series, trm, hoy, cur, unidad)
        sc = vistas(series, trm, hoy, "COP", unidad)
        slides.append(slide_html(i, foco, cur, unidad, rango, su, sc, P))
        dots.append(f'<i class="dot" style="--i:{i}"></i>')
    carr = (f'<div class="pn hero"><div class="carousel">{"".join(slides)}</div>'
            f'<div class="pie"><div class="dots">{"".join(dots)}</div>'
            f'<div class="prog"><i></i></div></div></div>')
    pie = ('<div class="ft">Futuros con retraso · COMEX · NYMEX (CRU) · Dalian · TRM oficial Superfinanciera. '
           'HRC = insumo del conduit EMT · PVC Dalian = insumo del conduit PVC, '
           'mostrado sin IVA chino (13 %) para comparar con referencias internacionales. '
           'COP = USD × TRM del mismo día. Rojo = el costo sube · verde = baja. '
           'Referencia para tendencia y negociación, no tarifa de compra.</div>')
    return ('<div class="wrap"><div class="bd">' + header_html(estado, P)
            + '<div class="cards">' + "".join(cards) + '</div>'
            + carr + side_html(series, ks, P) + pie + '</div></div>')


# ============================================================
# ESTILO — glassmorphism minimalista, escala con la pantalla
# ============================================================
CSS_COMUN = """
@import url('https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;600;700&family=Barlow:wght@400;500;600;700&display=swap');
:root{--font:'Barlow','Segoe UI',system-ui,-apple-system,Arial,sans-serif;
--fnum:'Barlow Condensed','Arial Narrow','Roboto Condensed','Segoe UI',sans-serif}
.stApp,[data-testid="stAppViewContainer"],[data-testid="stMain"]{background:var(--bg)!important;color:var(--txt)}
.stApp p,.stApp label,.stApp li,.stApp td,.stApp th{font-family:var(--font)}
.stApp h1,.stApp h2,.stApp h3,.stApp h4{font-family:var(--fnum);color:var(--txt)}
[data-stale="true"]{opacity:1!important;transition:none!important}
[data-testid="stAlert"]{background:var(--panel)!important;border:1px solid var(--line)!important;border-radius:.6rem}
[data-testid="stAlert"] *{color:var(--txt)!important}
iframe[height="0"],[data-testid="stCustomComponentV1"]{position:absolute;width:0;height:0;border:0;opacity:0}
[data-testid="stRadioGroup"]{gap:.4rem!important;flex-wrap:wrap}
label[data-testid="stRadioOption"]{background:var(--panel2);border:1px solid var(--line);border-radius:999px;
padding:.25rem .85rem!important;margin:0!important;cursor:pointer;transition:border-color .15s}
label[data-testid="stRadioOption"] > div:first-of-type{display:none!important}
label[data-testid="stRadioOption"] p{color:var(--txt)!important;font-weight:600;font-size:.9rem;margin:0}
label[data-testid="stRadioOption"][data-selected="true"]{background:var(--txt);border-color:var(--txt)}
label[data-testid="stRadioOption"][data-selected="true"] p{color:var(--bg)!important}
button[data-testid^="stBaseButton"],a[data-testid^="stBaseLinkButton"]{background:var(--panel2)!important;
border:1px solid var(--line)!important;border-radius:999px!important;color:var(--txt)!important;font-weight:600}
[data-testid="stTabs"] [role="tablist"]{gap:.4rem;border-bottom:1px solid var(--line)}
[data-testid="stTab"] p{color:var(--mut)!important;font-weight:600}
[data-testid="stTab"][aria-selected="true"] p{color:var(--txt)!important}
[data-baseweb="tab-highlight"],[data-testid="stTabs"] .react-aria-SelectionIndicator{background:var(--txt)!important}
[data-testid="stPlotlyChart"]{background:var(--panel);border:1px solid var(--line);border-radius:.7rem;padding:.35rem}

/* bloques del modo análisis */
.stg{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.7rem}
.stc{background:var(--panel);border:1px solid var(--line);border-left:4px solid var(--c,var(--line));border-radius:.5rem;padding:.6rem .8rem;color:var(--txt)}
.stc .l{font-size:.78rem;color:var(--mut);font-weight:600}
.stc .v{font-family:var(--fnum);font-size:1.7rem;font-weight:700;line-height:1.15;font-variant-numeric:tabular-nums}
.stc .s{font-size:.78rem;color:var(--mut)}
.caja{background:var(--panel);border:1px solid var(--line);border-radius:.7rem;padding:.4rem .6rem;overflow:auto;max-height:460px}
.caja table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums;color:var(--txt);font-size:.92rem}
.caja th,.caja td{text-align:right;padding:.45rem .6rem;border-bottom:1px solid var(--line)}
.caja th{color:var(--mut);font-weight:600;font-size:.82rem}
.caja th:first-child,.caja td:first-child{text-align:left}
.estado{display:inline-block;width:.6rem;height:.6rem;border-radius:50%;margin-right:.45rem}
"""

CSS_TV = """
html,body{margin:0;padding:0;overflow:hidden;background:var(--bg)}
[data-testid="stHeader"],[data-testid="stToolbar"],[data-testid="stDecoration"],
[data-testid="stStatusWidget"],[data-testid="stAppDeployButton"],#MainMenu,footer{display:none!important}
[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]{padding:0!important;max-width:100%!important}
[data-testid="stVerticalBlock"]{gap:0!important}
[data-testid="stVerticalBlockBorderWrapper"]{border:0!important;padding:0!important;background:transparent!important}

/* lienzo a pantalla completa */
.wrap{container-type:size;width:100vw;height:100vh;position:relative;overflow:hidden;
background:
 radial-gradient(60vw 60vh at 12% -10%, var(--g1), transparent 60%),
 radial-gradient(55vw 55vh at 92% 108%, var(--g2), transparent 62%),
 var(--bg)}
.bd{--u:1vw;--u:min(1cqw,1.62cqh);position:absolute;inset:calc(var(--u)*1.1);
display:grid;grid-template-columns:repeat(12,minmax(0,1fr));
grid-template-rows:auto auto minmax(0,1fr) auto;gap:calc(var(--u)*.95);
font-family:var(--font);color:var(--txt)}
.bd *{box-sizing:border-box}

/* vidrio */
.pn,.card{background:var(--glass);border:1px solid var(--line);border-radius:calc(var(--u)*.95);
backdrop-filter:blur(20px) saturate(150%);-webkit-backdrop-filter:blur(20px) saturate(150%);
box-shadow:0 calc(var(--u)*.5) calc(var(--u)*1.6) rgba(0,0,0,.20), inset 0 1px 0 rgba(255,255,255,.07)}

/* encabezado */
.hd{grid-column:1/-1;display:flex;justify-content:space-between;align-items:center;gap:calc(var(--u)*2)}
.hd-l{display:flex;align-items:center;gap:calc(var(--u)*.9)}
.mark{width:calc(var(--u)*2.3);height:auto;flex:none;opacity:.95}
.t1{font-family:var(--fnum);font-weight:700;font-size:calc(var(--u)*2.5);line-height:1;letter-spacing:.01em}
.t2{font-size:calc(var(--u)*.92);color:var(--mut);margin-top:calc(var(--u)*.22)}
.hd-r{text-align:right}
.hora{font-family:var(--fnum);font-weight:700;font-size:calc(var(--u)*2.5);line-height:1;font-variant-numeric:tabular-nums}
.fecha{font-size:calc(var(--u)*.92);color:var(--mut);margin-top:calc(var(--u)*.1)}
.fus{display:flex;gap:calc(var(--u)*.8);justify-content:flex-end;margin-top:calc(var(--u)*.35);flex-wrap:wrap}
.fu{display:inline-flex;align-items:center;gap:calc(var(--u)*.35);font-size:calc(var(--u)*.85);font-weight:500}
.fu i{width:calc(var(--u)*.5);height:calc(var(--u)*.5);border-radius:50%}
.fu em{font-style:normal;color:var(--mut);margin-left:calc(var(--u)*.15)}

/* tarjetas */
.cards{grid-column:1/-1;display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:calc(var(--u)*.95)}
.card{position:relative;padding:calc(var(--u)*.62) calc(var(--u)*.85) calc(var(--u)*.6) calc(var(--u)*1.25);overflow:hidden;min-width:0}
.card::before{content:"";position:absolute;left:0;top:0;bottom:0;width:calc(var(--u)*.32);background:var(--c)}
.ch{display:flex;justify-content:space-between;align-items:baseline;gap:calc(var(--u)*.3)}
.cn{font-family:var(--fnum);font-weight:700;font-size:calc(var(--u)*1.32);color:var(--c);white-space:nowrap}
.cu{font-size:calc(var(--u)*.76);color:var(--mut);font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cv{font-family:var(--fnum);font-weight:700;font-size:calc(var(--u)*2.5);line-height:1.02;font-variant-numeric:tabular-nums;white-space:nowrap;margin:calc(var(--u)*.06) 0 0}
.cv2{font-family:var(--fnum);font-weight:600;font-size:calc(var(--u)*1.5);line-height:1.1;color:var(--c);font-variant-numeric:tabular-nums;white-space:nowrap}
.cc{display:flex;gap:calc(var(--u)*.3);margin-top:calc(var(--u)*.28);flex-wrap:nowrap}
.chip{display:inline-flex;align-items:center;gap:calc(var(--u)*.22);padding:calc(var(--u)*.1) calc(var(--u)*.45);border-radius:999px;font-weight:700;
font-size:calc(var(--u)*.82);font-variant-numeric:tabular-nums;white-space:nowrap;background:var(--glass2);border:1px solid var(--line)}
.chip b{font-size:.8em}
.chip .cl{font-weight:500;color:var(--mut)}
.chip.up{color:var(--up)} .chip.dn{color:var(--down)} .chip.mu{color:var(--mut)}
.flip{position:relative;height:calc(var(--u)*1.05);margin-top:calc(var(--u)*.3);font-size:calc(var(--u)*.8);
color:var(--mut);font-variant-numeric:tabular-nums;white-space:nowrap}
.flip b{color:var(--txt);font-weight:700}
.flip span{position:absolute;left:0;top:0}
.sp{display:block;width:100%;height:calc(var(--u)*2.1);margin:calc(var(--u)*.18) 0 calc(var(--u)*.1)}
.gg{margin-top:calc(var(--u)*.1)}
.gt{font-size:calc(var(--u)*.76);color:var(--mut);white-space:nowrap}
.gt b{color:var(--txt)}
.gr{position:relative;height:calc(var(--u)*.4);border-radius:999px;margin:calc(var(--u)*.42) 0 calc(var(--u)*.2);
background:linear-gradient(90deg,var(--down),var(--warn) 55%,var(--up));opacity:.8}
.gr i{position:absolute;top:calc(var(--u)*-.28);width:calc(var(--u)*.3);height:calc(var(--u)*.96);border-radius:999px;
background:var(--txt);transform:translateX(-50%);box-shadow:0 0 0 calc(var(--u)*.16) var(--bg)}
.gl{display:flex;justify-content:space-between;font-size:calc(var(--u)*.72);color:var(--mut);font-variant-numeric:tabular-nums;white-space:nowrap}
.wr{font-size:calc(var(--u)*.76);color:var(--warn);font-weight:600;margin-top:calc(var(--u)*.2)}
.mini{font-size:calc(var(--u)*.72);color:var(--mut);margin-top:calc(var(--u)*.12)}

/* héroe + carrusel */
.hero{grid-column:span 7;display:flex;flex-direction:column;min-height:0;padding:calc(var(--u)*.7) calc(var(--u)*1.05) calc(var(--u)*.5)}
.carousel{position:relative;flex:1;min-height:0}
.slide{position:absolute;inset:0;display:flex;flex-direction:column;opacity:0}
.ph{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:calc(var(--u)*.25)}
.pt{font-family:var(--fnum);font-weight:700;font-size:calc(var(--u)*1.6);line-height:1.1}
.ps{font-family:var(--font);font-weight:500;font-size:calc(var(--u)*.88);color:var(--mut);margin-left:calc(var(--u)*.7)}
.hs{flex:1;min-height:0;width:100%;display:block}
.hs text{font-family:var(--font)}
.hs .ax{font-size:17px;fill:var(--mut);font-variant-numeric:tabular-nums}
.hs .gl{stroke:var(--line);stroke-width:1}
.hs .g0{stroke:var(--mut);stroke-dasharray:4 7}
.hs .cn{stroke:var(--mut);stroke-width:1;fill:none;opacity:.5}
.hs .eln{font-size:23px;font-weight:700}
.hs .elv{font-size:34px;font-weight:700;fill:var(--txt);font-family:var(--fnum)}
.hs .fl{font-size:22px}
.hs .mk{font-size:18px;font-weight:600;fill:var(--txt);font-variant-numeric:tabular-nums}
.hs .bub{font-size:25px;font-weight:700;font-family:var(--fnum)}
.hs .bub2{font-size:23px;font-weight:700;font-family:var(--fnum)}
.vacio{flex:1;display:flex;align-items:center;justify-content:center;color:var(--mut);font-size:calc(var(--u)*1.1)}
.pie{display:flex;align-items:center;gap:calc(var(--u)*.8);margin-top:calc(var(--u)*.3)}
.dots{display:flex;gap:calc(var(--u)*.3)}
.dot{width:calc(var(--u)*.5);height:calc(var(--u)*.28);border-radius:999px;background:var(--mut);opacity:.35}
.prog{flex:1;height:2px;background:var(--line);border-radius:2px;overflow:hidden}
.prog i{display:block;height:100%;background:var(--txt);opacity:.5;transform-origin:left center}

/* panel derecho */
.side{grid-column:span 5;display:flex;flex-direction:column;gap:calc(var(--u)*.95);min-height:0}
.side .pn{padding:calc(var(--u)*.6) calc(var(--u)*1.05);min-height:0;display:flex;flex-direction:column}
.side-a{flex:0 0 auto} .side-b{flex:1}
.n{font-weight:700;font-size:calc(var(--u)*.95);display:inline-flex;align-items:center;gap:calc(var(--u)*.4);white-space:nowrap}
.n::before{content:"";width:calc(var(--u)*.32);height:calc(var(--u)*.95);border-radius:2px;background:var(--c)}
.ef{display:grid;grid-template-columns:1.35fr .95fr .8fr .95fr 1fr;gap:calc(var(--u)*.4);align-items:center;
padding:calc(var(--u)*.2) 0;border-top:1px solid var(--line)}
.ef.eh{border-top:0;font-size:calc(var(--u)*.78);color:var(--mut);font-weight:600;padding-top:0}
.ef .chip{justify-self:start}
.dv{position:relative;height:calc(var(--u)*.55);background:var(--glass2);border-radius:999px;overflow:hidden}
.dv i{position:absolute;top:0;bottom:0;border-radius:999px}
.dv u{position:absolute;left:50%;top:0;bottom:0;width:2px;background:var(--mut);opacity:.6}
.tb{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums;color:var(--txt);font-size:calc(var(--u)*.95);margin-top:calc(var(--u)*.2)}
.tb th{text-align:right;font-size:calc(var(--u)*.78);color:var(--mut);font-weight:600;padding:calc(var(--u)*.2) calc(var(--u)*.5);border-bottom:1px solid var(--line)}
.tb td{text-align:right;padding:calc(var(--u)*.26) calc(var(--u)*.5);border-bottom:1px solid var(--line);font-weight:600;border-radius:calc(var(--u)*.22)}
.tb th:first-child,.tb td:first-child{text-align:left}
.tb tr:last-child td{border-bottom:0}
.mu{color:var(--mut)}
.ft{grid-column:1/-1;font-size:calc(var(--u)*.76);color:var(--mut);text-align:center;line-height:1.35}
@media (prefers-reduced-motion:reduce){.slide{animation:none!important;opacity:1!important;position:relative!important}}
"""

CSS_ANALISIS = """
html,body{overflow:auto!important}
[data-testid="stMainBlockContainer"],[data-testid="stAppViewBlockContainer"]{padding:1.2rem 1.8rem 2rem!important;max-width:100%!important}
[data-testid="stVerticalBlock"]{gap:1rem}
"""


def css_anim(n: int, paso: float) -> str:
    total = n * paso
    vis = 100.0 / n
    fi = min(0.7 / total * 100, vis / 3)
    fo = vis - fi
    return f"""
@keyframes ciclo{{
0%{{opacity:0;transform:translateY(12px) scale(.994)}}
{fi:.3f}%{{opacity:1;transform:none}}
{fo:.3f}%{{opacity:1;transform:none}}
{vis:.3f}%{{opacity:0;transform:translateY(-10px) scale(.994)}}
100%{{opacity:0}}}}
.slide{{animation:ciclo {total:.2f}s cubic-bezier(.4,0,.2,1) infinite both;
animation-delay:calc(var(--i) * {paso:.2f}s)}}
@keyframes punto{{
0%{{opacity:1;width:calc(var(--u)*1.5);background:var(--txt)}}
{vis:.3f}%{{opacity:1;width:calc(var(--u)*1.5);background:var(--txt)}}
{vis+0.01:.3f}%{{opacity:.35;width:calc(var(--u)*.5);background:var(--mut)}}
100%{{opacity:.35;width:calc(var(--u)*.5);background:var(--mut)}}}}
.dot{{animation:punto {total:.2f}s linear infinite both;animation-delay:calc(var(--i) * {paso:.2f}s)}}
@keyframes prog{{from{{transform:scaleX(0)}}to{{transform:scaleX(1)}}}}
.prog i{{animation:prog {paso:.2f}s linear infinite}}
@keyframes fa{{0%,44%{{opacity:1;transform:none}}50%,94%{{opacity:0;transform:translateY(-5px)}}100%{{opacity:1;transform:none}}}}
@keyframes fb{{0%,44%{{opacity:0;transform:translateY(5px)}}50%,94%{{opacity:1;transform:none}}100%{{opacity:0;transform:translateY(5px)}}}}
.fa{{animation:fa 14s ease-in-out infinite}}
.fb{{animation:fb 14s ease-in-out infinite;opacity:0}}
"""


def inyectar_css(P: dict, n: int = 0, paso: float = 12.0, analisis: bool = False):
    claves = (("bg", "bg"), ("line", "line"), ("txt", "txt"), ("mut", "mut"),
              ("up", "up"), ("down", "down"), ("warn", "warn"),
              ("glass", "glass"), ("glass2", "glass2"),
              ("panel", "panel"), ("panel2", "panel2"))
    tok = (":root{" + "".join(f"--{k}:{P[v]};" for k, v in claves)
           + f"--g1:{rgba(P['glow1'], .13)};--g2:{rgba(P['glow2'], .11)};")
    tok += "color-scheme:light" if (analisis and P is PAL["Claro"]) else "color-scheme:dark"
    tok += "}"
    bloque = tok + CSS_COMUN + (CSS_ANALISIS if analisis else CSS_TV + css_anim(n, paso))
    md("<style>" + bloque + "</style>")


# ============================================================
# MODO ANÁLISIS (escritorio: pestañas histórico, comparar, simulador, fuentes)
# ============================================================
def mostrar(fig, key=None):
    cfg = {"displayModeBar": False, "scrollZoom": False}
    try:
        st.plotly_chart(fig, width="stretch", theme=None, config=cfg, key=key)
    except Exception:
        st.plotly_chart(fig, use_container_width=True, theme=None, config=cfg, key=key)


def estilo_fig(fig, P, alto, titulo=None):
    fig.update_layout(
        height=alto, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", hovermode="x unified",
        separators=",.", font=dict(family="Barlow, 'Segoe UI', system-ui, sans-serif", color=P["txt"], size=14),
        margin=dict(l=12, r=16, t=42 if titulo else 14, b=10),
        title=dict(text=titulo or "", x=0.01, font=dict(size=16, color=P["txt"])),
        legend=dict(orientation="h", y=1.1, x=0, font=dict(size=14, color=P["txt"])),
        hoverlabel=dict(bgcolor=P["panel2"], bordercolor=P["line"], font=dict(color=P["txt"], size=14)))
    fig.update_xaxes(gridcolor=rgba(P["line"], 0.55), zeroline=False, linecolor=P["line"],
                     hoverformat="%d/%m/%Y", tickfont=dict(color=P["mut"]))
    fig.update_yaxes(gridcolor=rgba(P["line"], 0.55), zeroline=False, tickfont=dict(color=P["mut"]), automargin=True)
    return fig


def tabla_html(headers, rows) -> str:
    th = "".join(f"<th>{escape(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<div class="caja"><table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>'


def tarjetas_stat(items, color) -> str:
    return '<div class="stg">' + "".join(
        f'<div class="stc" style="--c:{color}"><div class="l">{a}</div><div class="v">{b}</div><div class="s">{c}</div></div>'
        for a, b, c in items) + "</div>"


def pestana_historico(series, trm, hoy, moneda, unidad, P):
    c1, c2 = st.columns([2.2, 2.2])
    with c1:
        nom = st.radio("Serie", SERIES, horizontal=True, key="h_serie")
    with c2:
        mm = st.radio("Media móvil", ["Sin media", "30 d", "90 d", "200 d"], horizontal=True, key="h_mm")
    curs = ["COP"] if nom == TRM else (["USD", "COP"] if moneda == "Ambos" else [moneda])
    full = {c: vistas(series, trm, hoy, c, unidad).get(nom) for c in curs}
    if any(v is None or len(v) < 3 for v in full.values()):
        st.info("Esta fuente no está disponible en este momento.")
        return
    s = full[curs[0]]
    if len(s) < 3:
        st.info("Muy pocos datos: amplía el periodo.")
        return
    col = P["serie"][nom]
    _, f_full = formateadores(nom, curs[0], unidad)
    cambio = (float(s.iloc[-1]) / float(s.iloc[0]) - 1) * 100
    vol = float(np.log(s).diff().std() * math.sqrt(252) * 100) if (s > 0).all() else float("nan")
    extra = ""
    if len(curs) == 2:
        extra = " · " + precio(float(full["COP"].iloc[-1]), nom, "COP", unidad)
    md(tarjetas_stat([("Actual", f_full(float(s.iloc[-1])), f"dato del {s.index[-1]:%d/%m/%Y}" + extra),
                      (f"Cambio ({curs[0]})", num(cambio, 1, True) + " %", f"desde {s.index[0]:%d/%m/%Y}"),
                      ("Mínimo", f_full(float(s.min())), f"{s.idxmin():%d/%m/%Y}"),
                      ("Máximo", f_full(float(s.max())), f"{s.idxmax():%d/%m/%Y}"),
                      ("Promedio", f_full(float(s.mean())), f"{len(s):,} datos".replace(",", ".")),
                      ("Volatilidad anual", num(vol, 1) + " %", "variación típica de un año")], col))
    if _PLT:
        n = len(curs)
        fig = make_subplots(rows=n, cols=1, shared_xaxes=True, vertical_spacing=0.07,
                            row_heights=[0.58, 0.42] if n == 2 else [1.0])
        for i, cur in enumerate(curs, start=1):
            sv = full[cur]
            d = dec(nom, cur, unidad)
            lo, hi = float(sv.min()), float(sv.max())
            base_y = lo - (hi - lo) * 0.05
            fig.add_trace(go.Scatter(x=[sv.index[0], sv.index[-1]], y=[base_y, base_y], mode="lines",
                                     line=dict(width=0), hoverinfo="skip", showlegend=False), row=i, col=1)
            fig.add_trace(go.Scatter(x=sv.index, y=sv.values, name=etiqueta(nom, cur, unidad), mode="lines",
                                     showlegend=False, line=dict(color=col, width=2.6), fill="tonexty",
                                     fillcolor=rgba(col, 0.13),
                                     hovertemplate=f"%{{y:,.{d}f}}<extra>{etiqueta(nom, cur, unidad)}</extra>"), row=i, col=1)
            if mm != "Sin media":
                dd = int(mm.split()[0])
                ma = sv.rolling(dd).mean().dropna()
                fig.add_trace(go.Scatter(x=ma.index, y=ma.values, name=f"Media {dd} d", mode="lines",
                                         showlegend=(i == 1), line=dict(color=P["txt"], width=1.8, dash="dot"),
                                         hovertemplate=f"%{{y:,.{d}f}}<extra>Media {dd} d</extra>"), row=i, col=1)
            fig.add_trace(go.Scatter(x=[sv.idxmax(), sv.idxmin()], y=[hi, lo], mode="markers", showlegend=False,
                                     marker=dict(size=10, color=P["bg"], line=dict(color=col, width=2.5)),
                                     hoverinfo="skip"), row=i, col=1)
            fig.update_yaxes(range=[base_y, hi + (hi - lo) * 0.06],
                             tickformat=f",.{0 if (cur=='COP' or unidad=='t' or nom==TRM) else 2}f",
                             title_text=etiqueta(nom, cur, unidad),
                             title_font=dict(size=12, color=P["mut"]), row=i, col=1)
        estilo_fig(fig, P, 560 if n == 2 else 480, f"{nom} · histórico completo")
        if n == 1:
            fig.update_xaxes(rangeslider=dict(visible=True, thickness=0.07, bgcolor=P["panel2"],
                                              bordercolor=P["line"]))
        mostrar(fig, "g_hist")
    else:
        st.line_chart(s)
    st.caption("En «Ambos» ves USD y COP juntos; la diferencia entre curvas es el efecto del dólar.")
    if nom == TRM:
        vista = trm[trm["fecha"] <= hoy][["fecha", "trm"]].rename(columns={"trm": "TRM"})
        dmap = {"TRM": 2}
    else:
        d = series[nom]
        f = factor(nom, unidad)
        un = u_nom(nom, unidad)
        vista = pd.DataFrame({"fecha": d["fecha"], f"USD/{un}": d["usd"] * f,
                              f"COP/{un}": d["cop"] * f, "TRM": d["trm"]})
        dmap = {f"USD/{un}": dec(nom, "USD", unidad), f"COP/{un}": 0, "TRM": 2}
    hd = list(vista.columns)
    filas = [[r["fecha"].strftime("%d/%m/%Y")] + [num(r[c], dmap[c]) for c in hd[1:]]
             for _, r in vista.tail(15).sort_values("fecha", ascending=False).iterrows()]
    md('<div style="margin-top:.4rem"></div>' + tabla_html(["Fecha"] + hd[1:], filas))
    st.download_button("Descargar CSV", vista.to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"{nom.lower().replace(' ', '_')}.csv", mime="text/csv")


def pestana_comparar(series, trm, hoy, cur, unidad, P):
    dias = st.radio("Periodo", list(RANGOS), horizontal=True, key="c_rango", index=list(RANGOS).index("2A"))
    su = vistas(series, trm, hoy, cur, unidad)
    b = base100(unir(su, RANGOS[dias]), list(su))
    if b.empty or len(b) < 3:
        st.info("No hay fechas comunes suficientes: elige un periodo más largo.")
        return
    if _PLT:
        fig = estilo_fig(go.Figure(), P, 450, f"¿Cuál subió más? · variación % en {cur} desde {b.index[0]:%d/%m/%Y}")
        for c in b.columns:
            fig.add_trace(go.Scatter(x=b.index, y=b[c] - 100, name=c, mode="lines",
                                     line=dict(color=P["serie"][c], width=2.6),
                                     hovertemplate="%{y:+.1f} %<extra>" + c + "</extra>"))
        fig.update_yaxes(ticksuffix=" %", tickformat="+.0f", zeroline=True, zerolinecolor=P["mut"], zerolinewidth=1)
        mostrar(fig, "g_cmp")
    st.caption(f"Todas arrancan en 0 % el mismo día (primera fecha común), medidas en {cur}. +15 % = 15 % más caro que ese día.")
    usd = {n: s for n, s in vistas(series, trm, hoy, "USD", "bolsa").items() if len(s) > 10}
    w = unir(usd, RANGOS[dias]).resample("W").last().ffill().pct_change().dropna()
    if len(w) < 12 or w.shape[1] < 2:
        return
    cm = w.corr()
    cabeza = "<tr><th></th>" + "".join(f"<th>{escape(c)}</th>" for c in cm.columns) + "</tr>"
    cuerpo = ""
    for a in cm.index:
        fila = f"<td><b>{escape(a)}</b></td>"
        for c in cm.columns:
            v = float(cm.loc[a, c])
            colr = P["serie"]["Aluminio"] if v >= 0 else P["serie"]["Cobre"]
            fila += f'<td style="background:{rgba(colr, 0.08 + 0.5 * abs(v))}">{num(v, 2)}</td>'
        cuerpo += f"<tr>{fila}</tr>"
    par = max(((a, c, float(cm.loc[a, c])) for i, a in enumerate(cm.index) for c in cm.columns[i + 1:]),
              key=lambda t: abs(t[2]))
    md('<div style="margin-top:.6rem;font-weight:700;color:var(--txt)">¿Se mueven juntos? · variaciones semanales en USD</div>'
       f'<div class="caja"><table><thead>{cabeza}</thead><tbody>{cuerpo}</tbody></table></div>')
    rel = "se mueven en el mismo sentido" if par[2] > 0.3 else ("se mueven en sentidos opuestos" if par[2] < -0.3 else "casi no se relacionan")
    st.caption(f"1,00 = idénticos · 0 = sin relación · negativo = opuestos. Los más ligados: {par[0]} y {par[1]} "
               f"({num(par[2], 2)}): {rel}.")


def pestana_simulador(series, P):
    st.caption("Costo del material puro (cable, tubería…). No incluye fabricación, aislantes, flete, arancel ni IVA.")
    disp = [m for m in MAT if m in series] or ["Cobre"]
    c1, c2, c3, c4 = st.columns([2.6, 1, 1, 1])
    with c1:
        mat = st.radio("Material", disp, horizontal=True, key="s_mat")
    kg_u = c2.number_input("kg de material por unidad", min_value=0.0, value=140.0, step=10.0, key="s_kg")
    n_u = c3.number_input("unidades (km, m, piezas)", min_value=0.0, value=1.0, step=0.5, key="s_n")
    merma = c4.number_input("Merma %", min_value=0.0, max_value=30.0, value=2.0, step=0.5, key="s_merma")
    if mat not in series or series[mat]["cop"].dropna().empty:
        st.info("Falta el precio o la TRM de este material para calcular en pesos.")
        return
    d = series[mat].set_index("fecha")
    cop_kg = (d["cop"] / MAT[mat]["kg_u"]).dropna()
    usd_kg = (d["usd"] / MAT[mat]["kg_u"]).dropna()
    hoy_kg = float(cop_kg.iloc[-1])
    p30, p12 = float(cop_kg.tail(30).mean()), float(ventana(cop_kg, 365).mean())
    kg_ef = kg_u * n_u * (1 + merma / 100)
    col = P["serie"][mat]
    md(tarjetas_stat([("Costo hoy", cop_corto(hoy_kg * kg_ef), f"US$ {num(float(usd_kg.iloc[-1]) * kg_ef, 0)} · TRM del día"),
                      ("Con el promedio de 30 días", cop_corto(p30 * kg_ef),
                       f"hoy está {num((hoy_kg / p30 - 1) * 100, 2, True)} % frente a ese promedio"),
                      ("Con el promedio de 12 meses", cop_corto(p12 * kg_ef),
                       f"hoy está {num((hoy_kg / p12 - 1) * 100, 2, True)} % frente a ese promedio"),
                      ("Kilos efectivos", num(kg_ef, 1) + " kg", "incluye la merma")], col))
    if _PLT and kg_ef > 0:
        w = ventana(cop_kg, 730) * kg_ef
        fig = estilo_fig(go.Figure(), P, 330,
                         f"Costo del pedido en el tiempo (COP, TRM de cada día) · {num(kg_ef, 1)} kg efectivos")
        fig.add_trace(go.Scatter(x=w.index, y=w.values, name="Costo", mode="lines",
                                 line=dict(color=col, width=2.6),
                                 hovertemplate="$ %{y:,.0f}<extra>Costo</extra>"))
        fig.update_layout(showlegend=False)
        fig.update_yaxes(tickformat=",.0f")
        mostrar(fig, "g_sim")
    dm, dt = (-10, -5, 0, 5, 10), (-5, -2.5, 0, 2.5, 5)
    base = hoy_kg * kg_ef
    cab = "<tr><th>Precio ↓ · Dólar →</th>" + "".join(f"<th>{num(t, 1, True)} %</th>" for t in dt) + "</tr>"
    cuerpo = ""
    for m in dm:
        fila = f"<td><b>{num(m, 0, True)} %</b></td>"
        for t in dt:
            v = base * (1 + m / 100) * (1 + t / 100)
            dif = (v / base - 1) * 100 if base else 0
            c = P["up"] if dif > 0.01 else (P["down"] if dif < -0.01 else None)
            fila += (f'<td style="background:{rgba(c, 0.10 + min(abs(dif) / 15, 1) * 0.5) if c else "transparent"}">'
                     f'{cop_corto(v)}</td>')
        cuerpo += f"<tr>{fila}</tr>"
    md('<div style="margin-top:.6rem;font-weight:700;color:var(--txt)">¿Qué pasa si se mueven el precio o el dólar?</div>'
       f'<div class="caja"><table><thead>{cab}</thead><tbody>{cuerpo}</tbody></table></div>')
    st.caption("Costo de este pedido si el precio en USD y la TRM cambian estos porcentajes desde hoy. Rojo = más caro · verde = más barato.")


def pestana_fuentes(series, estado, P):
    info = {TRM: ("datos.gov.co · Superfinanciera", "COP por USD", "oficial, diaria", "Dólar TRM (Banrep/Superfinanciera)"),
            "USD/CNY": ("Yahoo Finance · CNY=X", "CNY por USD", "tipo de cambio (para el PVC)", "USD/CNY")}
    for n, c in MAT.items():
        org = (f"Sina Finance · {c['ticker']} ({c['bolsa']})" if c["src"] == "sina"
               else f"Yahoo Finance · {c['ticker']} ({c['bolsa']})")
        if n == "Resina PVC" and _url_pvc():
            org = "CSV propio (PVC_CSV_URL)"
        info[n] = (org, f"{c['nat']}/{c['u']}", "futuro continuo, con retraso", c["inv"])
    filas = []
    for n in SERIES + ["USD/CNY"]:
        e, (org, un, nota, inv) = estado.get(n, {}), info[n]
        ult = ""
        if n in series:
            r = series[n].iloc[-1]
            ult = f"{num(r['nat'], 4 if MAT[n]['kg_u'] < 1 else 2)} {r['mon']}/{MAT[n]['u']}"
        if e.get("ok"):
            col = P["down"] if e["dias"] <= 3 else (P["warn"] if e["dias"] <= 7 else P["up"])
            col = P["warn"] if e.get("respaldo") else col
            et = "Dato guardado" if e.get("respaldo") else ("Al día" if e["dias"] <= 3 else f"Atrasado {e['dias']} d")
            filas.append([f'<span class="estado" style="background:{col}"></span>{et}', f"<b>{escape(n)}</b>",
                          escape(org), escape(un), escape(ult) or "\u2014", escape(inv),
                          e["fecha"].strftime("%d/%m/%Y"), num(e["n"], 0)])
        else:
            filas.append([f'<span class="estado" style="background:{P["up"]}"></span>Sin datos', f"<b>{escape(n)}</b>",
                          escape(org), escape(un), "\u2014", escape(inv), "\u2014", "0"])
    md(tabla_html(["Estado", "Serie", "Origen", "Unidad de bolsa", "Última cotización",
                   "Equivale en Investing", "Último dato", "Registros"], filas))
    st.caption("«Última cotización» está en la unidad y moneda de la bolsa: compárala con Investing para validar. "
               "Pequeñas diferencias son normales (retraso de ~10 min y cambio de contrato).")
    with st.expander("Contraste con el FMI (precios LME, promedio mensual) — verificación opcional"):
        st.caption("Los futuros de Yahoo son COMEX (EE. UU.). El FMI publica el promedio mensual LME, referencia mundial. "
                   "Diferencias de unos puntos son normales; si pasan de 10 %, conviene revisar.")
        hay = False
        for n, c in MAT.items():
            if not c["fred"] or n not in series:
                continue
            f = fred_serie(c["fred"])
            if f.empty:
                st.caption(f"{n}: el FMI no respondió ahora (verificación opcional).")
                continue
            m = pd.concat([(series[n].set_index("fecha")["usd"] * factor(n, "t")).resample("MS").mean().rename("c"),
                           f.set_index("fecha")["valor"].rename("f")], axis=1).dropna().tail(6)
            if m.empty:
                continue
            hay = True
            rows = [[f"{MESES[t.month - 1]} {t.year}", num(r["c"], 0), num(r["f"], 0), chip((r["c"] / r["f"] - 1) * 100)]
                    for t, r in m.iterrows()]
            md(f'<div style="margin-top:.5rem;font-weight:700;color:var(--txt)">{n} · USD por tonelada métrica</div>'
               + tabla_html(["Mes", "COMEX (Yahoo)", "FMI · LME", "Diferencia"], rows))
        if not hay:
            st.caption("No se pudo obtener el contraste ahora; el tablero funciona igual.")
    st.markdown("""
**Cómo se calcula cada cifra**
- **Bolsa**: cobre en USD/libra, aluminio en USD/t métrica, acero HRC en USD/tonelada **corta** (907,18 kg), PVC en CNY/t (Dalian).
- **COP = USD × TRM del mismo día** (la TRM oficial vigente en la fecha de cada precio; las vigencias futuras no se usan para valorar hoy).
- **PVC**: futuro de Dalian en yuanes, convertido a dólares con USD/CNY y mostrado **sin IVA chino (13 %)** para comparar con referencias internacionales (`&pvciva=1` lo vuelve a mostrar con IVA). Es tendencia, no precio de importación a Colombia.
- Para usar tu propia cotización del PVC define `PVC_CSV_URL` (CSV `fecha,usd_t`).
- Aluminio y acero HRC son contratos de poco volumen y referencia de EE. UU.: úsalos para tendencia y negociación, no como tarifa de compra.
- Futuros continuos: pueden mostrar un salto pequeño el día que cambia el contrato activo.
""")


def modo_analisis():
    tema_def = "Claro" if qp("theme").lower() in ("light", "claro") else "Oscuro"
    ss = st.session_state
    ss.setdefault("tema", tema_def)
    ss.setdefault("moneda", "Ambos")
    ss.setdefault("unidad", "bolsa")
    P = PAL[ss["tema"]]
    inyectar_css(P, analisis=True)
    series, trm, estado, hoy = panel(qp("pvciva", "0").lower() not in ("1", "true", "si", "sí"))
    st.session_state["_panel"] = (series, trm)

    c0, c1, c2, c3 = st.columns([1.5, 2.0, 2.0, 1.2])
    with c0:
        st.radio("Tema", ["Claro", "Oscuro"], horizontal=True, key="tema")
    with c1:
        st.radio("Moneda", ["USD", "COP", "Ambos"], horizontal=True, key="moneda",
                 help="COP = precio en USD × TRM de ese mismo día. «Ambos» muestra las dos.")
    with c2:
        st.radio("Unidad", ["bolsa", "kg", "t"], horizontal=True, key="unidad",
                 help="bolsa = como cotiza cada bolsa (cobre por libra, acero por tonelada corta…).")
    with c3:
        st.markdown('<div style="height:1.5rem"></div>', unsafe_allow_html=True)
        b1, b2 = st.columns(2)
        with b1:
            if st.button("Actualizar"):
                st.cache_data.clear()
                st.rerun()
        with b2:
            st.link_button("Modo TV", "/")

    moneda, unidad = ss["moneda"], ss["unidad"]
    cur = "COP" if moneda == "COP" else "USD"
    t1, t2, t3, t4 = st.tabs(["Tablero", "Histórico", "Comparar", "Simulador de costos"])
    with t1:
        pestana_fuentes(series, estado, P)
    with t2:
        pestana_historico(series, trm, hoy, moneda, unidad, P)
    with t3:
        pestana_comparar(series, trm, hoy, cur, unidad, P)
    with t4:
        pestana_simulador(series, P)


# ============================================================
# MODO TV — carrusel 100 % CSS (sin mouse, sin estado de sesión)
# ============================================================
def _playlist(moneda: str):
    if moneda == "USD":
        return [("Comparar", "USD", "2A")] + [(m, "USD", "1A") for m in MAT] + \
               [(TRM, "COP", "1A"), ("Comparar", "USD", "5A")]
    if moneda == "COP":
        return [("Comparar", "COP", "2A")] + [(m, "COP", "1A") for m in MAT] + \
               [(TRM, "COP", "1A"), ("Comparar", "COP", "5A")]
    return [("Comparar", "USD", "2A"), ("Comparar", "COP", "2A")] + \
           [(m, "USD", "1A") for m in MAT] + [(TRM, "COP", "1A"), ("Comparar", "USD", "5A")]


@st.fragment(run_every="5min")
def modo_tv():
    tema = "Claro" if qp("theme").lower() in ("light", "claro") else "Oscuro"
    P = PAL[tema]
    try:
        paso = max(6.0, min(60.0, float(qp("paso", "12"))))
    except ValueError:
        paso = 12.0
    moneda = qp("moneda").upper()
    moneda = moneda if moneda in ("USD", "COP") else "AMBOS"
    sin_iva = qp("pvciva", "0").lower() not in ("1", "true", "si", "sí")
    st.session_state["_pvc_sin_iva"] = sin_iva

    series, trm, estado, hoy = panel(sin_iva)
    st.session_state["_panel"] = (series, trm)
    if not series and trm.empty:
        st.error("Sin conexión con las fuentes de mercado. El tablero se recuperará solo.")
        return

    su = vistas(series, trm, hoy, "USD", "bolsa")
    ks = {}
    for nom, s in su.items():
        k = kpis(s)
        if k:
            ks[nom] = k
            ks[nom + "_s"] = s
    pl = _playlist(moneda)
    inyectar_css(P, len(pl), paso)
    md(tablero_html(series, trm, estado, hoy, ks, pl, "bolsa", P, paso))


def _vigilante(minutos: int):
    """Recarga la página cada N minutos: recupera la TV si se cayó la conexión
    o si la memoria del navegador crece."""
    if minutos <= 0:
        return
    try:
        import streamlit.components.v1 as components
        components.html("<script>setTimeout(function(){try{window.parent.location.reload()}catch(e){}},%d)</script>"
                        % (minutos * 60000), height=0)
    except Exception:
        pass


# ============================================================
# ENTRADA
# ============================================================
if qp("analisis") in ("1", "true", "si", "sí"):
    modo_analisis()
else:
    modo_tv()
    try:
        _vigilante(int(qp("reload", "60")))
    except ValueError:
        _vigilante(60)