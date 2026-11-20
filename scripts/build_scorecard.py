"""Build data/scorecard.json — the statewide "Economy at a glance" rollup (Phase 5, WS5).

Pulls one headline KPI from each topic's JSON into a single small file the home
page renders as a card strip (GE.scorecard in assets/app.js). Values are
formatted here so the renderer stays dumb. Deterministic output, so an unchanged
run produces no git diff. Run in CI after the topic JSONs refresh.

  trend: "good" | "bad" | "neutral"  → drives the delta colour (teal / coral / grey).

Usage:  python3 scripts/build_scorecard.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def _load(name):
    return json.loads((DATA / f"{name}.json").read_text())


def _latest_yoy(section, key):
    """Most recent non-null value of section[key] (a YoY list aligned to
    section["months"]), with its YYYY-MM. (None, None) if there is none."""
    months = section.get("months") or []
    vals = section.get(key) or []
    for m, v in zip(reversed(months), reversed(vals[:len(months)])):
        if isinstance(v, (int, float)):
            return v, m
    return None, None


_MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _ym_label(ym):
    return f"{_MON[int(ym[5:7]) - 1]} {ym[:4]}" if ym else ""


def _inflation_card(infl, infl_full):
    """Headline South CPI YoY. When the latest month's YoY is null (BLS never
    published the year-ago month — e.g. Oct 2025, skipped during the federal
    shutdown), fall back to the most recent month that has a YoY and say which
    month it is, rather than crashing the whole nightly run."""
    south, us = infl.get("headline_yoy_south"), infl.get("headline_yoy_us")
    period = None
    if not isinstance(south, (int, float)):
        head = infl_full.get("south_headline") or {}
        south, ym = _latest_yoy(head, "south_yoy")
        us_ym_val = None
        if ym:
            months = head.get("months") or []
            us_list = head.get("us_yoy") or []
            i = months.index(ym)
            us_ym_val = us_list[i] if i < len(us_list) else None
        us = us_ym_val
        period = _ym_label(ym)
    card = {"topic": "Inflation", "href": "/inflation/", "label": "Inflation (South)"}
    if not isinstance(south, (int, float)):
        return {**card, "value": "n/a", "delta": "BLS CPI, YoY", "trend": "neutral"}
    delta = f"U.S. {us:.1f}%" if isinstance(us, (int, float)) else "BLS CPI, YoY"
    if period:
        delta = f"{period} · {delta}"
    # above the Fed's ~2% target reads as bad
    return {**card, "value": f"{south:+.1f}%", "delta": delta,
            "trend": "bad" if south > 2.5 else "neutral"}


def _safe(build):
    """Build one card; if its source KPI is missing/null, skip that card with a
    warning instead of failing the whole nightly workflow (which would also
    block the commit of every other refreshed page)."""
    try:
        return build()
    except (KeyError, TypeError, ValueError) as e:
        print(f"build_scorecard: WARNING skipped a card ({type(e).__name__}: {e})")
        return None


def main() -> int:
    labor = _load("labor")["kpis"]
    house = _load("housing")["kpis"]
    gdp = _load("gdp")["kpis"]
    infl_full = _load("inflation")
    infl = infl_full["kpis"]
    pop = _load("population")["kpis"]
    out = _load("outlook")["kpis"]
    try:
        cons = _load("consumer").get("kpis", {})
    except FileNotFoundError:
        cons = {}

    pop_latest = pop["population_latest"]
    base_year = pop["latest_year"] - 5

    cards = [c for c in (
        _safe(lambda: {
            "topic": "Labor", "href": "/labor/", "label": "Unemployment",
            "value": f"{labor['unemployment_rate_latest']:.1f}%",
            "delta": f"{labor['unemployment_rate_yoy_delta']:+.1f} pp YoY",
            # rising unemployment is bad; falling is good
            "trend": "bad" if labor["unemployment_rate_yoy_delta"] > 0 else ("good" if labor["unemployment_rate_yoy_delta"] < 0 else "neutral"),
        }),
        _safe(lambda: {
            "topic": "Population", "href": "/population/", "label": "Population",
            "value": f"{pop_latest/1e6:.1f}M",
            "delta": f"+{pop['growth_pct_5yr']:.1f}% since {base_year}",
            "trend": "good" if pop["growth_pct_5yr"] >= 0 else "bad",
        }),
        _safe(lambda: {
            "topic": "GDP", "href": "/gdp/", "label": "GDP per capita",
            "value": f"${gdp['ga_gdp_per_capita']:,}",
            "delta": f"{gdp['ga_real_gdp_yoy']:+.1f}% real GDP YoY",
            "trend": "good" if gdp["ga_real_gdp_yoy"] >= 0 else "bad",
        }),
        _safe(lambda: {
            "topic": "Housing", "href": "/housing/", "label": "Home prices",
            "value": f"{house['ga_home_price_yoy']:+.1f}%",
            "delta": "FHFA HPI, YoY",
            "trend": "neutral",
        }),
        _safe(lambda: _inflation_card(infl, infl_full)),
        _safe(lambda: {
            "topic": "Migration", "href": "/migration/", "label": "Net migration",
            "value": f"+{pop['net_mig_latest']:,}",
            "delta": "domestic + international, yr",
            "trend": "good" if pop["net_mig_latest"] >= 0 else "bad",
        }),
        _safe(lambda: {
            "topic": "Outlook", "href": "/outlook/", "label": "Business cycle",
            "value": out["cycle_direction"].title(),
            "delta": f"{out['cycle_vs_peak']:+.1f}% vs peak",
            "trend": "good" if str(out["cycle_direction"]).lower() in ("expanding", "recovering") else ("bad" if str(out["cycle_direction"]).lower() in ("contracting", "slowing") else "neutral"),
        }),
    ) if c is not None]

    # Consumer card — only when the BEA PCE per-capita figure is live (guards the
    # offline/degraded state where consumer KPIs are still null).
    if isinstance(cons.get("pce_per_capita"), (int, float)):
        card = {
            "topic": "Consumer", "href": "/consumer/", "label": "Spending per capita",
            "value": f"${cons['pce_per_capita']:,}",
            "delta": (f"{cons['pce_yoy_pct']:+.1f}% PCE YoY"
                      if isinstance(cons.get("pce_yoy_pct"), (int, float)) else "BEA PCE per resident"),
            "trend": ("good" if isinstance(cons.get("pce_yoy_pct"), (int, float)) and cons["pce_yoy_pct"] >= 0 else "neutral"),
        }
        cards.append(card)

    payload = {
        "_note": "Statewide headline KPIs for the home-page scorecard; generated by scripts/build_scorecard.py.",
        "as_of": {
            "labor": _load("labor").get("latest_label"),
            "gdp": _load("gdp").get("latest_label"),
            "inflation": infl.get("latest_month_south"),
        },
        "cards": cards,
    }
    (DATA / "scorecard.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n")
    print(f"build_scorecard: wrote {len(cards)} cards -> data/scorecard.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
