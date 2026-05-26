"""ITA (International Trade Administration) Metropolitan Area Exports pulls.

The ITA publishes annual MSA-level export totals by product (3-digit NAICS) and
by destination country. Historically this lived on TradeStats Express; today the
data is served through api.trade.gov under several possible endpoint shapes —
exact URL depends on the dataset version.

This module is *defensive*: it tries the most likely endpoint patterns in
priority order and returns the first one that yields data. Production CI logs
will reveal which endpoint is currently live; once confirmed we can prune the
fallback list.

Exposes:
    fetch_msa_exports(cbsa, year=None) -> dict
        Returns:
            {
              "year":                 2024,
              "total_usd_millions":   9910,
              "by_product":           [{"naics3":"336", "label":"Transportation equipment", "value_usd_mil":4820}, ...],
              "by_destination":       [{"country":"China", "value_usd_mil":1240}, ...],
              "source_endpoint":      "https://api.trade.gov/v3/...",
            }

Env: ITA_API_KEY (api.data.gov key — get one at https://api.data.gov/signup/).
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
import urllib.error
import urllib.parse
from datetime import date
from typing import Optional, List, Dict

ITA_API_KEY = os.environ.get("ITA_API_KEY", "").strip()


# Candidate endpoint templates. Tried in order; first one to return non-empty
# data wins. Add new patterns to the front of the list as ITA updates the API.
#
# v3 documented endpoints (api.trade.gov):
#   /v3/maed/                      Metropolitan Area Export Data
#   /v3/ita_exports/               Industry & Country exports (state-level)
#   /v3/metropolitan_exports/      (legacy, sometimes redirects to v3/maed)
#
# Each template gets {cbsa} and {year} interpolated.
CANDIDATE_ENDPOINTS = [
    {
        "name": "v3/maed by product",
        "url":  "https://api.trade.gov/v3/maed/search?cbsa_code={cbsa}&year={year}&size=200",
        "auth": "header",   # X-Api-Key header
    },
    {
        "name": "v3/metropolitan_exports by product",
        "url":  "https://api.trade.gov/v3/metropolitan_exports/search?cbsa_code={cbsa}&year={year}&size=200",
        "auth": "header",
    },
    {
        "name": "v3/maed (query-string key)",
        "url":  "https://api.trade.gov/v3/maed/search?cbsa_code={cbsa}&year={year}&size=200&api_key={key}",
        "auth": "query",
    },
]


def _try_endpoint(template: dict, cbsa: str, year: int) -> Optional[list]:
    """Attempt one candidate endpoint. Returns the JSON 'results' list or None."""
    if not ITA_API_KEY:
        return None
    url = template["url"].format(cbsa=cbsa, year=year, key=ITA_API_KEY)
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    if template["auth"] == "header":
        headers["X-Api-Key"] = ITA_API_KEY

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            print(f"  [ITA {template['name']}] HTTP {e.code} — auth issue with ITA_API_KEY", file=sys.stderr)
        elif e.code != 404:
            print(f"  [ITA {template['name']}] HTTP {e.code}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  [ITA {template['name']}] {type(e).__name__}: {e}", file=sys.stderr)
        return None

    # api.trade.gov v3 wraps results in {"total": N, "results": [...]}
    results = data.get("results") or data.get("hits") or []
    if not results:
        return None
    print(f"  [ITA] {template['name']} returned {len(results)} rows", file=sys.stderr)
    return results


def _rollup_by_field(rows: list, field: str, value_field: str = "all_origins_value") -> List[dict]:
    """Aggregate a list of api.trade.gov MAED row dicts by a single field."""
    bucket: Dict[str, float] = {}
    for r in rows:
        key = r.get(field) or r.get(field + "_name") or "Other"
        try:
            v = float(r.get(value_field) or r.get("export_value") or 0) / 1e6  # to $M
        except (ValueError, TypeError):
            continue
        bucket[key] = bucket.get(key, 0) + v
    out = sorted(
        ({"label": k, "value_usd_mil": round(v, 1)} for k, v in bucket.items()),
        key=lambda x: -x["value_usd_mil"],
    )
    return out


def fetch_msa_exports(cbsa: str, year: Optional[int] = None) -> Optional[dict]:
    """Pull MSA-level export totals by product (NAICS3) and destination country.

    Strategy: try the most-recent ITA endpoint shapes in order. Production CI
    logs will indicate which pattern is currently live; we prune the list once
    confirmed.
    """
    if not ITA_API_KEY:
        print("  [ITA] no ITA_API_KEY in env", file=sys.stderr)
        return None

    # ITA MAED is published annually with ~12-month lag. Probe newest first.
    candidate_years = [year] if year else list(range(date.today().year - 1, date.today().year - 5, -1))

    for y in candidate_years:
        for template in CANDIDATE_ENDPOINTS:
            rows = _try_endpoint(template, cbsa, y)
            if not rows:
                continue
            # Build product + destination rollups from the rows.
            # Field names from current api.trade.gov MAED docs:
            #   commodity         = NAICS3 description
            #   commodity_id      = NAICS3 code
            #   country           = destination country
            #   all_origins_value = export value (USD)
            by_product = _rollup_by_field(rows, "commodity")
            by_dest    = _rollup_by_field(rows, "country")
            total = round(sum(p["value_usd_mil"] for p in by_product), 1)
            return {
                "year":               y,
                "total_usd_millions": total,
                "by_product":         by_product[:10],     # top 10
                "by_destination":     by_dest[:10],
                "source_endpoint":    template["url"].split("?")[0],
                "source":             f"ITA Metropolitan Area Exports via {template['name']}",
            }

    print("  [ITA] no candidate endpoint returned data for any of the probed years", file=sys.stderr)
    return None


if __name__ == "__main__":
    cbsa = sys.argv[1] if len(sys.argv) > 1 else "42340"
    print(f"Fetching ITA exports for CBSA {cbsa} ...", file=sys.stderr)
    d = fetch_msa_exports(cbsa)
    if d:
        print(f"  Year:  {d['year']}")
        print(f"  Total: ${d['total_usd_millions']:,}M")
        print(f"  Top products:")
        for p in d["by_product"][:5]:
            print(f"    {p['label']:50s}  ${p['value_usd_mil']:,.0f}M")
        print(f"  Top destinations:")
        for c in d["by_destination"][:5]:
            print(f"    {c['label']:30s}  ${c['value_usd_mil']:,.0f}M")
