"""Build the statewide Georgia Migration page dataset -> data/migration.json

Phase 4, WS2 #1 (first "unbury the MSA depth" page). Centers on FLOWS:
where people move to/from Georgia (state-to-state), a 159-county net-migration
map, the domestic/international/natural split over time, which metros attract
movers, and a non-metro aggregate.

Lowest data cost in Phase 4 — almost everything already exists:
  • County layer (all 159) ... data/population.json counties[] carry dom_mig_total,
                               intl_mig_total, pop_latest. No new pull.
  • Components trend ......... population.json.state dom_mig/intl_mig/natural/net_mig
                               (Census PEP). No new pull.
  • Metro roll-up ............ irs_soi_migration + census_net_migration from the 14
                               msa_reports/*.json (live). No new pull.
  • State-to-state flows ..... NEW aggregation via pull_irs_soi.fetch_state_flows(),
                               which reuses the existing CSV download + parser.
                               This is the only network-dependent section.
  • Non-Metro Georgia ........ sum dom_mig_total over the 86 non-metro counties.

Graceful degradation (house convention): each section wrapped in try/except;
on failure we preserve the prior value and don't bump _meta.<section>.last_updated.

Env: no API key needed for the local sections; state_flows fetches public IRS CSVs.

Usage:
  python scripts/fetch_migration.py            # full run (state flows need network)
  python scripts/fetch_migration.py --rollup   # everything except live SOI state flows
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
OUT = DATA / "migration.json"

STALE_MONTHS = 14  # IRS SOI + PEP are annual

sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "reporting"))
try:
    from pull_irs_soi import fetch_state_flows  # type: ignore
except Exception:
    fetch_state_flows = None


def _now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_prior() -> dict:
    if OUT.exists():
        try:
            return json.loads(OUT.read_text())
        except Exception:
            return {}
    return {}


def _safe(d, *keys, default=None):
    for k in keys:
        if not isinstance(d, dict) or k not in d:
            return default
        d = d[k]
    return d


def _metro_ga_fips() -> set:
    m = json.loads((DATA / "ga_msa_counties.json").read_text())["msas"]
    out = set()
    for info in m.values():
        for c in info["counties"]:
            if c.startswith("13"):
                out.add(c)
    return out


# --------------------------------------------------------------------------- #
# local sections (population.json + metro reports) — no network
# --------------------------------------------------------------------------- #
def components_trend() -> Optional[dict]:
    try:
        st = json.loads((DATA / "population.json").read_text())["state"]
    except Exception:
        return None
    yrs = st.get("years")
    if not yrs:
        return None
    return {
        "years": yrs,
        "domestic": st.get("dom_mig"),
        "international": st.get("intl_mig"),
        "natural": st.get("natural"),
        "net": st.get("net_mig"),
        "source": "Census PEP components of change (state)",
    }


def county_layer() -> Optional[dict]:
    try:
        rows = json.loads((DATA / "population.json").read_text())["counties"]
    except Exception:
        return None
    counties = {}
    for r in rows:
        fips = r.get("fips")
        nd = r.get("dom_mig_total")
        pop = r.get("pop_latest")
        if fips is None or nd is None:
            continue
        per_1k = round(1000 * nd / pop, 1) if pop else None
        counties[fips] = {
            "net_domestic": nd,
            "net_international": r.get("intl_mig_total"),
            "per_1k": per_1k,
            "name": r.get("county"),
        }
    if not counties:
        return None
    return {"counties": counties,
            "source": "Census PEP components of change (county), data/population.json"}


def non_metro_aggregate(county: dict) -> Optional[dict]:
    if not county:
        return None
    try:
        rows = json.loads((DATA / "population.json").read_text())["counties"]
    except Exception:
        return None
    metro = _metro_ga_fips()
    nm = [r for r in rows if r.get("fips") not in metro]
    if not nm:
        return None
    nd = sum(r.get("dom_mig_total") or 0 for r in nm)
    ni = sum(r.get("intl_mig_total") or 0 for r in nm)
    pop = sum(r.get("pop_latest") or 0 for r in nm)
    return {
        "county_count": len(nm), "net_domestic": nd, "net_international": ni,
        "population": pop,
        "per_1k": round(1000 * nd / pop, 1) if pop else None,
        "note": "Aggregate of the 86 GA counties outside the 14 MSAs.",
        "source": "Census PEP components of change (county)",
    }


def rollup_metros() -> List[dict]:
    metros: List[dict] = []
    for path in sorted(MSA_REPORTS.glob("*.json")):
        rep = json.loads(path.read_text())
        sec, st = rep.get("sections", {}), rep.get("section_status", {})
        block: Dict[str, Any] = {
            "slug": path.stem, "short_name": rep.get("short_name"),
            "population": rep.get("population"),
        }
        soi = sec.get("irs_soi_migration") if st.get("irs_soi_migration") in ("live", "partial", "stale") else None
        pep = sec.get("census_net_migration") if st.get("census_net_migration") in ("live", "partial", "stale") else None
        if soi:
            block["total_in"] = soi.get("total_in")
            block["total_out"] = soi.get("total_out")
            block["soi_net"] = soi.get("net")
        if pep:
            nm = pep.get("net_migration") or []
            dm = pep.get("domestic_migration") or []
            im = pep.get("international_migration") or []
            block["pep_net"] = nm[-1] if nm else None
            block["pep_domestic"] = dm[-1] if dm else None
            block["pep_international"] = im[-1] if im else None
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


def build(rollup_only: bool = False) -> dict:
    prior = _load_prior()
    prior_meta = prior.get("_meta", {})
    meta: Dict[str, dict] = {}
    out: Dict[str, Any] = {"fetched_at": _now_iso(), "schema": "migration/v1"}

    # always-on local sections
    out["metros"] = rollup_metros()
    meta["metro_rollup"] = {"last_updated": _now_iso(), "n_metros": len(out["metros"])}

    def local(name, fn, *args):
        try:
            val = fn(*args)
        except Exception as e:
            print(f"  [migration] {name} raised {type(e).__name__}: {str(e)[:80]}", file=sys.stderr)
            val = None
        if val:
            out[name] = val
            meta[name] = {"last_updated": _now_iso()}
        elif name in prior:
            out[name] = prior[name]
            meta[name] = prior_meta.get(name, {"last_updated": None})
        return out.get(name)

    local("components", components_trend)
    cty = local("counties", county_layer)
    local("non_metro", non_metro_aggregate, cty)

    # network section: state-to-state flows
    if rollup_only or fetch_state_flows is None:
        if "state_flows" in prior:
            out["state_flows"] = prior["state_flows"]
            meta["state_flows"] = prior_meta.get("state_flows", {})
    else:
        try:
            sf = fetch_state_flows()
        except Exception as e:
            print(f"  [migration] state_flows raised {type(e).__name__}: {str(e)[:80]}", file=sys.stderr)
            sf = None
        if sf:
            out["state_flows"] = sf
            meta["state_flows"] = {"last_updated": _now_iso()}
        elif "state_flows" in prior:
            out["state_flows"] = prior["state_flows"]
            meta["state_flows"] = prior_meta.get("state_flows", {"last_updated": None})

    # KPIs
    comp = out.get("components") or {}
    dom = (comp.get("domestic") or [])
    intl = (comp.get("international") or [])
    sf = out.get("state_flows") or {}
    out["kpis"] = {
        "net_domestic": dom[-1] if dom else None,
        "net_international": intl[-1] if intl else None,
        "top_origin_state": (sf.get("top_in") or [{}])[0].get("state") if sf.get("top_in") else None,
        "top_dest_state": (sf.get("top_out") or [{}])[0].get("state") if sf.get("top_out") else None,
        "net_soi": sf.get("net"),
        "non_metro_county_count": _safe(out, "non_metro", "county_count"),
    }

    for v in meta.values():
        v["stale"] = _is_stale(v)
    out["_meta"] = meta
    out["latest_label"] = (sf.get("year_pair_label")
                           or (str(comp["years"][-1]) if comp.get("years") else str(date.today().year - 1)))
    out["coverage_note"] = ("Components and the 159-county map cover 100% of Georgia "
                            "(Census PEP). State-to-state flows are IRS SOI (tax-return "
                            "based, ~1.5-year lag). The Non-Metro aggregate covers the 86 "
                            "counties outside the 14 MSAs.")
    out["source_summary"] = {
        "state_flows": "IRS SOI county-to-county migration, aggregated to GA statewide",
        "components": "Census PEP components of change (state)",
        "counties": "Census PEP components of change (county)",
        "metros": "Roll-up of msa_reports/*.json (irs_soi_migration + census_net_migration)",
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
