"""Build data/county_metrics.json — the static per-county metric layer that powers
the extra metrics on the /counties/ map.

The counties page already animates unemployment over 12 months from
data/counties.json (BLS LAUS). This roll-up assembles the *static* (latest-vintage)
county metrics the page also offers, pulled from layers we already compute:

  median_income      Census ACS 5-yr  (data/housing.json  county_acs.median_household_income)
  median_home_value  Census ACS 5-yr  (data/housing.json  county_acs.median_home_value)
  poverty_rate       Census ACS 5-yr  (data/housing.json  county_acs.pct_poverty)
  population_change  Census PEP       (data/population.json counties[].growth_pct)
  net_migration      Census PEP       (data/population.json counties[].dom_mig_total + intl_mig_total)
  gdp_per_capita     BEA CAGDP2       (data/gdp.json       county_gdp.counties[].gdp_per_capita)

Pure local read — no API keys. Each metric is included only when ≥ ~half the
counties have a value, so the page can disable a still-empty metric (e.g. poverty
before the next ACS pull adds B17001). Deterministic output.

Run AFTER fetch_housing.py / fetch_gdp.py / fetch_migration.py in CI so it reads
fresh layers.

Usage:  python3 scripts/build_county_metrics.py
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = DATA / "county_metrics.json"

# Metric catalog. colorscale/polarity drive the map + leaderboards:
#   good_high → sequential (high = strong),  good_low → inverse (low = strong),
#   neutral   → sequential (informational),  signed    → diverging (centred on 0).
METRICS = [
    {"key": "median_income", "label": "Median household income", "unit": "$", "fmt": "usd0",
     "polarity": "good_high", "colorscale": "sequential", "source": "Census ACS 5-year (B19013)"},
    {"key": "population_change", "label": "Population change", "unit": "%", "fmt": "pct1signed",
     "polarity": "good_high", "colorscale": "diverging", "source": "Census PEP (5-yr growth)"},
    {"key": "poverty_rate", "label": "Poverty rate", "unit": "%", "fmt": "pct1",
     "polarity": "good_low", "colorscale": "inverse", "source": "Census ACS 5-year (B17001)"},
    {"key": "median_home_value", "label": "Median home value", "unit": "$", "fmt": "usd0",
     "polarity": "neutral", "colorscale": "sequential", "source": "Census ACS 5-year (B25077)"},
    {"key": "net_migration", "label": "Net migration (persons/yr)", "unit": "", "fmt": "num0signed",
     "polarity": "good_high", "colorscale": "diverging", "source": "Census PEP (domestic + international)"},
    {"key": "gdp_per_capita", "label": "GDP per capita", "unit": "$", "fmt": "usd0",
     "polarity": "good_high", "colorscale": "sequential", "source": "BEA CAGDP2 (county)"},
]

MIN_COVERAGE = 80   # of 159 counties; below this a metric is omitted (not yet live)


def _load(name: str) -> dict:
    p = DATA / f"{name}.json"
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _num(v) -> Optional[float]:
    return v if isinstance(v, (int, float)) else None


def main() -> int:
    housing = _load("housing")
    population = _load("population")
    gdp = _load("gdp")

    acs = ((housing.get("county_acs") or {}).get("counties")) or {}
    pep = {str(r.get("fips")): r for r in (population.get("counties") or []) if r.get("fips")}
    cgdp = ((gdp.get("county_gdp") or {}).get("counties")) or {}

    # Union of all county fips seen across the layers.
    fips_all = set(acs) | set(pep) | set(cgdp)

    counties: Dict[str, Dict[str, Any]] = {}
    for fips in sorted(fips_all):
        a = acs.get(fips) or {}
        p = pep.get(fips) or {}
        g = cgdp.get(fips) or {}
        name = a.get("name") or p.get("county") or g.get("name") or fips
        dom, intl = _num(p.get("dom_mig_total")), _num(p.get("intl_mig_total"))
        net_mig = (dom or 0) + (intl or 0) if (dom is not None or intl is not None) else None
        rec = {
            "name": name,
            "median_income": _num(a.get("median_household_income")),
            "median_home_value": _num(a.get("median_home_value")),
            "poverty_rate": _num(a.get("pct_poverty")),
            "population_change": _num(p.get("growth_pct")),
            "net_migration": net_mig,
            "gdp_per_capita": _num(g.get("gdp_per_capita")),
        }
        counties[fips] = rec

    # Keep only metrics with enough coverage to be worth showing.
    metrics_out = []
    for m in METRICS:
        n = sum(1 for r in counties.values() if r.get(m["key"]) is not None)
        if n >= MIN_COVERAGE:
            mm = dict(m); mm["n_counties"] = n
            metrics_out.append(mm)
        else:
            print(f"  [county_metrics] omitting {m['key']} — only {n}/{len(counties)} counties "
                  f"(needs ≥ {MIN_COVERAGE}); will appear once its source layer is live")

    out = {
        "_note": "Static per-county metrics for the /counties/ map; built by scripts/build_county_metrics.py.",
        "fetched_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "metrics": metrics_out,
        "counties": counties,
        "acs_vintage": (housing.get("county_acs") or {}).get("vintage_window"),
        "source_summary": {
            "median_income / median_home_value / poverty_rate": "Census ACS 5-year (via housing layer)",
            "population_change / net_migration": "Census PEP (via population layer)",
            "gdp_per_capita": "BEA CAGDP2 (via GDP layer)",
        },
    }
    OUT.write_text(json.dumps(out, indent=1))
    live = [m["key"] for m in metrics_out]
    print(f"Wrote {OUT.relative_to(ROOT)} — {len(counties)} counties; live metrics: {live}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
