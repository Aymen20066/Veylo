Historical working document from the prototype phase, kept for transparency.

# Apify Investigation — Test Results (2026-09-03)

Account used: a second test account (temporary — spending cap on primary account).
Actor: `blackfalcondata/autoscout24-scraper`. Pricing model: PAY_PER_EVENT, $0.005/run-start (one-time) + $0.0008/result event, plus separate platform usage (compute/data-transfer) billed to the user.

---

## TEST F — Does single-pass enrichment hold at volume?

**Query:** BMW 5-series, Germany only, €5k-50k, 2015-2026, `condition=used`, `compact=false`, `includeDetails=true`, `maxResults=100`. Run ID: `MMUhF6CASA9D8pySn`.

**1. Results returned:** **100/100** requested — full match. Underlying market for this filter is 4,216 listings (per the run log's pagination count); the actor pulled 20 SERP pages, deduplicated 39 cross-page overlaps down to 100 unique listings, then enriched all 100.

**2. Duration: 68.9s wall-clock (64.1s actor runtime).** Breakdown from the run log:
- SERP search/pagination: ~2.4s
- Detail-page enrichment (concurrency=3, 100 listings): **~54.2s** — this is the dominant cost
- Dataset push/finalize: ~4.3s

So: enriching 100 listings takes about **1 minute**, not 5 — but it scales roughly linearly with concurrency=3, so this doesn't imply enriching 1,000 listings takes 10 minutes without checking if concurrency changes at larger volumes (not tested here).

**3. Exact total cost: $0.0856443** (settled).
- Event fees: $0.005 (1× actor-start) + $0.08 (100× result @ $0.0008) = $0.085
- **Platform usage portion: $0.0006444** — compute units ($0.00082) + dataset writes ($0.0005) + data transfer (~$0.00006), all negligible
- **Residential proxy: $0 billed** (`PROXY_RESIDENTIAL_TRANSFER_GBYTES: 0`)

**Discrepancy worth flagging:** the run log explicitly states `Proxy: enabled (RESIDENTIAL, rotating per attempt, maxAttempts=4)` — so residential proxy rotation was actively used for the 100 detail-page fetches — but the account's Apify usage shows **zero** residential proxy bytes billed. The most likely explanation is the actor developer runs their own proxy pool outside Apify's pass-through proxy service and has priced that cost into the flat $0.0008/result event rate, rather than surfacing it as separate `PROXY_RESIDENTIAL_TRANSFER_GBYTES` usage. Not confirmed from the outside, but consistent with what's billed. Practically: **no surprise proxy cost materializes on your account regardless of enrichment volume**, at least not through this channel.

**4. Field population at volume (100 enriched listings):**
- `engineDisplacementCcm`: **99/100 populated** — the 1 null is a Diesel BMW 520d ("BMW 520 520d Touring M Sport | 20 Zoll | AHK"), **not an EV** — this is a genuine data gap, not an explainable zero.
- `emissionClass`: **89/100 populated**, 11 null. No pattern by fuel type or age — nulls span Diesel/Gasoline/Electric-Gasoline hybrids and registration years 2017–2025. Looks like real-world incompleteness in AutoScout24's own listing data, not an actor bug.

**This is the real success rate to plan around: ~99% for engine size, ~89% for emissions class — not 100%, even with full enrichment.** Confirms the project's existing "never fabricate — show 'unknown' when data is missing" principle stays load-bearing even after paying for enrichment; it reduces but does not eliminate missing-field handling.

**5. Errors / retries / rate-limiting:** **None visible in the run log.** Full log:
```
Proxy: enabled (RESIDENTIAL, rotating per attempt, maxAttempts=4)
Starting {"searchUrls":1,"detailUrls":0,"maxResults":100}
Page 1: 19 listings (total: 4216, pages: 200)
SERP dedup: 39 duplicates across pages
SERP complete: 100 unique listings
Fetching details for 100 listings (concurrency=3, memory=256 MB, perItemBudgetMs=6000, hardTimeoutMs=7000)...
Detail enrichment: 100/100 enriched
Done. Pushed 100 items.
✓ 100 new/updated listings exported
```
`exitCode: 0`, no WARN/ERROR lines besides the routine dedup notice. Caveat: this is INFO-level logging only — no per-request attempt/retry detail is surfaced, so a clean aggregate result doesn't rule out individual retries happening silently under the hood within the 6-7s per-item timeout budget.

**Bottom line for the two-pass question:** at this volume (100), single-pass enrichment works — 100/100 returned, ~1 minute, $0.086, no proxy surprises, no errors. The only real caveat is the ~89-99% field success rate, which is a data-completeness problem to handle with "unknown" fallbacks, not a retrieval-strategy problem requiring a second pass.

---

## TEST B — Enrichment cost

**Query:** 5 listing URLs (from the earlier BMW 5-series/Germany search) via `searchUrls`, `includeDetails=true`, `compact=false`, `maxResults=5`.

- Run ID: `5Oa46cJhCy3lyB6QX`, duration 9.1s, 5/5 items returned.
- **Exact total cost (settled): $0.009084**
  - Event fees: $0.005 (1× actor-start) + $0.004 (5× result @ $0.0008) = **$0.009**
  - **Platform usage portion: $0.000084** (compute units + dataset writes + data transfer — no proxy cost, `PROXY_RESIDENTIAL_TRANSFER_GBYTES` was 0)

**Platform usage is negligible** — detail fetching did NOT trigger expensive proxy traffic in this run. No `proxyConfiguration` was set, and all 5 detail pages still returned fully populated data (see below), despite the actor's own input-schema description warning that datacenter IPs get challenged on detail pages without residential proxy. That warning did not materialize here.

**Per-listing enrichment cost — two numbers, because "per-listing" isn't single-valued:**
- **Marginal/variable cost: $0.0008/listing** — identical to a non-enriched result event. Apify does not charge a premium event price for `includeDetails=true`; the per-item event price is the same regardless.
- **Batch-average cost at n=5: $0.0018/listing** — higher only because the $0.005 actor-start fee is a *fixed one-time* cost per run, amortized over just 5 items here. At larger batch sizes this average converges toward the $0.0008 marginal rate.

**Bonus finding (not asked, but relevant):** all 5 enriched listings came back with `engineDisplacementCcm`, `emissionClass`, `bodyType`, `vatDeductible`, `hadAccident`, `previousOwners` fully populated (non-null) — confirming `includeDetails=true` is what actually unlocks the tax-calculation fields that were null in both compact and standard modes in the prior test round.

---

## TEST E — Does priceEvaluation catch damaged cars?

**Query:** same `sort=price&desc=0` searchUrls query (BMW 5-series, Germany, no price floor), `maxResults=30`, `compact=false`, `includeDetails=false`.

- Run ID: `ZLsSajcdI3YiW4rkh`, 30/30 items returned.
- **Exact cost: $0.029207** ($0.005 start + $0.024 for 30 results + ~$0.0002 platform usage) — reported for completeness, not asked for explicitly but consistent with the $0.0008/result rate.

### Full 30 results (ascending price order, confirmed correct)

| # | Price | Mileage | Reg | priceEvaluation | Title |
|---|---|---|---|---|---|
| 1 | €1 | 170,000 | 02-2010 | toolow-price | BMW 520 520i |
| 2 | €444 | 180,000 | 02-1998 | toolow-price | BMW 520 520i |
| 3 | €600 | 251,000 | 12-2003 | toolow-price | BMW 520 520i touring Edition Exclusive |
| 4 | €699 | 257,291 | 06-1997 | toolow-price | BMW 520 520i |
| 5 | €750 | 460,000 | 06-2004 | toolow-price | BMW 525 525d Touring Aut. |
| 6 | €800 | 360,000 | 10-2003 | toolow-price | BMW 520 520i |
| 7 | €840 | 315,384 | 08-2000 | unknown | BMW 523 5er 523i touring |
| 8 | €890 | 348,000 | 12-2000 | toolow-price | BMW 520 i Touring KLIMA NAVI Leder FACELIFT |
| 9 | €900 | 260,000 | 02-2008 | unknown | BMW 523 523i |
| 10 | €900 | 400,000 | 12-1998 | toolow-price | BMW 525 525 |
| 11 | €950 | 216,000 | 03-1998 | toolow-price | BMW 520 520i |
| 12 | €999 | 204,500 | 06-2003 | toolow-price | BMW 520 5er 520i |
| 13 | €999 | 218,000 | 08-2003 | toolow-price | BMW 520 520i touring |
| 14 | €1,000 | 330,000 | 12-2000 | toolow-price | BMW 525 525i touring |
| 15 | €1,000 | 280,000 | 01-1997 | unknown | BMW 523 523i |
| 16 | €1,100 | 341,929 | 09-2003 | toolow-price | BMW 525 d A Exclusive touring Automatik Leder Alu |
| 17 | €1,200 | 370,000 | 10-2006 | toolow-price | BMW 520 520 d |
| 18 | €1,200 | 334,250 | 08-2002 | toolow-price | BMW 525 i A Lifestyle Automatik Klima Leder AHK |
| 19 | €1,200 | 342,000 | 06-2002 | toolow-price | BMW 525 525d touring |
| 20 | €1,200 | 315,000 | 01-2007 | toolow-price | BMW 530 d Touring *Klima.+Xen+Leder+HUD+AHK+Alu |
| 21 | €1,250 | 330,000 | 08-1999 | toolow-price | BMW 530 530d |
| 22 | €1,250 | 300,000 | 10-1999 | unknown | BMW 523 523i touring |
| 23 | €1,299 | 270,510 | 02-2006 | unknown | BMW 523 5 Touring 523 i NAVI*Panorama-dach* |
| 24 | €1,300 | 280,000 | 02-2005 | toolow-price | BMW 520 i Klima Tempomat Xenon Navi 6 Zelinder |
| 25 | €1,300 | 360,000 | 10-2002 | toolow-price | BMW 525 525d touring Edition Exclusive |
| 26 | €1,300 | 194,000 | 07-2000 | toolow-price | BMW 520 520i touring |
| 27 | €1,390 | 239,987 | 11-2006 | **unknown** | BMW 523 i Touring*Aut*Pano*Leder*Navi***MOTORSCHADEN*** |
| 28 | €1,390 | 257,000 | 06-2002 | toolow-price | BMW 520 520i |
| 29 | €1,400 | 221,479 | 03-1997 | toolow-price | BMW 520 520i |
| 30 | €1,453 | 312,000 | 09-2007 | unknown | BMW 523 5 Touring 523i |

### Distinct `priceEvaluation` values observed
Only two: **`"toolow-price "`** (note: trailing space in the raw string — verbatim API quirk) and **`"unknown"`**. None of the values from earlier documentation/assumptions (`good-price`, `top-price`, `somewhat-expensive`) appear in this price band — expected, since every listing here is far under market rate.

### Does damage get a worse rating? — No evidence that it does.

Only one listing in this batch has a damage keyword in its title: **#27, "...MOTORSCHADEN" (engine damage), €1,390 → `unknown`**.

Comparing it to clean-titled listings at essentially the same price — #26 (€1,300, `toolow-price`), #28 (€1,390, `toolow-price`), #29 (€1,400, `toolow-price`) — the damaged listing got a *different* value (`unknown` vs. `toolow-price`), but **`unknown` is not evidence of a worse rating.** Six other listings with no damage indicator whatsoever (#7, #9, #15, #22, #23, #30) also got `unknown`, scattered through the price range with no discernible pattern tied to condition. `unknown` looks like "AutoScout24 had insufficient reference data to rate this listing" — not a damage flag.

**Plain answer: No. `priceEvaluation` shows no sensitivity to disclosed damage.** It appears to be a pure price-vs-market-reference signal, blind to title content or condition. A car explicitly marked "MOTORSCHADEN" gets the same treatment as any other listing AutoScout24 can't confidently price — it is not down-rated *because* it's damaged. This is a single-listing sample (n=1 damaged), so it's not statistically strong, but it's consistent with `priceEvaluation` having no visibility into damage/condition signals at all, which matches how the field is described (price-based, not condition-based) and reinforces the project's existing "never trust priceEvaluation as a proxy for condition" caution.

---

## TEST G — Can we read the total match count cheaply? (2026-09-06)

**Question:** could price-band boundaries be set from the real price distribution (via cheap `maxResults=1` probes reading a total-match-count) instead of a fixed €15,000-per-band constant?

**Query:** BMW 5-series, Germany, wide range (€1,000–€300,000), `sort=price&desc=0`, `maxResults=1`, `includeDetails=false`. Run ID: `gqrAXnGHSE2UDJrgc`.

**1. Exact cost: $0.005188876710125142** (settled) — $0.005 actor-start + $0.0008 for 1 result + ~$0.0000009 negligible platform usage. Matches the ~$0.006/probe expectation.

**2. Is the total match count accessible anywhere other than the log? No.** Checked every structured surface:

| Surface | Checked | Result |
|---|---|---|
| Dataset item fields | Keys on the 1 returned item mentioning "count"/"total" | Only unrelated fields (`sellerRatingCount`, `imageCount`, `countryVersion`) — no match-count field |
| Dataset metadata (`GET /v2/datasets/{id}`) | `itemCount` | `0` (lags behind the actual 1 pushed item — not usable as a proxy for anything, let alone a market total) |
| Key-value store | All keys | Only `INPUT` (our own request echoed back) — no `OUTPUT` or stats record |
| Run object (`GET /v2/actor-runs/{id}`) | `stats`, `options`, all top-level fields | Compute/timing/memory stats only — no match-count field anywhere |
| Run log (`GET /v2/actor-runs/{id}/log`) | Full text | **`"7947 listings match this search."`** — present, but only here |

The number exists and is real (7,947 BMW 5-series listings in Germany between €1k–€300k), but it is **only ever printed as a free-text log line**, not exposed through any documented or structured API surface.

**Critical additional finding — the log message format itself is not stable.** Test F's log (100-item enrichment run, same actor) printed the total as part of a different message shape entirely:
```
Page 1: 20 listings (total: 4216, pages: 200)
```
This run printed:
```
7947 listings match this search.
```
Two different sentence structures from the same actor, observed across two of our own test runs. A regex written to parse one would not match the other. This means the log is not a version-pinned or documented contract — it's incidental debug output that has already changed shape once in our own testing history, with no changelog or schema backing it.

**3. Per-range probes — not run.** Per the original instruction ("if it IS accessible: run three more probes"), the gate wasn't met: the count is not accessible as structured data, only as an unstable log string. Running the three narrower-range probes would have confirmed the *number* varies sensibly by price range, but would not have changed the answer to the actual question (whether this is a *reliable, cheap* way to source real distribution data) — parsing a log format that has already drifted once is not something to build a pricing-critical feature (band boundaries) on. Stopped here rather than spending further for a result that wouldn't change the conclusion. Cost avoided: ~$0.017 (3 × ~$0.0057).

**Bottom line: no, not safely.** The total-match-count IS retrievable per price sub-range for ~$0.005/probe, which would make real-distribution-based band boundaries cheap in principle — but the only access path is regex-scraping an internal, undocumented, already-observed-to-change log line, not a stable field. Building `bands_for_price_range()` on that would mean the band-sizing logic could silently break (parse to `None`/0, or mis-extract a number) the next time the actor's authors tweak their own log wording, with no warning and no version to pin against. **Recommendation: keep the current reasoned-constant approach (`PRICE_BAND_WIDTH_EUR`, `MIN_PRICE_BANDS`, `MAX_PRICE_BANDS`) rather than wiring in log-scraping.** If Apify or the actor author ever expose total-match-count as a real schema field (e.g. in `OUTPUT` or the dataset metadata), this is worth revisiting — flagged here for that.

---

## TEST H — Gemini API verification for damage/salvage screening (A3, 2026-09-06)

Account: free tier, key from aistudio.google.com, stored as `GEMINI_API_KEY`. Purpose: settle every assumption in A3's design (model name, free-tier rate limit, and max reliable batch size for `responseSchema`-constrained classification) against real API behavior rather than documentation or blog posts, following the project's rule of verifying against real output.

### 1. Model name — `gemini-2.5-flash-lite` is retired

A direct call to `models/gemini-2.5-flash-lite:generateContent` returned:
```
404 NOT_FOUND: "This model models/gemini-2.5-flash-lite is no longer available to new
users. Please update your code to use models/gemini-3.5-flash-lite..."
```
`gemini-3.5-flash-lite` and the alias `gemini-flash-lite-latest` both returned `200`. **`gemini-3.5-flash-lite` is the model used going forward.** Google's own docs page for rate limits (`ai.google.dev/gemini-api/docs/rate-limits`) no longer publishes numeric free-tier limits at all — it redirects to a personalized AI Studio dashboard — so nothing here could have been confirmed by reading documentation; every number below came from deliberately triggering real API responses.

### 2. Free-tier rate limit — confirmed by triggering a real 429

25 requests fired back-to-back against `gemini-3.5-flash-lite` succeeded through request #15, then failed on #16 with:
```
429 RESOURCE_EXHAUSTED: "Quota exceeded for metric:
generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 15, model:
gemini-3.5-flash-lite" (quotaId: GenerateRequestsPerMinutePerProjectPerModel-FreeTier)
"Please retry in 35.104886228s."
```
**Confirmed: 15 requests/minute**, with an exact retry-delay hint in the error body (used for the retry-after-429 logic instead of a guessed backoff).

### 3. `responseSchema`-constrained batch classification — reliable, but with a hard property-count cliff

Design: one Gemini call per batch, with a JSON schema whose properties are the batch's own numeric keys ("0", "1", ...), each constrained to an enum of the 6 categories (DAMAGE, PARTS_ONLY, LEASE_OR_FINANCE_PRICE, AUCTION, EXPORT_PRICE, CLEAN). This should make the model's output structurally guaranteed to parse — tested against synthetic multilingual (DE/EN/FR/IT/NL) titles with a known expected category per title, at increasing batch sizes:

| Batch size | Result | Accuracy | Prompt+output tokens | Latency |
|---|---|---|---|---|
| 6 (real listing titles, hand-written) | 200 OK | 6/6 correct incl. a "Unfallfrei" (accident-**free**) title correctly NOT flagged as damage | 234+65=299 | ~1.1s |
| 50 | 200 OK | 50/50 (100%) | 1266+558=1824 | 2.2s |
| 75 | 200 OK | 75/75 (100%) | 1835+846=2681 | 3.0s |
| 90 | 200 OK | 90/90 (100%) | 2181+1014=3195 | 3.1s |
| 95 | 200 OK | 95/95 (100%) | 2291+1071=3362 | 3.4s |
| **96** | **400 INVALID_ARGUMENT** | — | — | 1.8s (near-instant — schema rejected before generation) |
| 97, 99, 100 | **400 INVALID_ARGUMENT**, same message every time | — | — | 0.9–1.0s |
| 150 | not separately tested | — | — | inferred, not measured — see below |

**Every batch from 50 through 95 titles classified with 100% accuracy against the known-correct label**, including correct handling of negation ("Unfallfrei" ≠ damage) and all 5 target languages. Accuracy is not the limiting factor at any tested size.

**The limiting factor is a hard, undocumented property-count cliff between 95 and 96.** All failures at 96+ returned the exact same generic `400 INVALID_ARGUMENT` message (no detail on which limit was hit) in under 2 seconds — consistent with the request being rejected during schema validation, before any model generation happens, not a token-length or output-truncation failure. This means the cause is almost certainly the *number of properties* in the `responseSchema` object itself, not title length or total prompt size (confirming this: the 95-item request that succeeded already carried more prompt tokens (2291) than plenty of successful smaller ones, so token volume isn't what broke at 96).

**150 was not tested directly** — once the cliff was found at 96, sending a request 50%+ larger than the confirmed failure point would only fail the same way for the same reason; that would have been spending free-tier quota to reconfirm an already-explained negative, not new information. This is stated explicitly as an inference from the 96 finding, not a separate measurement — the verify-against-real-output rule cuts both ways: don't assume a working case without testing it, but also don't claim to have tested a failing case that wasn't run.

**Decision: `DAMAGE_SCREEN_BATCH_SIZE = 80`.** Chosen with a 15-title margin below the confirmed 95-title ceiling (not pinned at exactly 95, since an undocumented internal limit could shift slightly) while still cutting the batch count roughly in half versus the original conservative default of 40 — e.g. a 280-listing search now needs ~4 calls instead of ~7, comfortably inside the confirmed 15 RPM limit either way. The 100-150 range the original plan hoped for ("2-3 calls instead of 7") is not achievable — it fails outright, not just less reliably.

## Transport estimate recalibrated (17 November 2026)

The earlier transport estimate (distance × €0.20/km) was found to be 2–4× too low against the Clicktrans 2024 marketplace report and carrier price lists, and was replaced with €150 + a per-km rate (default €0.40/km), minimum €300.
