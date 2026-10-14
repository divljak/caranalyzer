import type {
  Evidence,
  Filters,
  Segment,
  WebsiteCollection,
} from "../pages/dealerShared";

export const SIGNALS = Object.freeze({
  withinCeiling: 0.6,
  cuts: 0.15,
  pricedAds: 20,
  range: 0.2,
  smallSample: 10,
});
export type ModelEvidence = Evidence & { within_budget?: number };
export function signals(s: ModelEvidence) {
  const priced = s.priced_sample;
  return {
    ceiling:
      priced > 0 && (s.within_budget ?? 0) / priced >= SIGNALS.withinCeiling,
    steady: priced > 0 && s.price_cut_listings / priced <= SIGNALS.cuts,
    enough: priced >= SIGNALS.pricedAds,
    tight:
      s.median != null &&
      s.median > 0 &&
      s.p25 != null &&
      s.p75 != null &&
      (s.p75 - s.p25) / s.median <= SIGNALS.range,
  };
}
export const signalCount = (s: ModelEvidence) =>
  Object.values(signals(s)).filter(Boolean).length;
export const isSuggested = (s: ModelEvidence) => signalCount(s) === 4;
export type Sort = "signals" | "observed" | "price" | "recent" | "cuts";
export function sortedModels(items: ModelEvidence[], sort: Sort) {
  return [...items].sort((a, b) => {
    if (sort === "signals")
      return (
        signalCount(b) - signalCount(a) || b.priced_sample - a.priced_sample
      );
    if (sort === "price")
      return (a.median ?? Infinity) - (b.median ?? Infinity);
    if (sort === "recent")
      return (b.newest_observation ?? "").localeCompare(
        a.newest_observation ?? "",
      );
    if (sort === "cuts") return b.price_cut_listings - a.price_cut_listings;
    return b.priced_sample - a.priced_sample;
  });
}
export const defaultFilters: Filters = {
  make: "",
  model: "",
  year_min: 2020,
  year_max: 2023,
  price_max: "50000",
  mileage_min: "50000",
  mileage_max: "150000",
  fuel: "diesel",
  transmission: "automatic",
  category: "suv",
  days: 30,
};
export const withoutCeiling = (f: Filters): Filters => ({
  ...f,
  price_max: "",
});
export const segmentKey = (s: Segment) =>
  `${s.make.toLowerCase()}|${s.model.toLowerCase()}`;
export const name = (s: Segment) => `${s.make} ${s.model}`;
export const num = (v: number | null | undefined) =>
  v == null
    ? "Unavailable"
    : new Intl.NumberFormat("en-GB", { maximumFractionDigits: 0 }).format(v);
export const km = (v: number | null | undefined) =>
  v == null ? "Unavailable" : `${num(v)} KM`;
export const range = (s: Pick<Evidence, "p25" | "p75">, currency = false) =>
  s.p25 == null || s.p75 == null
    ? "Unavailable"
    : `${num(s.p25)} – ${num(s.p75)}${currency ? " KM" : ""}`;
export const percent = (count: number, total: number) =>
  total ? `${Math.round((count / total) * 100)}%` : "Unavailable";
export const years = (
  s: Pick<Evidence, "year_min" | "year_max">,
  short = false,
) =>
  s.year_min == null || s.year_max == null
    ? "No represented years"
    : short
      ? `${s.year_min}–${String(s.year_max).slice(-2)}`
      : `${s.year_min} – ${s.year_max}`;
export function displayDate(value?: string | null, full = true) {
  if (!value) return "Unavailable";
  const d = new Date(value.length === 10 ? `${value}T12:00:00Z` : value);
  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    ...(full ? { year: "numeric" as const } : {}),
    timeZone: "Europe/Sarajevo",
  })
    .format(d)
    .replace("Sept", "Sep");
}
export function collectionFreshness(
  asOf?: string | null,
  latest?: WebsiteCollection | null,
  observed?: WebsiteCollection | null,
  now = Date.now(),
) {
  const anchor = asOf ? Date.parse(asOf) : -Infinity;
  const time = (run?: WebsiteCollection | null) =>
    run?.finished_at || run?.started_at;
  const newer = (run?: WebsiteCollection | null) =>
    !!time(run) && Date.parse(time(run)!) > anchor;
  const describe = (run: WebsiteCollection) =>
    `${displayDate(time(run))} · ${run.status} · ${num(run.observations)} recorded observations${run.status === "running" ? " so far" : ""}.`;
  const latestLine = newer(latest)
    ? `Latest website collection: ${describe(latest!)}`
    : null;
  const observationLine =
    newer(observed) && time(observed) !== time(latest)
      ? `Latest run with observations: ${describe(observed!)}`
      : null;
  return {
    show:
      (!!asOf && now - anchor > 3 * 86400000) ||
      !!latestLine ||
      !!observationLine,
    cleanLine: `Last clean collection: ${displayDate(asOf)}.`,
    latestLine,
    observationLine,
  };
}
export function scaleFor(
  items: Pick<Evidence, "p25" | "p75">[],
  defaults: [number, number],
  step = 10000,
): [number, number] {
  const values = items
    .flatMap((s) => [s.p25, s.p75])
    .filter((v): v is number => v != null);
  return values.length
    ? [
        Math.min(defaults[0], Math.floor(Math.min(...values) / step) * step),
        Math.max(defaults[1], Math.ceil(Math.max(...values) / step) * step),
      ]
    : defaults;
}
export const position = (value: number, scale: [number, number]) =>
  ((value - scale[0]) / (scale[1] - scale[0])) * 100;
export function baselineChange(s: {
  new_data: boolean;
  baseline: Evidence;
  current: Evidence;
}) {
  return s.new_data &&
    s.current.median != null &&
    s.baseline.median != null &&
    s.current.median !== s.baseline.median
    ? s.current.median - s.baseline.median
    : null;
}
export const fingerprint = (f: Filters) =>
  JSON.stringify(
    Object.entries({
      ...withoutCeiling(f),
      make: f.make.trim().toLowerCase(),
      model: f.model.trim().toLowerCase(),
      mileage_min: f.mileage_min ? Number(f.mileage_min) : null,
      mileage_max: f.mileage_max ? Number(f.mileage_max) : null,
    }).sort(([a], [b]) => a.localeCompare(b)),
  );

export const small = (s: Pick<Evidence, "priced_sample">) =>
  s.priced_sample < SIGNALS.smallSample;
