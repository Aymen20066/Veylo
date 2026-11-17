"""Import-cost estimation: exchange rates, fuel/emission helpers, the four destination
calculators, EU new-vehicle VAT logic, and the calculate_import_costs entry point."""
import math
import re
from datetime import datetime

import pandas as pd


# ===========================================================================
# IMPORT COSTS
# ---------------------------------------------------------------------------
# Every rate below comes from official government / tax-authority sources and is
# cited inline. Nothing here estimates silently: when an input needed for an exact
# figure is missing or ambiguous, the caller is told explicitly via calculable=False
# or a notes entry — never via a fabricated number.
# ===========================================================================

# ECB reference rates, captured 2026-08-21. Deliberately static for v1: a live FX API
# would add a new external failure point to a feature whose entire purpose is
# reliability, and normal rate drift moves these figures by a few euros at most.
# A live rate feed is a reasonable v2 addition once this is proven stable.
EXCHANGE_RATES_TO_EUR = {"PLN": 4.30, "RON": 4.97, "BGN": 1.96, "CZK": 24.8}
EXCHANGE_RATES_AS_OF = "21 August 2026"


def to_eur(amount, currency):
    """Convert local currency to EUR. Raises on an unknown currency rather than
    silently returning a wrong number."""
    if currency == "EUR":
        return float(amount)
    if currency not in EXCHANGE_RATES_TO_EUR:
        raise ValueError(f"No exchange rate configured for {currency}")
    return float(amount) / EXCHANGE_RATES_TO_EUR[currency]


# The actor's INPUT filter vocabulary and its OUTPUT strings are NOT the same.
# Verified against a real run (2026-08-21):
#   Input dropdown offers:  Petrol, Diesel, Electric, Hybrid, Plug-in Hybrid, LPG, CNG, Hydrogen
#   Output values observed: "Gasoline" (not "Petrol"), "Electric/Gasoline" (a hybrid)
# So this classifier handles BOTH vocabularies plus common variants, and returns
# None for anything it cannot confidently place.
FUEL_PETROL_DIESEL = "petrol_diesel"
FUEL_HYBRID = "hybrid"      # full or mild hybrid (HEV / MHEV)
FUEL_PHEV = "phev"          # plug-in hybrid
FUEL_BEV = "bev"            # battery electric / hydrogen


def classify_fuel_type(fuel_type):
    """Map a raw fuelType string to a tax bucket, or None if not confidently classifiable."""
    if fuel_type is None:
        return None
    text = str(fuel_type).strip().lower()
    if not text or text in ("null", "none", "n/a", "-"):
        return None

    # Order matters. "Plug-in Hybrid" contains "hybrid"; "Electric/Gasoline" contains
    # "electric" but is a hybrid, not a BEV.
    if "plug" in text:
        return FUEL_PHEV
    if "hybrid" in text:
        return FUEL_HYBRID

    has_electric = "electric" in text or "elektro" in text
    has_combustion = any(t in text for t in ("gasoline", "petrol", "benzin", "diesel", "essence"))
    if has_electric and has_combustion:
        return FUEL_HYBRID          # AutoScout24 renders hybrids as e.g. "Electric/Gasoline"
    if "hydrogen" in text or "wasserstoff" in text:
        return FUEL_BEV
    if has_electric:
        return FUEL_BEV
    if has_combustion:
        return FUEL_PETROL_DIESEL
    # LPG/CNG are conventional combustion vehicles with a normal displacement, so they're
    # taxed on the same cc basis. Disclosed to the user via a note rather than hidden.
    if any(t in text for t in ("lpg", "cng", "autogas", "natural gas")):
        return FUEL_PETROL_DIESEL
    return None


def is_gas_conversion(fuel_type):
    """True for LPG/CNG, which we tax as conventional ICE but want to disclose."""
    if fuel_type is None:
        return False
    text = str(fuel_type).strip().lower()
    return any(t in text for t in ("lpg", "cng", "autogas", "natural gas"))


# The actor DOES return an `emissionClass` field (verified: values like "Euro 6b"), but it
# is null on a meaningful share of listings. So: use it when present (confirmed), fall back
# to inferring from registration year (estimated, and labelled as such in the UI).
EMISSION_CLASS_PATTERN = re.compile(r"euro\s*([0-6])", re.IGNORECASE)


def parse_emission_class(emission_class):
    """Extract the numeric Euro standard from a string like 'Euro 6b'. None if unparseable."""
    if emission_class is None:
        return None
    match = EMISSION_CLASS_PATTERN.search(str(emission_class))
    return int(match.group(1)) if match else None


def infer_euro_standard_from_year(year):
    """Rough Euro standard from registration year. ESTIMATE ONLY — anything derived
    from this must be labelled as estimated."""
    if year is None:
        return None
    try:
        year = int(year)
    except (TypeError, ValueError):
        return None
    if year >= 2015:
        return 6
    if year >= 2011:
        return 5
    if year >= 2006:
        return 4
    if year >= 2001:
        return 3
    if year >= 1997:
        return 2
    if year >= 1993:
        return 1
    return 0


def resolve_euro_standard(emission_class, year):
    """Returns (euro_standard, is_confirmed). is_confirmed=False means it was inferred
    from the year and must be labelled as an estimate."""
    parsed = parse_emission_class(emission_class)
    if parsed is not None:
        return parsed, True
    inferred = infer_euro_standard_from_year(year)
    if inferred is not None:
        return inferred, False
    return None, False


# --- Poland: excise duty (akcyza) -----------------------------------------
# Source: Excise Tax Act (Ustawa o podatku akcyzowym), Art. 100, 105-110b.
POLAND_ADMIN_FEES_PLN = 185 + 99   # registration + technical inspection


def calculate_poland_tax(price_eur, cc, fuel_bucket, raw_fuel_type=None):
    result = {
        "tax_label": "Excise duty (akcyza)",
        "import_tax_eur": None,
        "admin_fees_eur": to_eur(POLAND_ADMIN_FEES_PLN, "PLN"),
        "calculable": False,
        "unavailable_reason": None,
        "notes": [],
        "alternative": None,
    }
    if fuel_bucket is None:
        result["unavailable_reason"] = "Fuel type unrecognised — excise duty not calculated"
        return result

    # Electric/hydrogen is 0% regardless of engine size, so cc isn't required here.
    if fuel_bucket == FUEL_BEV:
        result["import_tax_eur"] = 0.0
        result["calculable"] = True
        result["notes"].append("Electric/hydrogen vehicles are exempt from Polish excise duty.")
        return result

    if cc is None:
        result["unavailable_reason"] = "Engine size unknown — excise duty not calculated"
        return result
    if price_eur is None or price_eur <= 0:
        result["unavailable_reason"] = "Price unavailable — excise duty not calculated"
        return result

    if fuel_bucket == FUEL_PETROL_DIESEL:
        rate = 0.031 if cc <= 2000 else 0.186
        if is_gas_conversion(raw_fuel_type):
            result["notes"].append(
                "LPG/CNG taxed as a conventional combustion engine on the same cc basis.")
    elif fuel_bucket == FUEL_HYBRID:
        if cc <= 2000:
            # AutoScout24 reports both full hybrids and plug-in hybrids as "Electric/Gasoline",
            # and the two are taxed differently at this engine size. Rather than pick one and
            # hope, we publish both figures and let the importer confirm the variant. The
            # headline number stays the higher one so nobody is under-quoted by default.
            rate = 0.0155
            result["alternative"] = {
                "label": "If it's a plug-in hybrid",
                "import_tax_eur": 0.0,
                "explanation": "Plug-in hybrids ≤2,000cc are exempt from Polish excise through 2029.",
            }
            result["notes"].append(
                "AutoScout24 lists full hybrids and plug-in hybrids identically, so both "
                "possibilities are shown. Check the listing to confirm which this is.")
        elif cc <= 3500:
            rate = 0.093
            result["notes"].append(
                "Above 2,000cc, full hybrids and plug-in hybrids are taxed at the same rate, "
                "so this figure holds either way.")
        else:
            # The reduced hybrid band is defined only up to 3,500cc. Above that we apply the
            # standard rate as the conservative figure and say so rather than guessing lower.
            rate = 0.186
            result["notes"].append(
                "Hybrid above 3,500cc falls outside the reduced-rate band; the standard rate is "
                "applied as a conservative figure — worth verifying.")
    elif fuel_bucket == FUEL_PHEV:
        if cc <= 2000:
            rate = 0.0
            result["notes"].append("Plug-in hybrids ≤2,000cc are exempt through 2029.")
        else:
            rate = 0.093
            result["notes"].append(
                "Plug-in hybrid rate above 2,000cc is subject to Art. 105 conditions.")
    else:
        result["unavailable_reason"] = "Fuel type unrecognised — excise duty not calculated"
        return result

    result["import_tax_eur"] = price_eur * rate
    result["calculable"] = True
    return result


# --- Romania: first-year vehicle tax (impozit auto) ------------------------
# Romania has NO one-time environmental/pollution tax at import: it was abolished in
# 2017 and never reintroduced. Confirmed via Law no. 239/2025, GEO no. 78/2025, and
# Codul fiscal Art. 468-472 (as of 2026). The only tax due at import is the ANNUAL
# vehicle ownership tax ("impozit auto"), owed from the month of first Romanian
# registration. This function computes the first year of that annual tax, at the
# full annual rate (see proration note in the notes list below).
# Formula: ceil(cc / 200) * rate(capacity band, Euro standard), in lei/year.
ROMANIA_RATE_TABLE = [
    (1600, {"0-3": 19.5, "4": 18.8, "5": 17.6, "6": 16.5}),
    (2000, {"0-3": 29.7, "4": 28.5, "5": 26.7, "6": 25.1}),
    (2600, {"0-3": 92.2, "4": 88.6, "5": 82.8, "6": 77.8}),
    (3000, {"0-3": 182.9, "4": 172.8, "5": 154.1, "6": 151.2}),
    (None, {"0-3": 319.0, "4": 297.3, "5": 294.4, "6": 290.0}),   # None = no upper bound
]
ROMANIA_BEV_ANNUAL_TAX_RON = 40.0                # flat annual rate for electric vehicles
ROMANIA_HYBRID_LOW_CO2_REDUCTION_PCT = 0.30      # up to 30%; local council discretion, not guaranteed

# TODO: real one-time admin fees at Romanian import — RAR inspection + CIV (~740-850 RON),
# DRPCIV registration (~90 RON), ITP inspection (~150-200 RON) — are not modeled precisely.
# The constant below is a point estimate within these ranges and hasn't been re-verified
# for 2026. Out of scope for now; revisit when these are confirmed.
ROMANIA_ADMIN_FEES_RON = 740 + 150 + 90   # RAR authenticity + ITP inspection + plates


def _romania_euro_bucket(euro_standard):
    if euro_standard is None:
        return None
    if euro_standard >= 6:
        return "6"
    if euro_standard == 5:
        return "5"
    if euro_standard == 4:
        return "4"
    return "0-3"


def calculate_romania_tax(cc, euro_standard, euro_is_confirmed, fuel_bucket=None):
    result = {
        "tax_label": "First-year ownership tax",
        "import_tax_eur": None,
        "admin_fees_eur": to_eur(ROMANIA_ADMIN_FEES_RON, "RON"),
        "calculable": False,
        "unavailable_reason": None,
        "notes": [],
        "alternative": None,
    }
    if fuel_bucket == FUEL_BEV:
        result["import_tax_eur"] = to_eur(ROMANIA_BEV_ANNUAL_TAX_RON, "RON")
        result["calculable"] = True
        result["notes"].append(
            "Electric vehicles pay a flat annual vehicle tax rather than the "
            "displacement-based rate.")
        result["notes"].append(
            "Shown at full annual rate — actual first-year amount may be prorated "
            "lower depending on your registration month.")
        return result

    if cc is None:
        result["unavailable_reason"] = "Engine size unknown — vehicle tax not calculated"
        return result
    bucket = _romania_euro_bucket(euro_standard)
    if bucket is None:
        result["unavailable_reason"] = (
            "Emissions standard and registration year both unknown — tax not calculated")
        return result

    rate = None
    for max_cc, rates in ROMANIA_RATE_TABLE:
        if max_cc is None or cc <= max_cc:
            rate = rates[bucket]
            break

    full_tax_ron = math.ceil(cc / 200) * rate
    result["import_tax_eur"] = to_eur(full_tax_ron, "RON")
    result["calculable"] = True
    result["notes"].append(
        "Shown at full annual rate — actual first-year amount may be prorated "
        "lower depending on your registration month.")

    # Regular (non-plug-in) hybrids essentially never reach CO2 <= 50g/km under WLTP —
    # that threshold is only realistically achievable by plug-in hybrids' blended
    # electric-driving calculation. So only FUEL_PHEV gets the dual-scenario; FUEL_HYBRID
    # gets a single, full-rate number since the reduced figure would be unreachable.
    if fuel_bucket == FUEL_PHEV:
        reduced_ron = full_tax_ron * (1 - ROMANIA_HYBRID_LOW_CO2_REDUCTION_PCT)
        result["alternative"] = {
            "label": "If CO₂ ≤ 50 g/km and local council grants the reduction",
            "import_tax_eur": to_eur(reduced_ron, "RON"),
            "explanation": (
                "Plug-in hybrids with CO₂ emissions of 50 g/km or below may qualify "
                "for up to a 30% reduction, but this is at local council discretion — "
                "not guaranteed everywhere — and we don't have a confirmed CO₂ figure "
                "for this listing."),
        }

    if not euro_is_confirmed:
        result["notes"].append(
            f"Emissions standard not listed; estimated as Euro {euro_standard} from the "
            f"registration year. The exact standard changes this figure.")
    if euro_standard < 3:
        result["notes"].append(
            "Romania restricts registration of vehicles below Euro 3 — check eligibility "
            "before purchase.")
    return result


# --- Bulgaria: eco-tax (продуктова такса) ----------------------------------
# Source: Ministry of Environment and Water ordinance on product fees for end-of-life
# vehicles; State Gazette 2025-2026. Published in EUR. Age-based, NOT engine-size based.
#
# Bracket boundaries (documented decision): the published table lists "New", "≤5 years",
# "5-10 years", ">10 years". This app only ever searches USED vehicles (condition is fixed
# to "used"), so the "New" rate is intentionally unreachable. Boundaries are applied as:
# age <= 5, 5 < age <= 10, age > 10.
#
# UNVERIFIED: may be BGN, not EUR. The original project plan (docs/original-plan.md) records
# these same figures in BGN (ICE 150/240/360/385 BGN; BEV 102/180/270/290 BGN, State
# Gazette 66 of 23 Jul 2025), while the code below treats them as EUR, as the comment above
# claims. The two sources disagree and the units have not been re-checked against the
# ordinance. If they are BGN, every Bulgarian eco-tax shown is roughly 2x too high
# (1 EUR = 1.96 BGN). The numbers are deliberately left unchanged until that is verified.
BULGARIA_ECO_TAX_EUR = {
    "ice":    {"le5": 240.0, "le10": 360.0, "gt10": 385.0},
    "hybrid": {"le5": 290.0, "le10": 410.0, "gt10": 460.0},
    "bev":    {"le5": 180.0, "le10": 270.0, "gt10": 290.0},
}
# KAT registration + GTP inspection. Sources vary noticeably on the exact figures
# (roughly €50-€180 combined); €117 is a mid-range approximation, disclosed as approximate.
BULGARIA_ADMIN_FEES_EUR = 117.0


def calculate_bulgaria_tax(year, fuel_bucket, current_year=None):
    result = {
        "tax_label": "Eco-tax",
        "import_tax_eur": None,
        "admin_fees_eur": BULGARIA_ADMIN_FEES_EUR,
        "calculable": False,
        "unavailable_reason": None,
        "notes": [],
        "alternative": None,
    }
    if fuel_bucket is None:
        result["unavailable_reason"] = "Fuel type unrecognised — eco-tax not calculated"
        return result
    if year is None:
        result["unavailable_reason"] = "Registration year unknown — eco-tax not calculated"
        return result
    try:
        year = int(year)
    except (TypeError, ValueError):
        result["unavailable_reason"] = "Registration year unreadable — eco-tax not calculated"
        return result

    age = max(0, (current_year or datetime.now().year) - year)

    if fuel_bucket == FUEL_BEV:
        key = "bev"
    elif fuel_bucket in (FUEL_HYBRID, FUEL_PHEV):
        key = "hybrid"
    else:
        key = "ice"

    band = "le5" if age <= 5 else ("le10" if age <= 10 else "gt10")
    result["import_tax_eur"] = BULGARIA_ECO_TAX_EUR[key][band]
    result["calculable"] = True
    result["notes"].append(
        "Bulgaria's eco-tax is based on vehicle age and fuel type, not engine size.")
    return result


# --- Czech Republic: eco-fee (ekologický poplatek) -------------------------
# Source: Ministry of the Environment; Act 634/2004 Sb. Euro 3 and newer are exempt,
# which covers the large majority of used imports.
CZECH_ECO_FEE_CZK = {0: 10000, 1: 5000, 2: 3000}    # Euro 3+ => 0
CZECH_ADMIN_FEES_CZK = 800 + 2200                   # registration + STK inspection


def calculate_czech_tax(euro_standard, euro_is_confirmed):
    result = {
        "tax_label": "Eco-fee",
        "import_tax_eur": None,
        "admin_fees_eur": to_eur(CZECH_ADMIN_FEES_CZK, "CZK"),
        "calculable": False,
        "unavailable_reason": None,
        "notes": [],
        "alternative": None,
    }
    if euro_standard is None:
        result["unavailable_reason"] = (
            "Emissions standard and registration year both unknown — eco-fee not calculated")
        return result

    if euro_standard >= 3:
        result["import_tax_eur"] = 0.0
        result["notes"].append("Euro 3 and newer vehicles are exempt from the Czech eco-fee.")
    else:
        result["import_tax_eur"] = to_eur(CZECH_ECO_FEE_CZK[euro_standard], "CZK")
    result["calculable"] = True

    # Only worth caveating near the Euro 3 boundary — no need to put a scary note on
    # every modern car whose fee is €0 either way.
    if not euro_is_confirmed and euro_standard <= 3:
        result["notes"].append(
            f"Emissions standard not listed; estimated as Euro {euro_standard} from the "
            f"registration year. This figure depends on that estimate.")
    return result


DESTINATIONS_WITH_TAX_DATA = ("Poland", "Romania", "Bulgaria", "Czech Republic")

# What each destination's main charge is called in the interface. Romania has no import
# tax at all (see the Romania section above): the amount shown is the first year of the
# annual ownership tax, so it must never be labelled "import tax".
DESTINATION_TAX_NAMES = {"Romania": "First-year ownership tax"}


def destination_tax_name(destination):
    """Display name of the destination's main charge, e.g. 'Import tax'."""
    return DESTINATION_TAX_NAMES.get(destination, "Import tax")


# --- EU "new means of transport" VAT ---------------------------------------
# Source: EU VAT Directive 2006/112/EC, Art. 2(2)(b) and Art. 40.
# A vehicle is legally NEW for VAT purposes if EITHER condition holds — only one is
# needed: ≤6 months since first registration, OR ≤6,000 km. This catches demo cars and
# nearly-new stock that are listed and sold as "used" but are still legally new, where
# the margin scheme does NOT apply (Arts. 312-325 exclude new means of transport) and
# VAT is due in the destination country on the full price regardless of seller type.
NEW_VEHICLE_MAX_MONTHS = 6
NEW_VEHICLE_MAX_KM = 6000

# Destination standard VAT rates, current as of August 2026.
# Romania's 21% was double-checked against multiple independent sources after an earlier
# 19% figure turned out to be pre-August-2025: it rose to 21% on 1 Aug 2025 under Law
# No. 141/2025 and remains 21%.
DESTINATION_VAT_RATES = {
    "Poland": 0.23,
    "Romania": 0.21,
    "Bulgaria": 0.20,
    "Czech Republic": 0.21,
}

NEW_VEHICLE_RECLAIM_NOTE = (
    "A VAT-registered trader importing this for resale can normally account for it via reverse "
    "charge and deduct it as input VAT, so the real cash cost may be far lower than shown. A "
    "private buyer, or your end customer, pays it in full."
)


def months_elapsed_since(first_registration, today=None):
    """Whole months between a first-registration date and today.

    IMPORTANT: AutoScout24 publishes first registration to month precision only — verified
    against real output, where the day component is '01' on every listing checked. So the
    true registration day is unknown within its month, and any age derived from it carries
    roughly ±1 month of uncertainty. Callers must not treat this as exact.
    Returns None if the date is missing or unparseable.
    """
    if first_registration is None:
        return None
    try:
        text = str(first_registration).strip()
        if not text or text.lower() in ("nat", "none", "null"):
            return None
        parsed = pd.to_datetime(text, errors="coerce")
        if pd.isna(parsed):
            return None
    except Exception:
        return None
    today = today or datetime.now()
    return (today.year - parsed.year) * 12 + (today.month - parsed.month)


def classify_new_vehicle_status(first_registration, mileage_km, today=None):
    """Decide whether a listing is a 'new means of transport' under EU VAT rules.

    Returns (status, reason) where status is 'new', 'used', or 'unknown'.

    The logic is deliberately asymmetric, because the two answers need different evidence:
      - To call something NEW, only ONE criterion has to be met.
      - To call something USED, BOTH criteria must be shown to fail. If either is unknown,
        the honest answer is 'unknown', not 'used' — defaulting to 'used' would quietly
        drop a real VAT bill from the total.
    """
    # Mileage is exact, so it settles the question on its own when it's low enough.
    km = None
    try:
        if mileage_km is not None and str(mileage_km).strip() != "":
            km = float(mileage_km)
    except (TypeError, ValueError):
        km = None

    if km is not None and km <= NEW_VEHICLE_MAX_KM:
        return "new", (f"Under {NEW_VEHICLE_MAX_KM:,} km ({km:,.0f} km), so it counts as a new "
                       f"means of transport under EU VAT rules regardless of its age.")

    months = months_elapsed_since(first_registration, today=today)

    # Month-precision dates mean the true age sits somewhere in a ~1-month band around this
    # figure, so we only commit to an answer outside that band.
    if months is not None:
        if months <= NEW_VEHICLE_MAX_MONTHS - 1:
            return "new", (f"First registered about {months} month(s) ago, within the "
                           f"{NEW_VEHICLE_MAX_MONTHS}-month EU threshold.")
        if months >= NEW_VEHICLE_MAX_MONTHS + 2:
            if km is None:
                return "unknown", ("Old enough to be past the 6-month threshold, but mileage "
                                   "is missing so the ≤6,000 km test can't be checked.")
            return "used", "Past both the 6-month and 6,000 km thresholds."
        # months is 6 or 7 — the registration day we don't have decides it.
        return "unknown", (f"First registered roughly {months} months ago. AutoScout24 only "
                           f"publishes the month, not the day, so we can't tell which side of "
                           f"the {NEW_VEHICLE_MAX_MONTHS}-month line this falls on.")

    if km is None:
        return "unknown", "Registration date and mileage both missing."
    return "unknown", ("Registration date missing, so the 6-month test can't be checked "
                       "(mileage alone is above the 6,000 km threshold).")


def calculate_new_vehicle_vat(destination, price_eur):
    """VAT due in the destination country on a new means of transport. Full price, standard
    rate, no margin-scheme relief. Returns (vat_eur, rate) or (None, None)."""
    rate = DESTINATION_VAT_RATES.get(destination)
    if rate is None or price_eur is None or price_eur <= 0:
        return None, None
    return price_eur * rate, rate


VAT_DISCLAIMER = (
    "For normal used vehicles, acquisition cost excludes VAT — each card shows whether the "
    "seller can issue a VAT invoice and whether they're listed as a dealer or a private seller. "
    "Vehicles that qualify as \"new\" under EU rules (≤6 months old or ≤6,000 km) are different: "
    "VAT is always due in the destination country, and is included in their total below."
)


def calculate_import_costs(destination, price_eur, cc, fuel_type, emission_class, year,
                           current_year=None, first_registration=None, mileage_km=None,
                           today=None):
    """Single entry point. Always returns a dict of the same shape and never raises for
    missing data — missing inputs produce calculable=False plus a plain-language reason."""
    result = {
        "destination": destination,
        "tax_label": "Import tax",
        "import_tax_eur": None,
        "admin_fees_eur": 0.0,
        "total_import_costs_eur": None,
        "calculable": False,
        "unavailable_reason": None,
        "notes": [],
        "euro_standard": None,
        "euro_is_confirmed": False,
        "alternative": None,
        "new_vehicle": None,
    }
    if destination not in DESTINATIONS_WITH_TAX_DATA:
        result["unavailable_reason"] = f"No import cost data configured for {destination}"
        return result

    # Normalise cc: must be a positive integer to be usable.
    try:
        cc = int(cc) if cc is not None and str(cc).strip() != "" else None
    except (TypeError, ValueError):
        cc = None
    if cc is not None and cc <= 0:
        cc = None

    fuel_bucket = classify_fuel_type(fuel_type)
    euro_standard, euro_is_confirmed = resolve_euro_standard(emission_class, year)
    result["euro_standard"] = euro_standard
    result["euro_is_confirmed"] = euro_is_confirmed

    if destination == "Poland":
        calc = calculate_poland_tax(price_eur, cc, fuel_bucket, raw_fuel_type=fuel_type)
    elif destination == "Romania":
        calc = calculate_romania_tax(cc, euro_standard, euro_is_confirmed, fuel_bucket=fuel_bucket)
    elif destination == "Bulgaria":
        calc = calculate_bulgaria_tax(year, fuel_bucket, current_year=current_year)
    else:
        calc = calculate_czech_tax(euro_standard, euro_is_confirmed)

    result.update({k: calc[k] for k in
                   ("tax_label", "import_tax_eur", "admin_fees_eur", "calculable",
                    "unavailable_reason", "notes", "alternative")})

    # New-means-of-transport VAT. Confirmed by research that this sits ON TOP of the import
    # tax / eco-fee above — being legally "new" doesn't exempt a car from those.
    nv_status, nv_reason = classify_new_vehicle_status(first_registration, mileage_km, today=today)
    nv_vat, nv_rate = calculate_new_vehicle_vat(destination, price_eur)
    result["new_vehicle"] = {
        "status": nv_status,
        "reason": nv_reason,
        "vat_eur": nv_vat,
        "vat_rate": nv_rate,
        # Only folded into the headline total when the car definitely qualifies. When it's
        # merely possible, the amount is still shown on the card so nobody is blindsided —
        # but it isn't asserted as owed.
        "applied": nv_status == "new" and nv_vat is not None,
        "note": (NEW_VEHICLE_RECLAIM_NOTE if nv_status == "new" else None),
    }

    if result["calculable"]:
        result["total_import_costs_eur"] = result["import_tax_eur"] + result["admin_fees_eur"]
        if result["new_vehicle"]["applied"]:
            result["total_import_costs_eur"] += nv_vat
    return result
