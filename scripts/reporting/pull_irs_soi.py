"""IRS SOI county-to-county migration pulls for Metro Economic Profile reports.

IRS publishes annual county-to-county migration data based on filed tax returns
(approximately a year-and-a-half lag). Inflow file lists every flow INTO a county
(prior county -> current county). Outflow file lists every flow OUT of a county.

Source: https://www.irs.gov/statistics/soi-tax-stats-migration-data
Latest available is usually two filing years behind today. Files are CSV inside ZIPs:
    https://www.irs.gov/pub/irs-soi/{YY}_to_{YZ}_county_data.zip   (e.g. 22_to_23)

We aggregate the county-level flows up to MSAs using _ga_msas.COUNTY_TO_MSA.

Exposes:
  fetch_migration_flows(cbsa, year=None) -> dict

Returns:
    {
      "year": "2022→2023",
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
import zipfile
from datetime import date
from pathlib import Path
from typing import Optional, Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ga_msas import GA_MSAS, COUNTY_TO_MSA  # noqa: E402

# Build inverse + label maps
CBSA_TO_SHORT: Dict[str, str] = {cbsa: short for cbsa, short, _, _ in GA_MSAS}

# Cache the ZIP between calls within a single orchestrator run
_ZIP_CACHE: Dict[str, bytes] = {}


def _fetch_zip(url: str) -> Optional[bytes]:
    if url in _ZIP_CACHE:
        return _ZIP_CACHE[url]
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read()
        _ZIP_CACHE[url] = body
        return body
    except urllib.error.HTTPError as e:
        if e.code != 404:
            print(f"  [IRS SOI] HTTP {e.code} for {url}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  [IRS SOI] {type(e).__name__}: {e}", file=sys.stderr)
        return None


def _discover_latest_year() -> Optional[Tuple[int, int]]:
    """Find the most recent (from_year, to_year) IRS SOI migration ZIP that exists."""
    today_y = date.today().year
    # IRS typically publishes data ~18-24 months after the tax year ends.
    # Try newest first.
    for yz in range(today_y - 1, today_y - 6, -1):
        yy = yz - 1
        url = f"https://www.irs.gov/pub/irs-soi/{yy % 100:02d}_to_{yz % 100:02d}_county_data.zip"
        if _fetch_zip(url) is not None:
            return (yy, yz)
    return None


def _parse_migration_csv(csv_bytes: bytes, direction: str) -> List[dict]:
    """Parse a single IRS SOI county-migration CSV.

    Each row has:
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
        try:
            y1_fips = f"{int(row['y1_statefips']):02d}{int(row['y1_countyfips']):03d}"
            y2_fips = f"{int(row['y2_statefips']):02d}{int(row['y2_countyfips']):03d}"
            n_returns = int(row.get("n1", 0) or 0)
        except (ValueError, KeyError, TypeError):
            continue
        out.append({
            "from_fips": y1_fips,
            "to_fips":   y2_fips,
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
    zip_url = f"https://www.irs.gov/pub/irs-soi/{yy % 100:02d}_to_{yz % 100:02d}_county_data.zip"
    zip_bytes = _fetch_zip(zip_url)
    if zip_bytes is None:
        return None

    # The ZIP contains two CSVs: ...inflow.csv and ...outflow.csv
    inflows: List[dict] = []
    outflows: List[dict] = []
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for name in zf.namelist():
                low = name.lower()
                if "inflow" in low and low.endswith(".csv"):
                    inflows = _parse_migration_csv(zf.read(name), "in")
                elif "outflow" in low and low.endswith(".csv"):
                    outflows = _parse_migration_csv(zf.read(name), "out")
    except zipfile.BadZipFile:
        print(f"  [IRS SOI] {zip_url} not a valid ZIP", file=sys.stderr)
        return None

    if not inflows or not outflows:
        return None

    # Counties that belong to our target MSA
    target_counties = {fips for fips, c in COUNTY_TO_MSA.items() if c == cbsa}
    if not target_counties:
        return None

    # Inflow rows: keep where TO (y2) is in target MSA. Aggregate by source MSA (or county+state for non-MSA origins).
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
        "source":          f"IRS SOI county-to-county migration (https://www.irs.gov/pub/irs-soi/{yy % 100:02d}_to_{yz % 100:02d}_county_data.zip)",
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
