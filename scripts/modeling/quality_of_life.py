"""EIG Quality-of-Life Index — a standardized 0-300 composite for an MSA.

Combines liveability signals, each z-scored against an approximate U.S.-metro
reference distribution (sign-adjusted so higher z = better), averaged, then
scaled to a 0-300 band (150 = U.S. metro average):

    air quality — median AQI    (EPA AirData)         lower = better
    commute 60+ min share       (ACS B08303)          lower = better
    housing affordability        (ACS price-to-income) lower = better
    poverty rate                 (ACS B17001)          lower = better
    school spending per pupil    (Census F-33)         higher = better  [if available]

CRIME IS DELIBERATELY EXCLUDED. There is no clean, automated, current MSA-level
crime feed: the FBI's Crime Data API is key-gated and reports only by agency/state
(no MSA), and the FBI's "Crime by MSA" table was discontinued after 2019. Rather
than embed a stale 2019 figure, we omit crime and document it here.

Output:
    {
      "value": 182,                 # 0-300 (150 = avg; higher = better QoL)
      "rank_estimate": 156,          # estimated rank of ~387 (1 = best)
      "n_metros": 387,
      "mean_z": 0.34,
      "components": {...}, "excluded": ["crime"], "method": "...",
    }

Returns None if no component is available. Pure stdlib.
"""

from __future__ import annotations

import math
from typing import Optional, Dict

N_METROS = 387

# (mean, sd, higher_is_better) approximate U.S.-metro reference distribution.
BENCHMARKS = {
    "median_aqi":           (45.0, 14.0, False),
    "commute_60_plus_pct":  (9.0, 4.0, False),
    "price_to_income":      (4.0, 1.3, False),
    "poverty_rate_pct":     (13.0, 4.5, False),
    "per_pupil_spending":   (15000.0, 3500.0, True),
}
WEIGHTS = {
    "median_aqi":          1.0,
    "commute_60_plus_pct": 0.8,
    "price_to_income":     1.0,
    "poverty_rate_pct":    1.0,
    "per_pupil_spending":  0.8,
}


def _norm_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def compute(cbsa: str, output_so_far: dict) -> Optional[dict]:
    sec = (output_so_far or {}).get("sections", {})
    acs = (sec.get("census_acs_demographics") or {}).get("derived", {}) or {}
    air = sec.get("epa_air_quality") or {}
    school = sec.get("school_finance") or {}

    raw: Dict[str, Optional[float]] = {
        "median_aqi": air.get("median_aqi"),
        "commute_60_plus_pct": acs.get("pct_commute_60_plus"),
        "price_to_income": acs.get("price_to_income_ratio"),
        "poverty_rate_pct": acs.get("poverty_rate_pct"),
        "per_pupil_spending": school.get("per_pupil_current") if school else None,
    }

    components: Dict[str, dict] = {}
    zsum = 0.0
    wsum = 0.0
    for name, val in raw.items():
        if val is None:
            continue
        mean, sd, higher_better = BENCHMARKS[name]
        z = (val - mean) / sd if sd else 0.0
        if not higher_better:
            z = -z                      # invert so higher z = better QoL
        w = WEIGHTS[name]
        components[name] = {"value": val, "z": round(z, 3), "weight": w}
        zsum += w * z
        wsum += w

    if not components:
        return None

    mean_z = zsum / wsum
    value = max(0, min(300, round(150 + 50 * mean_z)))
    rank = max(1, min(N_METROS, round((1.0 - _norm_cdf(mean_z)) * N_METROS)))

    return {
        "value": value,
        "rank_estimate": rank,
        "n_metros": N_METROS,
        "mean_z": round(mean_z, 3),
        "components": components,
        "excluded": ["crime"],
        "method": ("EIG composite: weighted average of sign-adjusted z-scores for air "
                   "quality, commute, affordability, poverty and school spending vs. "
                   "approximate U.S.-metro benchmarks; scaled to 0-300 (150 = average)."),
        "note": ("Crime excluded — no clean automated current MSA-level source exists "
                 "(FBI API is agency/state only; the FBI by-MSA table ended in 2019). "
                 "Rank is an estimate from the composite's normal percentile."),
        "source": "EIG composite — computed from EPA, ACS and Census F-33 inputs",
    }
