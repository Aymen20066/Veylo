import base64
import re
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from veylo import config
from veylo.config import (
    AS24_CY_CODES,
    BODY_TYPES,
    CARDS_SHOWN,
    COUNTRIES,
    CURRENT_YEAR,
    DAMAGE_CATEGORY_LABELS,
    FLAGGED_DAMAGE_CATEGORIES,
    MAKES,
    MIN_GROUP_SIZE_FOR_SCORING,
    PER_BAND_MAX_RESULTS_DEFAULT,
    PER_BAND_MAX_RESULTS_WITH_BODY_TYPE_FILTER,
    SHIPPING_DISTANCES,
    TRANSPORT_FIXED_EUR,
    TRANSPORT_MINIMUM_EUR,
    TRANSPORT_RATE_PER_KM_DEFAULT,
)
from veylo.costs import EXCHANGE_RATES_AS_OF, VAT_DISCLAIMER, destination_tax_name
from veylo.retrieval import (
    compute_price_bands,
    format_price_band,
    get_results,
    is_market_truncated,
    poll_run,
    run_apify_band_search,
    slugify,
    strip_generation_suffix,
)
from veylo.screening import screen_titles_for_damage
from veylo.transform import (
    acquisition_cost_sort_key,
    add_cost_columns,
    build_listing_card_html,
    build_market_comparison,
    clean_and_filter_listings,
    deduplicate_listings,
    get_distances,
    resolve_transport_cost,
    safe_listing_url,
    split_cards_with_damage_filter,
)

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
BRAND_ORANGE = "#E64B1B"
SEARCH_MARKETS_TEXT = ", ".join(COUNTRIES[:-1]) + " and " + COUNTRIES[-1]

st.set_page_config(page_title="Veylo", page_icon=str(ASSETS_DIR / "veylo_icon.png"),
                   layout="wide")

# Brand styling. Card and KPI colours are derived from the active theme's text colour
# (currentColor), so the same rules stay readable in both the light and the dark theme.
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500&family=Montserrat:wght@600;700&display=swap');
    .stApp, .stApp p, .stApp li, .stApp label, .stApp input, .stApp textarea, .stApp button {
        font-family: 'Inter', sans-serif;
    }
    .stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp h5, .stApp h6 {
        font-family: 'Montserrat', sans-serif;
        font-weight: 700;
    }
    .block-container { padding-top: 4.5rem; max-width: 1200px; }
    .veylo-logo { text-align: center; }
    .veylo-logo img { width: 110px; height: auto; }
    .veylo-subtitle { text-align: center; }
    .stApp {
        --veylo-accent: #E64B1B;
        --veylo-muted: color-mix(in srgb, currentColor 70%, transparent);
        --veylo-faint: color-mix(in srgb, currentColor 55%, transparent);
        --veylo-line: color-mix(in srgb, currentColor 14%, transparent);
        --veylo-inset: color-mix(in srgb, currentColor 6%, transparent);
        --veylo-good: color-mix(in srgb, #16a34a 80%, currentColor);
        --veylo-warn: color-mix(in srgb, #d97706 80%, currentColor);
        --veylo-bad: color-mix(in srgb, #dc2626 80%, currentColor);
    }
    .listing-card {
        background: color-mix(in srgb, currentColor 4%, transparent);
        border: 1px solid var(--veylo-line);
        border-left: 3px solid var(--veylo-accent);
        border-radius: 10px;
        padding: 1.5rem;
        margin-bottom: 1rem;
    }
    .listing-card h4 { font-family: 'Montserrat', sans-serif; font-weight: 600; }
    .listing-card a { color: var(--veylo-accent); }
    [data-testid="stMetric"] {
        background: color-mix(in srgb, currentColor 4%, transparent);
        border: 1px solid var(--veylo-line);
        border-left: 3px solid var(--veylo-accent);
        border-radius: 10px;
        padding: 0.75rem 1rem;
    }
    [data-testid="stMetricValue"] { font-family: 'Montserrat', sans-serif; font-weight: 600; }
    .tag-excellent { color: var(--veylo-good); font-weight: 600; }
    .tag-good { color: var(--veylo-good); font-weight: 600; }
    .tag-average { color: var(--veylo-warn); font-weight: 600; }
    .tag-weak { color: var(--veylo-bad); font-weight: 600; }
    .tag-neutral { color: var(--veylo-muted); font-weight: 600; }
    .divider { border-top: 1px solid var(--veylo-line); margin: 1rem 0; }
    .source-tag { color: var(--veylo-faint); font-size: 0.75rem; }
</style>
""", unsafe_allow_html=True)

# Session state init
for key, val in [("results_df", None), ("search_done", False), ("search_params", {})]:
    if key not in st.session_state:
        st.session_state[key] = val

# Header
logo_b64 = base64.b64encode((ASSETS_DIR / "veylo_logo.png").read_bytes()).decode("ascii")
st.markdown(
    f'<div class="veylo-logo"><img src="data:image/png;base64,{logo_b64}" alt="Veylo"></div>'
    '<h3 class="veylo-subtitle">Cross-border used-car sourcing: estimated total acquisition '
    'cost.</h3>',
    unsafe_allow_html=True,
)
st.markdown("---")

# Search form
with st.expander("Search", expanded=not st.session_state.search_done):
    col1, col2 = st.columns(2)

    with col1:
        st.markdown("#### Vehicle")
        make_display = st.selectbox("Make", options=list(MAKES.keys()), key="make_select")
        model_input = st.text_input(
            "Model", placeholder="M4", value=st.session_state.search_params.get("model", ""),
            help="Just the model name (e.g. 'Golf', not 'Golf 7'). To search a specific generation, use the Registration Year filter below instead."
        )
        if re.search(r"\s\d+$", model_input.strip()):
            st.caption(
                "Ends in a plain number. If that's a generation (e.g. 'Golf 8'), write it as "
                "'Golf VIII' or 'Golf Mk8' so it searches correctly — a plain number breaks the "
                "whole model match and returns unrelated cars. If the number is actually part of "
                "the model name (e.g. 'LC 500'), you're fine as-is."
            )
        body_type_display = st.selectbox(
            "Body Type (optional)", options=list(BODY_TYPES.keys()), key="body_type_select",
            help=("Leave as 'Any' to see every body style. Picking one keeps only listings "
                  "AutoScout24 tags with that body type (checked locally after results come "
                  "back). It does not affect any rating.")
        )
        exclude_damaged = st.checkbox(
            "Exclude damaged/salvage listings from cards", value=True,
            help=(
                "Every listing's title is screened (German/Dutch/French/Italian/English) for "
                "accident damage, parts-only, lease/finance pricing, auctions, and export-only "
                "prices, via Gemini. Flagged listings are never removed from the results — with "
                "this on, they're just kept out of the card view above and still appear in the "
                "full results table below with the flag visible. Turn off if you specialise in "
                "repair-and-flip and want them in the cards too."
            ),
        )

        st.markdown("#### Import Destination")
        importer_country = st.selectbox("Your Country", list(SHIPPING_DISTANCES.keys()))
        city = st.text_input("City (optional — improves transport estimates)", placeholder="Warsaw")

    with col2:
        st.markdown("#### Filters")
        price_range = st.slider("Vehicle Price (€)", 1000, 300000, (20000, 150000), step=1000, format="€%d")
        mileage_range = st.slider("Mileage (km)", 0, 300000, (0, 150000), step=5000, format="%d km")
        year_range = st.slider("Registration Year", 2000, CURRENT_YEAR, (2015, CURRENT_YEAR), step=1)

        st.markdown("#### Transport Cost")
        st.caption(
            f"Estimate for one car on a shared open transporter, door-to-door: "
            f"€{TRANSPORT_FIXED_EUR} + distance × your per-km rate, minimum "
            f"€{TRANSPORT_MINIMUM_EUR}. Based on the Clicktrans 2024 marketplace report "
            f"(average Germany→Poland order ≈ €430) and carrier price lists (2025–2026). "
            f"Real quotes typically range from €100 + €0.30/km to €250 + €0.60/km; a "
            f"dedicated tow truck costs about 1.5–2× more and enclosed transport about "
            f"1.3–1.6× more. Some carriers quote including VAT, some without. **Not a "
            f"confirmed carrier quote.**"
        )
        cost_per_km = st.slider("Per-km rate (€/km)", 0.30, 0.60, TRANSPORT_RATE_PER_KM_DEFAULT,
                                step=0.05, format="€%.2f")

        distances, matched_city = get_distances(importer_country, city)
        if matched_city is None:
            known = [c for c in SHIPPING_DISTANCES.get(importer_country, {}) if c != "default"]
            typed = str(city or "").strip()
            st.warning(
                f"Using average distances for {importer_country}"
                + (f" — “{typed}” isn't in our distance database yet." if typed
                   else " — no city entered.")
                + (f" Cities we have exact distances for: {', '.join(known)}. " if known else " ")
                + "If you already know your real shipping cost, enter it below for an exact figure."
            )

        with st.expander("Already know your shipping cost? Override the estimate"):
            st.caption(
                "Enter your actual cost per vehicle from any source country. An entered value "
                "replaces the distance estimate entirely for that country's listings. Leave at 0 "
                "to keep using the estimate."
            )
            shipping_overrides = {}
            oc1, oc2 = st.columns(2)
            for i, country in enumerate(COUNTRIES):
                with (oc1 if i % 2 == 0 else oc2):
                    val = st.number_input(
                        f"{country} (€)", min_value=0, max_value=20000, value=0, step=25,
                        key=f"ship_override_{country}",
                    )
                    if val > 0:
                        shipping_overrides[country] = float(val)

        st.caption("**Estimated transport cost to your destination:**")
        for country in COUNTRIES:
            cost, is_user = resolve_transport_cost(country, distances, cost_per_km,
                                                   shipping_overrides)
            if cost is None:
                continue
            if is_user:
                st.caption(f"• {country}: **€{cost:,.0f}** (your figure)")
            else:
                st.caption(f"• {country}: ~{distances[country]:,} km → est. **€{cost:,.0f}**")

        with st.expander("Advanced"):
            use_residential_proxy = st.checkbox(
                "Use residential proxy for more complete listing details",
                value=False,
                help=(
                    "AutoScout24 sometimes returns partially empty details to datacenter "
                    "connections. A residential proxy reduces that — but Apify bills it at "
                    "$8/GB on top of the normal per-result cost, which can add up quickly on "
                    "large searches. Off by default."
                ),
            )
            st.caption(
                "Residential proxy traffic is billed separately by Apify (~$8/GB). Leave off "
                "unless you're seeing a lot of missing transmission/fuel/body-type fields."
            )

    search_clicked = st.button("Search", type="primary")

# Search execution
if search_clicked:
    if not model_input.strip():
        st.error("Please enter a Model before searching.")
    else:
        make_slug = MAKES[make_display]
        model_clean, generation_suffix = strip_generation_suffix(model_input)
        model_slug = slugify(model_clean)
        body_type_slug = BODY_TYPES[body_type_display]
        max_results = (PER_BAND_MAX_RESULTS_WITH_BODY_TYPE_FILTER if body_type_slug
                        else PER_BAND_MAX_RESULTS_DEFAULT)

        st.session_state.search_params = {
            "make": make_display, "model": model_input, "model_clean": model_clean,
            "body_type": body_type_display, "generation_suffix": generation_suffix,
            "price_range": price_range, "mileage_range": mileage_range,
            "year_range": year_range, "importer_country": importer_country,
            "city": city, "cost_per_km": cost_per_km,
            "matched_city": get_distances(importer_country, city)[1],
            "shipping_overrides": dict(shipping_overrides),
            "exclude_damaged": exclude_damaged,
        }

        all_results = []
        fetch_failures = []
        truncated_bands = []
        bands = compute_price_bands(price_range[0], price_range[1])
        # What the user sees while this runs is deliberately plain: which markets are being
        # searched and how far along it is. Price bands, run ids and provider names are
        # internal detail and stay out of the interface.
        failed_steps = []
        last_failure_detail = None
        search_text = f"Searching AutoScout24 in {SEARCH_MARKETS_TEXT}…"
        progress_bar = st.progress(0.0, text=f"{search_text} (step 1 of {len(bands)})")

        for step_num, band in enumerate(bands, start=1):
            label = format_price_band(band)
            progress_bar.progress((step_num - 1) / len(bands),
                                  text=f"{search_text} (step {step_num} of {len(bands)})")

            run_id, start_error = run_apify_band_search(
                make_slug=make_slug, model_slug=model_slug,
                band_min=band[0], band_max=band[1],
                min_year=year_range[0], max_year=year_range[1],
                max_mileage=mileage_range[1],
                cy_codes=[AS24_CY_CODES[c] for c in COUNTRIES],
                max_results=max_results,
                use_residential_proxy=use_residential_proxy
            )

            if run_id:
                status = poll_run(run_id)
                if status == "SUCCEEDED":
                    items = get_results(run_id)
                    if items is None:
                        # The run finished but we couldn't retrieve its output. That is NOT
                        # the same as "this band has no matching cars", so it must not be
                        # passed off as a clean zero.
                        fetch_failures.append(label)
                    else:
                        all_results.extend(items)
                        # The actor returning exactly the requested cap almost certainly means
                        # this band has more listings than we asked for — never trust that a
                        # round number at the cap means "that's all there is." Flagged here (not
                        # silently) since every median/count below is computed from this slice.
                        if is_market_truncated(len(items), max_results):
                            truncated_bands.append(label)
                else:
                    failed_steps.append(label)
                    last_failure_detail = status
            else:
                failed_steps.append(label)
                last_failure_detail = start_error

        st.session_state.search_params["truncated_bands"] = truncated_bands
        st.session_state.search_params["per_band_max_results"] = max_results
        incomplete_steps = len(fetch_failures) + len(failed_steps)

        if not all_results:
            # Clear any previous successful search before erroring. Without this, the old
            # results stay in session state and any later widget interaction re-renders
            # them, silently showing stale data for a search that actually returned nothing.
            st.session_state.results_df = None
            st.session_state.search_done = False
            progress_bar.empty()
            if failed_steps:
                # Nothing came back because the search itself failed, not because there are
                # no cars. The technical detail is shown here (and only here) so a missing
                # or invalid key can actually be diagnosed.
                st.error(
                    f"The search could not be completed, so we don't know whether there are "
                    f"matching cars. Please check your setup and try again. "
                    f"(Technical detail: {last_failure_detail})"
                )
            elif fetch_failures:
                st.error(
                    "No results could be shown. The search ran but its results could not be "
                    "retrieved, so we don't know whether there were matches. Please try again "
                    "before concluding there are no cars."
                )
            else:
                st.error("No results found. Try widening your price, mileage, or year range.")
            st.stop()

        if incomplete_steps:
            st.warning(
                f"Some results are missing: {incomplete_steps} of {len(bands)} parts of this "
                f"search did not return data, so any cars they had (across all markets) are "
                f"missing from everything below. This is not the same as them having no "
                f"matches."
            )

        # Process
        try:
            progress_bar.progress(1.0, text="Removing duplicates and checking filters…")
            df, missing_fields = clean_and_filter_listings(
                all_results, price_range, mileage_range, year_range, body_type_slug)
            if missing_fields:
                st.error(
                    f"The data source returned results in an unexpected format (missing: "
                    f"{', '.join(missing_fields)}). This usually means AutoScout24 or the actor "
                    f"changed something on their end — not a problem with your search. No results "
                    f"are shown rather than risking incorrect ones."
                )
                st.stop()

            # Deduplicate BEFORE any statistics (counts, medians, group stats) are computed —
            # a car cross-listed on two national domains must not be counted or costed
            # twice. See deduplicate_listings() for the two-tier matching logic.
            df, duplicates_merged = deduplicate_listings(df)
            st.session_state.search_params["duplicates_merged"] = duplicates_merged

            # Damage/salvage/pricing-caveat title screening via Gemini.
            # Runs on every search regardless of the "exclude damaged" toggle — the toggle
            # only controls the card-view filter below, not whether screening happens, so
            # the flag is always available and never hidden. Deliberately after dedup: a
            # duplicate title must not be paid for and screened twice.
            id_to_title = df["title"].fillna("").to_dict() if "title" in df.columns else {}
            progress_bar.progress(0.0, text="Checking listing titles for damage…")

            def _report_screening_progress(batch_num, total_batches):
                progress_bar.progress(min(batch_num / max(total_batches, 1), 1.0),
                                      text="Checking listing titles for damage…")

            flags, unavailable_ids, damage_tokens_used, damage_batches_run, damage_error = (
                screen_titles_for_damage(id_to_title, progress_fn=_report_screening_progress)
            )
            progress_bar.progress(1.0, text="Estimating import costs…")
            df["damage_category"] = [flags.get(idx, "UNAVAILABLE") for idx in df.index]
            df["is_flagged"] = df["damage_category"].isin(FLAGGED_DAMAGE_CATEGORIES)
            st.session_state.search_params["damage_screening"] = {
                "total_count": len(df),
                "unavailable_count": len(unavailable_ids),
                "flagged_count": int(df["is_flagged"].sum()),
                "tokens_used": damage_tokens_used,
                "batches_run": damage_batches_run,
                "error_summary": damage_error,
            }

            distances, _ = get_distances(importer_country, city)
            df = add_cost_columns(df, importer_country, distances, cost_per_km,
                                  shipping_overrides)
        except Exception as e:
            st.error(
                f"Something unexpected happened while processing the results (technical detail: {e}). "
                f"This usually means the data source's format changed. No results are shown rather "
                f"than risking incorrect ones — this is worth reporting so it can be fixed."
            )
            st.stop()

        st.session_state.results_df = df
        st.session_state.search_done = True
        st.rerun()

# Results
if st.session_state.search_done and st.session_state.results_df is not None:
    df = st.session_state.results_df
    params = st.session_state.search_params

    city_str = f", {params['city']}" if params.get("city") else ""
    body_type_str = f" · {params['body_type']}" if params.get("body_type") and params["body_type"] != "Any body type" else ""
    st.markdown(f"## {params['make']} {params['model']} — lowest estimated acquisition cost")
    st.markdown(
        f"> **{len(df)} vehicles found across {df['source_country'].nunique()} markets** · "
        f"€{params['price_range'][0]:,}–€{params['price_range'][1]:,} · "
        f"{params['mileage_range'][0]:,}–{params['mileage_range'][1]:,} km · "
        f"{params['year_range'][0]}–{params['year_range'][1]}{body_type_str} · "
        f"Import to {params['importer_country']}{city_str}"
    )

    if params.get("generation_suffix"):
        st.caption(
            f"Searched for '{params['model_clean']}' — AutoScout24 doesn't support generation numbers "
            f"like '{params['generation_suffix']}' as part of the model search. To narrow to a specific "
            f"generation, use the Registration Year filter instead."
        )

    duplicates_merged = params.get("duplicates_merged", 0)
    if duplicates_merged:
        listing_word = "listing" if duplicates_merged == 1 else "listings"
        st.caption(
            f"{duplicates_merged} duplicate {listing_word} merged — the same car was cross-listed "
            f"on more than one national AutoScout24 domain. Counts, medians, and totals below "
            f"reflect the deduplicated set."
        )

    # A price band that returned exactly the requested cap almost certainly has more
    # listings than we fetched — its slice of the count/median/"vehicles found" total
    # below is truncated, and that must not pass silently.
    truncated_bands = params.get("truncated_bands") or []
    if truncated_bands:
        st.warning("Some results may be missing: AutoScout24 limited part of this search.")

    # Damage/salvage screening summary — always shown, success or failure, so screening
    # status is never left implicit. Reports actual token usage, so free-tier fit can be
    # confirmed search by search.
    screening = params.get("damage_screening") or {}
    if screening:
        unavailable_n = screening.get("unavailable_count", 0)
        total_n = screening.get("total_count", 0)
        if total_n and unavailable_n == total_n:
            st.warning(
                f"Damage/salvage screening was unavailable for this entire search "
                f"({screening.get('error_summary') or 'Gemini API not reachable'}). "
                f"Listings below are shown **unflagged and unverified** — not confirmed clean."
            )
        else:
            screened_n = total_n - unavailable_n
            extra = (f" {unavailable_n} listing(s) could not be screened and are shown "
                     f"unverified." if unavailable_n else "")
            st.caption(
                f"Damage/salvage screening (Gemini {config.GEMINI_MODEL}): {screened_n} of "
                f"{total_n} listings screened in {screening.get('batches_run', 0)} API "
                f"call(s), ~{screening.get('tokens_used', 0):,} tokens used. "
                f"{screening.get('flagged_count', 0)} flagged.{extra}"
            )

    st.markdown("---")

    median_price = df["price"].median()

    costable_count = int(df["import_calculable"].sum())
    tax_name = destination_tax_name(params["importer_country"])
    lowest_total = df["total_cost"].min()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Vehicles Found", len(df))
    c2.metric(
        "Sample Median Price", f"€{median_price:,.0f}",
        help="Median of listings found in this search — not a market-wide figure. "
             "Discovery deliberately samples cheapest-first within each price band, "
             "so this skews lower than the true market median."
    )
    c3.metric("Lowest Price", f"€{df['price'].min():,.0f}")
    c4.metric(
        "Lowest Acquisition Cost",
        f"€{lowest_total:,.0f}" if pd.notna(lowest_total) else "—",
    )

    if costable_count == 0:
        st.warning(
            f"{tax_name} for {params['importer_country']} could not be calculated for any of "
            f"these listings, so no full acquisition cost is available. Each card explains what "
            f"was missing."
        )
    elif costable_count < len(df):
        st.caption(
            f"Full acquisition cost (including {params['importer_country']} "
            f"{tax_name.lower()}) could be calculated for {costable_count} of {len(df)} listings. "
            f"The rest are missing data the tax calculation needs — each card says which."
        )

    st.markdown("---")

    view = st.radio("Ranking", ["Lowest Acquisition Cost", "Lowest Vehicle Price"], horizontal=True)
    st.caption(
        "**AutoScout24 price rating:** each badge is AutoScout24's own price rating for "
        "that listing, computed against their full database — not our analysis, and not a "
        "comparison against the medians in this search (see why below). We don't know their "
        "exact methodology, so it's shown attributed rather than presented as our reasoning. "
        "Green: top or good price · Amber: fair price, or unusually low (verify before "
        "treating as a bargain — this reading came from junk/damaged listings in testing) · "
        "Red: priced high. "
        "A grey badge means AutoScout24 hasn't rated that listing (insufficient reference "
        "data on their end), or returned a rating we haven't seen before — shown as reported "
        "either way, never guessed at."
    )
    sort_col = "total_cost" if "Acquisition Cost" in view else "price"
    if sort_col == "total_cost":
        # Complete costs always rank above incomplete ones: a figure with no tax in it
        # is not comparable to a full total and must not float to the top. Incomplete
        # listings follow, ordered by price + transport. See acquisition_cost_sort_key().
        sort_key = acquisition_cost_sort_key(df)
    else:
        sort_key = df["price"]
    # Every result is shown somewhere — the rest render as a sortable table further down,
    # not silently dropped.
    # Cards are chosen only from listings that passed damage screening clean when the
    # "exclude damaged" toggle is on — flagged listings are never dropped, just kept out
    # of this slice and shown in the table below with their flag visible instead. See
    # split_cards_with_damage_filter().
    exclude_damaged = params.get("exclude_damaged", True)
    df_sorted, df_remaining = split_cards_with_damage_filter(
        df, sort_key, CARDS_SHOWN, exclude_damaged)
    if len(df) > CARDS_SHOWN:
        st.caption(
            f"Showing the top {CARDS_SHOWN} as full cards, ranked by {view}. "
            f"All {len(df_remaining)} other results are in the sortable table below the cards — "
            f"nothing is hidden."
        )
    if exclude_damaged and df["is_flagged"].any():
        st.caption(
            "Flagged listings (damage, parts-only, lease/finance pricing, auction, or "
            "export-only) are excluded from the cards above, per the toggle — they're still "
            "in the table below with their flag visible, not hidden from the results."
        )

    for _, row in df_sorted.iterrows():
        st.markdown(build_listing_card_html(row, params), unsafe_allow_html=True)

    st.caption(VAT_DISCLAIMER)
    st.caption(
        f"{tax_name} and registration fees are calculated for **{params['importer_country']}** "
        f"from published national rates. Currency conversions use ECB reference rates as of "
        f"{EXCHANGE_RATES_AS_OF}. Customs duty is €0 — all source markets are inside the EU."
    )

    # The remaining results — never dropped, just not shown as full cards. Independently
    # sortable so the user can hunt this slice their own way rather than only the ranking
    # chosen for the cards above.
    if not df_remaining.empty:
        st.markdown("---")
        st.markdown(f"### All Other Results ({len(df_remaining):,})")
        table_sort_label = st.selectbox(
            "Sort by",
            ["Total Acquisition Cost", "Vehicle Price", "Mileage"],
            key="remaining_results_sort",
        )
        table_sort_col = {
            "Total Acquisition Cost": "total_cost",
            "Vehicle Price": "price",
            "Mileage": "mileageKm",
        }[table_sort_label]
        if table_sort_col == "total_cost":
            table_sort_key = acquisition_cost_sort_key(df_remaining)
        else:
            table_sort_key = df_remaining[table_sort_col]
        table_df = df_remaining.assign(_table_sort_key=table_sort_key).sort_values("_table_sort_key")

        # Only https:// listing URLs become links; anything else is left blank.
        if "url" in table_df.columns:
            listing_links = table_df["url"].map(safe_listing_url)
        else:
            listing_links = None
        display_table = pd.DataFrame({
            "Title": table_df.get("title", "N/A"),
            "Year": table_df["year"],
            "Mileage (km)": table_df["mileageKm"],
            "Price (€)": table_df["price"],
            "Transport (€)": table_df["shipping_est"],
            "Total Acquisition Cost (€)": table_df["total_cost"],
            "Market": table_df["effective_country"],
            "Body Type": table_df["body_type_group"],
            # Flagged/unscreened listings excluded from the cards above still show their
            # flag here — never dropped from the results.
            "Screening": table_df.get("damage_category", "CLEAN").map(
                lambda c: DAMAGE_CATEGORY_LABELS.get(c, c)),
            "Listing": listing_links,
        })
        st.dataframe(
            display_table,
            column_config={
                "Listing": st.column_config.LinkColumn("Listing", display_text="View →"),
                "Price (€)": st.column_config.NumberColumn(format="€%.0f"),
                "Transport (€)": st.column_config.NumberColumn(format="€%.0f"),
                "Total Acquisition Cost (€)": st.column_config.NumberColumn(
                    format="€%.0f", help="Blank means it couldn't be calculated for this listing."),
                "Mileage (km)": st.column_config.NumberColumn(format="%.0f km"),
            },
            hide_index=True,
            width="stretch",
        )

    st.markdown("---")
    st.markdown("### Market Comparison (Sample Medians)")
    st.caption(
        "Grouped by each listing's own country field — where the car actually is — not "
        "which national AutoScout24 site it was scraped from. A car cross-listed across "
        "domains is counted once, under the country it's actually in. **These are sample "
        "medians** — the median of listings this search found, not a market-wide figure. "
        "Discovery deliberately stratifies the price range and sorts each band "
        "cheapest-first, so the sample skews lower than the true market."
    )

    market_stats = build_market_comparison(df)

    # Same honesty rule already applied elsewhere: a "median" drawn from one or two listings
    # isn't a reliable figure, it's just those listings. Showing it in the same confident
    # style as a market with twenty would invite a comparison that isn't supported.
    thin_markets = market_stats["Vehicles"] < MIN_GROUP_SIZE_FOR_SCORING
    for col in ["Sample Median Price (€)", "Sample Median Transport (€)",
                "Sample Median Import Costs (€)", "Sample Median Acquisition (€)"]:
        market_stats[col] = [
            "Not enough data" if thin else (f"€{x:,.0f}" if pd.notna(x) else "—")
            for x, thin in zip(market_stats[col], thin_markets)
        ]
    st.dataframe(market_stats, width="stretch", hide_index=True)
    st.caption(
        f"Sample median import costs shown are {params['importer_country']} {tax_name.lower()} plus "
        f"registration and inspection fees. A dash means no listing in that market had the data "
        f"needed to calculate it. Markets with fewer than {MIN_GROUP_SIZE_FOR_SCORING} listings "
        f"show \"Not enough data\" — a median from one or two cars isn't a reliable figure."
    )
    st.caption(
        "Each column above is its own independent sample median — Price, Transport, and "
        "Import Costs are not all taken from the same car. Adding the three across a row "
        "won't always match Sample Median Acquisition exactly (e.g. some cars in a market "
        "carry a non-zero excise that others don't, which shifts the acquisition median "
        "without shifting the import-cost median by the same amount). This is expected, "
        "not an error."
    )

    # The chart follows the same rule: a market too thin for a trustworthy sample median in
    # the table is also too thin to be a bar someone compares against.
    market_counts = df.groupby("effective_country")["price"].count()
    eligible = market_counts[market_counts >= MIN_GROUP_SIZE_FOR_SCORING].index
    chart_data = (df[df["effective_country"].isin(eligible)]
                    .groupby("effective_country")["total_cost"].median()
                    .reset_index().dropna(subset=["total_cost"]))
    if len(chart_data) > 1:
        st.markdown("### Estimated Sample Median Acquisition Cost by Market")
        chart_data.columns = ["Market", "Sample Median Acquisition Cost (€)"]
        chart_data = chart_data.sort_values("Sample Median Acquisition Cost (€)")
        fig = px.bar(chart_data, x="Market", y="Sample Median Acquisition Cost (€)",
                     text="Sample Median Acquisition Cost (€)")
        # One brand colour, transparent background: Streamlit's theme supplies the text
        # and grid colours, so the chart reads correctly in both light and dark.
        fig.update_traces(texttemplate="€%{text:,.0f}", textposition="outside",
                          marker_color=BRAND_ORANGE)
        fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                          showlegend=False)
        st.plotly_chart(fig, width="stretch")
    else:
        st.info(
            f"The acquisition cost chart needs at least 2 markets that each have "
            f"{MIN_GROUP_SIZE_FOR_SCORING}+ listings and a full acquisition cost calculated. "
            f"Try widening your filters."
        )
