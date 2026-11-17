Historical working document from the prototype phase, kept for transparency.

# VEYLO — Project Plan & Context

**Last updated:** 30 August 2026

---

## 1. What Veylo is

A B2B vehicle intelligence platform for professional used-car importers in **Poland, Romania, Bulgaria, Czech Republic** who source premium/luxury vehicles from **Germany, Belgium, France, Netherlands, Italy**.

**The core value proposition:** aggregate listings across multiple national AutoScout24 domains and calculate the **true total acquisition cost** — vehicle price + transport estimate + destination-country import tax — rather than just the sticker price. No competitor shows the tax breakdown.

**The pain being solved:** importers currently spend 1–3 hours per day manually checking multiple country sites, then working out transport and tax by hand in spreadsheets.

**Validation:** one Polish importer, an active cross-border buyer, confirmed the pain and a willingness to pay €50–150/month. Their specific requirement was total acquisition cost, not raw price. 19 LinkedIn outreaches sent, 1 full validation.

**Brand:** Pixel-art orange rearing horse (horsepower + digital). Primary color `#E64B1B`. Wordmark is Gotham Bold in the logo artwork only (licensed — vector outlines only, never as a web font); Montserrat is the live display face, Manrope for body, IBM Plex Mono for figures.

---

## 2. Product philosophy (non-negotiable)

Premium through **quality, restraint, reliability, and attention to detail** — not feature count.

- Never fabricate data, progress, statistics, scores, or functionality
- Every number must have a defensible source
- Fewer features, each genuinely excellent, beats many mediocre ones
- Flag uncertainty, never hide it — and never silently guess
- Quality > quantity · Reliability > novelty · Accuracy > impressive claims · Trust > marketing

**Key distinction learned the hard way:** *looking up a published specification* (a 520d has a 1,995cc engine) is not fabrication. *Assuming a value you don't know* (defaulting to Euro 6) is. The first is fine if labelled; the second never is.

---

## 3. Architecture — SETTLED, backed by real tests

### The retrieval funnel

1. **Stratified price bands.** Split the user's price range into bands and take the cheapest N *from each band*, not the cheapest N overall. Prevents the "search a €150k car with a €10k floor and get 50 wrecks" problem, and keeps the sample representative across the price distribution.
2. **Sort cheapest-first** via `searchUrls` with `sort=price&desc=0`. When a band hits its cap, the *expensive* end is cut, never cheap hidden gems.
3. **All countries in one URL** — `cy=D,A,I,B,NL,E,L,F` works. ~5 runs per search, not 25.
4. **`includeDetails=true` on everything — single pass.** Enrichment costs the same as a plain listing, so two-pass would mean paying twice for the same cars.
5. **Haiku damage screening.** LLM pass over titles/descriptions, not keyword matching (handles typos, casing, 5 languages). Flags, never hides. "Exclude damaged/salvage" toggle, default ON — some importers specialise in repair-and-flip, so OFF is a feature for them.
6. **`priceEvaluation` as the deal signal.** AutoScout24 computes it against their entire database — better market data than we could ever afford to pull, and it's free. Our own medians get relabelled as *sample* medians.
7. **Spec-table fallback for missing fields** (see §4).
8. **Search history.** Re-opening a past search is free and timestamped ("Results from 30 Aug, 14:20 — not live") with an explicit Refresh button that costs a scrape and says so.

### Cost

One search ≈ **$0.20** (250 listings fully enriched).

**Credit-based billing (Clay-style) was considered and dropped** — it only made sense when enrichment was assumed expensive. Test B proved it isn't. Simple daily search limits are cheaper to build and far easier to explain.

---

## 4. Handling the ~10% data gap

Even with full enrichment: `engineDisplacementCcm` populated ~99%, `emissionClass` ~89%.

**Engine displacement:** parse the variant from the title (e.g. "BMW 520 520d Touring M Sport" → `520d`), look up against a static manufacturer spec table. Display as *"1,995cc (from model specification — not stated in this listing)."* Build the table once via research; **never do live web lookups at runtime** (slow, costs money per call, non-deterministic).

**Emissions class:** derive a floor from registration date using EU mandatory dates (e.g. anything registered new after Sept 2015 must be at least Euro 6). Because higher Euro standards mean *lower* tax in both Romania and Czech, assuming the legal minimum yields the **higher** tax figure — never understates the importer's cost. Label as derived.

**If neither works:** show unavailable with the reason. Never estimate silently.

---

## 5. Verified test findings (do not re-test)

All against actor `blackfalcondata/autoscout24-scraper` (ID `Dckez3uXbF6dh6dMe`).

| Finding | Result |
|---|---|
| Base listing cost | $0.0008/listing + $0.005/run start |
| Enrichment (`includeDetails=true`) cost | **Also $0.0008** — no premium |
| 100 listings fully enriched | 68 seconds, $0.086 |
| Residential proxy cost | **$0** — actor absorbs it into the flat rate |
| `sort=price&desc=0` via `searchUrls` | **Works** — strictly ascending, 20/20 |
| `searchUrls` input format | Requires `[{"url": "..."}]` objects, not bare strings |
| Multi-country in one URL | Works — `cy=D,A,I,B,NL,E,L,F` |
| Actor sort parameter | **Does not exist** — hence the `searchUrls` workaround |
| Compact mode fields | `engineDisplacementCcm` / `emissionClass` **absent entirely** |
| Standard mode, no details | Fields present but **null** — enrichment is mandatory for tax |
| `priceEvaluation` availability | Free, populated, no enrichment needed |
| `priceEvaluation` vs damage | **Blind to condition** — a MOTORSCHADEN listing rated same as clean cars |
| `priceEvaluation` values seen | `"toolow-price "` (note trailing space — API quirk) and `"unknown"` |
| Junk listings | **Real** — a €1 BMW 520i (2010, 170k km) appeared at the top of a price-ascending search |
| Price distribution above €5k | Smooth ramp, no cliff; cheap cars are legitimately cheap (high mileage / old / disclosed damage) |
| Market size example | BMW 5-series, DE, €5–50k, 2015–26 = 4,216 listings |

**Apify account:** free tier is $5/month, hard-blocks when exhausted (no top-ups possible — confirmed with support). The primary account resets **13 September 2026**. Currently using a second test account temporarily. A real paid account is needed before production.

---

## 6. Backend status

**Done — 7 bugs fixed, 58 tests across 8 files, all passing, committed to git:**

1. Romania EV tax — rebuilt entirely. There is **no** one-time pollution/environmental tax in Romania (abolished 2017, never reintroduced). What applies at import is the **annual ownership tax**, first year prorated. EVs pay a flat 40 lei/year. Verified against Law 239/2025, GEO 78/2025, Codul fiscal Art. 468–472.
2. Deal scoring comparability — narrowed to body type + ±2 registration-year rolling band, minimum group size 3, degrades to "Not Enough Comparables" rather than guessing.
3. Duplicate cross-market listings — deduplicated, merge count disclosed in UI.
4. Cross-listed transport cost — uses the listing's own country, flags disagreement rather than resolving silently.
5. Undisclosed truncation — flagged when a market hits its result cap.
6. Hidden results beyond 20 — all results shown; top 20 as cards, remainder in a sortable table.
7. Market Comparison rows not summing — explained via caption (each column is an independent median).

**Also verified against primary sources:**
- **Poland:** excise 3.1% ≤2000cc, 18.6% above (Excise Tax Act Art. 105)
- **Bulgaria:** one-time product fee by age + fuel type. ICE 150/240/360/385 BGN; BEV 102/180/270/290 BGN (State Gazette 66, 23 Jul 2025, amending Ordinance 37/2004)
- **Czech:** one-time eco-fee by Euro standard — Euro 0: 10,000 / Euro 1: 5,000 / Euro 2: 3,000 / Euro 3+: 0 CZK (Act 634/2004 Sb., Annex Item 16). A Euro 3 extension was proposed in 2026 but is not law — re-check periodically.

**Tech:** Python 3.14, Streamlit (`app.py`), pandas/plotly/requests/python-dotenv. Git initialised, `.gitignore` excludes `.env`.

---

## 8. THE PLAN

### Track A — Backend to mint condition

- [ ] **A1. Rebuild discovery as the funnel** — stratified price bands, `sort=price&desc=0` via `searchUrls`, all countries in one URL, `includeDetails=true` single pass. *(Biggest task. Replaces the current `run_apify_search` approach.)*
- [ ] **A2. Swap deal scoring** to `priceEvaluation`; relabel own medians as sample medians. Handle the `"toolow-price "` trailing-space quirk.
- [ ] **A3. Damage screening layer** — Haiku pass over titles/descriptions, plus comparable-group deviation and an implausibility floor. Flag, never hide. Add "exclude damaged" toggle, default ON.
- [ ] **A4. Spec-table fallback** for missing `engineDisplacementCcm` / `emissionClass` (§4).
- [ ] **A5. Model field UX** — autocomplete dropdown + fuzzy matching + LLM fallback for ambiguous input. Currently free text, silently breaks on "5 series" vs "5-series" vs "520d". Now that a Gemini key exists, the ambiguity-resolution step can use it.
- [ ] **A5b. Generation-aware model filtering.** `firstRegistration` is when a car was put on the road, not its model generation — so an importer wanting an "F80 M3" has to guess the registration years. Build a generation lookup table (e.g. F80 → 2014–2018) so a user can pick a generation and have it translate to the right year range. Needs a per-make/model generation table; real work, not urgent.
- [ ] **A5c. Card ranking within bands.** Rank each band's listings by €/1,000km of remaining life (price ÷ (250,000 − mileage), age-adjusted), take the top 2–3 per band so cards span the full price range rather than collapsing to the cheapest band. **Important: this metric is a disclosed heuristic, not a truth claim.** The 250,000 km lifespan is an average that doesn't hold for every drivetrain (diesels run longer, high-strung sports engines shorter, EVs are battery- not km-limited), and it ignores options, spec, and service history. Show the metric on screen and let the user re-sort by price or mileage. Never label it "best car" — label it what it is.
- [ ] **A6. Search history & caching** — save every search by date, free re-opens, explicit refresh.
- [ ] **A7. Queued small fixes** — truncation banner wording ("try narrowing your price, mileage, or year range"), expandable detail rows in the "All Other Results" table.
- [ ] **A8. Chart review** — audit every chart for genuine user value vs. decoration.
- [ ] **A8b. Tune the price-band sizing against real search data.** Band count now scales with range width (~1 band per €15,000, clamped 2–8), but that €15,000 figure and the max of 8 are reasoned starting values, not measured. **Decide by looking at real searches for:** (a) how often a band returns exactly its cap — consistent capping means bands are too wide and truncation is happening where it matters; (b) bands returning near-zero results — wasted $0.005 run fees, too many bands for that range; (c) which bands the genuinely interesting cars actually land in — if everything useful clusters in bands 2–3, the extremes are wasted budget; (d) once A3 exists, whether the flagged-junk ratio differs sharply per band — a bottom band that's 80% wrecks is too wide. **Target: no band consistently at its cap, no band consistently near-empty.**
- [ ] **A9. Stress test everything manually** — critical-think every potential user misstep, miscalculation, and unreliable output.
- [ ] **A10. Legal verification** — engage professionals in PL/RO/BG/CZ to confirm all tax formulas before real money rides on them.
- [ ] **A11. Real paid Apify account** before production.

### Track B — Launch (independent of Track A)

- [ ] **B4. Demo video** — real footage of the real funnel, once A1 lands
- [ ] **B5. Resume LinkedIn outreach** with a live link
- [ ] **B6. Transporter partnerships, phase 1** — lightweight "recommended transporters" list (costs them nothing; waitlist numbers are a legitimate signal). Phase 2 (live API quotes) waits for real transaction volume.

### Later

- [ ] **mobile.de as a second source** (~$0.6/1K, cheaper than AutoScout24; Germany-only but Germany→Poland is the dominant corridor). Needs cross-source dedup — the same dealer often lists the same car on both. Sequence: prove AutoScout24 economics → add source → extend dedup.
- [ ] **La Centrale** — no actor confirmed to exist yet; verify before roadmapping.
- [ ] **React frontend** — migrate off Streamlit. Option: scaffold in Lovable (free tier), export via GitHub, develop locally with Claude Code. Requires Node.js. Real integration work; don't rush it under deadline.
- [ ] **Price forecasting (ML)** — roadmap only. Never advertise before it exists.
- [ ] **Cross-user caching** — weak at low user counts, strong later.

---

## 9. Reusable open-source references

Worth reading for approach, not adopting blindly (scraper code rots fast as sites change):

- A local AutoScout24 "flip-ranker" — predicts mispriced listings with an **LLM vetting pass that flags lease/auction/damaged listings**. Directly relevant to A3.
- **Carggregator** — multi-source car aggregator (AutoScout24 + motor.es + autocasion), Scrapy + Elasticsearch.
- A cross-site price-difference deal finder (anibis.ch vs autoscout24.ch).
- Several standalone Python AutoScout24 scrapers.

**None of these calculate cross-border import tax.** They solve "find a cheap car," not "find a car that's profitable to import into my country after excise duty." Different product, different buyer. Their existence confirms the problems are real and solvable — not that the market is taken.

---

## 10. Working notes

- Actor output vocabulary differs from input: output says "Gasoline" not "Petrol", "Electric/Gasoline" not "Hybrid"
- Body-type filtering must be done locally — the actor silently drops make/model filters when `bodyType` is combined
- `firstRegistration` is month-precision only (day is always "01") — affects VAT "new means of transport" edge cases
- Apify run costs settle a few seconds *after* a run finishes; read `usageTotalUsd` late or you'll under-report by ~99%
- Always cap test queries at `maxResults=20` — a single uncapped test run cost $2.03
- Ask Claude Code to save findings to a file; terminal copying is unreliable
