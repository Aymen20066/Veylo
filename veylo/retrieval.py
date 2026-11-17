"""Listing retrieval: search-input cleanup, price bands, AutoScout24 search URLs, and the
Apify start/poll/fetch calls."""
import re
import time

import requests

from veylo import config
from veylo.config import MAX_PRICE_BANDS, MIN_PRICE_BANDS, PRICE_BAND_WIDTH_EUR


def slugify(text):
    """Turns free text like 'C Class' into the format the actor expects: 'c-class'."""
    return text.strip().lower().replace(" ", "-")


# Matches a trailing generation marker like " VII" or " Mk7" at the end of a model name.
# The actor's model field only accepts a bare model name (e.g. "golf") — AutoScout24 tracks
# generation as a completely separate thing, not part of the model. Sending "golf-7" is invalid
# input and (confirmed by testing) makes the actor silently drop the model filter entirely,
# returning every Volkswagen instead of just Golfs.
#
# NOTE: bare trailing numbers (e.g. the "7" in "Golf 7") are deliberately NOT auto-stripped here.
# The same "word + number" shape is also how real model names work (Lexus "LC 500", Mercedes
# "C 63", "E 350") — a generic pattern can't tell a generation number apart from part of the
# actual model name, and guessing wrong would silently break a correct search. Only patterns
# that are unambiguous generation markers are handled automatically; anything else relies on
# the Model field's help text guiding the user instead.
GENERATION_SUFFIX_PATTERN = re.compile(
    r"\s+(mk\.?\s?\d+|i|ii|iii|iv|vi|vii|viii|ix|xi|xii)$", re.IGNORECASE
)


def strip_generation_suffix(model_text):
    """Returns (clean_model, removed_suffix_or_None)."""
    match = GENERATION_SUFFIX_PATTERN.search(model_text.strip())
    if match:
        return model_text[:match.start()].strip(), match.group(1).strip()
    return model_text.strip(), None


def is_market_truncated(item_count, max_results_requested):
    """True when a market's raw result count hit (or somehow exceeded) the cap we asked
    the actor for. That's not proof more listings exist, but it means we genuinely don't
    know — a round number sitting exactly at the requested max is far more likely to be
    a cutoff than a coincidence, and treating it as "that's everything" would let a
    market's true size (and its median) understate itself silently."""
    return item_count >= max_results_requested


AS24_BASE_URL = "https://www.autoscout24.com/lst"


def bands_for_price_range(min_price, max_price):
    """Roughly one band per PRICE_BAND_WIDTH_EUR of the user's price range, clamped to
    [MIN_PRICE_BANDS, MAX_PRICE_BANDS]. A flat band count gave wildly different slice
    widths for a narrow vs. a wide search — e.g. €10k-15k and €10k-150k both got 5 bands
    under the old constant. €10k-15k -> 2, €10k-50k -> 3, €10k-150k -> 8."""
    width = max(0, int(max_price) - int(min_price))
    raw = round(width / PRICE_BAND_WIDTH_EUR)
    return max(MIN_PRICE_BANDS, min(MAX_PRICE_BANDS, raw))


def compute_price_bands(min_price, max_price, num_bands=None):
    """Splits [min_price, max_price] into num_bands equal-width, contiguous bands. Each
    band is searched and sorted cheapest-first on its own (see build_search_url), so a
    band's cap only ever cuts its expensive end — never hides a cheap car by letting one
    band's listings crowd out another's. num_bands defaults to
    bands_for_price_range(min_price, max_price) when not given explicitly. Degrades to a
    single band spanning the whole range if it can't be split (num_bands < 1, or
    max_price <= min_price)."""
    min_price, max_price = int(min_price), int(max_price)
    if num_bands is None:
        num_bands = bands_for_price_range(min_price, max_price)
    if num_bands < 1 or max_price <= min_price:
        return [(min_price, max_price)]
    width = (max_price - min_price) / num_bands
    bands = []
    for i in range(num_bands):
        band_min = min_price + round(i * width)
        band_max = max_price if i == num_bands - 1 else min_price + round((i + 1) * width)
        bands.append((band_min, band_max))
    return bands


def format_price_band(band):
    lo, hi = band
    return f"€{lo:,}–€{hi:,}"


def build_search_url(make_slug, model_slug, band_min, band_max, min_year, max_year,
                      max_mileage, cy_codes):
    """Builds one AutoScout24 search URL covering every requested country and a single
    price band, sorted cheapest-first. Query parameter names (pricefrom/priceto,
    fregfrom/fregto, kmto, cy, sort, desc) were confirmed against real API output with a
    live capped test before being wired in here — never assumed from documentation."""
    params = {
        "sort": "price",
        "desc": "0",
        "cy": ",".join(cy_codes),
        "pricefrom": band_min,
        "priceto": band_max,
        "fregfrom": min_year,
        "fregto": max_year,
        "kmto": max_mileage,
        "atype": "C",
    }
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{AS24_BASE_URL}/{make_slug}/{model_slug}?{query}"


def _apify_auth_headers():
    """The Apify token travels in the Authorization header, never in a URL — URLs end up
    in logs, proxies and error messages."""
    return {"Authorization": f"Bearer {config.APIFY_TOKEN}"}


def run_apify_band_search(make_slug, model_slug, band_min, band_max, min_year, max_year,
                           max_mileage, cy_codes, max_results, use_residential_proxy=False):
    """Starts one Apify run covering a single price band across every requested country in
    one URL. Returns (run_id, error_message), same contract as the old run_apify_search.

    searchUrls overrides all of the actor's structured filters (make/model/countries/
    price/year/etc.) per its own schema, so none of those are sent alongside it — body
    type is still filtered locally after results come back, same as before, since AS24's
    own body-type vocabulary was already found unreliable when combined with make/model."""
    url = f"https://api.apify.com/v2/acts/{config.ACTOR_ID}/runs"
    search_url = build_search_url(make_slug, model_slug, band_min, band_max,
                                   min_year, max_year, max_mileage, cy_codes)
    payload = {
        "searchUrls": [{"url": search_url}],
        "maxResults": max_results,
        "includeDetails": True,
        "condition": "used",
    }
    # Residential proxy is opt-in and off by default: an API run gets no proxy unless one
    # is sent explicitly, and Apify bills residential traffic separately.
    if use_residential_proxy:
        payload["proxyConfiguration"] = {
            "useApifyProxy": True,
            "apifyProxyGroups": ["RESIDENTIAL"],
        }
    try:
        response = requests.post(url, json=payload, headers=_apify_auth_headers(), timeout=30)
        if response.status_code != 201:
            return None, f"HTTP {response.status_code}: {response.text[:300]}"
        return response.json()["data"]["id"], None
    except Exception as e:
        return None, f"Request error: {e}"


def poll_run(run_id):
    """Returns the real Apify run status as a string, instead of just True/False,
    so we can see WHY a country failed (timeout vs actual failure vs error)."""
    url = f"https://api.apify.com/v2/actor-runs/{run_id}"
    for _ in range(60):
        try:
            response = requests.get(url, headers=_apify_auth_headers(), timeout=10)
            status = response.json()["data"]["status"]
            if status == "SUCCEEDED":
                return "SUCCEEDED"
            elif status in ["FAILED", "ABORTED", "TIMED-OUT"]:
                return status
        except Exception:
            return "REQUEST_ERROR"
        time.sleep(3)
    return "STILL_RUNNING_AFTER_3MIN"


def get_results(run_id):
    """Returns a list of items on success (possibly empty), or None if the fetch itself
    failed. The distinction matters: an empty list means the market genuinely had no
    matching cars, while None means we never got an answer — showing those identically
    would tell the user 'nothing found' when the truth is 'we don't know'."""
    url = f"https://api.apify.com/v2/actor-runs/{run_id}/dataset/items"
    try:
        response = requests.get(url, headers=_apify_auth_headers(), timeout=30)
        if response.status_code != 200:
            return None
        data = response.json()
        if not isinstance(data, list):
            return None
        return data
    except Exception:
        return None
