"""Turning raw listings into ranked results: cleaning and local filter re-verification,
dedup, effective country, transport cost, cost columns, market comparison, ranking and the
card/table split, and display formatting helpers."""
import html
import re

import pandas as pd

from veylo.config import (
    COUNTRY_CODES,
    DAMAGE_CATEGORY_LABELS,
    PRICE_EVALUATION_TIERS,
    SHIPPING_DISTANCES,
    TRANSPORT_FIXED_EUR,
    TRANSPORT_MINIMUM_EUR,
)
from veylo.costs import calculate_import_costs, destination_tax_name


def format_seller_type(seller_type):
    """Human label for AutoScout24's sellerType. Verified real values: 'PrivateSeller',
    'Dealer'. Any other value is expanded from camelCase and shown rather than collapsed,
    so a sub-type we haven't seen (e.g. 'FranchiseDealer') keeps its meaning instead of
    being flattened into a less informative label."""
    if seller_type is None:
        return None
    text = str(seller_type).strip()
    if not text or text.lower() in ("null", "none", "nan"):
        return None
    # Split camelCase into words: "FranchiseDealer" -> "Franchise Dealer".
    spaced = re.sub(r"(?<!^)(?=[A-Z])", " ", text).strip()
    # "Private Seller" reads redundantly after the "Seller listed as:" prefix.
    if spaced.lower().endswith(" seller"):
        spaced = spaced[: -len(" seller")].strip()
    if not spaced:
        return None
    return spaced[0].upper() + spaced[1:]


def format_price_evaluation(value):
    """Turn AutoScout24's rating slug ('good-price') into a readable label ('Good price').
    Deliberately generic: no hardcoded list of accepted values, so a rating we haven't seen
    before is still displayed rather than silently dropped."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in ("null", "none", "nan"):
        return None
    words = text.replace("_", "-").split("-")
    words = [w for w in words if w]
    if not words:
        return None
    return " ".join(words).capitalize()


def format_eur(value):
    """Money as shown to the user: '€12,345', or 'Not calculated' when the value is
    missing (None or NaN). A missing amount must never reach the page as '€nan'."""
    if value is None or pd.isna(value):
        return "Not calculated"
    return f"€{value:,.0f}"


def acquisition_cost_sort_key(df):
    """Sort key for 'lowest acquisition cost': every listing with a complete cost ranks
    above every listing without one. Complete listings are ordered by total cost;
    incomplete ones follow, ordered by price + transport among themselves. Without this,
    an incomplete figure (no tax yet) looks cheaper than a complete one and floats to the
    top. Returns each row's rank position as a Series aligned to df's index."""
    incomplete = df["total_cost"].isna()
    value = df["total_cost"].fillna(df["price_plus_transport"])
    order = (pd.DataFrame({"incomplete": incomplete, "value": value})
             .sort_values(["incomplete", "value"], kind="stable").index)
    return pd.Series(range(len(order)), index=order).reindex(df.index)


def calculate_price_rating(price_evaluation):
    """Price-rating badge driven entirely by AutoScout24's own priceEvaluation field —
    computed by them against their full database, not our own sample of listings. This
    replaces the old body-type/year-band median comparison: discovery now stratifies the
    price range into bands and sorts each cheapest-first (see compute_price_bands),
    so our own sample median is deliberately cheap-skewed and no longer represents the
    market — scoring a car against it would be misleading. AutoScout24's rating carries no
    such bias, and comes back free with enrichment.

    Normalizes the field's confirmed trailing-space quirk ("toolow-price ") and is
    case-insensitive. Matches by keyword (PRICE_EVALUATION_TIERS) rather than an
    exhaustive fixed list, since only "toolow-price" and "unknown" have actually been
    observed — an unrecognised value is still shown, attributed and verbatim, rather than
    guessed at or hidden. Returns (label, css_class, tier), where
    tier is the matched keyword, "unrated", or "unrecognized" — used by the caller to
    decide whether to show the "verify before treating as a bargain" caution note."""
    text = str(price_evaluation or "").strip().lower()
    if not text or text in ("unknown", "null", "none", "nan"):
        return "Not Rated by AutoScout24", "tag-neutral", "unrated"
    for keyword, label, css_class in PRICE_EVALUATION_TIERS:
        if keyword in text:
            return label, css_class, keyword
    formatted = format_price_evaluation(price_evaluation)
    return f"AutoScout24: {formatted}", "tag-neutral", "unrecognized"


def get_distances(importer_country, city):
    """Returns (distances_dict, matched_city_or_None).

    A None second value means no city matched and the country's average distances were
    used instead. Callers must surface that, since silently presenting an average as if
    it were the user's actual route is exactly the kind of quiet inaccuracy that erodes
    trust in the transport figure — the number one Polish importer said matters most.
    """
    country_data = SHIPPING_DISTANCES.get(importer_country, {})
    for key in country_data:
        if key == "default":
            continue
        if key.lower() == str(city or "").strip().lower():
            return country_data[key], key
    fallback = country_data.get(
        "default",
        {"Germany": 800, "Belgium": 1200, "France": 1500, "Netherlands": 1200, "Italy": 1600},
    )
    return fallback, None


def resolve_transport_cost(source_country, distances, cost_per_km, overrides):
    """Transport cost for one listing. A user-supplied override for that source country
    always wins over the distance estimate — they know their own freight rates better
    than our lookup table does. Otherwise the estimate is a fixed part plus a per-km
    part, with a minimum (see TRANSPORT_FIXED_EUR in config).
    Returns (cost_eur, is_user_supplied)."""
    override = (overrides or {}).get(source_country)
    if override is not None and override > 0:
        return float(override), True
    distance = distances.get(source_country)
    if distance is None:
        return None, False
    return max(TRANSPORT_MINIMUM_EUR, TRANSPORT_FIXED_EUR + cost_per_km * distance), False


COUNTRY_ISO_TO_NAME = {code: name for name, code in COUNTRY_CODES.items()}


def annotate_effective_country(df):
    """Adds 'source_country' and 'effective_country' columns to df, both derived directly
    from each listing's own ISO `country` field.

    Historically these two columns could disagree — a listing fetched from one country's
    AutoScout24 domain could physically be in another (confirmed real example: a BMW i3
    scraped from the French domain was physically in Belgium, charged France's transport
    distance instead of Belgium's). That was fixed by preferring the listing's own field.
    Now that discovery runs one combined search across all countries per price band (see
    build_search_url) instead of one search per country, there is no second, independent
    "which domain did this come from" signal left to disagree with — the listing's own
    `country` field is the only signal there ever was, so it is used directly rather than
    reconciled against anything. An unrecognised or missing ISO code becomes 'Unknown'
    rather than a guess."""
    country_iso = df["country"] if "country" in df.columns else pd.Series(index=df.index, dtype=object)
    resolved = country_iso.apply(
        lambda iso: COUNTRY_ISO_TO_NAME.get(str(iso or "").strip().upper(), "Unknown")
    )
    df["source_country"] = resolved
    df["effective_country"] = resolved
    return df


def build_market_comparison(df):
    """Aggregates per-market SAMPLE medians (of listings found in this search, not the
    market as a whole) grouped by each listing's
    effective_country (its own ISO `country` field, resolved in
    annotate_effective_country()) — so cross-listed cars land under the country they're
    actually in. Returns raw (unformatted) stats; the caller decides how to render values
    below the minimum group size."""
    stats = df.groupby("effective_country").agg(
        Vehicles=("price", "count"),
        Median_Price=("price", "median"),
        Median_Transport=("shipping_est", "median"),
        Median_Import=("import_costs_total", "median"),
        Median_Acquisition=("total_cost", "median"),
    ).reset_index()
    stats.columns = ["Market", "Vehicles", "Sample Median Price (€)", "Sample Median Transport (€)",
                      "Sample Median Import Costs (€)", "Sample Median Acquisition (€)"]
    return stats


REQUIRED_FIELDS = ["price", "mileageKm"]


def clean_and_filter_listings(all_results, price_range, mileage_range, year_range,
                              body_type_slug):
    """Builds the results DataFrame from raw actor items and re-applies every search filter
    locally (price, mileage, year, body type) rather than trusting the actor's own
    filtering. Returns (df, missing_fields): if a required field is absent from the data
    entirely, df is None and missing_fields names what was missing, so the caller can say
    so instead of showing possibly-wrong results."""
    df = pd.DataFrame(all_results)
    missing_fields = [f for f in REQUIRED_FIELDS if f not in df.columns]
    if missing_fields:
        return None, missing_fields

    df = df[df["price"].notna() & (df["price"] > 0)]
    df = df[df["mileageKm"].notna()]
    df["year"] = pd.to_datetime(df.get("firstRegistration", pd.Series(dtype=str)), errors="coerce").dt.year
    df = df[(df["price"] >= price_range[0]) & (df["price"] <= price_range[1])]
    df = df[(df["mileageKm"] >= mileage_range[0]) & (df["mileageKm"] <= mileage_range[1])]
    df = df[(df["year"] >= year_range[0]) & (df["year"] <= year_range[1])]

    # Normalise each listing's body type for the local filter below and for display. It
    # no longer feeds any score. Falls back to "Unknown" if AutoScout24 didn't tag that
    # particular listing.
    if "bodyType" in df.columns:
        df["body_type_group"] = df["bodyType"].fillna("Unknown").replace("", "Unknown")
    else:
        df["body_type_group"] = "Unknown"

    # Body type is filtered HERE, locally — not sent to the actor. See the note in
    # run_apify_band_search for why. A listing with no confirmed body type is excluded
    # when a specific type is requested, since we can't verify it actually matches.
    if body_type_slug:
        df = df[df["body_type_group"].str.lower() == body_type_slug.lower()]

    # NOTE: We deliberately do NOT auto-trim "outlier" prices here. A percentile cut can't
    # tell a data-entry error apart from a genuinely great deal — and hiding genuinely great
    # deals defeats the whole point of this product. Real listings stay visible; the
    # AutoScout24 price rating badge already tells the user when a price looks unusually low or high.
    return df, []


def add_cost_columns(df, importer_country, distances, cost_per_km, shipping_overrides):
    """Adds country, transport, import-cost, VAT/seller/rating and total-cost columns to
    the cleaned, deduplicated listings. Missing inputs never become a number: they become
    None/NaN plus a reason."""
    # Country (for both display and transport cost) comes straight from each
    # listing's own ISO `country` field — see annotate_effective_country().
    df = annotate_effective_country(df)
    transport = [
        resolve_transport_cost(c, distances, cost_per_km, shipping_overrides)
        for c in df["effective_country"]
    ]
    df["shipping_est"] = [t[0] for t in transport]
    df["shipping_is_user_supplied"] = [t[1] for t in transport]
    # Vehicle price + transport only. Kept as its own column so a listing whose
    # import tax can't be calculated still has a usable partial figure to show,
    # clearly labelled as incomplete rather than passed off as a total.
    df["price_plus_transport"] = df["price"] + df["shipping_est"]

    # Fields the tax calculations need. Absent columns become None per row rather
    # than raising, so a schema change degrades to "not calculated" instead of a crash.
    cc_series = df["engineDisplacementCcm"] if "engineDisplacementCcm" in df.columns else None
    emission_series = df["emissionClass"] if "emissionClass" in df.columns else None
    fuel_series = df["fuelType"] if "fuelType" in df.columns else None

    import_results = []
    for idx in df.index:
        import_results.append(calculate_import_costs(
            destination=importer_country,
            price_eur=df.at[idx, "price"],
            cc=cc_series.at[idx] if cc_series is not None else None,
            fuel_type=fuel_series.at[idx] if fuel_series is not None else None,
            emission_class=emission_series.at[idx] if emission_series is not None else None,
            year=df.at[idx, "year"],
            first_registration=(df.at[idx, "firstRegistration"]
                                if "firstRegistration" in df.columns else None),
            mileage_km=df.at[idx, "mileageKm"],
        ))

    df["import_tax_eur"] = [r["import_tax_eur"] for r in import_results]
    df["admin_fees_eur"] = [r["admin_fees_eur"] for r in import_results]
    df["import_costs_total"] = [r["total_import_costs_eur"] for r in import_results]
    df["import_calculable"] = [r["calculable"] for r in import_results]
    df["import_unavailable_reason"] = [r["unavailable_reason"] for r in import_results]
    df["import_notes"] = [r["notes"] for r in import_results]
    df["import_tax_label"] = [r["tax_label"] for r in import_results]
    df["import_alternative"] = [r["alternative"] for r in import_results]
    df["new_vehicle"] = [r["new_vehicle"] for r in import_results]

    # Whether the seller can issue a VAT invoice. Decision-critical for a VAT-registered
    # trader (it can mean reclaiming ~20% of the purchase price) and it also explains
    # some listings that look impossibly cheap next to retail-priced ones.
    if "vatDeductible" in df.columns:
        df["vat_deductible"] = df["vatDeductible"]
    else:
        df["vat_deductible"] = None

    # How the listing account is registered on AutoScout24. Verified values in real
    # output: "PrivateSeller" and "Dealer". Reported as the platform's label, never
    # asserted by us as a verified fact about the seller.
    if "sellerType" in df.columns:
        df["seller_type"] = df["sellerType"]
    else:
        df["seller_type"] = None

    # AutoScout24's own price rating (e.g. "good-price"). Shown as their independent
    # second opinion, clearly attributed — we don't know their methodology, so we never
    # present it as our reasoning.
    if "priceEvaluation" in df.columns:
        df["price_evaluation"] = df["priceEvaluation"]
    else:
        df["price_evaluation"] = None

    # Full acquisition cost. Deliberately left as NaN (not 0) when the import tax
    # can't be calculated — a 0 would read as "no tax owed", which is a different
    # and much more damaging claim than "we don't know".
    df["total_cost"] = df["price_plus_transport"] + pd.to_numeric(
        pd.Series([r["total_import_costs_eur"] for r in import_results], index=df.index),
        errors="coerce")
    return df


def deduplicate_listings(df):
    """Removes the same physical car cross-listed on more than one national AutoScout24
    domain. Confirmed real example: an identical BMW i3 120Ah - 42.2 kWh Advanced, 2019,
    55,863 km, €16,250 appeared once scraped from the Belgian domain and once from the
    French domain, with two different URLs — same dealer, same town, one physical car.
    Matches on listing URL first (catches an exact re-scrape of the same page); where the
    same car has a different URL because it's on a different national domain, falls back
    to a composite key of title + price + mileage + first registration, which is what
    actually catches the cross-domain case. Returns (deduped_df, merged_count) so the
    caller can disclose how many were merged rather than the count silently shrinking."""
    before = len(df)

    if "url" in df.columns:
        has_url = df["url"].notna() & (df["url"].astype(str).str.strip() != "")
        df = pd.concat([
            df[has_url].drop_duplicates(subset="url", keep="first"),
            df[~has_url],
        ]).sort_index()

    composite_cols = [c for c in ("title", "price", "mileageKm", "firstRegistration")
                       if c in df.columns]
    if composite_cols:
        complete = df[composite_cols].notna().all(axis=1)
        df = pd.concat([
            df[complete].drop_duplicates(subset=composite_cols, keep="first"),
            df[~complete],
        ]).sort_index()

    return df, before - len(df)


def rank_and_split(df, sort_key, cards_shown):
    """Ranks df by sort_key (ascending — best first) and splits it into (cards_df,
    remaining_df). Every row ends up in exactly one of the two — nothing is dropped, only
    moved out of the card view into the sortable table below it."""
    ranked = df.assign(_sort_key=sort_key).sort_values("_sort_key")
    return ranked.head(cards_shown).copy(), ranked.iloc[cards_shown:].copy()


def split_cards_with_damage_filter(df, sort_key, cards_shown, exclude_damaged):
    """Same contract as rank_and_split(df, sort_key, cards_shown) — every row ends up in
    exactly one of (cards_df, remaining_df), nothing dropped — except cards are chosen only
    from listings that passed damage/salvage screening clean when exclude_damaged is True.
    A flagged listing is never removed from the dataset, only kept out of the card slice;
    it still appears in remaining_df with its flag visible.
    If every listing is flagged, degrades to showing all of them as cards rather than an
    empty view — excluding everything would be a worse failure than showing unfiltered."""
    eligible = df[~df["is_flagged"]] if exclude_damaged else df
    if eligible.empty and not df.empty:
        eligible = df
    cards, _ = rank_and_split(eligible, sort_key.loc[eligible.index], cards_shown)
    remaining_key = sort_key.drop(cards.index)
    remaining = df.drop(cards.index).assign(_sort_key=remaining_key).sort_values("_sort_key")
    return cards, remaining


def escape_html(value):
    """Escapes text that came from a listing (or the user) before it is placed into
    unsafe_allow_html markup, so a title containing tags is shown as plain text instead
    of being interpreted by the browser."""
    return html.escape(str(value), quote=True)


def safe_listing_url(url):
    """A listing URL is only ever used as a link if it is an https:// URL. Anything else
    (missing, javascript:, http:, a relative path) returns None and no link is shown."""
    text = str(url).strip() if url is not None else ""
    return text if text.startswith("https://") else None


def build_listing_card_html(row, params):
    """HTML for one full result card. row is one results row (with the columns added by
    add_cost_columns and the screening step); params is the search-parameters dict."""
    group = row["body_type_group"]
    tax_name = destination_tax_name(params["importer_country"])

    rating_label, rating_class, rating_tier = calculate_price_rating(row.get("price_evaluation"))

    year_str = str(int(row["year"])) if pd.notna(row.get("year")) else "N/A"
    # Everything below that originates from the scraped listing is escaped before it goes
    # into the card's HTML.
    rating_label = escape_html(rating_label)
    title = escape_html(row.get("title", f"{params['make']} {params['model']}"))
    city_val = escape_html(row.get("city", "N/A"))
    country_val = escape_html(row.get("source_country", "N/A"))
    transmission = escape_html(row.get("transmission", "N/A"))
    fuel = escape_html(row.get("fuelType", "N/A"))
    body_type_val = escape_html(group if group != "Unknown" else "Body type unknown")
    listing_url = safe_listing_url(row.get("url"))
    if listing_url:
        listing_link = (f' · <a href="{escape_html(listing_url)}" target="_blank" '
                        f'rel="noopener noreferrer" style="color:var(--veylo-accent);">View Original Listing →</a>')
    else:
        listing_link = " · listing link unavailable"

    # AutoScout24 itself flagged this price as an outlier relative to their own market
    # data (see PRICE_EVALUATION_TIERS) — never hidden, since surfacing unusually cheap
    # cars is the whole point, but we tell the importer to look rather than letting the
    # badge alone imply it's unambiguously good news.
    caution_html = "<div></div>"
    if rating_tier == "toolow":
        caution_html = (
            '<div style="margin-top:0.5rem;">'
            '<p style="color:var(--veylo-warn); margin:2px 0; font-size:0.85rem;">'
            'AutoScout24 flags this price as unusually low relative to their own market '
            'data. Verify condition, accident history, spec, and whether this is a net '
            'export price before treating it as a bargain.</p></div>'
        )

    # A flagged listing only reaches the card view when the "exclude damaged" toggle
    # is off — never hidden even then, just clearly labeled.
    # A title that couldn't be screened at all gets its own distinct
    # note, since "not screened" and "screened clean" are different claims.
    flag_html = "<div></div>"
    if row.get("is_flagged"):
        flag_html = (
            '<div style="margin-top:0.5rem; padding:0.5rem; background:var(--veylo-inset); '
            'border-radius:8px;">'
            f'<p style="color:var(--veylo-warn); margin:0; font-size:0.85rem;">'
            f'{DAMAGE_CATEGORY_LABELS.get(row["damage_category"], "Flagged")} — '
            f'flagged by AI title screening (Gemini), not a human review. Verify before '
            f'treating this as a normal listing.</p></div>'
        )
    elif row.get("damage_category") == "UNAVAILABLE":
        flag_html = (
            '<div style="margin-top:0.5rem;">'
            '<p style="color:var(--veylo-faint); margin:0; font-size:0.78rem;">Damage/salvage '
            'screening could not be completed for this listing — shown unverified, not '
            'confirmed clean.</p></div>'
        )

    # Seller type — reported as AutoScout24's account label, not asserted as verified fact.
    seller_label = format_seller_type(row.get("seller_type"))
    seller_bit = (f' · Seller listed as: <b>{escape_html(seller_label)}</b>' if seller_label
                  else ' · Seller type not stated')

    # VAT presentation depends on whether EU new-vehicle rules apply, so the two are
    # handled together rather than shown side by side (which would contradict itself).
    nv = row.get("new_vehicle") or {}
    nv_status = nv.get("status")

    if nv_status == "new":
        vat_html = (
            f'<p style="color:var(--veylo-warn); font-size:0.78rem; margin:2px 0;"><b>New vehicle '
            f'(EU VAT rules apply).</b> {nv.get("reason", "")} VAT is charged in '
            f'{params["importer_country"]} on the full price and is included in the total '
            f'above.{seller_bit}</p>'
            f'<p style="color:var(--veylo-faint); font-size:0.72rem; margin:2px 0;">{nv.get("note") or ""}</p>'
        )
    else:
        vat_flag = row.get("vat_deductible")
        if vat_flag is True or str(vat_flag).strip().lower() == "true":
            vat_line = ('<b>VAT deductible</b> — the seller can issue a VAT invoice, so a '
                        'VAT-registered business may be able to reclaim it.')
            vat_color = "var(--veylo-good)"
        elif vat_flag is False or str(vat_flag).strip().lower() == "false":
            vat_line = ('<b>Not VAT deductible</b> — margin-scheme or private sale, no VAT '
                        'to reclaim.')
            vat_color = "var(--veylo-muted)"
        else:
            vat_line = 'VAT status not stated on this listing.'
            vat_color = "var(--veylo-faint)"
        vat_html = (f'<p style="color:{vat_color}; font-size:0.78rem; margin:2px 0;">'
                    f'{vat_line}{seller_bit}</p>')
        if nv_status == "unknown" and nv.get("vat_eur") is not None:
            # Genuinely ambiguous: AutoScout24 publishes the registration month but not
            # the day, and this car sits within ~1 month of the 6-month EU threshold.
            # Rather than pick a side and bury the other in a caveat, both fully-computed
            # totals are shown so the importer can see exactly what's at stake and settle
            # it by checking the listing.
            if row["import_calculable"]:
                used_total = (row["price_plus_transport"] + row["import_tax_eur"]
                              + row["admin_fees_eur"])
                used_text = format_eur(used_total)
                new_text = format_eur(used_total + nv["vat_eur"])
                ranking_note = "Ranked using the \"used\" figure, since the VAT isn't confirmed. "
            else:
                # No tax figure, so neither scenario has a total. Say so with the reason;
                # the VAT amount is still known and still shown.
                used_text = new_text = (
                    f'Not calculated — {escape_html(row["import_unavailable_reason"])}')
                ranking_note = ""
            vat_html += (
                '<div style="margin-top:0.5rem; padding:0.6rem; background:var(--veylo-inset); '
                'border-radius:8px; border-left:3px solid var(--veylo-warn);">'
                f'<p style="color:var(--veylo-warn); font-size:0.78rem; margin:0 0 4px 0;"><b>Age '
                f'uncertain — this car sits within about a month of the 6-month EU '
                f'threshold.</b> {nv.get("reason", "")}</p>'
                '<div style="display:flex; gap:2rem; flex-wrap:wrap; margin-top:6px;">'
                '<div>'
                '<p style="color:var(--veylo-muted); font-size:0.75rem; margin:0;">If it counts as used</p>'
                f'<p style="color:var(--veylo-accent); font-size:1.15rem; font-weight:700; margin:0;">{used_text}</p>'
                '<p style="color:var(--veylo-faint); font-size:0.68rem; margin:0;">No VAT due</p>'
                '</div>'
                '<div>'
                '<p style="color:var(--veylo-muted); font-size:0.75rem; margin:0;">If it counts as new</p>'
                f'<p style="color:var(--veylo-warn); font-size:1.15rem; font-weight:700; margin:0;">{new_text}</p>'
                f'<p style="color:var(--veylo-faint); font-size:0.68rem; margin:0;">Includes {format_eur(nv["vat_eur"])} '
                f'{params["importer_country"]} VAT</p>'
                '</div></div>'
                f'<p style="color:var(--veylo-faint); font-size:0.7rem; margin:6px 0 0 0;">{ranking_note}'
                'Check the listing\'s exact registration date to know which applies.</p>'
                '</div>'
            )

    # Where the data genuinely supports two different tax outcomes, show both rather than
    # picking one and hoping.
    alt = row.get("import_alternative")
    alt_html = "<div></div>"
    if isinstance(alt, dict) and row["import_calculable"]:
        alt_total = row["price_plus_transport"] + alt["import_tax_eur"] + row["admin_fees_eur"]
        alt_html = (
            '<div style="margin-top:0.5rem; padding:0.5rem; background:var(--veylo-inset); '
            'border-radius:8px;">'
            f'<p style="color:var(--veylo-muted); font-size:0.8rem; margin:0;">'
            f'<b>{alt["label"]}:</b> tax {format_eur(alt["import_tax_eur"])} → acquisition cost '
            f'<b style="color:var(--veylo-accent);">{format_eur(alt_total)}</b>. {alt["explanation"]}</p></div>'
        )

    # --- Import tax + admin fees block -------------------------------------
    # When the tax can't be calculated we say so in place of the number. We never
    # show €0 in that situation: "we don't know" and "nothing is owed" are very
    # different claims, and only one of them is true.
    if row["import_calculable"]:
        nv_line = ""
        if nv.get("applied"):
            nv_line = f"""
    <div style="color:var(--veylo-faint); font-size:1.2rem;">+</div>
    <div>
        <p style="color:var(--veylo-muted); margin:0; font-size:0.85rem;">VAT ({nv['vat_rate'] * 100:.0f}%)</p>
        <p style="color:var(--veylo-warn); font-size:1.2rem; font-weight:600; margin:0;">{format_eur(nv['vat_eur'])}</p>
        <p style="color:var(--veylo-faint); font-size:0.7rem; margin:0;">New-vehicle EU rule</p>
    </div>"""
        tax_block = f"""
    <div style="color:var(--veylo-faint); font-size:1.2rem;">+</div>
    <div>
        <p style="color:var(--veylo-muted); margin:0; font-size:0.85rem;">{row['import_tax_label']}</p>
        <p style="color:var(--veylo-muted); font-size:1.2rem; font-weight:600; margin:0;">{format_eur(row['import_tax_eur'])}</p>
        <p style="color:var(--veylo-faint); font-size:0.7rem; margin:0;">{tax_name} ({params['importer_country']})</p>
    </div>
    <div style="color:var(--veylo-faint); font-size:1.2rem;">+</div>
    <div>
        <p style="color:var(--veylo-muted); margin:0; font-size:0.85rem;">Registration &amp; Fees</p>
        <p style="color:var(--veylo-muted); font-size:1.2rem; font-weight:600; margin:0;">{format_eur(row['admin_fees_eur'])}</p>
        <p style="color:var(--veylo-faint); font-size:0.7rem; margin:0;">Plates, inspection, admin</p>
    </div>{nv_line}
    <div style="color:var(--veylo-faint); font-size:1.2rem;">=</div>
    <div>
        <p style="color:var(--veylo-muted); margin:0; font-size:0.85rem;">Est. Acquisition Cost</p>
        <p style="color:var(--veylo-accent); font-size:1.4rem; font-weight:700; margin:0;">{format_eur(row['total_cost'])}</p>
    </div>"""
    else:
        tax_block = f"""
    <div style="color:var(--veylo-faint); font-size:1.2rem;">+</div>
    <div>
        <p style="color:var(--veylo-muted); margin:0; font-size:0.85rem;">{tax_name} ({params['importer_country']})</p>
        <p style="color:var(--veylo-warn); font-size:1rem; font-weight:600; margin:0;">Not calculated</p>
        <p style="color:var(--veylo-faint); font-size:0.7rem; margin:0;">{escape_html(row['import_unavailable_reason'])}</p>
    </div>
    <div style="color:var(--veylo-faint); font-size:1.2rem;">=</div>
    <div>
        <p style="color:var(--veylo-muted); margin:0; font-size:0.85rem;">Price + Transport Only</p>
        <p style="color:var(--veylo-muted); font-size:1.4rem; font-weight:700; margin:0;">{format_eur(row['price_plus_transport'])}</p>
        <p style="color:var(--veylo-warn); font-size:0.7rem; margin:0;">Incomplete — {tax_name.lower()} still to add</p>
    </div>"""

    if row.get("shipping_is_user_supplied"):
        transport_block = f"""
    <div>
        <p style="color:var(--veylo-muted); margin:0; font-size:0.85rem;">Your Shipping Cost</p>
        <p style="color:var(--veylo-good); font-size:1.2rem; font-weight:600; margin:0;">{format_eur(row['shipping_est'])}</p>
        <p style="color:var(--veylo-faint); font-size:0.7rem; margin:0;">Your figure, not an estimate</p>
    </div>"""
    else:
        fallback_note = ("Estimate only — average distance for the country"
                         if params.get("matched_city") is None
                         else "Estimate only — not a confirmed quote")
        transport_block = f"""
    <div>
        <p style="color:var(--veylo-muted); margin:0; font-size:0.85rem;">Estimated Transport</p>
        <p style="color:var(--veylo-muted); font-size:1.2rem; font-weight:600; margin:0;">{format_eur(row['shipping_est'])}</p>
        <p style="color:var(--veylo-faint); font-size:0.7rem; margin:0;">{fallback_note}</p>
    </div>"""

    notes_html = "<div></div>"
    if row["import_notes"]:
        note_items = "".join(
            f'<p style="color:var(--veylo-muted); font-size:0.75rem; margin:2px 0;">{escape_html(n)}</p>'
            for n in row["import_notes"]
        )
        notes_html = f'<div style="margin-top:0.5rem;">{note_items}</div>'

    return f"""
<div class="listing-card">
<div style="display:flex; justify-content:space-between; align-items:flex-start;">
    <div>
        <h4 style="margin:0; color:inherit;">{title}</h4>
        <p style="color:var(--veylo-muted); margin:4px 0;">{year_str} · {row['mileageKm']:,.0f} km · {transmission} · {fuel} · {body_type_val}</p>
        <p style="color:var(--veylo-muted); margin:4px 0;">{city_val}, {country_val}</p>
    </div>
    <div style="text-align:right;">
        <p class="{rating_class}">{rating_label}</p>
    </div>
</div>
<div class="divider"></div>
<div style="display:flex; gap:1.5rem; align-items:center; flex-wrap:wrap;">
    <div>
        <p style="color:var(--veylo-muted); margin:0; font-size:0.85rem;">Vehicle Price</p>
        <p style="color:inherit; font-size:1.2rem; font-weight:600; margin:0;">{format_eur(row['price'])}</p>
    </div>
    <div style="color:var(--veylo-faint); font-size:1.2rem;">+</div>{transport_block}{tax_block}
</div>
{notes_html}
{alt_html}
{caution_html}
{flag_html}
<div class="divider"></div>
{vat_html}
<p class="source-tag">Source: AutoScout24{listing_link}</p>
</div>
"""
