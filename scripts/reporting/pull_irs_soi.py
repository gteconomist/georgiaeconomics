"""IRS SOI county-to-county migration pulls for Metro Economic Profile reports.

IRS publishes annual county-to-county migration data based on filed tax returns
(approximately a year-and-a-half lag). Inflow file lists every flow INTO a county
(prior county -> current county). Outflow file lists every flow OUT of a county.

Source: https://www.irs.gov/statistics/soi-tax-stats-migration-data

Modern (post-2011) hosting layout: direct CSVs, NOT ZIPs. Pattern verified
2026-05 against irs.gov:
    https://www.irs.gov/pub/irs-soi/countyinflow{YY}{YZ}.csv
    https://www.irs.gov/pub/irs-soi/countyoutflow{YY}{YZ}.csv
where YY/YZ are 2-digit "from year" / "to year" (e.g. 2122 = 2021->2022).

The pre-2011 ZIP layout (e.g. 22_to_23_county_data.zip) was a guess that turned
out to be wrong. The legacy `countyinflow1011.dat` files exist for tax years
2004-2011 only; modern files use the CSV pattern above.

We aggregate the county-level flows up to MSAs using _ga_msas.COUNTY_TO_MSA.

Exposes:
  fetch_migration_flows(cbsa, year=None) -> dict

Returns:
    {
      "year": "2021→2022",
      "total_in":  18500,
      "total_out": 12800,
      "net":       5700,
      "top_in":  [{"origin_msa": "Hinesville GA", "n_returns": 2840}, ...],
      "top_out": [{"dest_msa":   "Hinesville GA", "n_returns": 2340}, ...]
    }

No API key needed — public download.
"""

from __future__ import annotations

import csv
import io
import os
import sys
import urllib.request
import urllib.error
from datetime import date
from pathlib import Path
from typing import Optional, Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ga_msas import GA_MSAS, COUNTY_TO_MSA  # noqa: E402

# Build inverse + label maps
CBSA_TO_SHORT: Dict[str, str] = {cbsa: short for cbsa, short, _, _ in GA_MSAS}

# Cache the CSV bodies between calls within a single orchestrator run
_CSV_CACHE: Dict[str, bytes] = {}


def _fetch_csv(url: str, timeout: int = 60) -> Optional[bytes]:
    """Fetch one CSV. Caches by URL. Returns None silently on 404 / network error."""
    if url in _CSV_CACHE:
        return _CSV_CACHE[url]
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; EIG-MSA-reports/1.0)",
            "Accept": "text/csv,*/*",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
        if not body or len(body) < 1000:
            # IRS sometimes serves an HTML "not found" page with 200; sanity-check size.
            return None
        _CSV_CACHE[url] = body
        return body
    except urllib.error.HTTPError as e:
        if e.code != 404:
            print(f"  [IRS SOI] HTTP {e.code} for {url}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  [IRS SOI] {type(e).__name__}: {str(e)[:100]}", file=sys.stderr)
        return None


def _build_urls(yy: int, yz: int) -> Tuple[str, str]:
    """Return the (inflow_url, outflow_url) pair for tax-year pair (yy -> yz)."""
    yyt = f"{yy % 100:02d}"
    yzt = f"{yz % 100:02d}"
    base = "https://www.irs.gov/pub/irs-soi"
    return (
        f"{base}/countyinflow{yyt}{yzt}.csv",
        f"{base}/countyoutflow{yyt}{yzt}.csv",
    )


def _discover_latest_year() -> Optional[Tuple[int, int]]:
    """Find the most recent (from_year, to_year) IRS SOI migration CSV pair that exists.

    Probes newest first. Returns the first year-pair where the INFLOW file
    downloads cleanly — the matching outflow is assumed present (IRS publishes
    them together).
    """
    today_y = date.today().year
    # IRS publishes data ~18-24 months after the tax year ends.
    # In 2026 the latest expected pair is 2023->2024 or 2022->2023.
    for yz in range(today_y - 1, today_y - 7, -1):
        yy = yz - 1
        inflow_url, _ = _build_urls(yy, yz)
        body = _fetch_csv(inflow_url)
        if body is not None:
            return (yy, yz)
    return None


def _parse_migration_csv(csv_bytes: bytes, direction: str) -> List[dict]:
    """Parse a single IRS SOI county-migration CSV.

    Modern column layout (post-2011):
        y1_statefips, y1_countyfips, y2_statefips, y2_countyfips,
        y1_state, y2_state, y1_state_name, y2_state_name,
        n1 (number of returns), n2 (number of individuals), AGI

    For INFLOWS file: y2_* is the destination, y1_* is the origin.
    For OUTFLOWS file: y1_* is the origin, y2_* is the destination.
    """
    text = csv_bytes.decode("utf-8", errors="replace")
    out: List[dict] = []
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        # Skip aggregate rows (county FIPS == 000) and "Foreign" / "US Total" pseudo-rows
        try:
            y1_state = int(row.get("y1_statefips") or 0)
            y1_county = int(row.get("y1_countyfips") or 0)
            y2_state = int(row.get("y2_statefips") or 0)
            y2_county = int(row.get("y2_countyfips") or 0)
        except (ValueError, TypeError):
            continue
        # Skip rows where the "other" side is an aggregate or special code.
        # County code 0 is the state-level "all counties" row; we only want real flows.
        if direction == "in":
            # destination = y2 (must be a real county), origin = y1 (allow aggregate)
            if y2_county == 0 or y2_state == 0:
                continue
        else:
            if y1_county == 0 or y1_state == 0:
                continue
        try:
            n_returns = int(float(row.get("n1") or 0))
        except (ValueError, TypeError):
            n_returns = 0
        out.append({
            "from_fips": f"{y1_state:02d}{y1_county:03d}",
            "to_fips":   f"{y2_state:02d}{y2_county:03d}",
            "n_returns": n_returns,
            "direction": direction,
        })
    return out


def fetch_migration_flows(cbsa: str, year: Optional[int] = None) -> Optional[dict]:
    """Aggregate IRS SOI county-to-county flows up to the MSA level."""
    pair = _discover_latest_year() if year is None else None
    if pair is None:
        return None
    yy, yz = pair
    inflow_url, outflow_url = _build_urls(yy, yz)

    inflow_bytes = _fetch_csv(inflow_url)
    outflow_bytes = _fetch_csv(outflow_url)
    if not inflow_bytes or not outflow_bytes:
        print(f"  [IRS SOI] one of inflow/outflow missing for {yy}-{yz}", file=sys.stderr)
        return None

    inflows = _parse_migration_csv(inflow_bytes, "in")
    outflows = _parse_migration_csv(outflow_bytes, "out")
    if not inflows or not outflows:
        return None

    # Counties that belong to our target MSA
    target_counties = {fips for fips, c in COUNTY_TO_MSA.items() if c == cbsa}
    if not target_counties:
        return None

    # Inflow rows: keep where TO (y2) is in target MSA. Aggregate by source MSA.
    in_by_origin_msa: Dict[str, int] = {}
    total_in = 0
    for r in inflows:
        if r["to_fips"] not in target_counties:
            continue
        origin_cbsa = COUNTY_TO_MSA.get(r["from_fips"])
        origin_label = CBSA_TO_SHORT.get(origin_cbsa) or f"Other ({r['from_fips'][:2]})"
        if origin_cbsa == cbsa:
            # Within-MSA churn — skip
            continue
        in_by_origin_msa[origin_label] = in_by_origin_msa.get(origin_label, 0) + r["n_returns"]
        total_in += r["n_returns"]

    out_by_dest_msa: Dict[str, int] = {}
    total_out = 0
    for r in outflows:
        if r["from_fips"] not in target_counties:
            continue
        dest_cbsa = COUNTY_TO_MSA.get(r["to_fips"])
        dest_label = CBSA_TO_SHORT.get(dest_cbsa) or f"Other ({r['to_fips'][:2]})"
        if dest_cbsa == cbsa:
            continue
        out_by_dest_msa[dest_label] = out_by_dest_msa.get(dest_label, 0) + r["n_returns"]
        total_out += r["n_returns"]

    top_in = sorted(
        ({"origin_msa": k, "n_returns": v} for k, v in in_by_origin_msa.items()),
        key=lambda x: -x["n_returns"],
    )[:10]
    top_out = sorted(
        ({"dest_msa": k, "n_returns": v} for k, v in out_by_dest_msa.items()),
        key=lambda x: -x["n_returns"],
    )[:10]

    return {
        "year_pair_label": f"{yy}→{yz}",
        "from_year":       yy,
        "to_year":         yz,
        "total_in":        total_in,
        "total_out":       total_out,
        "net":             total_in - total_out,
        "top_in":          top_in,
        "top_out":         top_out,
        "source":          f"IRS SOI county-to-county migration ({inflow_url})",
    }


# ----------------------------- CLI smoke test -----------------------------

if __name__ == "__main__":
    cbsa = sys.argv[1] if len(sys.argv) > 1 else "42340"
    print(f"Fetching IRS SOI migration for CBSA {cbsa} ...", file=sys.stderr)
    d = fetch_migration_flows(cbsa)
    if d:
        print(f"  Year: {d['year_pair_label']}")
        print(f"  Total in:  {d['total_in']:,}")
        print(f"  Total out: {d['total_out']:,}")
        print(f"  Net:       {d['net']:+,}")
        print(f"  Top inbound MSAs:")
        for r in d['top_in'][:5]:
            print(f"    {r['origin_msa']:30s}  {r['n_returns']:,}")
        print(f"  Top outbound MSAs:")
        for r in d['top_out'][:5]:
            print(f"    {r['dest_msa']:30s}  {r['n_returns']:,}")
    else:
        print("  (no data)", file=sys.stderr)
