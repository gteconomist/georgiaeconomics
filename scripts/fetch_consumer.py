"""Build the statewide Georgia Consumer page dataset -> data/consumer.json

Phase 4, WS1 (the last "Coming soon" home stub). No metro-report dependency and
no existing pipeline — this is genuinely new wiring, but it stays inside the
house playbook (BEA / EIA / FRED + reuse of inflation.json), with graceful
degradation and a monthly cron.

Sections:
  pce_trend        BEA SAPCE1 LineCode 1 (total PCE), GA + US, annual timeseries
  pce_composition  BEA SAPCE1 LineCodes 1–5 (total / goods / durable / nondurable
                   / services), GA, latest year
  pce_by_function  BEA SAPCE2 by function (housing, health, food, transport, …),
                   GA latest year — best-effort (dynamic LineCode discovery)
  pce_peers        BEA SAPCE3 per-capita PCE, GA vs FL/NC/SC/TN/AL + US, latest yr
  electricity      EIA API v2 electricity/retail-sales, GA residential, monthly
                   (sales GWh + price ¢/kWh) — the most current consumer signal
  real_wages       copied from data/inflation.json (already live, BLS) — no pull
  sales_tax        best-effort FRED GA sales-tax/retail proxy (multi-ID fallback)

Graceful degradation (house convention): each section is wrapped; on failure we
PRESERVE the prior value from the existing data/consumer.json and do NOT bump
_meta.<section>.last_updated, so the page renders a "stale" badge when a section
is older than STALE_MONTHS. A section that has never succeeded is omitted.

Env:
  BEA_API_KEY    — PCE sections (SAPCE1/2/3).
  EIA_API_KEY    — residential electricity (EIA v2).
  FRED_API_KEY   — optional sales-tax/retail proxy.
  (CENSUS_API_KEY reserved for a future retail layer.)

Usage:
  python scripts/fetch_consumer.py            # full run (needs keys + network)
  python scripts/fetch_consumer.py --offline  # reuse prior + only the local
                                              #   real_wages copy (no keys)
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import date, datetime
from pathlib import Path
from typing import Optional, Dict, List, Any

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = DATA / "consumer.json"
STALE_MONTHS = 14  # BEA state PCE is annual and lags ~1 year

BEA_API_KEY = os.environ.get("BEA_API_KEY", "").strip()
EIA_API_KEY = os.environ.get("EIA_API_KEY", "").strip()
FRED_API_KEY = os.environ.get("FRED_API_KEY", "").strip()

BEA_URL = "https://apps.bea.gov/api/data"
EIA_URL = "https://api.eia.gov/v2/electricity/retail-sales/data/"
FRED_URL = "https://api.stlouisfed.org/fred/series/observations"

# Southeast peer set (matches GDP / Population pages). GeoFips -> display name.
PEERS = [
    ("13000", "Georgia"), ("12000", "Florida"), ("37000", "North Carolina"),
    ("45000", "South Carolina"), ("47000", "Tennessee"), ("01000", "Alabama"),
]
US_FIPS = "00000"
GA_FIPS = "13000"

# SAPCE1 (PCE by major type of product) standard LineCodes.
SAPCE1_LINES = {1: "total", 2: "goods", 3: "durable", 4: "nondurable", 5: "services"}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_prior() -> dict:
    if OUT.exists():
        try:
            return json.loads(OUT.read_text())
        except Exception:
            return {}
    return {}


def _is_stale(meta_entry: Optional[dict]) -> bool:
    if not meta_entry or not meta_entry.get("last_updated"):
        return True
    try:
        d = datetime.strptime(meta_entry["last_updated"][:10], "%Y-%m-%d")
    except Exception:
        return True
    return (datetime.utcnow() - d).days > STALE_MONTHS * 30


# --------------------------------------------------------------------------- #
# BEA Regional (mirrors the proven fetch_gdp.py mechanics)
# --------------------------------------------------------------------------- #
def bea_get(params: dict, retries: int = 3) -> Optional[dict]:
    if not BEA_API_KEY:
        print("  [consumer/BEA] no BEA_API_KEY in env", file=sys.stderr)
        return None
    p = dict(params)
    p["UserID"] = BEA_API_KEY
    p["ResultFormat"] = "JSON"
    # BEA rejects percent-encoded commas — build the query string with RAW values.
    qs = "&".join(f"{k}={v}" for k, v in p.items())
    url = BEA_URL + "?" + qs
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                j = json.loads(r.read().decode("utf-8"))
            beaapi = j.get("BEAAPI") or {}
            results = beaapi.get("Results") or {}
            if isinstance(results, list):
                results = results[0] if results else {}
            err = (beaapi.get("Error")
                   or (results.get("Error") if isinstance(results, dict) else None))
            if err:
                if isinstance(err, list):
                    err = err[0] if err else {}
                desc = (err.get("APIErrorDescription") or err.get("ErrorDescription") or "") \
                    if isinstance(err, dict) else str(err)
                code = str(err.get("APIErrorCode") or "") if isinstance(err, dict) else ""
                low = str(desc).lower()
                expected = (code == "101"
                            or any(s in low for s in ("not available", "no data", "invalid year")))
                if not expected:
                    print(f"  [consumer/BEA error] {params.get('TableName')} "
                          f"geo={params.get('GeoFips')} lc={params.get('LineCode')} "
                          f"yr={params.get('Year')}: {err}", file=sys.stderr)
                return None
            return results
        except Exception as e:
            print(f"  [consumer/BEA err] {type(e).__name__}: {str(e)[:80]}", file=sys.stderr)
            time.sleep(1.5 * (attempt + 1))
    return None


def _rows(results: Optional[dict]) -> List[dict]:
    if not results:
        return []
    data = results.get("Data") or []
    if isinstance(data, dict):
        data = [data]
    return data


def _val(row: dict) -> Optional[float]:
    try:
        return float(str(row.get("DataValue", "")).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _bea_series(table: str, geofips: str, years: Optional[List[int]], line_code: str = "1") -> Dict[int, float]:
    """{year: value} for one table / single GeoFips / linecode. years=None → Year=ALL."""
    res = bea_get({
        "method": "GetData", "DataSetName": "Regional", "TableName": table,
        "LineCode": line_code, "GeoFips": geofips,
        "Year": "ALL" if years is None else ",".join(str(y) for y in years),
    })
    out: Dict[int, float] = {}
    for row in _rows(res):
        try:
            yr = int(row.get("TimePeriod"))
        except (TypeError, ValueError):
            continue
        v = _val(row)
        if v is not None:
            out[yr] = v
    return out


def _bea_all_states(table: str, line_code: str, year: int) -> Dict[str, float]:
    """{state_fips: value} for all states in one year (GeoFips='STATE')."""
    res = bea_get({
        "method": "GetData", "DataSetName": "Regional", "TableName": table,
        "LineCode": line_code, "GeoFips": "STATE", "Year": str(year),
    })
    out: Dict[str, float] = {}
    for row in _rows(res):
        fips = (row.get("GeoFips") or "").strip()
        v = _val(row)
        if fips and v is not None:
            out[fips] = v
    return out


def _bea_linecodes(table: str) -> List[dict]:
    res = bea_get({
        "method": "GetParameterValuesFiltered", "DataSetName": "Regional",
        "TargetParameter": "LineCode", "TableName": table,
    })
    vals = (res or {}).get("ParamValue", []) if isinstance(res, dict) else []
    if isinstance(vals, dict):
        vals = [vals]
    return vals or []


# --------------------------------------------------------------------------- #
# PCE sections
# --------------------------------------------------------------------------- #
def fetch_pce_trend(years_back: int = 13) -> Optional[dict]:
    """GA + US total PCE ($M), annual, last `years_back` published years."""
    ga = _bea_series("SAPCE1", GA_FIPS, None, line_code="1")
    us = _bea_series("SAPCE1", US_FIPS, None, line_code="1")
    if not ga:
        return None
    years = sorted(ga.keys())[-years_back:]
    ga_vals = [round(ga[y]) for y in years]
    us_vals = [round(us[y]) if y in us else None for y in years]
    yoy = [None]
    for i in range(1, len(ga_vals)):
        prev = ga_vals[i - 1]
        yoy.append(round((ga_vals[i] - prev) / prev * 100, 1) if prev else None)
    return {
        "years": years,
        "ga_total_musd": ga_vals,
        "us_total_musd": us_vals,
        "yoy_pct": yoy,
        "latest_year": years[-1],
        "latest_total_musd": ga_vals[-1],
        "latest_yoy_pct": yoy[-1],
        "source": "BEA SAPCE1 LineCode 1 (total personal consumption expenditures, current $M)",
    }


def fetch_pce_composition(year: int) -> Optional[dict]:
    """GA PCE split into goods/durable/nondurable/services for one year."""
    out: Dict[str, Any] = {"year": year}
    for lc, key in SAPCE1_LINES.items():
        s = _bea_series("SAPCE1", GA_FIPS, [year], line_code=str(lc))
        if year in s:
            out[f"{key}_musd"] = round(s[year])
    # need at least goods + services to be meaningful
    if "goods_musd" not in out or "services_musd" not in out:
        return None
    out["total_musd"] = out.get("total_musd") or round(out.get("goods_musd", 0) + out.get("services_musd", 0))
    out["source"] = "BEA SAPCE1 (PCE by major type of product), GA, current $M"
    return out


def fetch_pce_by_function(year: int, top_n: int = 10) -> Optional[dict]:
    """GA PCE by function (housing, health, food, …) for one year — best-effort.

    Discovers SAPCE2 LineCodes dynamically, pulls GA for each, drops the total /
    obvious subtotal lines, and returns the largest `top_n`. Capped to keep the
    BEA call count reasonable; degrades to None on any trouble.
    """
    lines = _bea_linecodes("SAPCE2")
    if not lines:
        return None
    # The grand total is LineCode 1 ("Personal consumption expenditures"); skip it
    # and a couple of broad aggregates so we show actual spending categories.
    SKIP_DESC = ("personal consumption expenditures", "household consumption expenditures",
                 "final consumption expenditures of nonprofit")
    funcs = []
    for lv in lines:
        key = str(lv.get("Key") or "").strip()
        desc = str(lv.get("Desc") or "").strip()
        if not key or key == "1":
            continue
        if any(s in desc.lower() for s in SKIP_DESC):
            continue
        funcs.append((key, desc))
        if len(funcs) >= 40:  # hard cap on probing breadth
            break

    rows = []
    for key, desc in funcs:
        s = _bea_series("SAPCE2", GA_FIPS, [year], line_code=key)
        v = s.get(year)
        if isinstance(v, (int, float)) and v > 0:
            rows.append({"name": desc, "value_musd": round(v)})
    if len(rows) < 4:
        return None
    rows.sort(key=lambda r: -r["value_musd"])
    rows = rows[:top_n]
    total = sum(r["value_musd"] for r in rows)
    for r in rows:
        r["share_pct"] = round(r["value_musd"] / total * 100, 1) if total else None
    return {"year": year, "functions": rows,
            "source": "BEA SAPCE2 (PCE by function), GA, current $M; top categories"}


def fetch_pce_peers(year: int) -> Optional[dict]:
    """Per-capita PCE for GA + SE peers + US, one year (SAPCE3 LineCode 1)."""
    per_state = _bea_all_states("SAPCE3", "1", year)
    us = _bea_series("SAPCE3", US_FIPS, [year], line_code="1").get(year)
    if not per_state:
        return None
    states = []
    for fips, name in PEERS:
        v = per_state.get(fips)
        if isinstance(v, (int, float)):
            states.append({"fips": fips, "name": name, "per_capita": round(v)})
    if not states:
        return None
    states.sort(key=lambda s: -s["per_capita"])
    return {"year": year, "states": states,
            "us_per_capita": round(us) if isinstance(us, (int, float)) else None,
            "source": "BEA SAPCE3 (per-capita PCE), current $"}


def _latest_pce_year() -> int:
    """SAPCE annual lags ~1 yr (released ~Oct for the prior year)."""
    t = date.today()
    return t.year - 1 if t.month >= 11 else t.year - 2


# --------------------------------------------------------------------------- #
# EIA v2 — Georgia residential electricity
# --------------------------------------------------------------------------- #
def _http_json(url: str, timeout: int = 60, retries: int = 3) -> Optional[dict]:
    last = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8")[:200]
            except Exception:
                pass
            print(f"  [EIA/FRED HTTP {e.code}] {url[:90]} {body}", file=sys.stderr)
            if e.code in (400, 403, 404):
                return None
            last = e
        except Exception as e:
            print(f"  [http err] {type(e).__name__}: {str(e)[:80]}", file=sys.stderr)
            last = e
        time.sleep(1.5 * (attempt + 1))
    if last:
        print(f"  [http FAIL] {url[:90]} — {last}", file=sys.stderr)
    return None


def fetch_electricity(start_year: int = 2019) -> Optional[dict]:
    """GA residential electricity — monthly sales (→ GWh) + price (¢/kWh) via EIA v2."""
    if not EIA_API_KEY:
        print("  [consumer] no EIA_API_KEY; skipping electricity", file=sys.stderr)
        return None
    params = [
        ("api_key", EIA_API_KEY),
        ("frequency", "monthly"),
        ("data[0]", "sales"),
        ("data[1]", "price"),
        ("data[2]", "revenue"),
        ("facets[stateid][]", "GA"),
        ("facets[sectorid][]", "RES"),
        ("start", f"{start_year}-01"),
        ("sort[0][column]", "period"),
        ("sort[0][direction]", "asc"),
        ("offset", "0"),
        ("length", "5000"),
    ]
    url = EIA_URL + "?" + urllib.parse.urlencode(params)
    j = _http_json(url)
    data = (((j or {}).get("response") or {}).get("data")) or []
    if not data:
        return None
    months, sales_gwh, price = [], [], []
    for row in data:
        per = row.get("period")
        if not per:
            continue
        months.append(per)
        # EIA sales are million kWh = GWh; price is cents/kWh
        try:
            sales_gwh.append(round(float(row.get("sales")), 1) if row.get("sales") is not None else None)
        except (TypeError, ValueError):
            sales_gwh.append(None)
        try:
            price.append(round(float(row.get("price")), 2) if row.get("price") is not None else None)
        except (TypeError, ValueError):
            price.append(None)
    if not months:
        return None
    # latest non-null price + YoY
    def _last(arr):
        for v in reversed(arr):
            if v is not None:
                return v
        return None
    latest_price = _last(price)
    price_yoy = None
    if len(price) >= 13 and price[-1] is not None and price[-13] not in (None, 0):
        price_yoy = round((price[-1] - price[-13]) / price[-13] * 100, 1)
    return {
        "months": months,
        "sales_gwh": sales_gwh,
        "price_cents_kwh": price,
        "latest_month": months[-1],
        "latest_price_cents_kwh": latest_price,
        "price_yoy_pct": price_yoy,
        "source": "EIA API v2 electricity/retail-sales — Georgia residential (sales in GWh, price in ¢/kWh)",
    }


# --------------------------------------------------------------------------- #
# FRED — best-effort sales-tax / retail proxy
# --------------------------------------------------------------------------- #
# Census QTAX state series mirrored on FRED. We try a few candidate IDs and keep
# the first that returns data; the page is solid without this section.
SALES_TAX_FRED_CANDIDATES = [
    ("QTAXT09QTAXCAT3GANO", "Census QTAX — GA general sales & gross-receipts tax (quarterly, $K)"),
    ("GASLGRTAX", "FRED — GA state & local general sales tax"),
]


def fetch_sales_tax() -> Optional[dict]:
    if not FRED_API_KEY:
        return None
    for sid, label in SALES_TAX_FRED_CANDIDATES:
        params = {
            "series_id": sid, "file_type": "json", "api_key": FRED_API_KEY,
            "observation_start": f"{date.today().year - 7}-01-01",
        }
        url = FRED_URL + "?" + urllib.parse.urlencode(params)
        j = _http_json(url)
        obs = (j or {}).get("observations") or []
        pts = []
        for o in obs:
            try:
                pts.append((o.get("date"), float(o.get("value"))))
            except (TypeError, ValueError):
                continue
        if len(pts) >= 4:
            quarters = [d for d, _ in pts]
            values = [round(v) for _, v in pts]
            return {"series_id": sid, "quarters": quarters, "values_musd": values,
                    "source": label}
    return None


# --------------------------------------------------------------------------- #
# real wages — reuse inflation.json (no pull)
# --------------------------------------------------------------------------- #
def copy_real_wages() -> Optional[dict]:
    p = DATA / "inflation.json"
    if not p.exists():
        return None
    try:
        rw = json.loads(p.read_text()).get("real_wages")
    except Exception:
        return None
    if not rw or not rw.get("months"):
        return None
    # Keep just the series the consumer page draws.
    out = {"months": rw.get("months"), "source": "BLS via data/inflation.json (real-wage tracker)"}
    for k in ("index", "real_wage_index", "values", "yoy_pct"):
        if k in rw:
            out[k] = rw[k]
    return out


# --------------------------------------------------------------------------- #
# assembly
# --------------------------------------------------------------------------- #
def build(offline: bool = False) -> dict:
    prior = _load_prior()
    prior_meta = prior.get("_meta", {})
    meta: Dict[str, dict] = {}
    out: Dict[str, Any] = {"fetched_at": _now_iso(), "schema": "consumer/v1"}

    pce_year = _latest_pce_year()

    def section(name, fn, *args):
        """Network/key-gated section with graceful degradation."""
        if offline:
            if name in prior:
                out[name] = prior[name]
                meta[name] = prior_meta.get(name, {})
            return
        try:
            val = fn(*args)
        except Exception as e:
            print(f"  [consumer] {name} raised {type(e).__name__}: {str(e)[:80]}", file=sys.stderr)
            val = None
        if val:
            out[name] = val
            meta[name] = {"last_updated": _now_iso()}
        elif name in prior:
            out[name] = prior[name]
            meta[name] = prior_meta.get(name, {"last_updated": None})

    # BEA PCE
    section("pce_trend", fetch_pce_trend)
    section("pce_composition", fetch_pce_composition, pce_year)
    section("pce_by_function", fetch_pce_by_function, pce_year)
    section("pce_peers", fetch_pce_peers, pce_year)
    # EIA electricity
    section("electricity", fetch_electricity)
    # FRED sales-tax (optional)
    section("sales_tax", fetch_sales_tax)

    # real wages — always a local copy (no key); refresh from inflation.json
    rw = copy_real_wages()
    if rw:
        out["real_wages"] = rw
        meta["real_wages"] = {"last_updated": _now_iso()}
    elif "real_wages" in prior:
        out["real_wages"] = prior["real_wages"]
        meta["real_wages"] = prior_meta.get("real_wages", {})

    # ---- KPI strip ----
    trend = out.get("pce_trend") or {}
    peers = out.get("pce_peers") or {}
    elec = out.get("electricity") or {}
    ga_pc = next((s["per_capita"] for s in peers.get("states", []) if s.get("fips") == GA_FIPS), None)
    rw_yoy = None
    rwv = (out.get("real_wages") or {}).get("yoy_pct")
    if isinstance(rwv, list):
        rw_yoy = next((v for v in reversed(rwv) if isinstance(v, (int, float))), None)
    out["kpis"] = {
        "pce_total_bn": round(trend.get("latest_total_musd") / 1000, 1) if trend.get("latest_total_musd") else None,
        "pce_per_capita": ga_pc,
        "pce_yoy_pct": trend.get("latest_yoy_pct"),
        "elec_price_cents_kwh": elec.get("latest_price_cents_kwh"),
        "elec_price_yoy_pct": elec.get("price_yoy_pct"),
        "real_wage_yoy_pct": rw_yoy,
    }

    # staleness + labels
    for v in meta.values():
        v["stale"] = _is_stale(v)
    out["_meta"] = meta
    out["latest_label"] = str(trend.get("latest_year") or pce_year)
    out["coverage_note"] = ("Consumer spending uses BEA state Personal Consumption Expenditures "
                            "(annual, ~1-year lag). Georgia residential electricity (EIA) and the "
                            "real-wage tracker (BLS) provide the most current monthly signals.")
    out["source_summary"] = {
        "pce": "BEA SAPCE1/SAPCE2/SAPCE3 (state personal consumption expenditures)",
        "electricity": "EIA API v2 electricity/retail-sales (GA residential)",
        "real_wages": "BLS via data/inflation.json",
        "sales_tax": "Census QTAX / FRED (best-effort)",
    }
    return out


def main(argv: List[str]) -> int:
    offline = "--offline" in argv
    out = build(offline=offline)
    OUT.write_text(json.dumps(out, indent=2))
    live = [k for k, v in out["_meta"].items() if not v.get("stale")]
    stale = [k for k, v in out["_meta"].items() if v.get("stale")]
    print(f"Wrote {OUT.relative_to(ROOT)}")
    print(f"  live sections:  {live}")
    print(f"  stale/absent:   {stale}")
    k = out["kpis"]
    print(f"  KPIs: PCE ${k['pce_total_bn']}B  per-capita ${k['pce_per_capita']}  "
          f"YoY {k['pce_yoy_pct']}%  elec {k['elec_price_cents_kwh']}¢/kWh")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
