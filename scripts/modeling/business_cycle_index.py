"""Stock-Watson-style coincident business-cycle index for an MSA.

Produces a single smooth monthly time series that summarizes the level of
economic activity in the MSA, rebased to 100 at a chosen baseline month.

Inputs (read from the orchestrator's in-progress output dict):
    sections.ces_employment.{months, values}     — monthly nonfarm payrolls (K jobs)
    sections.laus_unemployment.{months, values}  — monthly unemployment rate (%, NSA)

Method:
    1. Align both series on their common months.
    2. Z-score each series (positive co-movement is required, so we negate
       the unemployment series — high unemployment = bad).
    3. Extract the first principal component (closed-form 2×2 PCA on the
       standardized correlation matrix). Loadings are equal-weight when
       correlation is negative (which would indicate the two series disagree
       — unlikely in practice but a graceful fallback).
    4. Rescale: shift+scale so the index equals 100 at `rebase_month`
       (default = first month of available history).
    5. Smooth lightly with a 3-month centered moving average to suppress
       monthly noise without lagging the cycle.

Output (suitable for direct insertion into a Chart.js dataset):
    {
        "method":      "Stock-Watson coincident (2-input PCA)",
        "components":  ["ces_employment", "laus_unemployment"],
        "loadings":    {"ces_employment": 0.71, "laus_unemployment_neg": 0.71},
        "correlation": 0.62,
        "rebase_month": "2019-01",
        "months":      ["2019-01", "2019-02", ...],
        "values":      [100.0, 100.2, ...],
        "latest_month": "2026-04",
        "latest_value": 108.3,
        "trough_month": "2020-05",
        "peak_month":   "2024-08",
    }

Returns None if either input section is missing or empty.

Pure-Python: no numpy / sklearn dependency. Fine for n < 200 months with k=2 inputs.
"""

from __future__ import annotations

import math
from typing import Optional, List, Dict, Tuple


def _zscore(xs: List[float]) -> Tuple[List[float], float, float]:
    """Return (z-scored series, mean, std). std is sample std (n-1)."""
    n = len(xs)
    if n < 2:
        return list(xs), 0.0, 1.0
    mu = sum(xs) / n
    var = sum((x - mu) ** 2 for x in xs) / (n - 1)
    sigma = math.sqrt(var) if var > 0 else 1.0
    return [(x - mu) / sigma for x in xs], mu, sigma


def _align_monthly(a_months: List[str], a_values: List[float],
                   b_months: List[str], b_values: List[float]
                   ) -> Tuple[List[str], List[float], List[float]]:
    """Inner-join two monthly series on their YYYY-MM keys, preserving order."""
    b_map: Dict[str, float] = {m: v for m, v in zip(b_months, b_values)}
    months: List[str] = []
    avals: List[float] = []
    bvals: List[float] = []
    for m, av in zip(a_months, a_values):
        if m in b_map and av is not None and b_map[m] is not None:
            months.append(m)
            avals.append(float(av))
            bvals.append(float(b_map[m]))
    return months, avals, bvals


def _pearson_corr(a: List[float], b: List[float]) -> float:
    """Pearson correlation. Inputs are already z-scored (mean 0, std 1)."""
    n = len(a)
    if n < 2:
        return 0.0
    return sum(a[i] * b[i] for i in range(n)) / (n - 1)


def _smooth_centered_ma(xs: List[float], window: int = 3) -> List[float]:
    """Centered moving average. Edges fall back to one-sided averages."""
    n = len(xs)
    half = window // 2
    out: List[float] = []
    for i in range(n):
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        out.append(sum(xs[lo:hi]) / (hi - lo))
    return out


def compute(cbsa: str, output_so_far: dict, rebase_month: Optional[str] = None) -> Optional[dict]:
    """Compute the coincident business-cycle index for one MSA.

    `output_so_far` is the orchestrator's in-progress output dict. It carries
    both freshly-fetched values (this run) and any stale-fallback values from
    a prior good run — both are usable inputs for the model.
    """
    sections = output_so_far.get("sections") or {}
    ces = sections.get("ces_employment") or {}
    laus = sections.get("laus_unemployment") or {}

    ces_months = ces.get("months") or []
    ces_values = ces.get("values") or []
    u_months   = laus.get("months") or []
    u_values   = laus.get("values") or []

    if not (ces_months and ces_values and u_months and u_values):
        return None
    if len(ces_months) != len(ces_values) or len(u_months) != len(u_values):
        return None

    # Align on common months
    months, emp_aligned, u_aligned = _align_monthly(ces_months, ces_values, u_months, u_values)
    if len(months) < 12:
        return None  # need at least a year of overlap for a meaningful index

    # Z-score each input; negate unemployment so positive z = "good" for both
    emp_z, _, _ = _zscore(emp_aligned)
    u_neg = [-x for x in u_aligned]
    u_neg_z, _, _ = _zscore(u_neg)

    # 2-input PCA: closed-form. Correlation matrix is [[1, rho], [rho, 1]],
    # whose eigenvalues are 1±rho with eigenvectors [1, 1]/√2 and [1, -1]/√2.
    rho = _pearson_corr(emp_z, u_neg_z)

    if rho >= 0:
        # First PC (largest eigenvalue 1+rho) → [1/√2, 1/√2]
        w1 = w2 = 1.0 / math.sqrt(2.0)
        method_note = "first principal component (positive co-movement, equal-weight loadings)"
    else:
        # Inputs disagree (employment up while unemployment also up). Rare;
        # take an equal-weight average rather than the second PC, which would
        # be a divergence indicator instead of a level indicator.
        w1 = w2 = 0.5
        method_note = "equal-weight average (inputs disagreed: rho < 0; PCA not applicable as level indicator)"

    factor = [w1 * emp_z[i] + w2 * u_neg_z[i] for i in range(len(months))]

    # Light smoothing: 3-month centered MA suppresses monthly noise without lag
    smoothed = _smooth_centered_ma(factor, window=3)

    # Rebase to 100 at the chosen baseline month.
    # Default rebase: first month of available history.
    if rebase_month and rebase_month in months:
        base_i = months.index(rebase_month)
    else:
        base_i = 0
        rebase_month = months[0]
    base_val = smoothed[base_i]

    # Convert factor-space (~ -3 to +3) to index-space (100 ± ~30) by adding 100
    # and scaling around the base point. Use a fixed scaling of 1 standard
    # deviation = 10 index points (standard practice for diffusion-style indices).
    SD_TO_INDEX_PTS = 10.0
    values = [round(100.0 + SD_TO_INDEX_PTS * (s - base_val), 2) for s in smoothed]

    # Peak and trough months for the layout caption
    peak_i = max(range(len(values)), key=lambda i: values[i])
    trough_i = min(range(len(values)), key=lambda i: values[i])

    return {
        "method":           f"Stock-Watson coincident (2-input PCA, {method_note})",
        "components":       ["ces_employment", "laus_unemployment"],
        "loadings":         {"ces_employment": round(w1, 3),
                             "laus_unemployment_neg": round(w2, 3)},
        "correlation":      round(rho, 3),
        "rebase_month":     rebase_month,
        "rebase_value":     100.0,
        "smoothing":        "3-month centered MA",
        "scale_note":       f"1 σ = {SD_TO_INDEX_PTS:.0f} index points",
        "months":           months,
        "values":           values,
        "latest_month":     months[-1],
        "latest_value":     values[-1],
        "peak_month":       months[peak_i],
        "peak_value":       values[peak_i],
        "trough_month":     months[trough_i],
        "trough_value":     values[trough_i],
        "n_months":         len(months),
        "source":           "EIG composite — computed from BLS CES + LAUS",
    }


# ----------------------------- CLI smoke test -----------------------------

if __name__ == "__main__":
    import json
    import sys
    from pathlib import Path
    cbsa = sys.argv[1] if len(sys.argv) > 1 else "42340"
    # Read a prior JSON to provide inputs
    repo_root = Path(__file__).parent.parent.parent
    slug = "savannah" if cbsa == "42340" else cbsa
    prior_path = repo_root / "data" / "msa_reports" / f"{slug}.json"
    if not prior_path.exists():
        print(f"  [BCI] no prior data at {prior_path}", file=sys.stderr)
        sys.exit(1)
    prior = json.loads(prior_path.read_text())
    out = compute(cbsa, prior)
    if out:
        print(f"  Coincident BCI for CBSA {cbsa}:")
        print(f"    Latest: {out['latest_month']} = {out['latest_value']:.1f}")
        print(f"    Peak:   {out['peak_month']}   = {out['peak_value']:.1f}")
        print(f"    Trough: {out['trough_month']} = {out['trough_value']:.1f}")
        print(f"    Correlation between inputs: {out['correlation']}")
        print(f"    {out['n_months']} months")
    else:
        print("  [BCI] compute returned None", file=sys.stderr)
