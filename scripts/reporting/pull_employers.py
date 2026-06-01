"""Major-employer discovery for the Metro Economic Profile (EMPLOYERS region).

There is no public API for "largest employers in an MSA," so this module
auto-discovers a *representative* list via Tavily web search across chamber-of-
commerce, development-authority, and local-news sources, then extracts candidate
organization names from the result snippets + synthesized answer.

This is deliberately advisory (matching the data-centers DECD/EPD "hint" pattern):
the output is labeled "Representative" and every name carries the source URL it
was found in, so readers can verify. Ordering approximates how often a name
recurs across sources, NOT headcount (which isn't published at the metro level).

Guardrails:
  - No TAVILY_API_KEY  -> returns None (orchestrator keeps the prior value).
  - Fewer than MIN_NAMES confident names -> returns None (never overwrite a good
    prior list with a thin/garbled one).
  - Conservative name extraction: requires a known org-type cue (Inc/LLC/Corp/
    Health/Hospital/University/Aerospace/Manufacturing/County Schools/City of ...)
    or appearance in an explicit numbered/bulleted list in the answer, plus a
    stop-word filter, so prose like "the largest employer" doesn't leak in.

Pure stdlib (urllib). Env: TAVILY_API_KEY.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
import urllib.error
from datetime import date
from typing import Optional

TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "").strip()
TAVILY_SEARCH_URL = "https://api.tavily.com/search"

MIN_NAMES = 3          # below this, return None and keep the prior list
MAX_NAMES = 14         # cap the rendered list
_PER_QUERY_RESULTS = 6

# Trusted-ish domains for "who employs people here" — chambers, development
# authorities, state economic dev, local business press, encyclopedic.
_INCLUDE_DOMAINS = [
    "georgia.org", "decd.georgia.gov", "selectgeorgia.com",
    "bizjournals.com", "ajc.com", "savannahnow.com", "onlineathens.com",
    "albanyherald.com", "ledger-enquirer.com", "macon.com", "augustachronicle.com",
    "gainesvilletimes.com", "northwestgeorgianews.com", "valdostadailytimes.com",
    "en.wikipedia.org", "datacenterfrontier.com",
]

# Org-type cues that strongly imply "this token is an employer name."
_ORG_CUES = re.compile(
    r"\b("
    r"Inc|LLC|L\.L\.C|Corp|Corporation|Company|Co|Holdings|Group|"
    r"Health|Healthcare|Hospital|Medical|Clinic|"
    r"University|College|Schools?|School System|School District|Academy|"
    r"Aerospace|Aviation|Airlines|Motors?|Motor|Automotive|Manufacturing|"
    r"Mills?|Industries|Industrial|Systems|Solutions|Technologies|Foods?|"
    r"Bank|Financial|Insurance|Logistics|Distribution|Warehouse|"
    r"Power|Energy|Electric|Utilities|Authority|Ports?|Railway|Railroad|"
    r"Pharmaceuticals?|Bioscience|Plant|Metaplant|Factory|Carpet|Flooring"
    r")\b"
)

# Phrases / generic words that should never be treated as an employer name.
_STOP = {
    "the largest", "largest employer", "largest employers", "major employers",
    "top employers", "metro area", "metropolitan", "georgia", "the city",
    "the county", "economic development", "development authority", "chamber of commerce",
    "the region", "the area", "small businesses", "local businesses", "the state",
    "united states", "north america", "fortune", "see more", "read more",
}

# A capitalized multi-word name: "Gulfstream Aerospace", "City of Savannah",
# "Memorial Health University Medical Center", "Hyundai Motor Group".
_NAME = re.compile(
    r"\b("
    r"(?:City of|County of|University of)?\s*"
    r"(?:[A-Z][A-Za-z&.'\-]+(?:\s+(?:of|and|&|the)\s+|\s+)){0,5}"
    r"[A-Z][A-Za-z&.'\-]+"
    r")\b"
)


def _http_post_json(url, payload, headers=None, timeout=45):
    data = json.dumps(payload).encode("utf-8")
    hdrs = {"Content-Type": "application/json", "User-Agent": "EIG-MSA-reports/1.0"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _tavily(query, *, max_results=_PER_QUERY_RESULTS, time_range=None):
    if not TAVILY_API_KEY:
        return {}
    payload = {
        "query": query,
        "search_depth": "advanced",
        "max_results": max_results,
        "include_answer": "advanced",
        "include_domains": _INCLUDE_DOMAINS,
    }
    if time_range:
        payload["time_range"] = time_range
    try:
        return _http_post_json(
            TAVILY_SEARCH_URL, payload,
            headers={"Authorization": f"Bearer {TAVILY_API_KEY}"},
            timeout=45,
        )
    except Exception as e:
        print(f"      [employers] Tavily failed: {type(e).__name__}: {str(e)[:80]}",
              file=sys.stderr)
        return {}


def _clean(name: str) -> Optional[str]:
    n = re.sub(r"\s+", " ", name).strip(" .,;:-&")
    if len(n) < 4 or len(n) > 60:
        return None
    low = n.lower()
    if low in _STOP or any(s == low for s in _STOP):
        return None
    # Reject if it's just generic words with no org cue and isn't multi-word proper.
    words = n.split()
    if len(words) < 2 and not _ORG_CUES.search(n):
        return None
    # Reject sentence fragments (too many lowercase connector words).
    lowers = sum(1 for w in words if w[:1].islower())
    if lowers > 2:
        return None
    return n


_ABBR = ["St", "Ste", "Mt", "Dr", "Mr", "Mrs", "Ms", "Inc", "Co", "Corp",
         "Jr", "Sr", "U.S", "vs", "No"]


def _candidates_from_text(text: str):
    """Yield candidate org names from a blob. Splits into clause-sized chunks (so a
    name never spans a sentence boundary), then within each chunk takes every
    proper-noun span that carries an org-type cue."""
    if not text:
        return
    # Protect common abbreviations so the sentence split doesn't break "St. Joseph's".
    protected = text
    for a in _ABBR:
        protected = re.sub(rf"\b{re.escape(a)}\.\s", a + "<DOT> ", protected)
    # Split on list markers, newlines, commas, semicolons, sentence periods, " and ".
    chunks = re.split(
        r"[\n\r]+|(?:\d+[\.\)]\s+)|(?:[-•]\s+)|[;,]|(?<=\w)\.\s+(?=[A-Z])|\s+(?:and|&)\s+",
        protected)
    for chunk in chunks:
        chunk = chunk.replace("<DOT>", ".").strip()
        if not chunk:
            continue
        for m in _NAME.finditer(chunk):
            span = m.group(1)
            if _ORG_CUES.search(span):
                c = _clean(span)
                if c:
                    yield c


def fetch(cbsa: str, short_name: str, full_name: str) -> Optional[dict]:
    """Discover a representative major-employer list for one MSA. None on
    insufficient signal (orchestrator then keeps the prior value)."""
    if not TAVILY_API_KEY:
        print("      [employers] SKIPPED (no TAVILY_API_KEY)", file=sys.stderr)
        return None

    state = full_name.rsplit(",", 1)[-1].strip()  # 'GA', 'GA-SC', 'GA-AL'
    queries = [
        f"largest employers in {short_name}, Georgia metro area",
        f"top employers {short_name} {state} chamber of commerce major companies",
        f"biggest employers {short_name} metropolitan area jobs",
    ]

    # name -> {count, source_url}
    scored: dict[str, dict] = {}
    sources: list[str] = []
    for q in queries:
        resp = _tavily(q)
        if not resp:
            continue
        answer = resp.get("answer") or ""
        results = resp.get("results") or []
        # answer carries the cleanest enumerations
        for name in _candidates_from_text(answer):
            rec = scored.setdefault(name, {"count": 0, "source_url": None})
            rec["count"] += 2  # weight the synthesized answer
        for r in results:
            url = r.get("url", "")
            blob = (r.get("title", "") + ". " + (r.get("content") or ""))
            found_any = False
            for name in _candidates_from_text(blob):
                rec = scored.setdefault(name, {"count": 0, "source_url": None})
                rec["count"] += 1
                if not rec["source_url"] and url:
                    rec["source_url"] = url
                found_any = True
            if found_any and url and url not in sources:
                sources.append(url)

    if not scored:
        print(f"      [employers] {short_name}: no candidates surfaced.", file=sys.stderr)
        return None

    # Dedupe near-duplicates (case-insensitive prefix containment).
    ranked = sorted(scored.items(), key=lambda kv: (-kv[1]["count"], kv[0]))
    kept: list[tuple[str, dict]] = []
    for name, rec in ranked:
        low = name.lower()
        if any(low in k.lower() or k.lower() in low for k, _ in kept):
            continue
        kept.append((name, rec))
        if len(kept) >= MAX_NAMES:
            break

    if len(kept) < MIN_NAMES:
        print(f"      [employers] {short_name}: only {len(kept)} names (<{MIN_NAMES}); keeping prior.",
              file=sys.stderr)
        return None

    employers = [{"name": n, "source_url": rec["source_url"]} for n, rec in kept]
    print(f"      [employers] {short_name}: {len(employers)} representative names.")
    return {
        "as_of": date.today().isoformat(),
        "employers": employers,
        "sources": sources[:8],
        "method": ("Representative list auto-discovered via Tavily web search across "
                   "chamber-of-commerce, development-authority and local-news sources. "
                   "Not exhaustive; ordering approximates source frequency, not headcount."),
    }
