"""EIG composite + forecast models for the Metro Economic Profile report.

Each module here is a *computed* section that derives its output from data
already fetched by scripts/reporting/. Modeling runners receive the orchestrator's
in-progress output dict (with current-run data and any stale-fallback values)
and return a single computed section payload.

Modules (this directory grows over Phase 2):
    business_cycle_index    Stock-Watson coincident index (employment + unemployment)
    forecast_arima          ARIMA per series → 2026F–2030F columns           [planned]
    vitality                Z-score composite (LFPR + earnings + ...)        [planned]
    quality_of_life         ACS + EPA + UCR composite                        [planned]
    housing_valuation       FHFA HPI residual vs. local fundamentals          [planned]
    business_costs          BLS RPP + Tax Foundation + commercial rent       [planned]
    credit_score            EMMA + EIG fiscal-strength composite              [planned]
"""
