"""EIG Relative Costs model — local cost of living and cost of doing business, US = 100.

Powers the "Relative Costs" indicator cell (Living / Business, US = 100). Both indices
are EIG composites built on the same logic the BEA uses for its Regional Price
Parities (RPP): a metro's price level is an expenditure-weighted blend of rents
(varies most across metros), tradable goods (near-uniform nationally) and local
services (track local wages).

Cost of LIVING index (BEA-RPP-style basket, US = 100):
    rents      weight 0.36   -> local/US median gross rent
    goods      weight 0.15   -> ~1.0 (tradables priced nationally)
    services   weight 0.49   -> wage-elastic: 1 + 0.6*(wage_ratio - 1)

Cost of doing BUSINESS index (operating-cost basket, US = 100):
    labor      weight 0.62   -> local/US wage proxy (largest operating cost)
    occupancy  weight 0.20   -> local/US rent (commercial occupancy direction)
    tax/other  weight 0.18   -> ~1.0 (GA effective business-tax burden ≈ national)

Wage proxy: BEA/ACS per-capita income ratio (QCEW average annual wage will replace it
once that section reliably populates — it is empty in current pulls).

US reference benchmarks (ACS 5-year national, documented + refinable, like vitality.py):
    US median gross rent        $1,400
    US per-capita income        $43,000

Inputs (read from output["sections"]):
    census_acs_demographics.values.{median_gross_rent, per_capita_income}

Output (section "business_costs"):
    {
      "cost_of_living_index": 100,        # US = 100
      "business_cost_index": 97,          # US = 100
      "rent_ratio": 1.04, "wage_ratio": 0.95,
      "components": {...}, "method": "...", "note": "...", "source": "...",
    }

Returns None if neither rent nor wage input is available. Pure stdlib.
"""

from __future__ import annotations

from typing import Optional, Dict

# US reference benchmarks — ACS 5-year national approximations (refinable).
US_MEDIAN_GROSS_RENT = 1400.0
US_PER_CAPITA_INCOME = 43000.0

# Cost-of-living basket weights (BEA RPP expenditure shares).
COL_RENT_W = 0.36
COL_GOODS_W = 0.15
COL_SERVICES_W = 0.49
SERVICES_WAGE_ELASTICITY = 0.6

# Business-cost basket weights.
BIZ_LABOR_W = 0.62
BIZ_OCCUPANCY_W = 0.20
BIZ_TAX_W = 0.18


def compute(cbsa: str, output_so_far: dict) -> Optional[dict]:
    sec = (output_so_far or {}).get("sections", {})
    acs_vals = (sec.get("census_acs_demographics") or {}).get("values") or {}

    rent = acs_vals.get("median_gross_rent")
    pci = acs_vals.get("per_capita_income")
    # BEA per-capita income is a cleaner wage proxy if ACS money income is missing.
    if not pci:
        pci = (sec.get("bea_personal_income") or {}).get("latest_per_capita_income")

    if not rent and not pci:
        return None

    rent_ratio = round(rent / US_MEDIAN_GROSS_RENT, 4) if rent else None
    wage_ratio = round(pci / US_PER_CAPITA_INCOME, 4) if pci else None

    components: Dict[str, dict] = {}

    # --- Cost of living ---
    col_index = None
    if rent_ratio is not None or wage_ratio is not None:
        rr = rent_ratio if rent_ratio is not None else 1.0
        wr = wage_ratio if wage_ratio is not None else 1.0
        services_ratio = 1.0 + SERVICES_WAGE_ELASTICITY * (wr - 1.0)
        col = (COL_RENT_W * rr
               + COL_GOODS_W * 1.0
               + COL_SERVICES_W * services_ratio)
        col_index = round(100.0 * col)
        components["cost_of_living"] = {
            "rent_ratio": rr,
            "goods_ratio": 1.0,
            "services_ratio": round(services_ratio, 4),
            "weights": {"rent": COL_RENT_W, "goods": COL_GOODS_W, "services": COL_SERVICES_W},
        }

    # --- Cost of doing business ---
    biz_index = None
    if wage_ratio is not None or rent_ratio is not None:
        labor = wage_ratio if wage_ratio is not None else 1.0
        occ = rent_ratio if rent_ratio is not None else 1.0
        biz = (BIZ_LABOR_W * labor
               + BIZ_OCCUPANCY_W * occ
               + BIZ_TAX_W * 1.0)
        biz_index = round(100.0 * biz)
        components["business_cost"] = {
            "labor_ratio": labor,
            "occupancy_ratio": occ,
            "tax_other_ratio": 1.0,
            "weights": {"labor": BIZ_LABOR_W, "occupancy": BIZ_OCCUPANCY_W, "tax_other": BIZ_TAX_W},
        }

    if col_index is None and biz_index is None:
        return None

    return {
        "cost_of_living_index": col_index,
        "business_cost_index": biz_index,
        "rent_ratio": rent_ratio,
        "wage_ratio": wage_ratio,
        "components": components,
        "method": (
            "EIG composite (BEA Regional-Price-Parity logic): cost of living = "
            "expenditure-weighted blend of rents (0.36), tradable goods (0.15, ~national) "
            "and wage-elastic services (0.49); cost of doing business = labor (0.62, wage "
            "proxy), occupancy (0.20, rent) and tax/other (0.18, ~national). US = 100."
        ),
        "note": (
            "Wage proxy is per-capita income (QCEW average annual wage will replace it once "
            "that section populates). US benchmarks are ACS 5-year national approximations: "
            f"median gross rent ${US_MEDIAN_GROSS_RENT:,.0f}, per-capita income "
            f"${US_PER_CAPITA_INCOME:,.0f}."
        ),
        "source": "EIG composite — computed from ACS rent + income vs. national benchmarks",
    }
