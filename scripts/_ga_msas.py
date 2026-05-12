"""Canonical list of Georgia's 14 Core-Based Statistical Areas (MSAs).

CBSA codes are stable OMB identifiers. Some GA MSAs cross state lines —
Augusta extends into SC; Columbus extends into AL. We carry the full multi-state
MSA totals (BLS/Census don't easily split MSAs back to state-only).

Also exports COUNTY_TO_MSA: a 5-digit county FIPS → CBSA code map for the
~76 counties that make up GA's 14 MSAs (including the 3 border-state counties:
Aiken SC, Edgefield SC, Russell AL). Used for aggregating county-level data
(e.g. BEA county GDP) up to the MSA level — needed because BEA's Regional API
does NOT expose MSA-level GDP as its own table.

OMB 2023 delineations (effective July 2023). Stable: OMB revises every ~5-10 yrs.
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


# County FIPS (5-digit: state + county) → CBSA code.
# Source: OMB 2023 delineation file (list1_2023.xlsx, July 2023).
# Includes 73 GA counties + 3 border counties (2 SC, 1 AL).
# The other 86 GA counties are micropolitan or non-metro and not in this map.
COUNTY_TO_MSA = {
    # Atlanta-Sandy Springs-Alpharetta, GA (29 counties)
    "13013": "12060",  # Barrow
    "13015": "12060",  # Bartow
    "13035": "12060",  # Butts
    "13045": "12060",  # Carroll
    "13057": "12060",  # Cherokee
    "13063": "12060",  # Clayton
    "13067": "12060",  # Cobb
    "13077": "12060",  # Coweta
    "13085": "12060",  # Dawson
    "13089": "12060",  # DeKalb
    "13097": "12060",  # Douglas
    "13113": "12060",  # Fayette
    "13117": "12060",  # Forsyth
    "13121": "12060",  # Fulton
    "13135": "12060",  # Gwinnett
    "13143": "12060",  # Haralson
    "13149": "12060",  # Heard
    "13151": "12060",  # Henry
    "13159": "12060",  # Jasper
    "13171": "12060",  # Lamar
    "13199": "12060",  # Meriwether
    "13211": "12060",  # Morgan
    "13217": "12060",  # Newton
    "13223": "12060",  # Paulding
    "13227": "12060",  # Pickens
    "13231": "12060",  # Pike
    "13247": "12060",  # Rockdale
    "13255": "12060",  # Spalding
    "13297": "12060",  # Walton

    # Augusta-Richmond County, GA-SC (5 GA + 2 SC = 7 counties)
    "13033": "12260",  # Burke, GA
    "13073": "12260",  # Columbia, GA
    "13181": "12260",  # Lincoln, GA
    "13189": "12260",  # McDuffie, GA
    "13245": "12260",  # Richmond, GA
    "45003": "12260",  # Aiken, SC
    "45037": "12260",  # Edgefield, SC

    # Savannah, GA (3 counties)
    "13029": "42340",  # Bryan
    "13051": "42340",  # Chatham
    "13103": "42340",  # Effingham

    # Columbus, GA-AL (6 GA + 1 AL = 7 counties)
    "13053": "17980",  # Chattahoochee, GA
    "13145": "17980",  # Harris, GA
    "13197": "17980",  # Marion, GA
    "13215": "17980",  # Muscogee, GA
    "13259": "17980",  # Stewart, GA
    "13263": "17980",  # Talbot, GA
    "01113": "17980",  # Russell, AL

    # Macon-Bibb County, GA (5 counties)
    "13021": "31420",  # Bibb
    "13079": "31420",  # Crawford
    "13169": "31420",  # Jones
    "13207": "31420",  # Monroe
    "13289": "31420",  # Twiggs

    # Athens-Clarke County, GA (4 counties)
    "13059": "12020",  # Clarke
    "13195": "12020",  # Madison
    "13219": "12020",  # Oconee
    "13221": "12020",  # Oglethorpe

    # Gainesville, GA (1 county)
    "13139": "23580",  # Hall

    # Warner Robins, GA (3 counties)
    "13153": "47580",  # Houston
    "13225": "47580",  # Peach
    "13235": "47580",  # Pulaski

    # Valdosta, GA (4 counties)
    "13027": "46660",  # Brooks
    "13101": "46660",  # Echols
    "13173": "46660",  # Lanier
    "13185": "46660",  # Lowndes

    # Albany, GA (5 counties)
    "13007": "10500",  # Baker
    "13095": "10500",  # Dougherty
    "13177": "10500",  # Lee
    "13273": "10500",  # Terrell
    "13321": "10500",  # Worth

    # Dalton, GA (2 counties)
    "13213": "19140",  # Murray
    "13313": "19140",  # Whitfield

    # Brunswick, GA (3 counties)
    "13025": "15260",  # Brantley
    "13127": "15260",  # Glynn
    "13191": "15260",  # McIntosh

    # Rome, GA (1 county)
    "13115": "40660",  # Floyd

    # Hinesville, GA (2 counties)
    "13179": "25980",  # Liberty
    "13183": "25980",  # Long
}

assert len(COUNTY_TO_MSA) == 76, f"Expected 76 county→MSA entries, got {len(COUNTY_TO_MSA)}"

# Sanity-check: every CBSA in the map must appear in GA_MSAS
_known_cbsas = {c for c, *_ in GA_MSAS}
_mapped_cbsas = set(COUNTY_TO_MSA.values())
assert _mapped_cbsas == _known_cbsas, (
    f"County→MSA map references unknown CBSAs: {_mapped_cbsas - _known_cbsas}; "
    f"or misses MSAs: {_known_cbsas - _mapped_cbsas}"
)
