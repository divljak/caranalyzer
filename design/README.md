# Car Analyzer — design → code handoff

Four desktop pages designed in pen.dev, ready to be implemented: **Market**, **Model analysis**, **Compare**, **Following**.

```
car-analyzer-handoff/
├── README.md               ← this brief (give it to Codex)
├── screens/                ← 2× PNGs: the visual target
│   ├── 01-market.png
│   ├── 02-model-analysis.png
│   ├── 03-compare.png
│   └── 04-following.png
├── html/                   ← Tailwind HTML exports: exact spacing / sizes / colors reference
├── tokens/tokens.css       ← CSS variables (colors, font, radius, spacing)
├── tokens/tokens.json      ← same tokens + type scale as JSON
└── data/sample-data.json   ← the illustrative data shown in the designs (NOT real data)
```

> The HTML files are a **reference**, not production code: they use fixed pixel widths and absolute positioning for chart marks. Rebuild them as real components with flex/grid, fluid widths and computed chart positions.

---

## 1. Product context

Private daily car-market evidence dashboard for a Bosnia-based dealer who buys cars in the EU, imports them and sells on **OLX.ba**. It helps decide **which models/specifications to investigate next** from observed Bosnia **asking prices** and supply. Currency is **KM** (convertible mark); mileage is **km**.

It is not: stock management, import-profit calculation, bookmarking individual cars, or a sales/demand predictor.

## 2. Tech expectations

- Use the framework/styling already in the repo. If starting fresh: **React + TypeScript + Vite + Tailwind CSS v4**, React Router for the 4 routes.
- Import `tokens/tokens.css` and map tokens to Tailwind theme values. **Never hardcode hex values in components.**
- Font: **Geist** (Google Fonts). Icons: **lucide-react** (names used: `layout-grid`, `chart-line`, `columns-3`, `eye`, `eye-off`, `database`, `target`, `history`, `info`, `sliders-horizontal`, `chevron-down/up/right`, `arrow-left`, `x`, `check`, `triangle-alert`, `refresh-cw`, `car-front`, `circle-help`).
- Load data from the existing local database/API. Until then, use `data/sample-data.json` as a mock.

## 3. Shared shell (all 4 pages)

- **Page:** `bg-canvas` background, 24px outer padding, 24px gap between the sidebar and the main column.
- **Floating sidebar:** 320px wide, `surface` background, 20px radius, 24px padding, full height. From top to bottom:
  - Brand: a round blue mark with a `car-front` icon, "Car Analyzer" and "OLX.ba · Bosnia".
  - Navigation: 4 items. The active item has a `surface-raised` background, a blue icon and a 500-weight label.
  - A page-specific panel (filters, or the saved-search note on Following).
  - A spacer.
  - A **Last updated** block: `database` icon, "Last updated" and the date of the latest database update. Show only this.
- **Header:**
  - Title: 44px, letter-spacing −1.4.
  - Meta line: "Sample · asking prices in KM" · dot · the **active scope** (e.g. "SUV · 2020 – 2023 · ceiling 50,000 KM · last 30 days").
  - **About the data** disclosure on the right. It holds every methodology explanation; don't repeat disclaimers elsewhere.
- **Blue band** (`accent-blue` background, `on-accent` text, 20px radius): exactly one per page. It highlights the single most useful thing for that page. Items are split by 1px dividers (`#12121426`).
- **Cautions:** pink (`caution`), always icon (`triangle-alert`) **plus text**, never color alone. Rule: fewer than 10 priced ads = small sample.
- **Focus:** visible 2px `focus-ring` outline. Touch targets are at least 44px.

## 4. Pages

### 4.1 Market (`/market`) — `screens/01-market.png`
- **Sidebar filters:**
  - OLX asking-price ceiling (KM), with a hint that it's selling-price positioning, not the EU budget.
  - Category, Year from, Year to.
  - **More filters**: mileage range, fuel, gearbox, observation window (7/14/30/90/365 d). Applied values show as removable chips.
  - "Search models" button and a filter-count badge.
- **Suggestion band: "Suggested to investigate".** Models that meet all 4 signals. Each item shows the model, typical price, a one-line reason (e.g. "88% of ads under your ceiling · 9% cut · 34 ads") and "Analyze →". It also has a "How suggestions work" disclosure.
- **Model table, on one shared KM scale** (20k–70k):
  - **Header row:** the KM ticks and a "50k ceiling" label. The ceiling line runs vertically through **every** row.
  - **Each row:**
    - a select checkbox (adds the model to the compare tray)
    - model and years
    - the middle-50% band (p25–p75) with a median dot and the median value label
    - columns **Under** (% within ceiling), **Cut** (% with cuts) and **Ads**; values that meet their signal render bright, others muted
    - a chevron that goes to Model analysis
  - Small samples show a caution icon and "small sample" text.
  - No grouping inside the table.
- **Footer:** a legend on the left and "Show N more" (pagination) on the right.
- **Compare tray:** a pill bar with "N selected", removable model chips and "Compare N models" (primary). Up to 3 models.

**Suggestion signals.** Compute these per model from the current filtered cohort:

| Signal | Rule |
|---|---|
| Fits ceiling | `withinCeiling / pricedAds ≥ 0.60` |
| Steady prices | `adsWithCuts / pricedAds ≤ 0.15` |
| Enough evidence | `pricedAds ≥ 20` |
| Tight range | `(p75 − p25) / median ≤ 0.20` |

- "Suggested" means all 4 signals are met.
- The default sort is signals met (descending), then priced ads.
- Other sorts: Most observed ads, Lowest typical price, Latest observation, Most price cuts.
- Keep thresholds in one config object.

### 4.2 Model analysis (`/models/:make/:model`) — `screens/02-model-analysis.png`
- **Sidebar filters:** Make, Model, Year from/to, plus More filters (mileage, fuel, gearbox, window) and an **Analyze** button. Header has "← Market" back link; scope line lists the exact specs (filtered sample ≠ whole model market).
- **Summary band:**
  - model name and represented years
  - **typical asking price** (median, large)
  - middle 50%, priced ads, ads with cuts (count and %), last observation
  - actions: + Compare, Follow
- **By year table:**
  - Each year row shows a band on a shared KM scale (30k–55k), Typical, Middle 50%, Priced ads (with caution if under 10) and a chevron.
  - Clicking a row **drills into that year**. Show a "Back to 2020 – 2023" control.
  - No min/max prices and no duplicate year chart.
- **Monthly chart:**
  - One chart covering the last 12 months: middle-50% band and median per month, plus priced-ad count bars, with KM and month labels.
  - The selected month shows in a detail strip. It must work by click and keyboard (arrow keys), not hover only.
  - Mark the partial current month.
  - Handle the one-month and no-history states explicitly.
  - Note: "Changing car mix can change the median — not a matched-car price index."
- **Disclosures** (collapsed by default):
  - **Comparable ads:**
    - Columns: original title, asking price, year, fuel, mileage, gearbox, observed date, optional price history, OLX link.
    - Availability is unverified.
    - Show the gearbox source as secondary text: OLX field, inferred from title, or missing. Unknown is not evidence.
  - **Gearbox details:** a refresh with idle, loading/progress, failed, source-paused and done states.

### 4.3 Compare (`/compare`) — `screens/03-compare.png`
- **Sidebar:** "Shared filters": category, years, More filters chips and an "updates automatically" note. **No ceiling.** The same filters apply to every column.
- **Selection band:** "Comparing N of 3". Each model is numbered 1–3, with a remove ✕ and "Analyze →". "+ Add model" is disabled at 3.
- **Comparison card:**
  - **Price position:** the selected models on one KM scale (30k–50k).
  - **Evidence:** six rows × N model columns:
    - Typical asking price (large)
    - Middle-50% range
    - Priced ads
    - Distinct ads with cuts (count and %)
    - Actual represented years
    - Last observation
  - An Analyze button per column.
  - Small-sample cautions go beside the affected model.
  - **No winner highlighting, no scores.**
- **Behavior:** recalculates automatically on selection or filter changes, with a loading state.
- **Footnote:** "Full price distributions, no asking-price ceiling. No ranking implied."

### 4.4 Following (`/following`) — `screens/04-following.png`
- **Sidebar:** no filters. A short note explains that a saved search is a model plus exact years, mileage, fuel, gearbox, category and window, not an individual car, and that there are no automatic notifications.
- **"Since you saved" band:** how many searches have a new collection and how many medians changed.
  - Show a median difference vs the saved baseline **only if a later collection exists AND the median changed**.
  - Always put "Car mix can change the median." next to it.
- **Saved searches table:**
  - Columns: search (model + spec line), median, middle 50%, ads · cuts, last seen, since saved, actions.
  - "Since saved" values: "New collection" with the difference or "Median unchanged", or "No collection since saved".
  - Actions: Compare, Unfollow (`eye-off`) and Analyze (chevron).
- **Empty state:** "No followed models", with a **Browse market** action.
- **Baseline:** repeated equivalent saves keep the original baseline.

## 5. Evidence rules (must hold in UI copy and logic)

1. **Sample coverage:** the collector samples the newest 300 OLX car results daily. That is not full market coverage.
2. **No sales data:** leaving the collected window is **not** a sale, and active status is not verified availability. Never show sales counts, demand, days-to-sell, profit or "best-selling".
3. **Price stats:** typical price = **median**, and the range = **25th–75th percentile**. Each ad counts once, and missing prices are excluded.
4. **Ceiling scope:** the Market ceiling decides eligibility and the "within ceiling" count only. Median and range use the full cohort, so they can exceed the ceiling.
5. **Price cuts:** count distinct ads with recorded reductions, not cut events.
6. **Monthly history:** uses the last usable price per ad per UTC month, and repeated snapshots add no weight.
7. **Inferred labels:** make/model/category labels are inferred. Attribute filters exclude unknowns, and unknown is never shown as a verified spec.

## 6. Acceptance checklist
- [ ] Four routes render and match the PNGs (spacing, type, colors, radii) at 1440px.
- [ ] Tokens come from `tokens.css`; no hardcoded colors.
- [ ] Charts are computed from data (band position = value → scale), not hardcoded pixels.
- [ ] The ceiling line aligns across all Market rows.
- [ ] Signals and suggestions come from one config.
- [ ] Small-sample cautions use icon + text.
- [ ] Keyboard: nav, table rows, chart months, disclosures and chips are all focusable with a visible ring.
- [ ] Loading, empty, error and stale-data states exist for each page.
- [ ] Responsive: below 1024px the sidebar collapses to a top bar or drawer and the tables become stacked rows. Text never shrinks below 12px.
