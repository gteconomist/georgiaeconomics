"""Deterministic narrative + scorecard generators for the MSA report pages.

Phase 3, Milestone 2. Every sentence is filled from a metro's
`data/msa_reports/<slug>.json` via threshold-driven templates. Each sentence is
gated on its inputs and is *dropped* (never fabricated) when a field is absent —
smaller metros lack some sections (CES detail, exports, affordability, etc.).

The single hard rule, learned the hard way: MSA *sector* facts come from CES
(`ces_by_supersector`), NOT QCEW MSA, because QCEW's MSA by-industry detail is
disclosure-suppressed for big metros (Atlanta shows 0 sectors). This mirrors the
page's own `cesMsaShares` / `replaceDiffusionChartCES` logic so the prose and the
charts always agree. GA/US shares + MSA wages still come from QCEW (where
disclosed).

Public API:
    build_narrative(data)  -> inner HTML for the GEN:NARRATIVE region
    build_scorecard(data)  -> inner HTML for the GEN:SCORECARD region
    footer_has_school(data) -> bool (parameterizes the methodology footer)

`data` is the parsed JSON dict (top-level: cbsa, short_name, sections, ...).
"""

from __future__ import annotations

MONTHS = ["", "January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December"]

# QCEW/CES comparative-table sector label -> CES by-supersector key.
# (Same map the page uses; MSA shares are derived from CES.)
CES_SECTOR_MAP = {
    "Mining": "Mining and logging",
    "Construction": "Construction",
    "Manufacturing": "Manufacturing",
    "Transportation/Utilities": "Transportation, warehousing and utilities",
    "Wholesale Trade": "Wholesale trade",
    "Retail Trade": "Retail trade",
    "Information": "Information",
    "Financial Activities": "Financial activities",
    "Prof. & Bus. Services": "Professional and business services",
    "Education & Health Services": "Education and health services",
    "Leisure & Hospitality": "Leisure and hospitality",
    "Other Services": "Other services",
    "Government": "Government",
}

# Supersectors used for the diffusion-index breadth count (matches the page).
CES_CHART_SECTORS = [
    "Mining and logging", "Construction", "Manufacturing", "Wholesale trade",
    "Retail trade", "Transportation, warehousing and utilities", "Information",
    "Financial activities", "Professional and business services",
    "Education and health services", "Leisure and hospitality", "Other services",
    "Government",
]

# Prose names keyed by the comparative-table sector label.
SECTOR_PROSE = {
    "Mining": "mining",
    "Construction": "construction",
    "Manufacturing": "manufacturing",
    "Transportation/Utilities": "transportation &amp; utilities",
    "Wholesale Trade": "wholesale trade",
    "Retail Trade": "retail trade",
    "Information": "information",
    "Financial Activities": "financial activities",
    "Prof. & Bus. Services": "professional &amp; business services",
    "Education & Health Services": "education &amp; health",
    "Leisure & Hospitality": "leisure &amp; hospitality",
    "Other Services": "other services",
    "Government": "government",
}

# Prose names keyed by the CES supersector key (for YoY sentences).
CES_PROSE = {
    "Mining and logging": "mining",
    "Construction": "construction",
    "Manufacturing": "manufacturing",
    "Wholesale trade": "wholesale trade",
    "Retail trade": "retail trade",
    "Transportation, warehousing and utilities": "transportation &amp; utilities",
    "Information": "information",
    "Financial activities": "financial activities",
    "Professional and business services": "professional &amp; business services",
    "Education and health services": "education &amp; health",
    "Leisure and hospitality": "leisure &amp; hospitality",
    "Other services": "other services",
    "Government": "government",
}


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _sec(data, name):
    return (data.get("sections") or {}).get(name) or {}


def _pct(v, dp=1):
    """Signed percent string, e.g. +0.1% / −4.6%."""
    if v is None:
        return None
    s = f"{abs(v):.{dp}f}"
    sign = "+" if v >= 0 else "&minus;"
    return f"{sign}{s}%"


def _pct_unsigned(v, dp=1):
    if v is None:
        return None
    return f"{v:.{dp}f}%"


def _usd_k(v):
    """$X,XXX from a dollar figure."""
    if v is None:
        return None
    return "$" + f"{round(v):,}"


def _last(seq):
    if not isinstance(seq, list):
        return None
    for x in reversed(seq):
        if x is not None:
            return x
    return None


def _ces_latest(entry):
    """Latest non-null employment level (000s) for a CES sector entry."""
    if not entry:
        return None
    if entry.get("latest_value") is not None:
        return entry["latest_value"]
    return _last(entry.get("values"))


def ces_msa_shares(data):
    """{comparative-label: share% of total nonfarm} from CES. None if no CES."""
    ces = _sec(data, "ces_by_supersector").get("sectors")
    if not ces:
        return None
    total = _ces_latest(ces.get("Total nonfarm"))
    if not total:
        return None
    out = {}
    for label, ces_key in CES_SECTOR_MAP.items():
        emp = _ces_latest(ces.get(ces_key))
        if emp is not None:
            out[label] = 100.0 * emp / total
    return out or None


def ces_yoy(data):
    """{ces_key: latest_yoy} for supersectors that report one."""
    ces = _sec(data, "ces_by_supersector").get("sectors") or {}
    out = {}
    for key in CES_CHART_SECTORS:
        e = ces.get(key)
        if e and e.get("latest_yoy") is not None:
            out[key] = e["latest_yoy"]
    return out


def diffusion_latest(data):
    """(value 0-100, n_sectors) share of supersectors growing YoY in the latest
    common month, or None when fewer than 8 supersectors report (small metros)."""
    ces = _sec(data, "ces_by_supersector").get("sectors")
    if not ces:
        return None
    agg = {}  # "YYYY-MM" -> [pos, n]
    count = 0
    for key in CES_CHART_SECTORS:
        e = ces.get(key)
        if not e or not e.get("months") or not e.get("yoy_pct"):
            continue
        count += 1
        for m, y in zip(e["months"], e["yoy_pct"]):
            if y is None:
                continue
            a = agg.setdefault(m, [0.0, 0])
            a[1] += 1
            if y > 0:
                a[0] += 1
            elif y == 0:
                a[0] += 0.5
    if count < 8:
        return None
    months = [m for m in agg if agg[m][1] >= max(8, count - 2)]
    if not months:
        return None
    m = sorted(months)[-1]
    pos, n = agg[m]
    return (100.0 * pos / n, count)


def _unemployment(data):
    """(rate, 'Mon YYYY') preferring monthly LAUS, else QCEW health-check."""
    laus = _sec(data, "laus_unemployment")
    if laus.get("latest_value") is not None and laus.get("latest_month"):
        iso = str(laus["latest_month"])
        try:
            y, mm = int(iso[:4]), int(iso[5:7])
            return laus["latest_value"], f"{MONTHS[mm][:3]} {y}"
        except Exception:
            return laus["latest_value"], iso
    hc = _sec(data, "health_check")
    rate = _last(hc.get("unemployment_rate"))
    q = _last(hc.get("quarters"))
    if rate is not None:
        return rate, q
    return None, None


def _clean_trade_label(label):
    """'336--Transportation Equipment' -> 'transportation equipment'; drop residual/other."""
    if not label:
        return None
    txt = label.split("--", 1)[-1].strip()
    low = txt.lower()
    if "residual" in low or "not in top" in low or low in ("other", "all other"):
        return None
    return txt.lower()


# --------------------------------------------------------------------------- #
# narrative
# --------------------------------------------------------------------------- #
def _p(html):
    return f"      <p>{html}</p>"


def build_narrative(data):
    short = data.get("short_name", "the metro")
    secs = data.get("sections") or {}
    as_of = str(data.get("as_of", ""))
    try:
        y, mm = int(as_of[:4]), int(as_of[5:7])
        stamp = f"{MONTHS[mm]} {y}"
    except Exception:
        stamp = "latest release"

    shares = ces_msa_shares(data)
    yoy = ces_yoy(data)
    qis = _sec(data, "qcew_industry_shares")
    us = qis.get("us") or {}
    qmsa = qis.get("msa") or {}

    out = []  # list of <p> blocks

    # ---- Labor Market ------------------------------------------------------
    labor = []
    rate, when = _unemployment(data)
    if rate is not None:
        whentxt = f" ({when})" if when else ""
        labor.append(f"{short}'s unemployment rate is <strong>{rate:.1f}%</strong>{whentxt}")
    yoy_total = (_sec(data, "ces_employment").get("latest_yoy"))
    if yoy_total is None:
        yoy_total = (secs.get("ces_by_supersector", {}).get("sectors", {})
                     .get("Total nonfarm", {}) or {}).get("latest_yoy")
    if yoy_total is not None:
        src = "Current Employment Trends"
        if yoy_total >= 1.5:
            phrase = f"total nonfarm employment is expanding (<strong>{_pct(yoy_total)}</strong> YoY, {src})"
        elif yoy_total >= 0.5:
            phrase = f"total nonfarm employment is growing modestly (<strong>{_pct(yoy_total)}</strong> YoY, {src})"
        elif yoy_total > -0.5:
            phrase = f"total nonfarm employment is essentially <strong>flat year-over-year</strong> (<strong>{_pct(yoy_total)}</strong>, {src})"
        else:
            phrase = f"total nonfarm employment is <strong>contracting</strong> ({_pct(yoy_total)} YoY, {src})"
        labor.append(phrase if labor else f"{short}'s {phrase}")
    if labor:
        s = ", while ".join(labor) if len(labor) > 1 else labor[0]
        # join first two with em dash for readability
        if len(labor) >= 2:
            s = f"{labor[0]} &mdash; yet {labor[1]}"
            for extra in labor[2:]:
                s += f"; {extra}"
        sent = s + "."

        diff = diffusion_latest(data)
        if diff is not None:
            val, _n = diff
            if val >= 55:
                d = (f" Breadth is broad: the diffusion index reads <strong>{val:.0f}</strong> "
                     "(Diffusion Index), so most major industries are adding jobs.")
            elif val >= 50:
                d = (f" Breadth is positive but narrowing: the diffusion index reads "
                     f"<strong>{val:.0f}</strong> (Diffusion Index).")
            elif val >= 45:
                d = (f" Breadth has softened: the diffusion index reads <strong>{val:.0f}</strong> "
                     "(Diffusion Index), meaning slightly more major industries are shrinking than growing.")
            else:
                d = (f" Breadth has turned down: the diffusion index reads <strong>{val:.0f}</strong> "
                     "(Diffusion Index), with most major industries below year-ago employment.")
            sent += d

        # sectoral: top growers / decliners by CES YoY
        if yoy:
            growers = sorted([(k, v) for k, v in yoy.items() if v > 0.3], key=lambda kv: -kv[1])[:2]
            decliners = sorted([(k, v) for k, v in yoy.items() if v < -0.3], key=lambda kv: kv[1])[:2]
            parts = []
            if growers:
                gtxt = " and ".join(f"{CES_PROSE.get(k, k)} ({_pct(v)})" for k, v in growers)
                parts.append(f"{gtxt} {'are' if len(growers) > 1 else 'is'} still adding jobs")
            if decliners:
                dtxt = " and ".join(f"{CES_PROSE.get(k, k)} ({_pct(v)})" for k, v in decliners)
                parts.append(f"{dtxt} {'are' if len(decliners) > 1 else 'is'} contracting")
            if parts:
                sent += (" The movement is sectoral &mdash; " + ", while ".join(parts)
                         + " (Industry Employment).")
        out.append(_p(sent))

    # ---- Sector Mix --------------------------------------------------------
    if shares:
        mix = []
        # location quotients vs US
        lqs = []
        for label, msha in shares.items():
            ush = (us.get(label) or {}).get("share_pct")
            if ush and ush > 0:
                lqs.append((label, msha, ush, msha / ush))
        defining = sorted([x for x in lqs if x[3] >= 1.4 and x[1] >= 4.0],
                          key=lambda x: -x[3])[:2]
        if defining:
            dtxt = " and ".join(
                f"{SECTOR_PROSE.get(l, l)} is <strong>{m:.1f}% of employment</strong> "
                f"versus {u:.1f}% nationally"
                for (l, m, u, q) in defining)
            lqtxt = " and ".join(f"{q:.1f}" for (_l, _m, _u, q) in defining)
            mix.append(f"The metro's base is tilted toward {SECTOR_PROSE.get(defining[0][0], defining[0][0])}"
                       + (f" and {SECTOR_PROSE.get(defining[1][0], defining[1][0])}" if len(defining) > 1 else "")
                       + f": {dtxt} (Comparative Employment) &mdash; location quotients of {lqtxt}, "
                       "the metro's defining industries (Economic Drivers).")
        # largest by headcount share
        biggest = sorted(shares.items(), key=lambda kv: -kv[1])[:2]
        if biggest:
            btxt = " and ".join(f"<strong>{SECTOR_PROSE.get(l, l)} ({m:.1f}%)</strong>"
                                for l, m in biggest)
            mix.append(f"The largest sectors by headcount are {btxt}.")
        # thin white-collar spots
        thin = []
        for label in ("Prof. & Bus. Services", "Financial Activities"):
            msha = shares.get(label)
            ush = (us.get(label) or {}).get("share_pct")
            if msha is not None and ush and msha < ush - 1.0:
                thin.append(f"{SECTOR_PROSE.get(label, label)} is <strong>{msha:.1f}%</strong> "
                            f"of jobs (vs. {ush:.1f}% US)")
        if thin:
            mix.append("The thinner spots are white-collar: " + " and ".join(thin) + ".")
        # wages where QCEW MSA discloses them
        wage_bits = []
        pbs_w = (qmsa.get("Prof. & Bus. Services") or {}).get("avg_annual_wage")
        pbs_uw = (us.get("Prof. & Bus. Services") or {}).get("avg_annual_wage")
        man_w = (qmsa.get("Manufacturing") or {}).get("avg_annual_wage")
        if pbs_w and pbs_uw:
            wage_bits.append(f"professional-services pay averages <strong>{_usd_k(pbs_w)}</strong> "
                             f"a year against {_usd_k(pbs_uw)} nationally")
        if man_w:
            wage_bits.append(f"manufacturing averages <strong>{_usd_k(man_w)}</strong>")
        if wage_bits:
            mix.append("On pay, " + " while ".join(wage_bits) + " (QCEW, where disclosed).")
        if mix:
            out.append(_p(" ".join(mix)))

    # ---- Trade Exposure ----------------------------------------------------
    ex = _sec(data, "ita_msa_exports")
    total_m = ex.get("total_usd_millions")
    if total_m:
        yr = ex.get("year")
        b = total_m / 1000.0
        trade = [f"Goods exports totaled <strong>${b:.1f}B</strong>"
                 + (f" in {yr}" if yr else "") + " (Exports)"]
        gmp_b = _sec(data, "bea_gmp").get("latest_gmp_billions_usd")
        if gmp_b:
            share = 100.0 * total_m / (gmp_b * 1000.0)
            intensity = ("a high trade intensity" if share >= 18
                         else "a moderate trade intensity" if share >= 9
                         else "a modest trade intensity")
            trade[0] += f", about <strong>{share:.0f}% of gross metro product</strong> &mdash; {intensity}"
        trade[0] += "."
        # top products (suppress sub-$0.05B and residual/other labels)
        prods = []
        for item in (ex.get("by_product") or [])[:5]:
            name = _clean_trade_label(item.get("label"))
            val = item.get("value_usd_mil")
            if name and val and val >= 50:
                prods.append(f"{name} (<strong>${val/1000.0:.1f}B</strong>)")
            if len(prods) == 3:
                break
        # top real destination — preserve original case (acronyms like USMCA/EU),
        # but skip the trade-bloc aggregates that aren't single markets.
        dest = None
        for item in (ex.get("by_destination") or []):
            raw = (item.get("label") or "").split("--", 1)[-1].strip()
            low = raw.lower()
            if not raw or "residual" in low or "not in top" in low:
                continue
            if low in ("apec", "fta partners", "fta", "non-fta partners", "world total"):
                continue
            dest = raw
            break
        tail = []
        if prods:
            if len(prods) > 1:
                ptxt = ", ".join(prods[:-1]) + " and " + prods[-1]
            else:
                ptxt = prods[0]
            verb = "lead" if len(prods) > 1 else "leads"
            tail.append(f"{ptxt} {verb} the basket")
        if dest:
            tail.append(f"{dest} is the largest single destination market")
        if tail:
            s = "; ".join(tail) + "."
            trade.append(s[0].upper() + s[1:])
        out.append(_p(" ".join(trade)))

    # ---- Housing -----------------------------------------------------------
    house = []
    hpi_yoy = _sec(data, "fhfa_hpi").get("latest_yoy")
    if hpi_yoy is not None:
        if hpi_yoy >= 6:
            house.append(f"House prices are still rising briskly (<strong>{_pct(hpi_yoy)} YoY</strong>, House Price Index)")
        elif hpi_yoy >= 1:
            house.append(f"House-price growth has cooled to <strong>{_pct(hpi_yoy)} YoY</strong> (House Price Index)")
        elif hpi_yoy > -1:
            house.append(f"House prices are roughly flat (<strong>{_pct(hpi_yoy)} YoY</strong>, House Price Index)")
        else:
            house.append(f"House prices are falling (<strong>{_pct(hpi_yoy)} YoY</strong>, House Price Index)")
    val = _sec(data, "housing_valuation").get("latest_valuation_pct")
    if val is not None:
        if val > 10:
            v = f"The EIG valuation model places {short} <strong>well above</strong> its income-implied fair value ({_pct(val, 0)})"
        elif val > 3:
            v = f"The EIG valuation model places {short} <strong>modestly above</strong> income-implied fair value ({_pct(val, 0)})"
        elif val >= -3:
            v = f"The EIG valuation model places {short} <strong>on its income-implied fair value</strong> ({val:.0f}% deviation), so prices track local income fundamentals rather than running ahead of them"
        elif val >= -10:
            v = f"The EIG valuation model places {short} <strong>modestly below</strong> income-implied fair value ({_pct(val, 0)})"
        else:
            v = f"The EIG valuation model places {short} <strong>well below</strong> income-implied fair value ({_pct(val, 0)})"
        house.append(v)
    bp = _sec(data, "census_bps_permits")
    sf, mf = bp.get("latest_single"), bp.get("latest_multi")
    if sf is not None or mf is not None:
        yr = bp.get("latest_year")
        bits = []
        if sf is not None:
            bits.append(f"{sf:,} single-family")
        if mf is not None:
            bits.append(f"{mf:,} multifamily")
        house.append(f"Builders authorized roughly <strong>{' and '.join(bits)} permits</strong>"
                     + (f" in {yr}" if yr else "") + " (headline table)")
    aff = _sec(data, "housing_affordability").get("latest_index")
    if aff is not None:
        if aff >= 100:
            house.append(f"Affordability sits above breakeven: the EIG affordability index reads <strong>{aff:.0f}</strong> "
                         "(Housing Affordability) &mdash; a reading over 100 means the median household can qualify for the median home")
        else:
            house.append(f"Affordability sits below breakeven: the EIG affordability index reads <strong>{aff:.0f}</strong> "
                         "(Housing Affordability) &mdash; a reading under 100 means the median household falls short of qualifying for the median home")
    if house:
        out.append(_p(". ".join(house) + "."))

    # ---- Demographics & Migration -----------------------------------------
    pep = _sec(data, "census_pep")
    pop = pep.get("latest_population")
    pop_yoy = pep.get("latest_yoy")
    demo = []
    if pop:
        s = f"Population reached about <strong>{pop:,}</strong>"
        yr = pep.get("latest_year")
        if yr:
            s += f" ({yr})"
        if pop_yoy is not None:
            mult = ""
            if pop_yoy >= 1.0:
                mult = " &mdash; roughly 2&ndash;3&times; the US pace"
            elif pop_yoy <= 0:
                mult = " &mdash; below the US pace"
            s += f" and grew <strong>{_pct_unsigned(pop_yoy)}</strong> into the latest year (Geographic Profile){mult}"
        nm = _last(_sec(data, "census_net_migration").get("net_migration"))
        if nm is not None:
            s += f", with <strong>net migration of {'+' if nm >= 0 else '&minus;'}{abs(int(nm)):,}</strong> the main driver"
        demo.append(s + ".")
    mage = (_sec(data, "census_acs_demographics").get("values") or {}).get("median_age")
    gens = _sec(data, "acs_age_structure").get("generations") or {}
    age_bits = []
    if mage is not None:
        rel = "younger than" if mage < 38.5 else "older than" if mage > 39.5 else "near"
        age_bits.append(f"median age <strong>{mage:.1f}</strong>, {rel} the US (~39)")
    gz = gens.get("Gen Z")
    mil = gens.get("Millennial")
    if gz is not None and mil is not None:
        age_bits.append(f"Gen Z and Millennials a combined <strong>~{gz + mil:.0f}%</strong> of residents (Population by Age)")
    if age_bits:
        demo.append("The metro's age profile shows " + ", with ".join(age_bits) + ".")
    edu = (_sec(data, "census_acs_demographics").get("derived") or {}).get("pct_bachelors_or_higher")
    if edu is not None:
        rel = "above" if edu > 34 else "near" if edu > 28 else "below"
        demo.append(f"Educational attainment is {rel} the US average, with <strong>{edu:.1f}%</strong> "
                    "of adults holding a bachelor's degree or higher.")
    if demo:
        out.append(_p(" ".join(demo)))

    # ---- Inequality & Structural Position ----------------------------------
    vals = _sec(data, "census_acs_demographics").get("values") or {}
    derived = _sec(data, "census_acs_demographics").get("derived") or {}
    ineq = []
    gini = vals.get("gini_coefficient")
    if gini is not None:
        rel = "below" if gini < 0.475 else "above" if gini > 0.485 else "near"
        ineq.append(f"A Gini coefficient of <strong>{gini:.2f}</strong> sits {rel} the US average (~0.48), "
                    "pointing to broadly middling income inequality (Inequality table)")
    bgi = _sec(data, "acs_block_group_income")
    pct = bgi.get("pct")
    if isinstance(pct, list) and len(pct) >= 2:
        under50 = pct[0] + pct[1]
        frac = ("roughly a third" if under50 >= 30 else "roughly a fifth" if under50 >= 17
                else "roughly a tenth" if under50 >= 7 else "a small share")
        ineq.append(f"within the metro, {frac} of neighborhoods report a median household income under $50k")
    pov = derived.get("poverty_rate_pct")
    if pov is not None:
        rel = "above" if pov > 13.5 else "below" if pov < 11.5 else "near"
        ineq.append(f"the poverty rate of about <strong>{pov:.0f}%</strong> runs {rel} the US average")
    div = _sec(data, "industrial_diversity")
    score, ga_score = div.get("score"), div.get("ga_score")
    if score is not None:
        tail = ""
        if ga_score is not None:
            tail = (" above the Georgia average" if score > ga_score + 0.01
                    else " below the Georgia average" if score < ga_score - 0.01
                    else " near the Georgia average")
        ineq.append(f"the industrial diversity score (<strong>{score:.2f}</strong>, Industrial Structure) is{tail}")
    if ineq:
        # capitalize first, join the rest as clauses
        s = ineq[0] + ". "
        rest = ineq[1:]
        if rest:
            rest[0] = rest[0][0].upper() + rest[0][1:]
            s += "; ".join(rest) + "."
        out.append(_p(s.strip()))

    # ---- Synthesis ---------------------------------------------------------
    synth_strengths = []
    if rate is not None and rate <= 3.5:
        synth_strengths.append(f"a {rate:.1f}% unemployment rate")
    nm = _last(_sec(data, "census_net_migration").get("net_migration"))
    if nm is not None and nm > 0:
        synth_strengths.append("positive in-migration")
    if val is not None and -3 <= val <= 3:
        synth_strengths.append("on-trend home valuations")
    cr = _sec(data, "credit_score")
    if cr.get("grade") and (cr.get("outlook") in (None, "Positive", "Stable")):
        outlook = cr.get("outlook")
        s = f"an EIG credit score of {cr['grade']}"
        if outlook:
            s += f" ({outlook.lower()} outlook)"
        synth_strengths.append(s)
    synth = []
    if synth_strengths:
        synth.append(f"{short} pairs real strengths &mdash; " + ", ".join(synth_strengths) + " &mdash; ")
    # late-cycle vs expanding read
    diff = diffusion_latest(data)
    if yoy_total is not None:
        if yoy_total < 0.5 and diff is not None and diff[0] < 50:
            tail = (f"with a clear late-cycle signal: headline job growth has flattened "
                    f"({_pct(yoy_total)} YoY) and breadth has slipped below the expansion line "
                    f"(diffusion {diff[0]:.0f}).")
        elif yoy_total >= 1.0:
            tail = f"with employment still expanding ({_pct(yoy_total)} YoY)."
        else:
            tail = f"with employment growth slowing ({_pct(yoy_total)} YoY)."
        if synth:
            synth[0] = synth[0] + tail
        else:
            synth.append(f"{short}'s economy enters the latest reading {tail}")
    if synth:
        out.append(_p("".join(synth)))

    body = "\n".join(out) if out else (
        f'      <p style="font-size:13px;color:var(--ink-soft);font-style:italic;">'
        f'The live indicators required to generate a written analysis for {short} are not '
        f'currently available; the charts and tables above remain live.</p>')

    return f"""<!-- GEN:NARRATIVE -->
    <section class="analysis">
      <h2>Analysis<span class="as-of-stamp">{stamp} &middot; Generated by Economic Impact Group, LLC from the data shown on this page</span></h2>

      <p style="font-size: 12px; color: var(--ink-soft); font-style: italic; margin-bottom: 14px;">Every claim in this section is derived from a specific indicator displayed elsewhere on this page; section names in parentheses point to the source. This narrative is generated deterministically from the live data refresh shown in the as-of stamp &mdash; figures update on each refresh.</p>

{body}
    </section>
    <!-- /GEN:NARRATIVE -->"""


# --------------------------------------------------------------------------- #
# scorecard
# --------------------------------------------------------------------------- #
def _ul(items):
    return "\n".join(f"          <li>{it}</li>" for it in items)


def build_scorecard(data):
    short = data.get("short_name", "the metro")
    shares = ces_msa_shares(data)
    yoy = ces_yoy(data)
    us = _sec(data, "qcew_industry_shares").get("us") or {}
    qmsa = _sec(data, "qcew_industry_shares").get("msa") or {}

    strengths, weaknesses, upside, downside = [], [], [], []

    rate, when = _unemployment(data)
    if rate is not None:
        whentxt = f", {when}" if when else ""
        if rate <= 3.5:
            strengths.append(f"Unemployment ({rate:.1f}%{whentxt}) is low, among the tighter labor markets of Georgia's 14 metros.")
        elif rate >= 5.0:
            weaknesses.append(f"Unemployment ({rate:.1f}%{whentxt}) is elevated relative to the state's tighter metros.")

    pep = _sec(data, "census_pep")
    pop_yoy = pep.get("latest_yoy")
    nm = _last(_sec(data, "census_net_migration").get("net_migration"))
    if pop_yoy is not None and pop_yoy >= 1.0:
        s = f"Population grew {pop_yoy:.1f}% in the latest year, faster than the US pace"
        if nm is not None and nm > 0:
            s += f", on net migration of +{int(nm):,}"
        strengths.append(s + ".")
    elif pop_yoy is not None and pop_yoy <= 0:
        weaknesses.append(f"Population growth has stalled ({_pct(pop_yoy)} in the latest year).")

    # high-LQ sectors -> strengths
    if shares:
        for label, msha in sorted(shares.items(), key=lambda kv: -kv[1]):
            ush = (us.get(label) or {}).get("share_pct")
            if ush and ush > 0 and (msha / ush) >= 1.5 and msha >= 5:
                lq = msha / ush
                strengths.append(f"{SECTOR_PROSE.get(label, label).capitalize()} share "
                                 f"({msha:.1f}% of jobs) runs ~{lq:.1f}&times; the US rate ({ush:.1f}%).")
            if len([x for x in strengths]) >= 5:
                break

    div = _sec(data, "industrial_diversity")
    if div.get("score") is not None and div.get("ga_score") is not None and div["score"] > div["ga_score"]:
        strengths.append(f"Industrial diversity score ({div['score']:.2f}) is above the Georgia average.")

    # weaknesses: total employment flat/negative
    yoy_total = _sec(data, "ces_employment").get("latest_yoy")
    if yoy_total is None:
        yoy_total = (_sec(data, "ces_by_supersector").get("sectors", {}).get("Total nonfarm", {}) or {}).get("latest_yoy")
    if yoy_total is not None and yoy_total < 0.5:
        weaknesses.append(f"Headline employment growth has stalled ({_pct(yoy_total)} YoY) "
                          "despite the labor market.")

    # underweight + low-paid white collar
    if shares:
        for label in ("Prof. & Bus. Services", "Financial Activities"):
            msha = shares.get(label)
            ush = (us.get(label) or {}).get("share_pct")
            mwage = (qmsa.get(label) or {}).get("avg_annual_wage")
            uwage = (us.get(label) or {}).get("avg_annual_wage")
            if msha is not None and ush and msha < ush - 1.5:
                bit = f"{SECTOR_PROSE.get(label, label).capitalize()} is underweight ({msha:.1f}% vs {ush:.1f}% US)"
                if mwage and uwage and mwage < uwage * 0.8:
                    bit += f" and low-paid &mdash; avg {_usd_k(mwage)} vs {_usd_k(uwage)} US"
                weaknesses.append(bit + ".")

    # contracting sectors
    if yoy:
        decliners = sorted([(k, v) for k, v in yoy.items() if v < -0.5], key=lambda kv: kv[1])[:2]
        if decliners:
            dtxt = " and ".join(f"{CES_PROSE.get(k, k)} ({_pct(v)})" for k, v in decliners)
            weaknesses.append(f"{dtxt[0].upper()}{dtxt[1:]} {'are' if len(decliners) > 1 else 'is'} contracting (Industry Employment).")

    # upside
    if yoy:
        growers = sorted([(k, v) for k, v in yoy.items() if v > 1.0], key=lambda kv: -kv[1])[:2]
        if growers:
            gtxt = " and ".join(f"{CES_PROSE.get(k, k)} ({_pct(v)})" for k, v in growers)
            upside.append(f"{gtxt[0].upper()}{gtxt[1:]} {'are' if len(growers) > 1 else 'is'} still expanding (Industry Employment).")
    val = _sec(data, "housing_valuation").get("latest_valuation_pct")
    if nm is not None and nm > 0 and val is not None and -3 <= val <= 3:
        upside.append(f"Net migration remains positive (+{int(nm):,}); home prices sit on income-implied fair value (no bubble).")
    elif nm is not None and nm > 0:
        upside.append(f"Net migration remains positive (+{int(nm):,} in the latest year).")

    # downside
    diff = diffusion_latest(data)
    if diff is not None and diff[0] < 50:
        downside.append(f"Job-growth breadth has turned negative &mdash; diffusion index at {diff[0]:.0f} "
                        "(below the 50 expansion line).")
    ex = _sec(data, "ita_msa_exports")
    gmp_b = _sec(data, "bea_gmp").get("latest_gmp_billions_usd")
    if ex.get("total_usd_millions") and gmp_b:
        share = 100.0 * ex["total_usd_millions"] / (gmp_b * 1000.0)
        if share >= 15:
            downside.append(f"Tariff and global-demand sensitivity, with exports near {share:.0f}% of gross metro product.")
    if val is not None and val > 10:
        downside.append(f"Home prices sit {val:.0f}% above income-implied fair value &mdash; a valuation risk if rates stay high.")

    boxes = []
    if strengths:
        boxes.append(f'      <div class="box strengths">\n        <h3>Strengths</h3>\n        <ul>\n{_ul(strengths[:6])}\n        </ul>\n      </div>')
    if weaknesses:
        boxes.append(f'      <div class="box weaknesses">\n        <h3>Weaknesses</h3>\n        <ul>\n{_ul(weaknesses[:6])}\n        </ul>\n      </div>')
    if upside:
        boxes.append(f'      <div class="box upside">\n        <h3>Upside Risks</h3>\n        <ul>\n{_ul(upside[:4])}\n        </ul>\n      </div>')
    if downside:
        boxes.append(f'      <div class="box downside">\n        <h3>Downside Risks</h3>\n        <ul>\n{_ul(downside[:4])}\n        </ul>\n      </div>')

    if not boxes:
        boxes.append(f'      <div class="box strengths">\n        <h3>Scorecard</h3>\n        '
                     f'<p style="font-size:12px;color:var(--ink-soft);">Insufficient live indicators to '
                     f'generate a scorecard for {short}.</p>\n      </div>')

    return "<!-- GEN:SCORECARD -->\n" + "\n".join(boxes) + "\n      <!-- /GEN:SCORECARD -->"


def footer_has_school(data):
    """True when the metro has F-33 school-finance data feeding the QoL composite."""
    qol = _sec(data, "quality_of_life")
    comps = qol.get("components") or {}
    return "per_pupil_spending" in comps
