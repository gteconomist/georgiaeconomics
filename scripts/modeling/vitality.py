"""EIG Vitality Index — a standardized composite of an MSA's economic dynamism.

Combines four forward-looking signals, each z-scored against an approximate U.S.
metro reference distribution, then averaged:

    labor-force participation  (ACS B23025)        higher = better
    income growth (latest YoY)  (BEA personal income) higher = better
    young-adult share 25-34     (ACS B01001)        higher = better
    net migration rate          (IRS SOI / population) higher = better

Output:
    {
      "value": 0.71,                # 0-1 score = normal-CDF of the mean z-score
      "rank_estimate": 47,          # estimated rank of ~387 metros (1 = most vital)
      "n_metros": 387,
      "mean_z": 0.55,
      "components": { "<name>": {"value":.., "z":.., "weight":..}, ... },
      "method": "...", "note": "...",
    }

The rank is an ESTIMATE derived from the composite's normal percentile against the
hard-coded reference benchmarks below — not a live ranking against all 387 metros
(we don't pull every metro). Benchmarks are approximate U.S.-metro mean/SD and are
documented so they can be refined later.

Returns None if no component is available. Pure stdlib.
"""

from __future__ import annotations

import math
from typing import Optional, Dict

N_METROS = 387

# Approximate U.S.-metro reference distribution (mean, sd) per component.
# Documented as EIG reference benchmarks; refine as better national data lands.
BENCHMARKS = {
    "labor_force_participation": (63.0, 4.5),
    "income_growth_yoy":         (5.0, 2.0),
    "young_adult_share":         (13.3, 1.8),
    "net_migration_rate":        (0.3, 1.2),
}
WEIGHTS = {
    "labor_force_participation": 1.0,
    "income_growth_yoy":         1.0,
    "young_adult_share":         0.8,
    "net_migration_rate":        1.2,
}


def _norm_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def compute(cbsa: str, output_so_far: dict) -> Optional[dict]:
    sec = (output_so_far or {}).get("sections", {})
    acs = (sec.get("census_acs_demographics") or {}).get("derived", {}) or {}
    pi = sec.get("bea_personal_income") or {}
    mig = sec.get("irs_soi_migration") or {}
    pep = sec.get("census_pep") or {}

    raw: Dict[str, Optional[float]] = {
        "labor_force_participation": acs.get("labor_force_participation_pct"),
        "income_growth_yoy": pi.get("latest_yoy"),
        "young_adult_share": acs.get("young_adult_25_34_pct"),
    }
    # net migration rate = net migrants / population * 100
    pop = pep.get("latest_population")
    if mig.get("net") is not None and pop:
        raw["net_migration_rate"] = round(100.0 * mig["net"] / pop, 3)
    else:
        raw["net_migration_rate"] = None

    components: Dict[str, dict] = {}
    zsum = 0.0
    wsum = 0.0
    for name, val in raw.items():
        if val is None:
            continue
        mean, sd = BENCHMARKS[name]
        z = (val - mean) / sd if sd else 0.0
        w = WEIGHTS[name]
        components[name] = {"value": val, "z": round(z, 3), "weight": w}
        zsum += w * z
        wsum += w

    if not components:
        return None

    mean_z = zsum / wsum
    value = round(_norm_cdf(mean_z), 2)
    rank = max(1, min(N_METROS, round((1.0 - _norm_cdf(mean_z)) * N_METROS)))

    return {
        "value": value,
        "rank_estimate": rank,
        "n_metros": N_METROS,
        "mean_z": round(mean_z, 3),
        "components": components,
        "method": ("EIG composite: weighted average of z-scores for LFP, income growth, "
                   "young-adult share and net-migration rate vs. approximate U.S.-metro "
                   "benchmarks; score = normal CDF of the mean z."),
        "note": ("Rank is an estimate from the composite's normal percentile against "
                 "reference benchmarks, not a live ranking of all metros."),
        "source": "EIG composite — computed from ACS, BEA and IRS SOI inputs",
    }
