"""Canonical list of Georgia's 14 Core-Based Statistical Areas (MSAs).

CBSA codes are stable OMB identifiers. Some GA MSAs cross state lines —
Augusta extends into SC; Columbus extends into AL. We carry the full multi-state
MSA totals (BLS/Census don't easily split MSAs back to state-only).
"""

GA_MSAS = [
    # (cbsa_code, short_name, full_name, approx_population_2024)
    ("12060", "Atlanta",      "Atlanta-Sandy Springs-Alpharetta, GA",        6307261),
    ("12260", "Augusta",      "Augusta-Richmond County, GA-SC",               611000),
    ("42340", "Savannah",     "Savannah, GA",                                 410000),
    ("17980", "Columbus",     "Columbus, GA-AL",                              329000),
    ("31420", "Macon",        "Macon-Bibb County, GA",                        232000),
    ("12020", "Athens",       "Athens-Clarke County, GA",                     217000),
    ("23580", "Gainesville",  "Gainesville, GA",                              211000),
    ("47580", "Warner Robins","Warner Robins, GA",                            199000),
    ("46660", "Valdosta",     "Valdosta, GA",                                 148000),
    ("10500", "Albany",       "Albany, GA",                                   146000),
    ("19140", "Dalton",       "Dalton, GA",                                   143000),
    ("15260", "Brunswick",    "Brunswick, GA",                                120000),
    ("40660", "Rome",         "Rome, GA",                                      99000),
    ("25980", "Hinesville",   "Hinesville, GA",                                78000),
]

assert len(GA_MSAS) == 14, f"Expected 14 GA MSAs, got {len(GA_MSAS)}"
