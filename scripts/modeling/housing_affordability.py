"""EIG Housing Affordability Index for the Metro Economic Profile report.

NAR-style Housing Affordability Index (HAI):

    index = (median household income / qualifying income) * 100

where *qualifying income* is the income needed to carry the principal+interest
payment on a median-priced home at the prevailing 30-year fixed rate, assuming a
20% down payment and a 28% front-end debt-to-income ratio. index = 100 means the
median household exactly qualifies for the median home; > 100 = more affordable,
< 100 = less affordable.

All inputs are public / subscription-free:
  - Median home PRICE per year: ACS B25077 (median owner-occupied value) as the
    MSA-specific dollar anchor, scaled across years by the FHFA HPI ratio so the
    level is local and the trajectory follows FHFA's index.
  - Median household INCOME per year: ACS B19013.
  - 30-year fixed mortgage RATE per year: Freddie Mac PMMS via FRED
    (MORTGAGE30US), annual average.

This is a modeling module: it reads `fhfa_hpi` from the orchestrator's in-progress
output and fetches the mortgage rate, ACS income, and ACS home value itself. It is
fail-soft — any missing input returns None so the section is honestly "failed"
rather than rendering a fabricated series.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent / "reporting"))
import pull_census  # noqa: E402
import pull_fhfa    # noqa: E402

DOWN_PAYMENT = 0.20
FRONT_END_DTI = 0.28
TERM_MONTHS = 360
MAX_YEARS = 6  # bound the number of ACS/FRED calls per run


def _annual_hpi(fhfa: dict) -> dict:
    """Annualize the quarterly FHFA HPI to {year: mean_index}."""
    by: dict = {}
    for q, v in zip(fhfa.get("quarters", []), fhfa.get("values", [])):
        if v is None:
            continue
        y = int(str(q)[:4])
        by.setdefault(y, []).append(v)
    return {y: sum(vals) / len(vals) for y, vals in by.items()}


# The 30-yr mortgage rate is a single national series — identical for every MSA —
# so cache it module-level. An --all run then hits FRED once instead of 14 times.
_RATE_CACHE: dict = {}


def _annual_mortgage_rate() -> dict:
    """{year: avg 30-yr fixed rate %} from FRED MORTGAGE30US (weekly Freddie PMMS).
    Cached on first success so a multi-MSA run only calls FRED once."""
    global _RATE_CACHE
    if _RATE_CACHE:
        return _RATE_CACHE
    obs = pull_fhfa._fred_observations("MORTGAGE30US", start_date="2014-01-01")
    if not obs:
        return {}
    by: dict = {}
    for o in obs:
        v = o.get("value")
        if v in (None, ".", ""):
            continue
        try:
            rate = float(v)
        except (ValueError, TypeError):
            continue
        y = int(str(o.get("date", ""))[:4])
        if y:
            by.setdefault(y, []).append(rate)
    result = {y: sum(vals) / len(vals) for y, vals in by.items()}
    if result:
        _RATE_CACHE = result  # cache only on success (transient FRED 429s retry)
    return result


def _monthly_pi(principal: float, annual_rate_pct: float) -> float:
    """Monthly principal+interest payment on a fully-amortizing fixed mortgage."""
    r = annual_rate_pct / 100.0 / 12.0
    if r <= 0:
        return principal / TERM_MONTHS
    factor = (1 + r) ** TERM_MONTHS
    return principal * r * factor / (factor - 1)


def compute(cbsa: str, output_so_far: dict) -> Optional[dict]:
    sections = (output_so_far or {}).get("sections", {})
    fhfa = sections.get("fhfa_hpi")
    if not fhfa:
        return None
    hpi = _annual_hpi(fhfa)
    if not hpi:
        return None
    rates = _annual_mortgage_rate()
    if not rates:
        print("  [affordability] no mortgage-rate series (FRED) — failed", file=sys.stderr)
        return None

    # Dollar price anchor: most recent ACS median home value we can fetch.
    anchor_year = anchor_value = None
    for y in sorted(hpi, reverse=True):
        hv = pull_census.fetch_acs_median_home_value(cbsa, y)
        if hv:
            anchor_year, anchor_value = y, hv
            break
    if not anchor_value or not hpi.get(anchor_year):
        print("  [affordability] no ACS median home value anchor — failed", file=sys.stderr)
        return None

    pred = pull_census._msa_predicate(cbsa)
    # Reuse the median household income the acs_affordability section already pulled
    # this run (avoids 6 fresh ACS calls per metro). Fall back to a direct fetch only
    # for years it didn't cover.
    aff = sections.get("acs_affordability") or {}
    income_by_year = {y: v for y, v in zip(aff.get("years") or [],
                                           aff.get("msa_median_income") or []) if v}

    # Years we can actually compute: have HPI and a mortgage rate, capped to recent.
    candidate_years = sorted(y for y in hpi if y in rates)[-MAX_YEARS:]

    years, index = [], []
    for y in candidate_years:
        income = income_by_year.get(y)
        if income is None:
            ri = pull_census._acs_rent_income(pred, y)  # (rent, income); income is [1]
            if not ri:
                continue
            income = ri[1]
        price = anchor_value * (hpi[y] / hpi[anchor_year])
        loan = price * (1 - DOWN_PAYMENT)
        pi = _monthly_pi(loan, rates[y])
        qualifying = (pi * 12) / FRONT_END_DTI
        if qualifying <= 0:
            continue
        years.append(y)
        index.append(round(100 * income / qualifying, 1))

    if len(years) < 2:
        print("  [affordability] <2 computable years — failed", file=sys.stderr)
        return None

    return {
        "years": years,
        "affordability_index": index,
        "latest_index": index[-1],
        "latest_year": years[-1],
        "anchor": {"year": anchor_year, "median_home_value": round(anchor_value)},
        "assumptions": {
            "down_payment_pct": 20,
            "front_end_dti_pct": 28,
            "term_years": 30,
        },
        "source": ("EIG composite — NAR-style affordability: ACS median household income "
                   "(B19013) vs. income needed for the median home (ACS B25077 value scaled "
                   "by FHFA HPI; Freddie Mac PMMS 30-yr rate via FRED). >100 = median household "
                   "can afford the median home."),
    }
