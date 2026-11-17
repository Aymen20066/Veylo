"""AI title screening: Gemini classification of listing titles and rate-limit-aware batching."""
import json
import math
import re
import time

import requests

from veylo import config
from veylo.config import (
    DAMAGE_CATEGORIES,
    DAMAGE_SCREEN_BATCH_SIZE,
    DAMAGE_SCREENING_PROMPT_HEADER,
    GEMINI_MIN_SECONDS_BETWEEN_CALLS,
)


def _parse_retry_delay_seconds(error_text):
    """Extracts the exact retry delay Gemini's own 429 body names (e.g. "Please retry in
    35.1s") rather than guessing a fixed backoff. Returns None if the text doesn't contain
    one (e.g. a different error shape) so the caller can fall back to a safe default."""
    match = re.search(r"retry in ([\d.]+)s", error_text or "")
    return float(match.group(1)) if match else None


def classify_title_batch(titles_by_local_index, post_fn=requests.post):
    """One Gemini call classifying a batch of listing titles. titles_by_local_index is
    {"0": title, "1": title, ...} — short local indices, not full listing IDs, to avoid
    wasting tokens on UUIDs; the caller maps them back to real IDs immediately after this
    returns. post_fn is injectable so tests can exercise the failure paths without a
    network call.

    The response schema constrains every key to one of DAMAGE_CATEGORIES, which is what
    makes the output reliably parseable (see docs/experiments.md, TEST H) — not a guarantee
    Gemini honors on its own, so the result is still validated below rather than trusted
    blindly.

    Returns (result_dict_or_None, tokens_used, error_message). None means this batch could
    not be classified — a network error, a non-200 response, or a response that didn't
    parse or validate. The caller must never treat None as "these are all clean"; that is
    exactly the kind of invented answer this project forbids. tokens_used is 0 whenever no billable call
    actually completed."""
    if not config.GEMINI_API_KEY:
        return None, 0, "No Gemini API key configured"

    keys = list(titles_by_local_index.keys())
    schema = {
        "type": "OBJECT",
        "properties": {k: {"type": "STRING", "enum": DAMAGE_CATEGORIES} for k in keys},
        "required": keys,
    }
    prompt = DAMAGE_SCREENING_PROMPT_HEADER + json.dumps(titles_by_local_index, ensure_ascii=False)
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": schema,
        },
    }
    # The key travels in the x-goog-api-key header, never in the URL.
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_MODEL}"
           f":generateContent")
    headers = {"x-goog-api-key": config.GEMINI_API_KEY}

    try:
        response = post_fn(url, json=payload, headers=headers, timeout=30)
    except Exception as e:
        return None, 0, f"Request error: {e}"

    if response.status_code == 429:
        wait_s = _parse_retry_delay_seconds(response.text) or 30.0
        time.sleep(wait_s + 1.0)
        try:
            response = post_fn(url, json=payload, headers=headers, timeout=30)
        except Exception as e:
            return None, 0, f"Request error on retry: {e}"

    if response.status_code != 200:
        return None, 0, f"HTTP {response.status_code}: {response.text[:200]}"

    try:
        body = response.json()
        tokens = (body.get("usageMetadata") or {}).get("totalTokenCount", 0)
        text = body["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(text)
    except Exception as e:
        return None, 0, f"Unparseable response: {e}"

    # The schema should already guarantee this, but a partial or malformed batch must
    # never be treated as clean by silently filling in a default.
    if not all(k in parsed and parsed[k] in DAMAGE_CATEGORIES for k in keys):
        return None, tokens, "Response missing or invalid categories for one or more titles"

    return parsed, tokens, None


def screen_titles_for_damage(id_to_title, classify_fn=classify_title_batch,
                              batch_size=DAMAGE_SCREEN_BATCH_SIZE,
                              min_seconds_between_calls=GEMINI_MIN_SECONDS_BETWEEN_CALLS,
                              progress_fn=None):
    """Screens every listing's title for damage/salvage/pricing-caveat signals via Gemini,
    batched to respect the confirmed free-tier rate limit (GEMINI_FREE_TIER_RPM). Runs
    regardless of the "exclude damaged" toggle — that toggle only controls the card-view
    filter, not whether screening happens, so the flag is always available.

    Returns (flags, unavailable_ids, tokens_used, batches_run, error_summary):
      - flags: {listing_id: category} for every listing successfully classified
      - unavailable_ids: listing_ids screening could not complete for — shown as "not
        screened", never defaulted to CLEAN
      - error_summary: None if every batch succeeded, else a short human-readable note

    Every id in id_to_title ends up in exactly one of flags or unavailable_ids — nothing is
    silently dropped. classify_fn, batch_size, and min_seconds_between_calls are injectable
    so tests can exercise multi-batch ID-mapping and failure paths without a real network
    call or the real per-call throttle delay."""
    ids = list(id_to_title.keys())
    flags = {}
    unavailable_ids = set()
    tokens_used = 0
    batches_run = 0
    errors = []
    last_call_at = None
    total_batches = math.ceil(len(ids) / batch_size) if ids else 0

    for start in range(0, len(ids), batch_size):
        batch_ids = ids[start:start + batch_size]
        local_to_real = {str(i): rid for i, rid in enumerate(batch_ids)}
        titles_by_local_index = {k: id_to_title[rid] for k, rid in local_to_real.items()}

        if last_call_at is not None:
            elapsed = time.time() - last_call_at
            if elapsed < min_seconds_between_calls:
                time.sleep(min_seconds_between_calls - elapsed)

        if progress_fn:
            progress_fn(batches_run + 1, total_batches)
        result, tokens, error = classify_fn(titles_by_local_index)
        last_call_at = time.time()
        batches_run += 1
        tokens_used += tokens

        if result is None:
            unavailable_ids.update(batch_ids)
            errors.append(error)
            continue
        for local_key, real_id in local_to_real.items():
            flags[real_id] = result[local_key]

    error_summary = (f"{len(errors)} of {batches_run} screening batch(es) failed: {errors[0]}"
                      if errors else None)
    return flags, unavailable_ids, tokens_used, batches_run, error_summary
