"""Pull GA inflation data from BLS Public Data API v2.

Output: data/inflation.json

Data sources by section:
  • south_headline   — South region CPI-U (CUUR0300SA0) + US City Avg (CUUR0000SA0),
                       monthly NSA, ~10-year window. Drives the main trend chart,
                       cumulative-price-index chart, and the headline YoY KPI.
  • atlanta_cpi      — Atlanta-Sandy Springs-Roswell MSA CPI-U (CUURS35CSA0),
                       bi-monthly NSA. BLS switched Atlanta to bi-monthly cadence
                       in 2018, so the series has gaps in Jan/Mar/May/Jul/Sep/Nov.
  • components       — 10 South-region item indexes (food, energy, shelter,
                       transportation, medical, apparel, recreation, education,
                       core, all-items) for the horizontal "what's driving
                       inflation" bar chart.
  • gasoline         — South Urban Avg gasoline regular unleaded (APUS37A74714)
                       + US City Avg (APU000074714), monthly NSA, 5-year window.
  • real_wages       — GA total private average weekly earnings nominal
                       (SMU13000000500000011), deflated by South CPI to give
                       real-wage growth. State-level CES is NSA only.

Graceful degradation:
  Each section is wrapped in try/except. On failure the prior section value
  is preserved AND `_meta.<section>.last_updated` is NOT bumped. The page
  renders a "stale" badge when a section is > 6 months out of date.

Environment:
  BLS_API_KEY — registered key (free: bls.gov/developers/api_signature.htm)
                gives 500 queries/day vs 25/day unregistered. Already in repo
                secrets for use by other workflows.
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path
from datetime import date

# ---------------------------------------------------------------------------
# Env / constants
# ---------------------------------------------------------------------------
BLS_API_KEY = os.environ.get("BLS_API_KEY", "").strip()

TODAY = date.today()
TODAY_ISO = TODAY.isoformat()

# 10-year inclusive window for the main trend chart. The BLS public API caps
# any single request at 10 years for unregistered keys and 20 for registered;
# requesting 11+ years gets silently trimmed to 10 — and BLS keeps the
# EARLIEST 10, dropping the most recent year (so we'd miss the freshest
# release). Keep this at END_YEAR - 9 to stay within the 10-year ceiling.
END_YEAR   = TODAY.year
START_YEAR = END_YEAR - 9

# 5-year window for gasoline (more volatile, recency matters more)
GAS_START_YEAR = END_YEAR - 5

# Real-wages section needs both wages and CPI; 6 years gives enough lead-in
# for YoY + smoothing while staying under the 10-yr cap.
WAGES_START_YEAR = END_YEAR - 6

# Components only need 13 months for YoY but we pull 6 years for the 5-yr
# cumulative comparison.
COMPONENTS_START_YEAR = END_YEAR - 6

OUT_PATH = Path(__file__).parent.parent / "data" / "inflation.json"

# BLS API endpoint
BLS_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
def http_post_json(url, payload, timeout=60, retries=3):
    """POST JSON, return parsed JSON. Retries on network errors."""
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json",
               "User-Agent": "georgiaeconomics.com fetch_inflation.py"}
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=body, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            last_err = e
            time.sleep(1 + attempt)
    raise RuntimeError(f"POST {url} failed after {retries} retries: {last_err}")


def bls_fetch(series_ids, start_year, end_year):
    """Call the BLS v2 timeseries API for a list of series. Returns a dict
    mapping series_id -> list of {year, period, value, ...} rows sorted
    oldest-first. Skips data points BLS marks as unavailable (value="-")."""
    payload = {
        "seriesid": series_ids,
        "startyear": str(start_year),
        "endyear":   str(end_year),
    }
    if BLS_API_KEY:
        payload["registrationkey"] = BLS_API_KEY
    resp = http_post_json(BLS_URL, payload)
    status = resp.get("status")
    if status != "REQUEST_SUCCEEDED":
        msgs = resp.get("message", [])
        raise RuntimeError(f"BLS API status={status}: {msgs}")
    # Surface non-fatal messages (e.g. "Series does not exist") to stderr
    for m in resp.get("message", []) or []:
        print(f"      [BLS msg] {m}", file=sys.stderr)
    out = {}
    for s in resp.get("Results", {}).get("series", []):
        sid = s.get("seriesID")
        rows = []
        for r in s.get("data", []):
            val_raw = r.get("value", "").strip()
            if val_raw in ("", "-"):
                continue  # data unavailable (e.g. 2025 lapse in appropriations)
            try:
                val = float(val_raw)
            except ValueError:
                continue
            rows.append({
                "year":   int(r["year"]),
                "period": r["period"],         # "M01"–"M12" for monthly
                "month":  int(r["period"][1:]) if r["period"].startswith("M") else None,
                "value":  val,
            })
        # API returns newest-first; flip to oldest-first
        rows.sort(key=lambda x: (x["year"], x["month"] or 0))
        out[sid] = rows
    return out


# ---------------------------------------------------------------------------
# Period helpers
# ---------------------------------------------------------------------------
def ym(row):
    """'2026-04' from a row dict."""
    return f"{row['year']:04d}-{row['month']:02d}"


def ym_label(row):
    """'Apr 2026' from a row dict."""
    return f"{MONTH_NAMES[row['month'] - 1]} {row['year']}"


def yoy_pct(series, idx):
    """Year-over-year % change for series[idx], looking back 12 months by date.
    Returns None if no 12-months-back row is available."""
    cur = series[idx]
    target_year = cur["year"] - 1
    target_month = cur["month"]
    for r in series[:idx]:
        if r["year"] == target_year and r["month"] == target_month:
            if r["value"]:
                return (cur["value"] - r["value"]) / r["value"] * 100
            return None
    return None


def yoy_pct_bimonthly(series, idx):
    """Atlanta is reported every other month (Feb/Apr/Jun/Aug/Oct/Dec).
    Year-over-year compares to same month one year prior."""
    return yoy_pct(series, idx)


# ---------------------------------------------------------------------------
# Section fetchers — each returns a (section_dict, section_meta) tuple
# ---------------------------------------------------------------------------

# --- 1) South + US headline CPI (monthly trend) -----------------------------
SOUTH_HEADLINE_SID = "CUUR0300SA0"
US_HEADLINE_SID    = "CUUR0000SA0"


def fetch_headline_section():
    """South + US monthly CPI for the trend chart, cumulative index, and KPIs.
    Returns ({months, south_index, south_yoy, us_index, us_yoy}, meta)."""
    # BLS API allows max 20 years per request — we split if needed
    series = bls_fetch([SOUTH_HEADLINE_SID, US_HEADLINE_SID], START_YEAR, END_YEAR)
    south = series.get(SOUTH_HEADLINE_SID, [])
    us    = series.get(US_HEADLINE_SID,    [])
    if not south or not us:
        raise RuntimeError(f"headline series empty (south={len(south)}, us={len(us)})")

    # Align by month — build a master list of unique YYYY-MM in the South series
    south_by_ym = {ym(r): r["value"] for r in south}
    us_by_ym    = {ym(r): r["value"] for r in us}
    all_ym = sorted(set(south_by_ym) | set(us_by_ym))

    months      = all_ym
    south_index = [south_by_ym.get(m) for m in all_ym]
    us_index    = [us_by_ym.get(m)    for m in all_ym]

    # YoY % at each index — only computed where both this-month and 12-mo-prior values exist
    south_yoy = [_yoy_at(south_by_ym, m) for m in all_ym]
    us_yoy    = [_yoy_at(us_by_ym,    m) for m in all_ym]

    latest_idx = len(months) - 1
    latest_south = next((i for i in range(latest_idx, -1, -1) if south_index[i] is not None), None)
    latest_us    = next((i for i in range(latest_idx, -1, -1) if us_index[i]    is not None), None)

    section = {
        "months":       months,
        "south_index":  [round(v, 3) if v is not None else None for v in south_index],
        "us_index":     [round(v, 3) if v is not None else None for v in us_index],
        "south_yoy":    [round(v, 2) if v is not None else None for v in south_yoy],
        "us_yoy":       [round(v, 2) if v is not None else None for v in us_yoy],
        "latest_month": months[latest_south] if latest_south is not None else None,
        "latest_south_label": _ym_to_label(months[latest_south]) if latest_south is not None else None,
        "latest_south_index": south_index[latest_south] if latest_south is not None else None,
        "latest_south_yoy":   south_yoy[latest_south]   if latest_south is not None else None,
        "latest_us_index":    us_index[latest_us]       if latest_us    is not None else None,
        "latest_us_yoy":      us_yoy[latest_us]         if latest_us    is not None else None,
    }
    meta = {
        "last_updated": TODAY_ISO,
        "source": "BLS CPI-U, South region (CUUR0300SA0) + US City Avg (CUUR0000SA0), NSA",
        "as_of": section["latest_south_label"],
        "coverage_months": [months[0], months[-1]],
    }
    return section, meta


def _yoy_at(by_ym, ym_key):
    """YoY % for a given YYYY-MM, looking back 12 months by date. None if either side missing."""
    cur = by_ym.get(ym_key)
    if cur is None:
        return None
    y, m = int(ym_key[:4]), int(ym_key[5:7])
    prior_key = f"{y - 1:04d}-{m:02d}"
    prior = by_ym.get(prior_key)
    if prior is None or prior == 0:
        return None
    return (cur - prior) / prior * 100


def _ym_to_label(ym_key):
    if not ym_key:
        return None
    y, m = int(ym_key[:4]), int(ym_key[5:7])
    return f"{MONTH_NAMES[m - 1]} {y}"


# --- 2) Atlanta MSA CPI (bi-monthly) ---------------------------------------
ATLANTA_SID = "CUURS35CSA0"


def fetch_atlanta_section():
    """Atlanta-Sandy Springs-Roswell CPI, bi-monthly (Feb/Apr/Jun/Aug/Oct/Dec)
    since 2018. Returns ({periods, index, yoy_pct, latest_*}, meta)."""
    series = bls_fetch([ATLANTA_SID], START_YEAR, END_YEAR)
    rows = series.get(ATLANTA_SID, [])
    if not rows:
        raise RuntimeError("Atlanta MSA series empty")

    by_ym = {ym(r): r["value"] for r in rows}
    periods = sorted(by_ym.keys())
    index   = [by_ym[p] for p in periods]
    yoy     = [_yoy_at(by_ym, p) for p in periods]

    latest_idx = len(periods) - 1

    section = {
        "periods":      periods,
        "period_labels": [_ym_to_label(p) for p in periods],
        "index":        [round(v, 3) for v in index],
        "yoy_pct":      [round(v, 2) if v is not None else None for v in yoy],
        "latest_period":      periods[latest_idx],
        "latest_period_label": _ym_to_label(periods[latest_idx]),
        "latest_index":       round(index[latest_idx], 3),
        "latest_yoy":         round(yoy[latest_idx], 2) if yoy[latest_idx] is not None else None,
    }
    meta = {
        "last_updated": TODAY_ISO,
        "source": "BLS CPI-U, Atlanta-Sandy Springs-Roswell GA (CUURS35CSA0), NSA",
        "as_of": section["latest_period_label"],
        "cadence_note": "Bi-monthly since 2018: published for Feb, Apr, Jun, Aug, Oct, Dec.",
        "coverage_periods": [periods[0], periods[-1]],
    }
    return section, meta


# --- 3) Components of inflation (South region, latest YoY) ------------------
COMPONENT_DEFS = [
    # (series_id, display_label, group)
    ("CUUR0300SA0",     "All items",                "headline"),
    ("CUUR0300SA0L1E",  "Core (all less food + energy)", "core"),
    ("CUUR0300SAF1",    "Food",                     "category"),
    ("CUUR0300SA0E",    "Energy",                   "category"),
    ("CUUR0300SAH1",    "Shelter",                  "category"),
    ("CUUR0300SAT",     "Transportation",           "category"),
    ("CUUR0300SAM",     "Medical care",             "category"),
    ("CUUR0300SAA",     "Apparel",                  "category"),
    ("CUUR0300SAR",     "Recreation",               "category"),
    ("CUUR0300SAE",     "Education + communication","category"),
]


def fetch_components_section():
    """South region item indexes for the latest available month; computes YoY
    and a 5-year cumulative change for each category."""
    sids = [d[0] for d in COMPONENT_DEFS]
    # Need at least 13 months for YoY plus 5 years for cumulative.
    series = bls_fetch(sids, COMPONENTS_START_YEAR, END_YEAR)

    out_rows = []
    latest_ym_seen = None
    for sid, label, group in COMPONENT_DEFS:
        rows = series.get(sid, [])
        if not rows:
            print(f"      [components] {sid} ({label}): no data", file=sys.stderr)
            continue
        by_ym = {ym(r): r["value"] for r in rows}
        latest_key = max(by_ym.keys())
        latest_val = by_ym[latest_key]
        yoy = _yoy_at(by_ym, latest_key)
        # 5-year cumulative: compare to same month 5 years prior, fall back to
        # the oldest available value if 5-yr-prior isn't there
        y, m = int(latest_key[:4]), int(latest_key[5:7])
        prior_key = f"{y - 5:04d}-{m:02d}"
        prior5 = by_ym.get(prior_key)
        if prior5 is None:
            # use oldest available
            oldest_key = min(by_ym.keys())
            prior5 = by_ym[oldest_key]
            cum_label = f"since {_ym_to_label(oldest_key)}"
        else:
            cum_label = "5-year"
        cum5 = ((latest_val - prior5) / prior5 * 100) if prior5 else None

        out_rows.append({
            "series_id":  sid,
            "label":      label,
            "group":      group,
            "latest":     round(latest_val, 3),
            "yoy_pct":    round(yoy, 2) if yoy is not None else None,
            "cum5_pct":   round(cum5, 2) if cum5 is not None else None,
            "cum_label":  cum_label,
        })
        if latest_ym_seen is None or latest_key > latest_ym_seen:
            latest_ym_seen = latest_key

    if not out_rows:
        raise RuntimeError("All component series empty")

    # Sort: pull headline + core to the top, then categories sorted by YoY desc
    cat_rows = [r for r in out_rows if r["group"] == "category"]
    head_rows = [r for r in out_rows if r["group"] != "category"]
    cat_rows.sort(key=lambda r: -(r["yoy_pct"] or -99))
    section = {
        "items":         head_rows + cat_rows,
        "latest_month":  latest_ym_seen,
        "latest_label":  _ym_to_label(latest_ym_seen),
    }
    meta = {
        "last_updated": TODAY_ISO,
        "source": "BLS CPI-U, South region (CUUR0300* series), NSA — latest month YoY",
        "as_of": section["latest_label"],
    }
    return section, meta


# --- 4) Gasoline (South + US, monthly $/gal) -------------------------------
SOUTH_GAS_SID = "APUS37A74714"  # South Urban average, regular unleaded
US_GAS_SID    = "APU000074714"  # US City Avg, regular unleaded


def fetch_gasoline_section():
    """Monthly retail gasoline prices ($/gal). Pulls 5 years."""
    series = bls_fetch([SOUTH_GAS_SID, US_GAS_SID], GAS_START_YEAR, END_YEAR)
    south = series.get(SOUTH_GAS_SID, [])
    us    = series.get(US_GAS_SID,    [])
    if not south:
        raise RuntimeError("South gasoline series empty")

    south_by_ym = {ym(r): r["value"] for r in south}
    us_by_ym    = {ym(r): r["value"] for r in us}
    months      = sorted(set(south_by_ym) | set(us_by_ym))

    south_price = [south_by_ym.get(m) for m in months]
    us_price    = [us_by_ym.get(m)    for m in months]

    # Find last month with a South price
    latest_idx = next((i for i in range(len(months) - 1, -1, -1)
                       if south_price[i] is not None), None)
    if latest_idx is None:
        raise RuntimeError("No South gasoline values")
    latest_ym = months[latest_idx]
    latest_val = south_price[latest_idx]
    yoy = _yoy_at(south_by_ym, latest_ym)

    section = {
        "months":      months,
        "south_price": [round(v, 3) if v is not None else None for v in south_price],
        "us_price":    [round(v, 3) if v is not None else None for v in us_price],
        "latest_month":     latest_ym,
        "latest_label":     _ym_to_label(latest_ym),
        "latest_south":     round(latest_val, 3),
        "latest_us":        round(us_by_ym.get(latest_ym), 3) if us_by_ym.get(latest_ym) is not None else None,
        "south_yoy_pct":    round(yoy, 2) if yoy is not None else None,
    }
    meta = {
        "last_updated": TODAY_ISO,
        "source": "BLS Average Price data — Gasoline, unleaded regular ($/gal). South Urban (APUS37A74714) + US City Avg (APU000074714).",
        "as_of": section["latest_label"],
        "note": "South Urban region is the closest BLS geography to Georgia — no GA-only monthly retail series.",
    }
    return section, meta


# --- 5) Real wages (GA total private, deflated) ----------------------------
GA_WAGES_SID = "SMU13000000500000011"  # GA Total Private Avg Weekly Earnings, NSA


def fetch_real_wages_section():
    """GA nominal avg weekly earnings deflated by South CPI. State CES is
    NSA only — we apply 3-month centered moving average to the YoY series
    to smooth out the seasonality."""
    # We need both wages and South CPI for the same months.
    series = bls_fetch([GA_WAGES_SID, SOUTH_HEADLINE_SID], WAGES_START_YEAR, END_YEAR)
    wages = series.get(GA_WAGES_SID,         [])
    cpi   = series.get(SOUTH_HEADLINE_SID,   [])
    if not wages or not cpi:
        raise RuntimeError(f"real_wages: missing series (wages={len(wages)}, cpi={len(cpi)})")

    wage_by_ym = {ym(r): r["value"] for r in wages}
    cpi_by_ym  = {ym(r): r["value"] for r in cpi}

    # Use Jan 2020 as the deflator base
    base_key = "2020-01"
    base_cpi = cpi_by_ym.get(base_key)
    if base_cpi is None:
        # Fall back to earliest CPI value
        base_key = min(cpi_by_ym)
        base_cpi = cpi_by_ym[base_key]

    months = sorted(set(wage_by_ym) & set(cpi_by_ym))
    nominal = [wage_by_ym[m] for m in months]
    # Real wage in Jan-2020 dollars = nominal * (base_cpi / cur_cpi)
    real    = [round(wage_by_ym[m] * base_cpi / cpi_by_ym[m], 2) for m in months]

    # YoY real wage growth %
    def yoy_at_idx(arr, i, lookback=12):
        if i < lookback:
            return None
        prior = arr[i - lookback]
        cur   = arr[i]
        if prior is None or cur is None or prior == 0:
            return None
        return (cur - prior) / prior * 100

    real_yoy    = [yoy_at_idx(real, i)    for i in range(len(real))]
    nominal_yoy = [yoy_at_idx(nominal, i) for i in range(len(nominal))]

    # 3-month centered moving average smoothing on YoY series to dampen NSA noise
    def smooth(arr):
        out = [None] * len(arr)
        for i in range(len(arr)):
            window = [arr[j] for j in (i - 1, i, i + 1)
                      if 0 <= j < len(arr) and arr[j] is not None]
            if len(window) >= 2:
                out[i] = sum(window) / len(window)
        return out

    real_yoy_smooth    = smooth(real_yoy)
    nominal_yoy_smooth = smooth(nominal_yoy)

    latest_idx = len(months) - 1
    while latest_idx >= 0 and real_yoy_smooth[latest_idx] is None:
        latest_idx -= 1

    section = {
        "months":              months,
        "nominal_weekly":      [round(v, 2) for v in nominal],
        "real_weekly_2020":    real,
        "real_yoy_pct":        [round(v, 2) if v is not None else None for v in real_yoy_smooth],
        "nominal_yoy_pct":     [round(v, 2) if v is not None else None for v in nominal_yoy_smooth],
        "base_period":         base_key,
        "base_label":          _ym_to_label(base_key),
        "latest_month":        months[latest_idx] if latest_idx >= 0 else None,
        "latest_label":        _ym_to_label(months[latest_idx]) if latest_idx >= 0 else None,
        "latest_nominal":      nominal[latest_idx] if latest_idx >= 0 else None,
        "latest_real":         real[latest_idx] if latest_idx >= 0 else None,
        "latest_real_yoy":     round(real_yoy_smooth[latest_idx], 2) if latest_idx >= 0 and real_yoy_smooth[latest_idx] is not None else None,
        "latest_nominal_yoy":  round(nominal_yoy_smooth[latest_idx], 2) if latest_idx >= 0 and nominal_yoy_smooth[latest_idx] is not None else None,
    }
    meta = {
        "last_updated": TODAY_ISO,
        "source": "BLS State CES, GA Total Private Avg Weekly Earnings (SMU13000000500000011, NSA), deflated by South region CPI (CUUR0300SA0). 3-mo centered MA applied to YoY series.",
        "as_of": section["latest_label"],
        "base_note": f"Real wages expressed in {section['base_label']} dollars.",
    }
    return section, meta


# ---------------------------------------------------------------------------
# Cumulative price index (derived from headline — no extra fetch needed)
# ---------------------------------------------------------------------------
def derive_cumulative_section(headline):
    """Rebase South + US headline indexes to Jan 2020 = 100 for the cumulative
    chart. Returns ({months, south, us}, meta)."""
    months = headline.get("months", [])
    south_idx = headline.get("south_index", [])
    us_idx    = headline.get("us_index", [])
    if not months:
        return None, None

    def rebase(series):
        # Anchor on Jan 2020 if present, else first non-null
        try:
            anchor_i = months.index("2020-01")
            base = series[anchor_i]
            anchor_label = "Jan 2020"
        except ValueError:
            anchor_i = next((i for i, v in enumerate(series) if v is not None), None)
            if anchor_i is None:
                return [None] * len(months), None
            base = series[anchor_i]
            anchor_label = months[anchor_i]
        if not base:
            return [None] * len(months), None
        return [round(v / base * 100, 2) if v is not None else None for v in series], anchor_label

    south_rebased, anchor_label = rebase(south_idx)
    us_rebased, _               = rebase(us_idx)

    # Cumulative growth % since anchor for the South series, latest month
    latest_idx = next((i for i in range(len(months) - 1, -1, -1)
                       if south_rebased[i] is not None), None)
    cum_pct = (south_rebased[latest_idx] - 100) if latest_idx is not None else None

    section = {
        "months":        months,
        "south":         south_rebased,
        "us":            us_rebased,
        "anchor_label":  anchor_label,
        "latest_cum_pct": round(cum_pct, 2) if cum_pct is not None else None,
    }
    meta = {
        "last_updated": TODAY_ISO,
        "source": "Derived from headline CPI series — rebased to anchor month = 100.",
        "as_of": _ym_to_label(months[latest_idx]) if latest_idx is not None else None,
        "anchor_label": anchor_label,
    }
    return section, meta


# ---------------------------------------------------------------------------
# Main — section-by-section graceful degradation
# ---------------------------------------------------------------------------
def main():
    if not BLS_API_KEY:
        # Not strictly required, but unregistered hits the 25/day cap fast.
        print("[warn] BLS_API_KEY not set — using unregistered quota (25 queries/day)",
              file=sys.stderr)

    if OUT_PATH.exists():
        with open(OUT_PATH) as f:
            existing = json.load(f)
    else:
        existing = {}

    out = dict(existing)
    out["fetched_at"] = TODAY_ISO
    meta = dict(existing.get("_meta", {}))
    for section in ("south_headline", "atlanta_cpi", "components",
                    "gasoline", "real_wages", "cumulative"):
        meta.setdefault(section, {"last_updated": None, "source": None})

    # Run each fetcher independently. On error, preserve existing data and
    # don't bump _meta.<section>.last_updated.
    successes = []

    print(f"\n[1/5] South + US headline CPI ({START_YEAR}-{END_YEAR}):")
    headline_data = out.get("south_headline")
    try:
        section, smeta = fetch_headline_section()
        out["south_headline"] = section
        meta["south_headline"] = smeta
        headline_data = section
        successes.append("south_headline")
        print(f"      OK: latest {section['latest_south_label']}, "
              f"S YoY={section['latest_south_yoy']}%, "
              f"US YoY={section['latest_us_yoy']}%")
    except Exception as e:
        print(f"      ERROR: {e} — preserving prior headline values.", file=sys.stderr)

    # Cumulative index is derived from headline — only run if we have headline data
    if headline_data and headline_data.get("months"):
        try:
            section, smeta = derive_cumulative_section(headline_data)
            if section:
                out["cumulative"] = section
                meta["cumulative"] = smeta
                successes.append("cumulative")
                print(f"      [derived] cumulative since {section['anchor_label']}: "
                      f"South +{section['latest_cum_pct']}%")
        except Exception as e:
            print(f"      [cumulative] derive failed: {e}", file=sys.stderr)

    print(f"\n[2/5] Atlanta-Sandy Springs-Roswell CPI ({START_YEAR}-{END_YEAR}):")
    try:
        section, smeta = fetch_atlanta_section()
        out["atlanta_cpi"] = section
        meta["atlanta_cpi"] = smeta
        successes.append("atlanta_cpi")
        print(f"      OK: latest {section['latest_period_label']}, "
              f"YoY={section['latest_yoy']}%")
    except Exception as e:
        print(f"      ERROR: {e} — preserving prior Atlanta values.", file=sys.stderr)

    print(f"\n[3/5] South region CPI components (latest YoY):")
    try:
        section, smeta = fetch_components_section()
        out["components"] = section
        meta["components"] = smeta
        successes.append("components")
        print(f"      OK: {len(section['items'])} components for {section['latest_label']}")
    except Exception as e:
        print(f"      ERROR: {e} — preserving prior components.", file=sys.stderr)

    print(f"\n[4/5] Gasoline ($/gal, {GAS_START_YEAR}-{END_YEAR}):")
    try:
        section, smeta = fetch_gasoline_section()
        out["gasoline"] = section
        meta["gasoline"] = smeta
        successes.append("gasoline")
        print(f"      OK: latest {section['latest_label']}, "
              f"South ${section['latest_south']}, "
              f"US ${section['latest_us']}, "
              f"S YoY={section['south_yoy_pct']}%")
    except Exception as e:
        print(f"      ERROR: {e} — preserving prior gasoline values.", file=sys.stderr)

    print(f"\n[5/5] Real wages (GA, deflated):")
    try:
        section, smeta = fetch_real_wages_section()
        out["real_wages"] = section
        meta["real_wages"] = smeta
        successes.append("real_wages")
        print(f"      OK: latest {section['latest_label']}, "
              f"real ${section['latest_real']}/wk "
              f"({section['base_label']} dollars), "
              f"real YoY={section['latest_real_yoy']}%")
    except Exception as e:
        print(f"      ERROR: {e} — preserving prior real_wages.", file=sys.stderr)

    # ----- Roll up KPIs -----
    k = {}
    head = out.get("south_headline") or {}
    comp = out.get("components") or {}
    gas  = out.get("gasoline") or {}
    atl  = out.get("atlanta_cpi") or {}
    cum  = out.get("cumulative") or {}
    rw   = out.get("real_wages") or {}

    k["latest_month_south"] = head.get("latest_south_label")
    k["headline_yoy_south"] = head.get("latest_south_yoy")
    k["headline_yoy_us"]    = head.get("latest_us_yoy")
    k["atlanta_yoy"]        = atl.get("latest_yoy")
    k["atlanta_period"]     = atl.get("latest_period_label")
    k["gas_latest"]         = gas.get("latest_south")
    k["gas_yoy_pct"]        = gas.get("south_yoy_pct")
    k["gas_period"]         = gas.get("latest_label")
    k["cum_growth_since_2020_south"] = cum.get("latest_cum_pct")
    k["real_wages_yoy"]     = rw.get("latest_real_yoy")
    k["nominal_wages_yoy"]  = rw.get("latest_nominal_yoy")

    # Component KPIs (pull by label so reordering doesn't break)
    by_label = {r["label"]: r for r in (comp.get("items") or [])}
    k["core_yoy"]     = (by_label.get("Core (all less food + energy)") or {}).get("yoy_pct")
    k["food_yoy"]     = (by_label.get("Food") or {}).get("yoy_pct")
    k["energy_yoy"]   = (by_label.get("Energy") or {}).get("yoy_pct")
    k["shelter_yoy"]  = (by_label.get("Shelter") or {}).get("yoy_pct")
    k["transport_yoy"] = (by_label.get("Transportation") or {}).get("yoy_pct")
    k["medical_yoy"]  = (by_label.get("Medical care") or {}).get("yoy_pct")

    out["kpis"] = k
    out["_meta"] = meta

    # Drop the seed-data fixture flag once we have at least one live section
    if successes and out.get("_fixture"):
        out.pop("_fixture", None)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nWrote {OUT_PATH} — sections updated this run: {successes or 'none'}")


if __name__ == "__main__":
    main()
