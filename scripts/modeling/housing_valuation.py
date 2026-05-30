"""EIG Housing Valuation model — % over/under "fair value" implied by local fundamentals.

The headline number answers: how far does the local house-price level sit above (or
below) what the metro's *income fundamentals* would justify?

Method — a log-log fair-value regression, the standard misalignment approach used by
the Dallas Fed and IMF for house-price gaps, reduced to pure stdlib:

    log(HPI_y) = a + b * log(per_capita_income_y) + e_y      (OLS over overlap years)

  * b is the estimated income elasticity of house prices (free to differ from 1).
  * fair_value_y = exp(a + b * log(income_y))  is the income-implied price level.
  * valuation_y  = 100 * (HPI_y / fair_value_y - 1) = 100 * (exp(e_y) - 1)
                   is the % over/under valuation for year y.

Because OLS residuals are mean-zero over the fitted window, valuations oscillate
around 0 and the latest year reads as "% above/below the income-justified trend".

Two current cross-checks are reported alongside the series (single ACS vintage, not a
series): price-to-income and price-to-rent ratios, the classic affordability yardsticks
that stand in for the "rents" and "rates" legs of the fundamentals story until a local
rent/rate *time series* is wired.

Inputs (read from output["sections"]):
    fhfa_hpi.{quarters, values}                              -> annualized HPI
    bea_personal_income.{years, per_capita_income}           -> income fundamental
    census_acs_demographics.values.{median_home_value,
        median_gross_rent, median_household_income}          -> current cross-checks (optional)

Output (section "housing_valuation"):
    {
      "years": [...], "valuation_pct": [...],          # the bar-chart series
      "hpi_index": [...], "fair_value_index": [...],
      "latest_year": 2024, "latest_valuation_pct": 14.2,
      "income_elasticity": 1.83, "r_squared": 0.97, "n_obs": 6,
      "price_to_income_ratio": 4.1, "price_to_rent_ratio": 18.7,   # if ACS present
      "method": "...", "note": "...", "source": "...",
    }

Returns None if fewer than MIN_OBS overlapping years are available. Pure stdlib.
"""

from __future__ import annotations

import math
from typing import Optional, List, Dict, Tuple

MIN_OBS = 4  # need a few years of HPI∩income overlap to fit the gap regression


def _quarterly_to_annual_mean(quarters: List[str], values: List[float]) -> Dict[int, float]:
    """Average a quarterly index by calendar year; require >= 2 quarters present."""
    by_year: Dict[int, List[float]] = {}
    for q, v in zip(quarters or [], values or []):
        if v is None:
            continue
        try:
            yr = int(str(q)[:4])
        except (ValueError, TypeError):
            continue
        by_year.setdefault(yr, []).append(v)
    return {y: sum(vs) / len(vs) for y, vs in by_year.items() if len(vs) >= 2}


def _ols(xs: List[float], ys: List[float]) -> Optional[Tuple[float, float, float]]:
    """Simple OLS y = a + b*x. Returns (a, b, r_squared) or None if degenerate."""
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    b = sxy / sxx
    a = my - b * mx
    syy = sum((y - my) ** 2 for y in ys)
    if syy == 0:
        r2 = 1.0
    else:
        ss_res = sum((y - (a + b * x)) ** 2 for x, y in zip(xs, ys))
        r2 = max(0.0, 1.0 - ss_res / syy)
    return a, b, r2


def compute(cbsa: str, output_so_far: dict) -> Optional[dict]:
    sec = (output_so_far or {}).get("sections", {})
    hpi = sec.get("fhfa_hpi") or {}
    pinc = sec.get("bea_personal_income") or {}

    hpi_annual = _quarterly_to_annual_mean(hpi.get("quarters"), hpi.get("values"))

    pci: Dict[int, float] = {}
    yrs = pinc.get("years") or []
    vals = pinc.get("per_capita_income") or []
    for y, v in zip(yrs, vals):
        if v:
            try:
                pci[int(y)] = float(v)
            except (ValueError, TypeError):
                continue

    # Overlap years with both a positive HPI level and positive income.
    overlap = sorted(
        y for y in hpi_annual
        if y in pci and hpi_annual[y] > 0 and pci[y] > 0
    )
    if len(overlap) < MIN_OBS:
        return None

    log_hpi = [math.log(hpi_annual[y]) for y in overlap]
    log_pci = [math.log(pci[y]) for y in overlap]

    fit = _ols(log_pci, log_hpi)
    if fit is None:
        return None
    a, b, r2 = fit

    years: List[int] = []
    valuation_pct: List[float] = []
    hpi_series: List[float] = []
    fair_series: List[float] = []
    for y in overlap:
        fair = math.exp(a + b * math.log(pci[y]))
        if fair <= 0:
            continue
        val = 100.0 * (hpi_annual[y] / fair - 1.0)
        years.append(y)
        valuation_pct.append(round(val, 1))
        hpi_series.append(round(hpi_annual[y], 2))
        fair_series.append(round(fair, 2))

    if not years:
        return None

    out: Dict[str, object] = {
        "years": years,
        "valuation_pct": valuation_pct,
        "hpi_index": hpi_series,
        "fair_value_index": fair_series,
        "latest_year": years[-1],
        "latest_valuation_pct": valuation_pct[-1],
        "income_elasticity": round(b, 3),
        "r_squared": round(r2, 3),
        "n_obs": len(overlap),
    }

    # --- Current affordability cross-checks from the latest ACS vintage ---
    acs_vals = (sec.get("census_acs_demographics") or {}).get("values") or {}
    home_value = acs_vals.get("median_home_value")
    gross_rent = acs_vals.get("median_gross_rent")
    hh_income = acs_vals.get("median_household_income")
    if home_value and hh_income:
        out["price_to_income_ratio"] = round(home_value / hh_income, 2)
    if home_value and gross_rent:
        annual_rent = 12.0 * gross_rent
        if annual_rent > 0:
            out["price_to_rent_ratio"] = round(home_value / annual_rent, 1)

    out["method"] = (
        "Log-log fair-value regression (Dallas Fed / IMF house-price-gap method): "
        "log(FHFA HPI) on log(per-capita income) by OLS over overlapping years; "
        "valuation = % deviation of actual HPI from the income-implied fair value."
    )
    out["note"] = (
        f"Fitted on {len(overlap)} overlapping years ({overlap[0]}-{overlap[-1]}); "
        "residuals are mean-zero over this window, so values read relative to the "
        "local income trend. Price-to-income / price-to-rent are current ACS-vintage "
        "cross-checks, not part of the fitted series."
    )
    out["source"] = "EIG valuation model — FHFA HPI vs. BEA per-capita income (log-log OLS)"
    return out
