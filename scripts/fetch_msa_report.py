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
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))
from _ga_msas import GA_MSAS

# Per-source fetchers (each module in scripts/reporting/)
from reporting import pull_bls, pull_fhfa, pull_census, pull_bea, pull_irs_soi, pull_ita, pull_bps

# Phase 2 composite/forecast models (each module in scripts/modeling/)
from modeling import business_cycle_index, forecast_arima

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


def run_fhfa_hpi(cbsa: str):
    data = pull_fhfa.fetch_hpi_quarterly_history(cbsa, years_back=10)
    if data is None:
        return None, "failed"
    return data, "live"


def run_census_pep(cbsa: str):
    data = pull_census.fetch_pep_population_history(cbsa, years_back=7)
    if data is None:
        return None, "failed"
    return data, "live"


def run_census_acs_demographics(cbsa: str):
    data = pull_census.fetch_acs_demographics(cbsa)
    if data is None:
        return None, "failed"
    return data, "live"


def run_bea_gmp(cbsa: str):
    data = pull_bea.fetch_gmp_history(cbsa, years_back=7)
    if data is None:
        return None, "failed"
    return data, "live"


def run_bea_personal_income(cbsa: str):
    data = pull_bea.fetch_personal_income_history(cbsa, years_back=7)
    if data is None:
        return None, "failed"
    return data, "live"


def run_qcew_industry_shares(cbsa: str):
    data = pull_bls.fetch_qcew_industry_shares(cbsa)
    if data is None:
        return None, "failed"
    return data, "live"


def run_qcew_yoy_changes(cbsa: str):
    data = pull_bls.fetch_qcew_yoy_changes(cbsa)
    if data is None:
        return None, "failed"
    return data, "live"


def run_census_bps_permits(cbsa: str):
    # Building permits now come from FRED (Census BPS mirror) rather than the
    # slow www2.census.gov flat files. FRED gives the proper SF/MF unit split
    # via {GEO}BP1FH (1-unit) and {GEO}BPPRIV (total). On failure the
    # orchestrator's never-blank-on-failure logic preserves prior cached values.
    data = pull_bps.fetch_bps_permits_annual(cbsa, years_back=6)
    if data is None:
        return None, "failed"
    return data, "live"


def run_ita_msa_exports(cbsa: str):
    data = pull_ita.fetch_msa_exports(cbsa)
    if data is None:
        return None, "failed"
    return data, "live"


def run_irs_soi_migration(cbsa: str):
    data = pull_irs_soi.fetch_migration_flows(cbsa)
    if data is None:
        return None, "failed"
    return data, "live"


# Section registry — order is the order we run them. Data fetches first; modeling
# sections (Phase 2 composites/forecasts) run after, with access to the in-progress
# output dict so they can read freshly-fetched data as their inputs.
SECTIONS = [
    ("ces_employment",          run_ces_employment),
    ("ces_by_supersector",      run_ces_by_supersector),
    ("laus_unemployment",       run_laus_unemployment),
    ("fhfa_hpi",                run_fhfa_hpi),
    ("census_pep",              run_census_pep),
    ("census_acs_demographics", run_census_acs_demographics),
    ("bea_gmp",                 run_bea_gmp),
    ("bea_personal_income",     run_bea_personal_income),
    ("qcew_industry_shares",    run_qcew_industry_shares),
    ("qcew_yoy_changes",        run_qcew_yoy_changes),
    ("census_bps_permits",      run_census_bps_permits),
    ("ita_msa_exports",         run_ita_msa_exports),
    ("irs_soi_migration",       run_irs_soi_migration),
]


# ----------------------------- Modeling runners (Phase 2) -----------------------------
# These run AFTER the data fetchers and receive the in-progress output dict.
# Each returns (data_or_None, status_string).

def run_business_cycle_index(cbsa: str, output_so_far: dict):
    data = business_cycle_index.compute(cbsa, output_so_far)
    if data is None:
        return None, "failed"
    return data, "live"


def run_forecast_arima(cbsa: str, output_so_far: dict):
    data = forecast_arima.compute(cbsa, output_so_far)
    if data is None:
        return None, "failed"
    return data, "live"


# Modeling section registry — runner signature is (cbsa, output_so_far)
MODELING_SECTIONS = [
    ("business_cycle_index", run_business_cycle_index),
    ("forecast_arima", run_forecast_arima),
]


def fetch_one_msa(cbsa: str, sections_filter=None, prior: Optional[dict] = None) -> dict:
    """Pull every (or selected) section for one MSA. Returns the full output dict.

    NEVER BLANKS DATA: if a section fails and `prior` contains a previously-good
    value, we keep the prior value and mark status as "stale" (per the
    feedback_full_automation memory rule: never propose manual updates, never
    blank live data on transient errors).
    """
    if cbsa not in MSA_BY_CBSA:
        raise SystemExit(f"Unknown CBSA {cbsa}")
    short, full, pop = MSA_BY_CBSA[cbsa]

    prior_sections = (prior or {}).get("sections", {}) if prior else {}
    prior_status   = (prior or {}).get("section_status", {}) if prior else {}

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
            # Carry prior values for sections not in this filter
            output["sections"][name] = prior_sections.get(name)
            output["section_status"][name] = prior_status.get(name, "pending")
            continue
        if runner is None:
            # No runner built yet: keep prior if present, else pending
            output["sections"][name] = prior_sections.get(name)
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
            # One-line freshness summary alongside the status
            stamp = _freshness_stamp(data) if data else ""
            print(f" {status}{' (' + stamp + ')' if stamp else ''}")

        # NEVER BLANK on failure — fall back to prior value if available.
        if status == "failed" and prior_sections.get(name) is not None:
            output["sections"][name] = prior_sections[name]
            output["section_status"][name] = "stale"
            print(f"     ↳ kept prior value, status=stale")
        else:
            output["sections"][name] = data
            output["section_status"][name] = status

    # Phase 2 modeling pass — runs after the data fetches so models can read
    # freshly-fetched inputs (plus any stale-fallback values) from output["sections"].
    for name, runner in MODELING_SECTIONS:
        if sections_filter and name not in sections_filter:
            output["sections"][name] = prior_sections.get(name)
            output["section_status"][name] = prior_status.get(name, "pending")
            continue
        print(f"  [{name:24s}] computing ...", end="", flush=True)
        try:
            data, status = runner(cbsa, output)
        except Exception as e:
            print(f" CRASHED — {type(e).__name__}: {e}")
            data, status = None, "failed"
        else:
            stamp = _freshness_stamp(data) if data else ""
            print(f" {status}{' (' + stamp + ')' if stamp else ''}")

        if status == "failed" and prior_sections.get(name) is not None:
            output["sections"][name] = prior_sections[name]
            output["section_status"][name] = "stale"
            print(f"     ↳ kept prior value, status=stale")
        else:
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


def _freshness_stamp(data) -> str:
    """Best-effort one-line freshness label for a section's data dict.
    Looks for the common 'latest_*' fields each fetcher populates."""
    if not isinstance(data, dict):
        return ""
    for k in ("latest_month", "latest_quarter", "latest_year", "year",
              "as_of_label", "year_pair_label"):
        v = data.get(k)
        if v:
            return str(v)
    return ""


def read_prior_report(out_dir: Path, short_name: str) -> Optional[dict]:
    """Load the previous run's JSON if it exists, for stale-fallback."""
    slug = short_name.lower().replace(" ", "-")
    path = out_dir / f"{slug}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception as e:
        print(f"  [prior load] {path} unreadable: {e}", file=sys.stderr)
        return None


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
        prior = read_prior_report(args.out, short)
        output = fetch_one_msa(cbsa, sections_filter, prior=prior)
        write_report(output, args.out)

        # Summary
        statuses = output["section_status"]
        live    = sum(1 for v in statuses.values() if v == "live")
        seed    = sum(1 for v in statuses.values() if v == "seed")
        stale   = sum(1 for v in statuses.values() if v == "stale")
        pending = sum(1 for v in statuses.values() if v == "pending")
        failed  = sum(1 for v in statuses.values() if v == "failed")
        print(f"  summary: {live} live, {seed} seed, {stale} stale (kept prior), {pending} pending, {failed} failed (of {len(statuses)})")


if __name__ == "__main__":
    main()
