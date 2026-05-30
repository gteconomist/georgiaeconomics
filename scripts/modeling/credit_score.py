"""EIG Credit Score — a municipal-rating-style grade for the metro economy.

Synthesizes the report's other live signals into a single 0-100 creditworthiness
score, a muni-bond-style letter grade (AAA … BB), and an outlook (Positive / Stable /
Negative). Runs LAST in the modeling pass so every input it reads is already computed.

Component sub-scores (each mapped to 0-1, then weighted):
    vitality            0.25   forward dynamism            -> vitality.value (already 0-1)
    business_cycle      0.20   current expansion           -> Φ((BCI - 100)/10)
    labor_market        0.20   unemployment level (low=good)-> Φ(-(unemp - 4.0)/1.3)
    quality_of_life     0.15   livability                  -> Φ(qol.mean_z)
    income_growth       0.10   latest personal-income YoY  -> Φ((yoy - 5.0)/2.0)
    valuation_stability 0.10   distance from fair value    -> 1 - min(|val|,30)/30

Weights renormalize over whatever components are available (so a missing input
doesn't deflate the score). Score 0-100 = 100 * weighted mean of sub-scores.

Letter grade bands (0-100): >=90 AAA · 85 AA+ · 80 AA · 75 AA- · 70 A+ · 65 A ·
60 A- · 55 BBB+ · 50 BBB · 45 BBB- · 40 BB+ · else BB.

Outlook from business-cycle momentum (latest vs. ~6 months prior) combined with
income growth: clearly rising -> Positive, clearly falling -> Negative, else Stable.

Inputs (output["sections"]): vitality, business_cycle_index, quality_of_life,
laus_unemployment, bea_personal_income, housing_valuation.

Output (section "credit_score"):
    { "score": 84, "grade": "AA", "outlook": "Stable",
      "components": {...}, "method": "...", "note": "...", "source": "..." }

Returns None if no component is available. Pure stdlib.
"""

from __future__ import annotations

import math
from typing import Optional, Dict

# US-metro reference points for the normalized sub-scores (documented, refinable).
US_UNEMP_MEAN, US_UNEMP_SD = 4.0, 1.3
US_INCOME_GROWTH_MEAN, US_INCOME_GROWTH_SD = 5.0, 2.0
BCI_SIGMA = 10.0          # 1 σ = 10 index points (per business_cycle_index scale_note)
VALUATION_BAND = 30.0     # |valuation| at/above this = zero stability credit

WEIGHTS = {
    "vitality": 0.25,
    "business_cycle": 0.20,
    "labor_market": 0.20,
    "quality_of_life": 0.15,
    "income_growth": 0.10,
    "valuation_stability": 0.10,
}

GRADE_BANDS = [
    (90, "AAA"), (85, "AA+"), (80, "AA"), (75, "AA-"), (70, "A+"),
    (65, "A"), (60, "A-"), (55, "BBB+"), (50, "BBB"), (45, "BBB-"),
    (40, "BB+"),
]


def _norm_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _grade(score: float) -> str:
    for cutoff, letter in GRADE_BANDS:
        if score >= cutoff:
            return letter
    return "BB"


def compute(cbsa: str, output_so_far: dict) -> Optional[dict]:
    sec = (output_so_far or {}).get("sections", {})
    vit = sec.get("vitality") or {}
    bci = sec.get("business_cycle_index") or {}
    qol = sec.get("quality_of_life") or {}
    laus = sec.get("laus_unemployment") or {}
    pinc = sec.get("bea_personal_income") or {}
    val = sec.get("housing_valuation") or {}

    subs: Dict[str, float] = {}

    # vitality value is already a 0-1 percentile-style score
    if vit.get("value") is not None:
        subs["vitality"] = max(0.0, min(1.0, float(vit["value"])))

    # business-cycle position: expansion above the 100 rebase line
    if bci.get("latest_value") is not None:
        subs["business_cycle"] = _norm_cdf((float(bci["latest_value"]) - 100.0) / BCI_SIGMA)

    # labor market: lower unemployment = higher score
    unemp = None
    if laus.get("values"):
        for v in reversed(laus["values"]):
            if v is not None:
                unemp = float(v)
                break
    if unemp is not None:
        subs["labor_market"] = _norm_cdf(-(unemp - US_UNEMP_MEAN) / US_UNEMP_SD)

    # quality of life: composite mean z-score
    if qol.get("mean_z") is not None:
        subs["quality_of_life"] = _norm_cdf(float(qol["mean_z"]))

    # income growth: latest personal-income YoY
    if pinc.get("latest_yoy") is not None:
        subs["income_growth"] = _norm_cdf(
            (float(pinc["latest_yoy"]) - US_INCOME_GROWTH_MEAN) / US_INCOME_GROWTH_SD
        )

    # valuation stability: prices near fair value = stable; extremes = risk
    if val.get("latest_valuation_pct") is not None:
        dev = min(abs(float(val["latest_valuation_pct"])), VALUATION_BAND)
        subs["valuation_stability"] = 1.0 - dev / VALUATION_BAND

    if not subs:
        return None

    wsum = sum(WEIGHTS[k] for k in subs)
    score01 = sum(WEIGHTS[k] * s for k, s in subs.items()) / wsum
    score = round(100.0 * score01)
    grade = _grade(score)

    # --- Outlook from business-cycle momentum + income growth ---
    outlook = "Stable"
    vals = [v for v in (bci.get("values") or []) if v is not None]
    momentum = None
    if len(vals) >= 7:
        momentum = vals[-1] - vals[-7]  # ~6-month change in index points
    inc_yoy = pinc.get("latest_yoy")
    if momentum is not None:
        rising = momentum > 1.0 or (inc_yoy is not None and inc_yoy > US_INCOME_GROWTH_MEAN + 1.0 and momentum > -1.0)
        falling = momentum < -3.0
        if falling:
            outlook = "Negative"
        elif rising:
            outlook = "Positive"

    return {
        "score": score,
        "grade": grade,
        "outlook": outlook,
        "components": {
            k: {"score": round(s, 3), "weight": WEIGHTS[k]} for k, s in subs.items()
        },
        "method": (
            "EIG composite credit rating: weighted mean of normalized sub-scores for "
            "vitality (0.25), business-cycle position (0.20), labor market (0.20), "
            "quality of life (0.15), income growth (0.10) and valuation stability (0.10); "
            "scaled 0-100 and mapped to a muni-bond-style letter grade. Outlook from "
            "business-cycle momentum and income growth."
        ),
        "note": (
            "A relative creditworthiness signal for the metro economy, not a household "
            "FICO score and not a rated municipal obligation. Sub-scores are percentile "
            "positions vs. approximate US-metro reference points."
        ),
        "source": "EIG composite — synthesized from vitality, BCI, QoL, BLS, BEA and valuation",
    }
