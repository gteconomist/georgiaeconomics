"""Five-year forecast (2026F-2030F) for the headline indicators table.

Populates the forecast columns of the Metro Economic Profile's headline table by
extrapolating each annual indicator from the actuals already fetched into the
report JSON.

Method — damped-trend Holt smoothing (a member of the ARIMA family: damped Holt
is equivalent to ARIMA(1,1,2)). Chosen over a full statsmodels ARIMA because the
pipeline is pure-stdlib (no numpy/statsmodels dependency, matching
business_cycle_index.py) and damped Holt is robust on the short (6-30 point)
annual series we have, without the overfitting risk of auto-ordered ARIMA on
small samples.

  * Level series (GMP, employment, population, personal income, HPI, permits) are
    forecast in LOG space, so growth is multiplicative and forecasts stay positive.
  * Rate series (unemployment) are forecast in raw space and clamped to a sane band.
  * The damping factor phi < 1 pulls the trend toward flat over the horizon,
    preventing runaway 5-year extrapolation.
  * alpha / beta / phi are fit per series by a small grid search minimising
    one-step-ahead squared error.

Inputs (read from the orchestrator's in-progress output dict, output["sections"]):
    bea_gmp.{years, gmp_billions_usd}
    ces_employment.{months, values}                 (monthly -> annual mean)
    laus_unemployment.{months, values}              (monthly -> annual mean)
    bea_personal_income.{years, personal_income_billions_usd}
    census_pep.{years, population}
    fhfa_hpi.{quarters, values}                      (quarterly -> annual mean)
    census_bps_permits.{years, single_family, multi_family}   (optional)
    census_acs_demographics.values.median_household_income    (optional anchor)

Output added to JSON as section "forecast_arima":
    {
        "method": "...", "horizon": [2026,2027,2028,2029,2030],
        "years": [...],
        "<metric>": [5 values], "<metric>_yoy": [5 values], ...
        "metrics": [list of metric keys populated],
    }

Returns None if no metric could be forecast (e.g. inputs missing).

Pure-Python: no numpy / statsmodels dependency.
"""

from __future__ import annotations

import math
from typing import Optional, List, Dict, Tuple

HORIZON_END = 2030
DISPLAY_YEARS = [2026, 2027, 2028, 2029, 2030]


# --------------------------------------------------------------------------- #
#  Damped-trend Holt smoothing                                                #
# --------------------------------------------------------------------------- #

def _holt_sse(y: List[float], alpha: float, beta: float, phi: float) -> float:
    """One-step-ahead squared error for damped Holt with given params."""
    if len(y) < 3:
        return float("inf")
    level = y[0]
    trend = y[1] - y[0]
    sse = 0.0
    for t in range(1, len(y)):
        fcast = level + phi * trend
        err = y[t] - fcast
        sse += err * err
        new_level = alpha * y[t] + (1 - alpha) * (level + phi * trend)
        trend = beta * (new_level - level) + (1 - beta) * phi * trend
        level = new_level
    return sse


def _holt_forecast(y: List[float], h: int) -> List[float]:
    """Fit damped Holt by grid search, return h-step-ahead forecasts."""
    best = (float("inf"), 0.5, 0.1, 0.9)
    for alpha in (0.2, 0.4, 0.6, 0.8):
        for beta in (0.05, 0.1, 0.2, 0.3):
            for phi in (0.80, 0.90, 0.95, 0.98):
                sse = _holt_sse(y, alpha, beta, phi)
                if sse < best[0]:
                    best = (sse, alpha, beta, phi)
    _, alpha, beta, phi = best

    level = y[0]
    trend = y[1] - y[0]
    for t in range(1, len(y)):
        new_level = alpha * y[t] + (1 - alpha) * (level + phi * trend)
        trend = beta * (new_level - level) + (1 - beta) * phi * trend
        level = new_level

    out, phi_pow = [], 0.0
    for step in range(1, h + 1):
        phi_pow += phi ** step
        out.append(level + phi_pow * trend)
    return out


def _forecast_series(annual: Dict[int, float], log_space: bool,
                     clamp: Optional[Tuple[float, float]] = None
                     ) -> Optional[Dict[int, float]]:
    """Extend an annual {year: value} dict with forecasts out to HORIZON_END.

    Returns {year: value} covering (last_actual+1 .. HORIZON_END), or None.
    """
    if not annual:
        return None
    years = sorted(annual)
    vals = [annual[y] for y in years]
    if len(vals) < 3:
        return None
    last_year = years[-1]
    h = HORIZON_END - last_year
    if h <= 0:
        return {}

    fit = [math.log(v) for v in vals] if log_space else list(vals)
    if log_space and any(v <= 0 for v in vals):
        log_space = False
        fit = list(vals)

    fc = _holt_forecast(fit, h)
    if log_space:
        fc = [math.exp(v) for v in fc]
    if clamp:
        lo, hi = clamp
        fc = [min(max(v, lo), hi) for v in fc]

    return {last_year + i + 1: fc[i] for i in range(h)}


# --------------------------------------------------------------------------- #
#  Annualisation helpers                                                       #
# --------------------------------------------------------------------------- #

def _monthly_to_annual_mean(months: List[str], values: List[float]) -> Dict[int, float]:
    """Average a monthly series by calendar year; keep only complete-ish years."""
    by_year: Dict[int, List[float]] = {}
    for m, v in zip(months, values):
        if v is None:
            continue
        yr = int(m[:4])
        by_year.setdefault(yr, []).append(v)
    # require >= 6 months present to treat a year as usable
    return {y: sum(vs) / len(vs) for y, vs in by_year.items() if len(vs) >= 6}


def _quarterly_to_annual_mean(quarters: List[str], values: List[float]) -> Dict[int, float]:
    by_year: Dict[int, List[float]] = {}
    for q, v in zip(quarters, values):
        if v is None:
            continue
        yr = int(q[:4])
        by_year.setdefault(yr, []).append(v)
    return {y: sum(vs) / len(vs) for y, vs in by_year.items() if len(vs) >= 2}


def _yoy_chain(actual_last: Tuple[int, float], fc: Dict[int, float]) -> Dict[int, float]:
    """Year-over-year % for forecast years, chaining off the last actual."""
    chain = {actual_last[0]: actual_last[1], **fc}
    yrs = sorted(chain)
    out: Dict[int, float] = {}
    for i in range(1, len(yrs)):
        prev, cur = chain[yrs[i - 1]], chain[yrs[i]]
        if prev:
            out[yrs[i]] = round(100 * (cur - prev) / prev, 1)
    return out


def _slice(d: Optional[Dict[int, float]], rnd: int) -> Optional[List[float]]:
    """Pull DISPLAY_YEARS out of a {year: value} dict, rounded; None if missing."""
    if not d:
        return None
    if not all(y in d for y in DISPLAY_YEARS):
        return None
    return [round(d[y], rnd) for y in DISPLAY_YEARS]


# --------------------------------------------------------------------------- #
#  Main entry                                                                  #
# --------------------------------------------------------------------------- #

def compute(cbsa: str, output_so_far: dict) -> Optional[dict]:
    sec = (output_so_far or {}).get("sections", {})
    out: Dict[str, object] = {}
    metrics: List[str] = []

    def add_level(key: str, annual: Dict[int, float], rnd: int,
                  log_space: bool = True, with_yoy: bool = False,
                  clamp=None):
        fc = _forecast_series(annual, log_space=log_space, clamp=clamp)
        lvl = _slice(fc, rnd)
        if lvl is None:
            return
        out[key] = lvl
        metrics.append(key)
        if with_yoy and annual:
            last_year = max(annual)
            yoy = _yoy_chain((last_year, annual[last_year]), fc)
            ys = _slice(yoy, 1)
            if ys is not None:
                out[key + "_yoy"] = ys
                metrics.append(key + "_yoy")
        return fc

    # --- GMP ($B) + % change ---
    gmp = sec.get("bea_gmp") or {}
    if gmp.get("years") and gmp.get("gmp_billions_usd"):
        add_level("gmp", dict(zip(gmp["years"], gmp["gmp_billions_usd"])),
                  rnd=1, with_yoy=True)

    # --- Total employment (000s, annual mean) + % change ---
    ces = sec.get("ces_employment") or {}
    if ces.get("months") and ces.get("values"):
        emp_annual = _monthly_to_annual_mean(ces["months"], ces["values"])
        add_level("total_employment", emp_annual, rnd=1, with_yoy=True)

    # --- Unemployment rate (%) — mean-reverting, clamped ---
    laus = sec.get("laus_unemployment") or {}
    if laus.get("months") and laus.get("values"):
        un_annual = _monthly_to_annual_mean(laus["months"], laus["values"])
        add_level("unemployment", un_annual, rnd=1, log_space=False, clamp=(1.0, 15.0))

    # --- Personal income growth (%) — forecast level, expose yoy ---
    pinc = sec.get("bea_personal_income") or {}
    pi_fc = None
    if pinc.get("years") and pinc.get("personal_income_billions_usd"):
        pi_annual = dict(zip(pinc["years"], pinc["personal_income_billions_usd"]))
        pi_fc = _forecast_series(pi_annual, log_space=True)
        if pi_fc and pi_annual:
            last_year = max(pi_annual)
            ys = _slice(_yoy_chain((last_year, pi_annual[last_year]), pi_fc), 1)
            if ys is not None:
                out["personal_income_yoy"] = ys
                metrics.append("personal_income_yoy")

    # --- Median HH income ($K) — anchored projection off personal-income growth ---
    acs = (sec.get("census_acs_demographics") or {}).get("values") or {}
    mhi = acs.get("median_household_income")
    if mhi and "personal_income_yoy" in out:
        v, series = mhi / 1000.0, []
        for g in out["personal_income_yoy"]:  # type: ignore[union-attr]
            v *= (1 + g / 100.0)
            series.append(round(v, 1))
        out["median_hh_income"] = series
        # median income tracks personal-income growth, so its yoy IS that series
        out["median_hh_income_yoy"] = list(out["personal_income_yoy"])  # type: ignore[arg-type]
        metrics += ["median_hh_income", "median_hh_income_yoy"]

    # --- Population (000s) + % change ---
    pep = sec.get("census_pep") or {}
    if pep.get("years") and pep.get("population"):
        pop_annual = {y: p / 1000.0 for y, p in zip(pep["years"], pep["population"])}
        add_level("population", pop_annual, rnd=1, with_yoy=True)

    # --- FHFA HPI growth (%) — forecast level, expose yoy ---
    hpi = sec.get("fhfa_hpi") or {}
    if hpi.get("quarters") and hpi.get("values"):
        hpi_annual = _quarterly_to_annual_mean(hpi["quarters"], hpi["values"])
        hpi_fc = _forecast_series(hpi_annual, log_space=True)
        if hpi_fc and hpi_annual:
            last_year = max(hpi_annual)
            ys = _slice(_yoy_chain((last_year, hpi_annual[last_year]), hpi_fc), 1)
            if ys is not None:
                out["hpi_yoy"] = ys
                metrics.append("hpi_yoy")

    # --- Building permits (SF + MF) — only if BPS section is live ---
    bps = sec.get("census_bps_permits") or {}
    if bps.get("years") and bps.get("single_family"):
        add_level("sf_permits", dict(zip(bps["years"], bps["single_family"])), rnd=0, with_yoy=True)
    if bps.get("years") and bps.get("multi_family"):
        add_level("mf_permits", dict(zip(bps["years"], bps["multi_family"])), rnd=0, with_yoy=True)

    if not metrics:
        return None

    out["method"] = ("Damped-trend Holt smoothing (ARIMA(1,1,2) family), per-series "
                     "grid-fit on annual actuals; level series modeled in log space")
    out["horizon"] = DISPLAY_YEARS
    out["years"] = DISPLAY_YEARS
    out["metrics"] = metrics
    out["source"] = "EIG forecast composite — computed from BLS/BEA/Census/FHFA actuals"
    return out
