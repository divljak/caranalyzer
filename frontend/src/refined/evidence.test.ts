import { test } from "node:test";
import { strict as assert } from "node:assert";
import {
  baselineChange,
  collectionFreshness,
  defaultFilters,
  fingerprint,
  isSuggested,
  position,
  scaleFor,
  signalCount,
  signals,
  sortedModels,
  type ModelEvidence,
} from "./evidence.ts";
import type { WebsiteCollection } from "../pages/dealerShared";

test("partial observations remain distinct from the clean analysis anchor", () => {
  const clean = "2026-10-01T17:31:22Z";
  const partial: WebsiteCollection = {
    status: "partial", started_at: "2026-10-13T16:48:01Z",
    finished_at: "2026-10-13T16:50:12Z", observations: 68,
  };
  const result = collectionFreshness(clean, partial, partial, Date.parse(partial.finished_at!));
  assert.equal(result.show, true);
  assert.equal(result.cleanLine, "Last clean collection: 1 Oct 2026.");
  assert.match(result.latestLine!, /13 Oct 2026 · partial · 68 recorded observations/);
  assert.equal(result.observationLine, null);
});

test("a later failed run does not hide observations from an earlier partial run", () => {
  const partial: WebsiteCollection = {
    status: "partial", started_at: "2026-10-11T07:17:20Z",
    finished_at: "2026-10-11T07:23:59Z", observations: 136,
  };
  const failed: WebsiteCollection = {
    status: "failed", started_at: "2026-10-13T07:17:20Z",
    finished_at: "2026-10-13T07:17:53Z", observations: 0,
  };
  const result = collectionFreshness("2026-10-01T17:31:22Z", failed, partial);
  assert.match(result.latestLine!, /13 Oct 2026 · failed · 0 recorded observations/);
  assert.match(result.observationLine!, /11 Oct 2026 · partial · 136 recorded observations/);
});

test("a running attempt is disclosed even with a recent clean anchor", () => {
  const running: WebsiteCollection = {
    status: "running", started_at: "2026-10-13T16:48:01Z",
    finished_at: null, observations: 0,
  };
  const result = collectionFreshness("2026-10-13T07:00:00Z", running, null, Date.parse(running.started_at!));
  assert.equal(result.show, true);
  assert.match(result.latestLine!, /running · 0 recorded observations so far/);
});
const cohort: ModelEvidence = {
  make: "Test",
  model: "Car",
  sample_size: 20,
  priced_sample: 20,
  median: 50000,
  p25: 45000,
  p75: 55000,
  within_budget: 12,
  price_cut_listings: 3,
  year_min: 2020,
  year_max: 2023,
  newest_observation: "2026-09-29",
  sample_warning: "",
  first_seen_14d: 0,
  outside_window: 0,
};
test("all four signal boundaries are inclusive and must hold together", () => {
  assert.equal(isSuggested(cohort), true);
  for (const delta of [
    { within_budget: 11 },
    { price_cut_listings: 4 },
    { priced_sample: 19 },
    { p75: 55001 },
  ])
    assert.equal(isSuggested({ ...cohort, ...delta }), false);
  assert.equal(
    signalCount({
      ...cohort,
      within_budget: 0,
      price_cut_listings: 4,
      priced_sample: 19,
      p75: 60000,
    }),
    0,
  );
});
test("missing or zero-price evidence is never a suggestion", () => {
  assert.equal(
    isSuggested({
      ...cohort,
      priced_sample: 0,
      median: null,
      p25: null,
      p75: null,
    }),
    false,
  );
  assert.equal(signals({ ...cohort, median: 0 }).tight, false);
});
test("default sorting uses signal count then priced ads without mutating data", () => {
  const larger = {
    ...cohort,
    model: "Larger",
    priced_sample: 30,
    within_budget: 18,
  };
  const cuts = {
    ...cohort,
    model: "Cuts",
    price_cut_listings: 4,
    priced_sample: 200,
  };
  const items = [cohort, cuts, larger];
  assert.deepEqual(
    sortedModels(items, "signals").map((s) => s.model),
    ["Larger", "Car", "Cuts"],
  );
  assert.equal(items[0], cohort);
});
test("baseline differences require later evidence and an actual median change", () => {
  assert.equal(
    baselineChange({
      new_data: false,
      baseline: cohort,
      current: { ...cohort, median: 49000 },
    }),
    null,
  );
  assert.equal(
    baselineChange({ new_data: true, baseline: cohort, current: cohort }),
    null,
  );
  assert.equal(
    baselineChange({
      new_data: true,
      baseline: cohort,
      current: { ...cohort, median: null },
    }),
    null,
  );
  assert.equal(
    baselineChange({
      new_data: true,
      baseline: cohort,
      current: { ...cohort, median: 49000 },
    }),
    -1000,
  );
});
test("saved-search identity ignores ceilings and normalizes API number fields", () => {
  const apiFilters = {
    ...defaultFilters,
    make: " ŠKODA ",
    model: "Karoq",
    mileage_min: 50000,
    mileage_max: 150000,
  } as unknown as typeof defaultFilters;
  assert.equal(
    fingerprint({ ...defaultFilters, make: "Škoda", model: "Karoq" }),
    fingerprint(apiFilters),
  );
  assert.equal(
    fingerprint(defaultFilters),
    fingerprint({ ...defaultFilters, price_max: "30000" }),
  );
  assert.notEqual(
    fingerprint(defaultFilters),
    fingerprint({ ...defaultFilters, transmission: "manual" }),
  );
});
test("price plots share one scale that expands to include full quartiles", () => {
  const scale = scaleFor([cohort, { p25: 10000, p75: 80000 }], [20000, 70000]);
  assert.deepEqual(scale, [10000, 80000]);
  assert.equal(position(45000, scale), 50);
});
