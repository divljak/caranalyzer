import sample from "../../../design/data/sample-data.json";
import { api, bodyFilters, params } from "../pages/dealerShared";
import type {
  Evidence,
  Filters,
  Segment,
  Overview,
  Discovery,
  Compared,
  SavedSearch,
  Car,
} from "../pages/dealerShared";
import {
  defaultFilters,
  name,
  segmentKey,
  withoutCeiling,
  fingerprint,
} from "./evidence";
export const demo =
  new URLSearchParams(window.location.search).get("demo") === "1" ||
  import.meta.env.VITE_DATA_MODE === "demo";
export const previewState = demo
  ? new URLSearchParams(window.location.search).get("state")
  : null;
const split = (n: string): Segment => {
  const [make, ...model] = n.split(" ");
  return { make, model: model.join(" ") };
};
export const mockModels: Evidence[] = sample.models.map((m) => ({
  ...split(m.model),
  sample_size: m.pricedAds,
  priced_sample: m.pricedAds,
  median: m.median,
  p25: m.p25,
  p75: m.p75,
  price_cut_listings: m.adsWithCuts,
  within_budget: m.withinCeiling,
  newest_observation: m.lastObserved,
  year_min: Number(m.years.slice(0, 4)),
  year_max: 2000 + Number(m.years.slice(-2)),
  first_seen_14d: 0,
  outside_window: 0,
  sample_warning: m.pricedAds < 10 ? "Small sample" : "",
}));
export type Year = {
  year: number;
  median: number | null;
  p25: number | null;
  p75: number | null;
  priced_sample: number;
};
export type Month = {
  month: string;
  median: number | null;
  p25: number | null;
  p75: number | null;
  priced_sample: number;
  partial?: boolean;
};
export type Analysis = {
  as_of: string;
  summary: Evidence & { cuts_available?: boolean };
  years: Year[];
  monthly: Month[];
  transmission_resume_at: string | null;
};
export type Job = {
  id: string;
  status: "queued" | "running" | "completed" | "partial";
  total: number;
  checked: number;
  enriched: number;
  errors: number;
  message: string;
};
export type History = {
  observed_at: string;
  asking_price: number | null;
  price_status: string;
};
const mockDate = `${sample.dataset.lastUpdated}T12:00:00Z`;
async function fixture<T>(value: T): Promise<T> {
  await new Promise((resolve) => setTimeout(resolve, 120));
  if (previewState === "error")
    throw new Error(
      "The local database could not be reached. Retry when it is available.",
    );
  if (previewState === "loading") return new Promise<T>(() => {});
  return value;
}
const emptyEvidence = (s: Segment): Evidence => ({
  ...s,
  priced_sample: 0,
  sample_size: 0,
  median: null,
  p25: null,
  p75: null,
  price_cut_listings: 0,
  newest_observation: null,
  year_min: null,
  year_max: null,
  first_seen_14d: 0,
  outside_window: 0,
  sample_warning: "",
});
// Fixtures contain aggregate evidence, not individual ads. Unsupported cohorts are empty;
// never estimate quartiles or ceiling counts from a band.
function fixtureCohort(f: Filters, analysis = false) {
  return (
    previewState !== "empty" &&
    f.category === "suv" &&
    f.fuel === "diesel" &&
    f.transmission === "automatic" &&
    String(f.mileage_min) === "50000" &&
    String(f.mileage_max) === "150000" &&
    f.days === 30 &&
    (analysis || (f.year_min === 2020 && f.year_max === 2023))
  );
}
function demoSearches(): SavedSearch[] {
  const saved = localStorage.getItem("car-analyzer-demo-searches-v1");
  if (saved) {
    try {
      return JSON.parse(saved);
    } catch {
      /* restore seed */
    }
  }
  return sample.following.map((s, index) => {
    const current = mockModels.find((m) => name(m) === s.model)!;
    const filters = {
      ...withoutCeiling(defaultFilters),
      ...split(s.model),
      ...(index === 2
        ? { transmission: "manual", mileage_min: "", mileage_max: "" }
        : {}),
      ...(index === 3 ? { year_min: 2021, days: 90 } : {}),
    };
    return {
      id: `demo-${index}`,
      filters,
      baseline: { ...current, median: s.baselineMedian },
      current,
      baseline_as_of: "2026-09-20T12:00:00Z",
      as_of: mockDate,
      new_data: s.newCollectionSinceSaved,
    };
  });
}
export const data = {
  overview: () =>
    demo
      ? fixture<Overview>({
          as_of: mockDate,
          snapshots: 0,
          runs: 0,
          api_listings: 0,
          models: mockModels.map((m) => ({ ...m, sample: m.sample_size })),
          labels: { recognized: 0, needs_review: 0, categorized: 0 },
        })
      : api<Overview>("overview"),
  discovery: (f: Filters) =>
    demo
      ? fixture<Discovery>({
          items:
            fixtureCohort(f) && (!f.price_max || f.price_max === "50000")
              ? mockModels.map((m) => ({
                  ...m,
                  within_budget: (m as Evidence & { within_budget: number })
                    .within_budget,
                  categorized: m.sample_size,
                  categories: ["suv"],
                }))
              : [],
          as_of: mockDate,
          coverage: { observed: 0, recognized: 0, categorized: 0 },
        })
      : api<Discovery>(`discovery?${params(f)}`),
  analysis: (f: Filters) => {
    if (!demo)
      return api<Analysis>(`model-analysis?${params(withoutCeiling(f))}`);
    const s =
      mockModels.find((m) => segmentKey(m) === segmentKey(f)) ??
      emptyEvidence(f);
    const isKaroq = name(s) === sample.modelAnalysis.model;
    const usable =
      fixtureCohort(f, true) &&
      f.year_min <= s.year_max! &&
      f.year_max >= s.year_min!;
    const fullRange = f.year_min === 2020 && f.year_max === 2023;
    const rows =
      isKaroq && usable
        ? sample.modelAnalysis.byYear
            .filter((y) => y.year >= f.year_min && y.year <= f.year_max)
            .map((y) => ({ ...y, priced_sample: y.pricedAds }))
        : [];
    const summary =
      usable && (fullRange || rows.length === 1)
        ? rows.length === 1
          ? {
              ...s,
              ...rows[0],
              year_min: rows[0].year,
              year_max: rows[0].year,
              cuts_available: false,
            }
          : s
        : emptyEvidence(f);
    return fixture<Analysis>({
      as_of: mockDate,
      summary,
      years: rows,
      monthly:
        isKaroq && usable && fullRange
          ? sample.modelAnalysis.monthly.map((m) => ({
              ...m,
              p25: m.median - 3400,
              p75: m.median + 3700,
              priced_sample: m.pricedAds,
            }))
          : [],
      transmission_resume_at: null,
    });
  },
  compare: (segments: Segment[], f: Filters) =>
    demo
      ? fixture<Compared>({
          as_of: mockDate,
          segments: segments.map((s) =>
            fixtureCohort(f)
              ? (mockModels.find((m) => segmentKey(m) === segmentKey(s)) ??
                emptyEvidence(s))
              : emptyEvidence(s),
          ),
        })
      : api<Compared>(
          "compare",
          { segments, filters: bodyFilters(withoutCeiling(f)) },
          "POST",
        ),
  following: () =>
    demo
      ? fixture<SavedSearch[]>(previewState === "empty" ? [] : demoSearches())
      : api<SavedSearch[]>("model-searches"),
  follow: async (f: Filters, current: Evidence) => {
    if (!demo)
      return api("model-searches", bodyFilters(withoutCeiling(f)), "POST");
    const searches = demoSearches();
    if (!searches.some((s) => fingerprint(s.filters) === fingerprint(f)))
      searches.unshift({
        id: `demo-${Date.now()}`,
        filters: withoutCeiling(f),
        current,
        baseline: current,
        as_of: mockDate,
        baseline_as_of: mockDate,
        new_data: false,
      });
    localStorage.setItem(
      "car-analyzer-demo-searches-v1",
      JSON.stringify(searches),
    );
  },
  unfollow: async (id: string) => {
    if (!demo)
      return api(
        `model-searches/${encodeURIComponent(id)}`,
        undefined,
        "DELETE",
      );
    localStorage.setItem(
      "car-analyzer-demo-searches-v1",
      JSON.stringify(demoSearches().filter((s) => s.id !== id)),
    );
  },
  listings: (f: Filters, page: number) =>
    demo
      ? fixture<{ items: Car[]; total: number }>({ items: [], total: 0 })
      : api<{ items: Car[]; total: number }>(
          `listings?${params(withoutCeiling(f))}&size=20&page=${page}`,
        ),
  history: async (id: string) => {
    const r = await api<{
      events: { observed_at: string; price: number | null }[];
    }>(`listings/${encodeURIComponent(id)}/history`);
    return r.events.map((e) => ({
      observed_at: e.observed_at,
      asking_price: e.price,
      price_status: e.price == null ? "missing" : "priced",
    }));
  },
  refreshGearbox: (f: Filters) =>
    api<Job>("model-transmission", bodyFilters(withoutCeiling(f)), "POST"),
  job: (id: string) => api<Job>(`transmission-jobs/${encodeURIComponent(id)}`),
};
