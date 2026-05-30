"""Industrial Diversity — Hachman index of the metro's employment mix vs. the U.S.

The Hachman index measures how closely a region's industrial distribution matches a
reference economy (here the U.S.). It runs (0, 1]; 1.0 means the local mix is identical
to the nation (the most balanced / "diversified" relative to the reference), while lower
values mean the economy is concentrated in industries that are over- or under-represented
versus the U.S.

    HI = 1 / Σ_i ( s_i² / r_i )

where s_i = local employment share of super-sector i (fraction) and r_i = U.S. share
(fraction). Equivalent to 1 / Σ_i ( s_i · LQ_i ) with LQ_i = s_i / r_i.

Inputs (read from output["sections"]):
    qcew_industry_shares.{msa, us, ga}  — each {sector: {"share_pct": x, ...}}

Output (section "industrial_diversity"):
    { "score": 0.83, "ga_score": 0.88, "n_sectors": 13, "method": "...", "source": "..." }

Returns None if QCEW shares aren't available. Pure stdlib.
"""

from __future__ import annotations

from typing import Optional, Dict


def _shares(block: dict) -> Dict[str, float]:
    """Extract {sector: share_pct} for sectors with a positive share."""
    out: Dict[str, float] = {}
    for sector, d in (block or {}).items():
        sp = (d or {}).get("share_pct")
        if sp and sp > 0:
            out[sector] = sp
    return out


def _hachman(local: Dict[str, float], ref: Dict[str, float]) -> Optional[float]:
    """Hachman index of `local` vs reference `ref` (both {sector: share_pct}).

    Both sides are renormalized to sum to 1 over their COMMON sectors so the inputs are
    proper distributions — this guarantees the index lands in (0, 1] regardless of whether
    the raw shares cover 100% of employment.
    """
    common = [s for s in local if s in ref and ref[s] > 0 and local[s] > 0]
    if not common:
        return None
    ls = sum(local[s] for s in common)
    rs = sum(ref[s] for s in common)
    if ls <= 0 or rs <= 0:
        return None
    denom = 0.0
    for s in common:
        sf = local[s] / ls          # normalized local share
        rf = ref[s] / rs            # normalized reference share
        denom += (sf * sf) / rf
    if denom <= 0:
        return None
    return round(1.0 / denom, 3)


def compute(cbsa: str, output_so_far: dict) -> Optional[dict]:
    sec = (output_so_far or {}).get("sections", {})
    q = sec.get("qcew_industry_shares") or {}
    msa = _shares(q.get("msa"))
    us = _shares(q.get("us"))
    ga = _shares(q.get("ga"))
    if not msa or not us:
        return None

    score = _hachman(msa, us)
    if score is None:
        return None
    ga_score = _hachman(ga, us) if ga else None

    return {
        "score": score,
        "ga_score": ga_score,
        "n_sectors": len(msa),
        "as_of_label": q.get("as_of_label"),
        "method": ("Hachman index = 1 / Σ(local_share² / US_share) over QCEW super-sectors; "
                   "1.0 = industrial mix identical to the U.S. (most balanced/diversified), "
                   "lower = more concentrated."),
        "source": "EIG composite — computed from QCEW employment shares (MSA & GA vs. US)",
    }
