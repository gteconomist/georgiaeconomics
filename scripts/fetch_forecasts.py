"""Build the Georgia Outlook page dataset (business cycle + 5-yr forecast)
-> data/outlook.json

Phase 4, WS2 #2. Surfaces, statewide, the two modeling outputs the metro
reports already compute per metro:
  • a Stock-Watson coincident BUSINESS-CYCLE INDEX, and
  • a damped-Holt 5-YEAR FORECAST of the headline indicators.

Zero new external pulls. The statewide versions are produced by feeding Georgia
actuals we already have into the SAME modeling helpers the metros use:

  ces_employment      <- data/labor.json total_payrolls_k        (always present)
  laus_unemployment   <- data/labor.json unemployment_rate       (always present)
  bea_gmp             <- data/gdp.json   ga_gdp.nominal_bn        (after BEA run)
  census_pep          <- data/population.json state              (always present)
  fhfa_hpi            <- data/housing.json ga_hpi                 (after FRED run)

So the GA cycle index is live from labor.json alone; the GA forecast covers
employment / unemployment / population immediately and fills in GMP / HPI once
gdp.json / housing.json populate. The metro roll-ups read the 14 reports.

⚠️ The 5-year figures are MODEL EXTRAPOLATIONS (damped-Holt / ARIMA family) from
historical actuals — NOT official forecasts or advice. The page carries a
visible disclaimer; this dataset includes a `disclaimer` string for it.

Graceful degradation: each section wrapped in try/except; on failure we preserve
the prior value and don't bump _meta.<section>.last_updated.

Env: none (pure reuse + local reads).

Usage:
  python scripts/fetch_forecasts.py            # full
  python scripts/fetch_forecasts.py --rollup   # identical here (no network either way)
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, date
from pathlib import Path
from typing import Optional, Dict, List, Any

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
MSA_REPORTS = DATA / "msa_reports"
OUT = DATA / "outlook.json"

STALE_MONTHS = 4  # cycle index is monthly; forecast refreshes when inputs do

sys.path.insert(0, str(ROOT / "scripts"))
try:
    from modeling import business_cycle_index, forecast_arima  # type: ignore
except Exception as e:  # pragma: no cover
    business_cycle_index = None
    forecast_arima = None
    print(f"  [outlook] modeling import failed: {e}", file=sys.stderr)

DISCLAIMER = ("The 5-year figures are model extrapolations from historical trends "
             "(damped-Holt / ARIMA-family) — not official forecasts, predictions, or "
             "investment advice. They assume recent trends persist and do not account "
             "for policy changes, shocks, or expert judgment.")


def _now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_prior() -> dict:
    if OUT.exists():
        try:
            return json.loads(OUT.read_text())
        except Exception:
            return {}
    return {}


def _read(name: str) -> dict:
    p = DATA / name
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _pairs_to_series(pairs) -> dict:
    """[[month, value], ...] -> {months:[...], values:[...]}."""
    months, values = [], []
    for row in pairs or []:
        if isinstance(row, (list, tuple)) and len(row) >= 2:
            months.append(row[0]); values.append(row[1])
    return {"months": months, "values": values}


# --------------------------------------------------------------------------- #
# build the statewide "sections" dict the modeling helpers expect
# --------------------------------------------------------------------------- #
def ga_sections() -> dict:
    labor = _read("labor.json")
    gdp = _read("gdp.json")
    pop = _read("population.json")
    housing = _read("housing.json")

    sec: Dict[str, Any] = {}
    if labor.get("total_payrolls_k"):
        sec["ces_employment"] = _pairs_to_series(labor["total_payrolls_k"])
    if labor.get("unemployment_rate"):
        sec["laus_unemployment"] = _pairs_to_series(labor["unemployment_rate"])

    ga = gdp.get("ga_gdp") or {}
    if ga.get("years") and ga.get("nominal_bn"):
        # align years with non-null nominal values
        yrs, vals = [], []
        for y, v in zip(ga["years"], ga["nominal_bn"]):
            if v is not None:
                yrs.append(y); vals.append(v)
        if yrs:
            sec["bea_gmp"] = {"years": yrs, "gmp_billions_usd": vals}

    st = pop.get("state") or {}
    if st.get("years") and st.get("population"):
        sec["census_pep"] = {"years": st["years"], "population": st["population"]}

    gh = housing.get("ga_hpi") or {}
    if gh.get("quarters") and gh.get("values"):
        sec["fhfa_hpi"] = {"quarters": gh["quarters"], "values": gh["values"]}

    return sec


# --------------------------------------------------------------------------- #
# metro roll-up
# --------------------------------------------------------------------------- #
def _cagr(arr: Optional[List[float]]) -> Optional[float]:
    if not arr or len(arr) < 2 or not arr[0]:
        return None
    n = len(arr) - 1
    try:
        return round(100 * ((arr[-1] / arr[0]) ** (1.0 / n) - 1), 2)
    except Exception:
        return None


def rollup_metros() -> List[dict]:
    metros: List[dict] = []
    for path in sorted(MSA_REPORTS.glob("*.json")):
        rep = json.loads(path.read_text())
        sec, st = rep.get("sections", {}), rep.get("section_status", {})
        block: Dict[str, Any] = {"slug": path.stem, "short_name": rep.get("short_name")}
        bci = sec.get("business_cycle_index") if st.get("business_cycle_index") in ("live", "partial", "stale") else None
        fc = sec.get("forecast_arima") if st.get("forecast_arima") in ("live", "partial", "stale") else None
        if bci:
            lv, pv = bci.get("latest_value"), bci.get("peak_value")
            block["cycle_latest"] = lv
            block["cycle_peak"] = pv
            block["cycle_latest_month"] = bci.get("latest_month")
            block["pct_from_peak"] = round(100 * (lv - pv) / pv, 1) if (lv is not None and pv) else None
        if fc:
            block["fc_gmp_cagr"] = _cagr(fc.get("gmp"))
            block["fc_emp_cagr"] = _cagr(fc.get("total_employment"))
            block["fc_gmp_2030"] = (fc.get("gmp") or [None])[-1]
            block["fc_emp_2030"] = (fc.get("total_employment") or [None])[-1]
        metros.append(block)
    return metros


# --------------------------------------------------------------------------- #
# assembly
# --------------------------------------------------------------------------- #
def _is_stale(meta_entry: Optional[dict]) -> bool:
    if not meta_entry or not meta_entry.get("last_updated"):
        return True
    try:
        d = datetime.strptime(meta_entry["last_updated"][:10], "%Y-%m-%d")
    except Exception:
        return True
    return (datetime.utcnow() - d).days > STALE_MONTHS * 30


def _cycle_direction(values: List[float]) -> Optional[str]:
    if not values or len(values) < 4:
        return None
    delta = values[-1] - values[-4]  # ~3-month change
    if delta > 0.5:
        return "expanding"
    if delta < -0.5:
        return "contracting"
    return "steady"


def build(rollup_only: bool = False) -> dict:
    prior = _load_prior()
    prior_meta = prior.get("_meta", {})
    meta: Dict[str, dict] = {}
    out: Dict[str, Any] = {"fetched_at": _now_iso(), "schema": "outlook/v1",
                           "disclaimer": DISCLAIMER}

    out["metros"] = rollup_metros()
    meta["metro_rollup"] = {"last_updated": _now_iso(), "n_metros": len(out["metros"])}

    sec = ga_sections()
    ga_output = {"sections": sec}

    def section(name, fn):
        try:
            val = fn()
        except Exception as e:
            print(f"  [outlook] {name} raised {type(e).__name__}: {str(e)[:80]}", file=sys.stderr)
            val = None
        if val:
            out[name] = val
            meta[name] = {"last_updated": _now_iso()}
        elif name in prior:
            out[name] = prior[name]
            meta[name] = prior_meta.get(name, {"last_updated": None})
        return out.get(name)

    section("ga_cycle",
            lambda: business_cycle_index.compute("GA", ga_output) if business_cycle_index else None)
    section("ga_forecast",
            lambda: forecast_arima.compute("GA", ga_output) if forecast_arima else None)

    cyc = out.get("ga_cycle") or {}
    fcst = out.get("ga_forecast") or {}
    metros = out["metros"]
    above = [m for m in metros if isinstance(m.get("pct_from_peak"), (int, float)) and m["pct_from_peak"] >= 0]

    def _last(key):
        arr = fcst.get(key)
        return arr[-1] if isinstance(arr, list) and arr else None

    out["kpis"] = {
        "cycle_latest": cyc.get("latest_value"),
        "cycle_vs_peak": (round(cyc["latest_value"] - cyc["peak_value"], 1)
                          if cyc.get("latest_value") is not None and cyc.get("peak_value") else None),
        "cycle_direction": _cycle_direction(cyc.get("values") or []),
        "fc_gmp_growth_2030": _last("gmp_yoy"),
        "fc_emp_growth_2030": _last("total_employment_yoy"),
        "metros_above_peak": len(above),
        "metro_count": len(metros),
    }

    for v in meta.values():
        v["stale"] = _is_stale(v)
    out["_meta"] = meta
    out["latest_label"] = cyc.get("latest_month") or date.today().strftime("%Y-%m")
    out["coverage_note"] = ("Statewide cycle index and forecast are computed from Georgia "
                            "actuals; metro figures roll up from the 14 metro reports. This "
                            "page is state- and metro-level (not county).")
    out["source_summary"] = {
        "ga_cycle": "Stock-Watson coincident index — BLS CES + LAUS (GA statewide)",
        "ga_forecast": "Damped-Holt 5-yr forecast — GA BLS/BEA/Census/FHFA actuals",
        "metros": "Roll-up of msa_reports/*.json (business_cycle_index + forecast_arima)",
    }
    return out


def main(argv: List[str]) -> int:
    out = build(rollup_only=("--rollup" in argv))
    OUT.write_text(json.dumps(out, indent=1))
    live = [k for k, v in out["_meta"].items() if not v.get("stale")]
    stale = [k for k, v in out["_meta"].items() if v.get("stale")]
    print(f"Wrote {OUT.relative_to(ROOT)} — {len(out['metros'])} metros; "
          f"live: {live}; stale/absent: {stale}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
