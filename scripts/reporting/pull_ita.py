"""ITA (International Trade Administration) Metropolitan Area Exports reader.

MAED has **no public REST API**. The only access path is the Tableau dashboard
at https://tsereports.trade.gov/views/MetroBulkDownload/MetropolitanAreaExports
which serves the data via an annual "Download Data → Crosstab" CSV. Building a
programmatic scraper for that Tableau server requires a ~300-line stateful
session+commands handshake, brittle against ITA's Tableau version bumps. The
data updates once a year (November), so we use a manual annual CSV refresh
instead and read from disk here.

Refresh procedure: see scripts/reporting/data/MAED_REFRESH.md (≈ 5 min once a
year). The orchestrator's never-blank-on-failure logic handles a stale or
missing cache gracefully — `ita_msa_exports` just shows the prior run's values
until the cache is refreshed.

Exposes:
    fetch_msa_exports(cbsa, year=None) -> dict | None
        Returns:
            {
              "year":                 2024,
              "total_usd_millions":   9910.0,
              "by_product":           [{"label": "Transportation equipment", "value_usd_mil": 4820.0}, ...],
              "by_destination":       [{"label": "EU",      "value_usd_mil": 1240.0}, ...],
              "source_endpoint":      "scripts/reporting/data/maed_2024.csv",
              "source":               "ITA Metropolitan Area Exports (annual CSV refresh)",
            }
        or None if the cache is missing, the MSA isn't in the data, or the
        CSV's schema has drifted past what we can parse.
"""

from __future__ import annotations

import csv
import io
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

DATA_DIR = Path(__file__).parent / "data"


# CBSA → MSA Full Name as it appears in the MAED Tableau dashboard.
# Most Georgia MSAs use the same name Census does; the two exceptions:
#  * Atlanta uses the pre-2023 OMB name in MAED ("Sandy Springs-Roswell"
#    rather than current "Sandy Springs-Alpharetta").
#  * Brunswick is published with the "-St. Simons" suffix in MAED.
# All other GA MSAs match _ga_msas.GA_MSAS verbatim, so we override only
# the mismatches here; everything else falls back to the GA_MSAS full_name.
MAED_NAME_OVERRIDES: Dict[str, str] = {
    "12060": "Atlanta-Sandy Springs-Roswell, GA",
    "15260": "Brunswick-St. Simons, GA",
}

# Dataset values inside the MAED CSV's "Dataset" column. These are exact
# strings — keep them aligned with whatever ITA publishes. If ITA renames a
# dataset, the corresponding section silently returns empty; fix here.
DATASET_TOTAL_TO_WORLD     = "All MSAs - Exports to World"
DATASET_BY_REGION_GROUP    = "All MSAs - Exports to Select Regions/Trading Groups"
DATASET_TOP_NAICS3_SECTORS = "All MSAs - Top 5 Exported Sectors (NAICS-3)"

SUPPRESSED_MARKER = "D"  # Census disclosure-suppression marker per MAED methodology


def _msa_full_name_for(cbsa: str) -> Optional[str]:
    """Resolve a CBSA code to its MAED-style MSA Full Name."""
    if cbsa in MAED_NAME_OVERRIDES:
        return MAED_NAME_OVERRIDES[cbsa]
    # Fall back to the canonical name in _ga_msas
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from _ga_msas import GA_MSAS  # type: ignore
    except Exception:
        return None
    for code, _short, full, _pop in GA_MSAS:
        if code == cbsa:
            return full
    return None


def _find_latest_maed_csv() -> Optional[Path]:
    """Return the newest maed_*.csv in the data directory (by filename year).

    File naming: maed_{YEAR}.csv where YEAR matches the latest annual data
    vintage published (per the dashboard footer). We sort lexicographically
    by stem so maed_2025 wins over maed_2024.
    """
    if not DATA_DIR.is_dir():
        return None
    candidates = sorted(DATA_DIR.glob("maed_*.csv"))
    return candidates[-1] if candidates else None


def _open_tableau_export(path: Path) -> Tuple[io.StringIO, str]:
    """Open a Tableau crosstab export, returning (text_stream, delimiter).

    Tableau exports its "Crosstab" downloads as **UTF-16 LE with BOM and
    tab delimiters**, even though the file extension is `.csv`. Users
    can also re-save them in other tools (forcing UTF-8 or comma), so we
    sniff the BOM and the delimiter rather than hard-coding.
    """
    raw = path.read_bytes()
    if raw[:2] == b"\xff\xfe":
        text = raw[2:].decode("utf-16-le", errors="replace")
    elif raw[:2] == b"\xfe\xff":
        text = raw[2:].decode("utf-16-be", errors="replace")
    elif raw[:3] == b"\xef\xbb\xbf":
        text = raw[3:].decode("utf-8", errors="replace")
    else:
        # No BOM — try UTF-8 first, fall back to latin-1 if that explodes
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("latin-1", errors="replace")
    # Pick delimiter by whichever is more common on the first non-empty line
    first_line = next((ln for ln in text.splitlines() if ln.strip()), "")
    delim = "\t" if first_line.count("\t") > first_line.count(",") else ","
    return io.StringIO(text), delim


def _as_float(cell: Optional[str]) -> Optional[float]:
    """Parse a MAED cell value. None, empty, or 'D' (suppressed) → None.
    Strips quotes, commas, dollar signs, and surrounding whitespace. Tableau
    crosstabs commonly render values like '$8,378.2' for "millions of USD"
    columns; we want the float 8378.2.
    """
    if cell is None:
        return None
    s = cell.strip().strip('"').lstrip("$").replace(",", "")
    if not s or s.upper() == SUPPRESSED_MARKER:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _year_columns(header: List[str]) -> List[Tuple[str, int]]:
    """Identify which header columns are 4-digit years, return [(colname, year), ...]."""
    out: List[Tuple[str, int]] = []
    for col in header:
        c = col.strip()
        if len(c) == 4 and c.isdigit() and 1990 <= int(c) <= 2100:
            out.append((col, int(c)))
    return out


def _rollup_by_field(
    rows: List[dict], field: str, year_col: str
) -> List[dict]:
    """Sum the year_col across rows grouped by `field`, return top-down sorted list."""
    bucket: Dict[str, float] = {}
    for r in rows:
        key = (r.get(field) or "").strip()
        if not key or key.lower() == "world":  # exclude "World" itself from destination rollups
            continue
        v = _as_float(r.get(year_col))
        if v is None:
            continue
        bucket[key] = bucket.get(key, 0.0) + v
    out = [{"label": k, "value_usd_mil": round(v, 1)} for k, v in bucket.items()]
    out.sort(key=lambda x: -x["value_usd_mil"])
    return out


def fetch_msa_exports(cbsa: str, year: Optional[int] = None) -> Optional[dict]:
    """Read the cached MAED CSV and return aggregated exports for `cbsa`.

    Strategy:
      1. Look up the MSA's name as published by MAED.
      2. Find the newest cached maed_*.csv.
      3. Stream the CSV, keeping only rows for this MSA.
      4. Use the rightmost numeric year column (or `year` if specified) for
         the snapshot. Aggregate per Dataset.

    Returns None if any of: name lookup fails, no cache file, MSA absent
    from the CSV, or CSV header doesn't include any year columns.
    """
    msa_name = _msa_full_name_for(cbsa)
    if not msa_name:
        print(f"  [ITA] no MSA name mapping for CBSA {cbsa}", file=sys.stderr)
        return None

    csv_path = _find_latest_maed_csv()
    if not csv_path:
        print(
            f"  [ITA] no maed_*.csv in {DATA_DIR} — see MAED_REFRESH.md for "
            "the annual refresh procedure",
            file=sys.stderr,
        )
        return None

    # Stream the CSV (it's 30-60MB). DictReader gives us name-keyed rows.
    # Tableau exports are UTF-16 LE BOM + tab-delimited; _open_tableau_export
    # sniffs both so we can also accept UTF-8 CSV reformats.
    msa_rows: List[dict] = []
    header: List[str] = []
    try:
        stream, delim = _open_tableau_export(csv_path)
        reader = csv.DictReader(stream, delimiter=delim)
        header = list(reader.fieldnames or [])
        # Sanity-check the headers we depend on
        required = {"Dataset", "MSA Full Name", "NAICS Sector", "Destination"}
        missing = required - set(header)
        if missing:
            print(
                f"  [ITA] CSV {csv_path.name} missing columns {missing}; "
                "schema may have shifted — refresh and review MAED_REFRESH.md",
                file=sys.stderr,
            )
            return None
        for row in reader:
            if (row.get("MSA Full Name") or "").strip() == msa_name:
                msa_rows.append(row)
    except Exception as e:
        print(f"  [ITA] failed to read {csv_path.name}: {type(e).__name__}: {e}",
              file=sys.stderr)
        return None

    if not msa_rows:
        print(f"  [ITA] CSV has no rows for MSA '{msa_name}' (CBSA {cbsa})",
              file=sys.stderr)
        return None

    # Pick the target year column
    year_cols = _year_columns(header)
    if not year_cols:
        print(f"  [ITA] no 4-digit year columns in CSV header", file=sys.stderr)
        return None
    if year is None:
        year_col, year = year_cols[-1]  # rightmost = latest
    else:
        match = [(c, y) for c, y in year_cols if y == year]
        if not match:
            print(f"  [ITA] requested year {year} not in CSV", file=sys.stderr)
            return None
        year_col = match[0][0]

    # --- Total: "Exports to World" dataset × Destination=World, summed over NAICS sectors
    world_rows = [
        r for r in msa_rows
        if (r.get("Dataset") or "").strip() == DATASET_TOTAL_TO_WORLD
        and (r.get("Destination") or "").strip() == "World"
    ]
    total_vals = [_as_float(r.get(year_col)) for r in world_rows]
    total = round(sum(v for v in total_vals if v is not None), 1)

    # --- by_destination: "Exports to Select Regions/Trading Groups"
    region_rows = [
        r for r in msa_rows
        if (r.get("Dataset") or "").strip() == DATASET_BY_REGION_GROUP
    ]
    by_destination = _rollup_by_field(region_rows, "Destination", year_col)[:10]

    # --- by_product: "Top 5 Exported Sectors (NAICS-3)"
    sector_rows = [
        r for r in msa_rows
        if (r.get("Dataset") or "").strip() == DATASET_TOP_NAICS3_SECTORS
    ]
    by_product = _rollup_by_field(sector_rows, "NAICS Sector", year_col)[:10]

    # If absolutely nothing came back, signal failure rather than empty success
    if total == 0 and not by_destination and not by_product:
        print(f"  [ITA] all three aggregates empty for {msa_name} in year {year}",
              file=sys.stderr)
        return None

    return {
        "year":               year,
        "total_usd_millions": total,
        "by_product":         by_product,
        "by_destination":     by_destination,
        "source_endpoint":    str(csv_path.relative_to(csv_path.parent.parent.parent))
                              if csv_path.is_absolute() else str(csv_path),
        "source":             f"ITA Metropolitan Area Exports (annual CSV refresh — {csv_path.name})",
    }


# ----------------------------- CLI smoke test -----------------------------

if __name__ == "__main__":
    cbsa = sys.argv[1] if len(sys.argv) > 1 else "42340"
    print(f"Fetching MAED for CBSA {cbsa} ...", file=sys.stderr)
    d = fetch_msa_exports(cbsa)
    if not d:
        print("  → None (see stderr above for reason)")
        sys.exit(1)
    print(f"  Year:  {d['year']}")
    print(f"  Total: ${d['total_usd_millions']:,}M")
    print(f"  Top products:")
    for p in d["by_product"][:5]:
        print(f"    {p['label']:50s}  ${p['value_usd_mil']:>10,.0f}M")
    print(f"  Top destinations:")
    for c in d["by_destination"][:5]:
        print(f"    {c['label']:30s}  ${c['value_usd_mil']:>10,.0f}M")
    print(f"  Source: {d['source']}")
