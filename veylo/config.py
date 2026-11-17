"""Constants and environment loading. Nothing here talks to the network or to Streamlit."""
import os
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()
APIFY_TOKEN = os.getenv("APIFY_API_TOKEN")
ACTOR_ID = "Dckez3uXbF6dh6dMe"

# Damage/salvage/pricing-caveat title screening. gemini-2.5-flash-lite
# is retired (confirmed via a live 404 pointing at this replacement — see docs/experiments.md
# TEST H); this is Google's current cheapest/fastest model.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
# Overridable via the GEMINI_MODEL environment variable, since Google retires model names.
GEMINI_MODEL = os.getenv("GEMINI_MODEL") or "gemini-3.5-flash-lite"

SHIPPING_DISTANCES = {
    "Poland": {
        "Warsaw": {"Germany": 575, "Belgium": 1150, "France": 1450, "Netherlands": 1150, "Italy": 1700},
        "Krakow": {"Germany": 650, "Belgium": 1250, "France": 1550, "Netherlands": 1250, "Italy": 1600},
        "Gdansk": {"Germany": 500, "Belgium": 1100, "France": 1400, "Netherlands": 1050, "Italy": 1750},
        # Germany is 850, not the Berlin–Warsaw 575: listings are spread across all of
        # Germany, so the country average has to be too.
        "default": {"Germany": 850, "Belgium": 1150, "France": 1450, "Netherlands": 1150, "Italy": 1700},
    },
    "Romania": {
        "Bucharest": {"Germany": 1600, "Belgium": 2100, "France": 2400, "Netherlands": 2100, "Italy": 1300},
        "Cluj": {"Germany": 1400, "Belgium": 1900, "France": 2200, "Netherlands": 1900, "Italy": 1100},
        "default": {"Germany": 1600, "Belgium": 2100, "France": 2400, "Netherlands": 2100, "Italy": 1300},
    },
    "Bulgaria": {
        "Sofia": {"Germany": 1800, "Belgium": 2300, "France": 2600, "Netherlands": 2300, "Italy": 1500},
        "default": {"Germany": 1800, "Belgium": 2300, "France": 2600, "Netherlands": 2300, "Italy": 1500},
    },
    "Czech Republic": {
        "Prague": {"Germany": 350, "Belgium": 900, "France": 1200, "Netherlands": 900, "Italy": 1200},
        "Brno": {"Germany": 400, "Belgium": 950, "France": 1250, "Netherlands": 950, "Italy": 1150},
        "default": {"Germany": 350, "Belgium": 900, "France": 1200, "Netherlands": 900, "Italy": 1200},
    },
}

# Transport estimate: max(TRANSPORT_MINIMUM_EUR, TRANSPORT_FIXED_EUR + rate_per_km × km),
# for one car on a shared open transporter, door-to-door. A fixed part plus a per-km
# part is how carriers actually price; plain distance × €0.20/km came out 2–4× too low.
# Calibrated on the Clicktrans 2024 marketplace report (average Germany→Poland order
# 1,826 zł ≈ €430) and carrier price lists (2025–2026), where real quotes range from
# about €100 + €0.30/km to €250 + €0.60/km.
TRANSPORT_FIXED_EUR = 150
TRANSPORT_MINIMUM_EUR = 300
TRANSPORT_RATE_PER_KM_DEFAULT = 0.40

COUNTRIES = ["Germany", "Belgium", "France", "Netherlands", "Italy"]
COUNTRY_CODES = {"Germany": "DE", "Belgium": "BE", "France": "FR", "Netherlands": "NL", "Italy": "IT"}

# AutoScout24's own country codes for its `cy=` search-URL parameter — confirmed different
# from the ISO codes above (Germany is "D" not "DE"). Verified live with a capped maxResults=3
# test run before being wired in (cy=D,B,F,NL,I returned IT/DE listings, all within the
# requested price/year/mileage bounds) — not assumed from AutoScout24's public docs.
AS24_CY_CODES = {"Germany": "D", "Belgium": "B", "France": "F", "Netherlands": "NL", "Italy": "I"}

# Display name -> exact slug AutoScout24 expects in its URLs/API.
# Verified against real autoscout24.com URLs (e.g. autoscout24.com/lst/land-rover/range-rover)
MAKES = {
    "BMW": "bmw",
    "Mercedes-Benz": "mercedes-benz",
    "Audi": "audi",
    "Porsche": "porsche",
    "Volkswagen": "volkswagen",
    "Volvo": "volvo",
    "Land Rover": "land-rover",
    "Jaguar": "jaguar",
    "Alfa Romeo": "alfa-romeo",
    "Mini": "mini",
    "Skoda": "skoda",
    "Maserati": "maserati",
    "Bentley": "bentley",
    "Lexus": "lexus",
    "Tesla": "tesla",
}

# Documented bodyType options for this actor. "Any" means we don't send the field at all.
BODY_TYPES = {
    "Any body type": None,
    "Coupe": "coupe",
    "Convertible": "convertible",
    "Sedan": "sedan",
    "Station Wagon": "station wagon",
    "SUV": "suv",
    "Hatchback": "hatchback",
    "Van": "van",
    "Transporter": "transporter",
}

# Below this many listings in a market's sample, its sample median is too shaky to show
# with any confidence (see build_market_comparison / the Market Comparison table).
MIN_GROUP_SIZE_FOR_SCORING = 3

# How many raw listings to request per price band (each band already covers every country in
# one URL — see build_search_url). Higher when a body type filter is active, because local
# filtering will discard some of what comes back — asking for more up front keeps the final,
# filtered result set from being too thin.
PER_BAND_MAX_RESULTS_DEFAULT = 50
PER_BAND_MAX_RESULTS_WITH_BODY_TYPE_FILTER = 200

# The user's price range is split into equal-width bands, each sorted cheapest-first and
# capped independently — so a cap only ever cuts a band's *expensive* end, never hides cheap
# cars by letting one band's listings crowd out another's. Band count scales with range width
# (bands_for_price_range) rather than being flat — a €10k-15k search and a €10k-150k search
# need very different slice widths.
# TODO: €15,000/band and the max of 8 are reasoned starting values, not measured optima —
# both still need tuning against real search results.
PRICE_BAND_WIDTH_EUR = 15000
MIN_PRICE_BANDS = 2
MAX_PRICE_BANDS = 8

# How many of the (deduplicated, filtered) results get shown as full cards.
# Everything beyond this is still shown — never dropped — in the sortable table below the
# cards: an earlier version showed only 20 of N with no indication, which was a bug.
CARDS_SHOWN = 20

# Computed at run time, never hardcoded. A hardcoded year here silently makes every car
# registered in the current year impossible to find, which is a real failure we hit in testing.
CURRENT_YEAR = datetime.now().year

# Price-rating tiers, keyed by keyword rather than an exhaustive fixed list of exact
# AutoScout24 priceEvaluation slugs. Only "toolow-price " and "unknown" have actually been
# observed in live testing (see docs/experiments.md) — there are almost certainly other values
# at higher price tiers we haven't seen yet, so matching by substring lets an unseen variant
# (e.g. a differently-punctuated "top-price" form) still land in the right tier instead of
# falling through to "unrecognised". "toolow" is deliberately a caution tier, not the top
# badge — it was observed exclusively on junk listings (a €1 BMW 520i, 250k+ km cars), which
# reads as AutoScout24 flagging the price as an outlier relative to their reference, not as
# "great bargain, buy now."
PRICE_EVALUATION_TIERS = [
    ("top", "AutoScout24: Top Price", "tag-excellent"),
    ("good", "AutoScout24: Good Price", "tag-good"),
    ("fair", "AutoScout24: Fair Price", "tag-average"),
    ("toolow", "AutoScout24: Unusually Low Price", "tag-average"),
    ("expensive", "AutoScout24: Priced High", "tag-weak"),
    ("high", "AutoScout24: Priced High", "tag-weak"),
]

# Empirically confirmed 2026-09-06 by deliberately triggering a 429 against this exact
# model/key (see docs/experiments.md, TEST H) — Google's rate-limits docs no longer publish
# numeric free-tier figures, only a link to a personalized AI Studio dashboard. The 429
# body named the limit directly: "limit: 15, model: gemini-3.5-flash-lite", quotaId
# GenerateRequestsPerMinutePerProjectPerModel-FreeTier.
GEMINI_FREE_TIER_RPM = 15
GEMINI_MIN_SECONDS_BETWEEN_CALLS = 60.0 / GEMINI_FREE_TIER_RPM

# Titles per Gemini call. Also empirically confirmed (docs/experiments.md, TEST H): a
# responseSchema keyed by one property per title hits a hard, undocumented property-count
# cliff — 95 titles/call succeeds (100% classification accuracy in testing), 96+ fails
# outright with 400 INVALID_ARGUMENT, near-instantly, regardless of title length. 80 keeps
# a 15-title margin below that cliff while still roughly halving the call count a
# conservative batch size of 40 would need.
DAMAGE_SCREEN_BATCH_SIZE = 80

DAMAGE_CATEGORIES = ["DAMAGE", "PARTS_ONLY", "LEASE_OR_FINANCE_PRICE", "AUCTION", "EXPORT_PRICE", "CLEAN"]
FLAGGED_DAMAGE_CATEGORIES = {"DAMAGE", "PARTS_ONLY", "LEASE_OR_FINANCE_PRICE", "AUCTION", "EXPORT_PRICE"}

DAMAGE_CATEGORY_LABELS = {
    "DAMAGE": "Possible accident/damage",
    "PARTS_ONLY": "For parts only",
    "LEASE_OR_FINANCE_PRICE": "Lease/finance price, not sale price",
    "AUCTION": "Auction listing",
    "EXPORT_PRICE": "Export-only price",
    "CLEAN": "No flags",
    # Screening could not be completed for this listing — never treated as CLEAN.
    "UNAVAILABLE": "Not screened",
}

# Trigger-word examples per category, in every language the screening must handle (German, Dutch,
# French, Italian, English), to steer the model without relying on it already knowing
# AutoScout24-specific slang.
DAMAGE_SCREENING_PROMPT_HEADER = (
    "Classify each numbered car listing TITLE into exactly one category, based only on "
    "wording in the title:\n"
    "DAMAGE — accident/crash damage disclosed (e.g. Unfallschaden, schadeauto, accidenté, "
    "incidentata, accident damage)\n"
    "PARTS_ONLY — for parts/spares only, not a running vehicle (e.g. nur Teile, alleen "
    "onderdelen, pour pièces, per pezzi, for parts only)\n"
    "LEASE_OR_FINANCE_PRICE — a monthly lease takeover or finance rate shown instead of the "
    "real sale price (e.g. Leasingübernahme, leaseovername, reprise de leasing, ripresa "
    "leasing, lease takeover)\n"
    "AUCTION — a judicial/insurance/repo auction listing, not a normal sale (e.g. "
    "Versteigerung, veiling, enchère, asta giudiziaria, auction)\n"
    "EXPORT_PRICE — price explicitly stated as export-only / excluding local tax (e.g. "
    "Exportpreis, exportprijs, prix export, prezzo export, export price)\n"
    "CLEAN — none of the above apply\n"
    "Titles may be in German, Dutch, French, Italian, or English. A title that explicitly "
    "denies damage (e.g. 'unfallfrei', 'accident-free') is CLEAN, not DAMAGE.\n\n"
)
