"""Inject shared chrome (head assets, header/nav, footer) into every page.

Phase 5, WS1. Pure-static output: each page keeps its own page-specific
<head> (title/description, any map libs) and <main> content; the shared bits
live in partials/ and are stamped into the page through GEN-marker regions —
the same mechanism scripts/generate_msa_pages.py already uses for report bodies.

A page opts in by containing the marker pair(s):

    <!-- GEN:HEAD -->...<!-- /GEN:HEAD -->       (in <head>)
    <!-- GEN:HEADER -->...<!-- /GEN:HEADER -->   (top of <body>)
    <!-- GEN:FOOTER -->...<!-- /GEN:FOOTER -->   (bottom of <body>)

Re-running is idempotent: the region BETWEEN the markers is replaced with the
current partial. Pages without a given marker are left untouched, so migration
is incremental and safe.

Ordering note for CI: run AFTER scripts/generate_msa_pages.py (which builds the
metro pages from the Savannah template) so the freshly-generated pages also get
the shared chrome stamped in.

Usage:
  python scripts/build_site.py            # stamp all pages
  python scripts/build_site.py msa/atlanta/index.html   # one or more specific pages
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PARTIALS = ROOT / "partials"

REGIONS = {
    "HEAD":   PARTIALS / "head.html",
    "HEADER": PARTIALS / "header.html",
    "FOOTER": PARTIALS / "footer.html",
}


def _partial(name: str) -> str:
    return REGIONS[name].read_text().rstrip("\n")


def _replace_region(html: str, name: str, body: str) -> tuple[str, bool]:
    """Replace the content between <!-- GEN:name --> and <!-- /GEN:name -->.
    Returns (new_html, replaced?). Leaves the page untouched if the marker is absent."""
    pat = re.compile(rf"(<!-- GEN:{name} -->).*?(<!-- /GEN:{name} -->)", re.S)
    if not pat.search(html):
        return html, False
    return pat.sub(lambda _m: f"<!-- GEN:{name} -->\n{body}\n<!-- /GEN:{name} -->", html), True


def stamp(path: Path) -> list[str]:
    html = path.read_text()
    applied = []
    for name in REGIONS:
        html, did = _replace_region(html, name, _partial(name))
        if did:
            applied.append(name)
    if applied:
        path.write_text(html)
    return applied


def iter_pages():
    for p in ROOT.rglob("index.html"):
        if "/.git/" in str(p) or "/partials/" in str(p):
            continue
        yield p


def main(argv: list[str]) -> int:
    targets = [ROOT / a for a in argv] if argv else list(iter_pages())
    total = 0
    for p in targets:
        applied = stamp(p)
        if applied:
            total += 1
            print(f"  stamped {p.relative_to(ROOT)}: {', '.join(applied)}")
    print(f"build_site: stamped {total} page(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
