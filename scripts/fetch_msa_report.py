"""Orchestrator for the Metro Economic Profile report data layer.

Pulls every section's raw data from public APIs and writes
/data/msa_reports/<slug>.json. Used by /msa/<slug>/index.html as the
single source of truth for charts and tables.

Usage:
    python3 scripts/fetch_msa_report.py 42340               # single MSA by CBSA
    python3 scripts/fetch_msa_report.py savannah            # single MSA by short name
    python3 scripts/fetch_msa_report.py --all               # all 14 GA MSAs
    python3 scripts/fetch_msa_report.py 42340 --sections bls,fhfa,census

Output JSON shape (top-level keys):
    {
      "cbsa": "42340",
      "short_name": "Savannah",
      "full_name": "Savannah, GA",
      "population": 410000,
      "as_of": "2026-05-25",
      "sections": {
        "ces_employment":         {...},     # BLS CES total nonfarm history
        "ces_by_supersector":     {...},     # BLS CES by sector
        "laus_unemployment":      {...},     # BLS LAUS unemployment rate history
        "qcew_industry":          null,      # not yet implemented (returns null)
        "fhfa_hpi":               null,
        "census_acs_demographics": null,
        ...
      },
      "section_status": {
        "ces_employment": "live",
        "qcew_industry":  "pending",
        ...
      }
    }

Each section is fault-tolerant: a failed pull becomes null and "pending" in
section_status — never blocks the rest of the run.

Env: BLS_API_KEY, FRED_API_KEY, CENSUS_API_KEY, BEA_API_KEY (all optional but
strongly recommended).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _ga_msas import GA_MSAS

# Per-source fetchers (each module in scripts/reporting/)
from reporting import pull_bls

# Lookup tables
MSA_BY_CBSA = {cbsa: (short, full, pop) for cbsa, short, full, pop in GA_MSAS}
MSA_BY_SLUG = {short.lower().replace(" ", "-"): (cbsa, short, full, pop) for cbsa, short, full, pop in GA_MSAS}


# ----------------------------- Section runners -----------------------------
# Each runner returns (data_or_None, status_string)
# Status strings: "live", "partial", "pending", "stale", "failed"

def run_ces_employment(cbsa: str):
    data = pull_bls.fetch_ces_employment_history(cbsa, years_back=7)
    if data is None:
        return None, "failed"
    return data, "live"


def run_ces_by_supersector(cbsa: str):
    data = pull_bls.fetch_ces_supersector_history(cbsa, years_back=2)
    if data is None:
        return None, "failed"
    return data, "live"


def run_laus_unemployment(cbsa: str):
    data = pull_bls.fetch_laus_unemployment_history(cbsa, years_back=7)
    if data is None:
        return None, "failed"
    return data, "live"


# Section registry — order is the order we run them
SECTIONS = [
    ("ces_employment",       run_ces_employment),
    ("ces_by_supersector",   run_ces_by_supersector),
    ("laus_unemployment",    run_laus_unemployment),
    # Future runners (return null until built)
    ("qcew_industry",        None),
    ("fhfa_hpi",             None),
    ("bea_gmp",              None),
    ("census_pep",           None),
    ("census_acs_demographics", None),
    ("census_acs_housing",   None),
    ("census_bps",           None),
    ("ita_exports",          None),
    ("irs_soi_migration",    None),
]


def fetch_one_msa(cbsa: str, sections_filter=None) -> dict:
    """Pull every (or selected) section for one MSA. Returns the full output dict."""
    if cbsa not in MSA_BY_CBSA:
        raise SystemExit(f"Unknown CBSA {cbsa}")
    short, full, pop = MSA_BY_CBSA[cbsa]

    output = {
        "cbsa": cbsa,
        "short_name": short,
        "full_name": full,
        "population": pop,
        "as_of": date.today().isoformat(),
        "sections": {},
        "section_status": {},
    }

    for name, runner in SECTIONS:
        if sections_filter and name not in sections_filter:
            continue
        if runner is None:
            output["sections"][name] = None
            output["section_status"][name] = "pending"
            print(f"  [{name:24s}] pending (no runner yet)")
            continue
        print(f"  [{name:24s}] pulling ...", end="", flush=True)
        try:
            data, status = runner(cbsa)
        except Exception as e:
            print(f" CRASHED — {type(e).__name__}: {e}")
            data, status = None, "failed"
        else:
            print(f" {status}")
        output["sections"][name] = data
        output["section_status"][name] = status

    return output


def write_report(output: dict, out_dir: Path) -> Path:
    slug = output["short_name"].lower().replace(" ", "-")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{slug}.json"
    path.write_text(json.dumps(output, indent=2, default=str))
    size_kb = path.stat().st_size / 1024
    print(f"  wrote {path}  ({size_kb:.1f} KB)")
    return path


def main():
    ap = argparse.ArgumentParser(description="Pull Metro Economic Profile data for a Georgia MSA.")
    ap.add_argument("target", nargs="?", help="CBSA code, short name, or slug. Omit with --all.")
    ap.add_argument("--all", action="store_true", help="Pull all 14 GA MSAs")
    ap.add_argument("--sections", type=str, default=None,
                    help="Comma-separated list of section names to run (default: all)")
    ap.add_argument("--out", type=Path, default=Path(__file__).parent.parent / "data" / "msa_reports",
                    help="Output directory (default: data/msa_reports/)")
    args = ap.parse_args()

    sections_filter = set(args.sections.split(",")) if args.sections else None

    if args.all:
        targets = [cbsa for cbsa, _, _, _ in GA_MSAS]
    elif args.target:
        t = args.target.strip().lower()
        if t in MSA_BY_CBSA:
            targets = [t]
        elif t in MSA_BY_SLUG:
            targets = [MSA_BY_SLUG[t][0]]
        else:
            # try matching short_name case-insensitively
            match = [cbsa for cbsa, short, _, _ in GA_MSAS if short.lower() == t]
            if not match:
                raise SystemExit(f"Unknown target: {args.target}")
            targets = match
    else:
        ap.print_help()
        sys.exit(1)

    for cbsa in targets:
        short = MSA_BY_CBSA[cbsa][0]
        print(f"\n=== {short} (CBSA {cbsa}) ===")
        output = fetch_one_msa(cbsa, sections_filter)
        write_report(output, args.out)

        # Summary
        statuses = output["section_status"]
        live = sum(1 for v in statuses.values() if v == "live")
        pending = sum(1 for v in statuses.values() if v == "pending")
        failed = sum(1 for v in statuses.values() if v == "failed")
        print(f"  summary: {live} live, {pending} pending, {failed} failed (of {len(statuses)})")


if __name__ == "__main__":
    main()
