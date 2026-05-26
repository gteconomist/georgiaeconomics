"""Per-source fetchers for Metro Economic Profile reports.

Each module exposes one or more `fetch_*` functions that:
  - take a CBSA code (string, 5-digit OMB)
  - return a JSON-serialisable dict (or None on hard failure)
  - never raise on transient errors — log and return None

The orchestrator (../fetch_msa_report.py) calls these in sequence and
assembles them into /data/msa_reports/<slug>.json.
"""
