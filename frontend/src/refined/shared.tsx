import { useEffect, useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  ChevronDown,
  RefreshCw,
  SlidersHorizontal,
  TriangleAlert,
  X,
} from "lucide-react";
import type { Filters, Model, Evidence } from "../pages/dealerShared";
import { defaultFilters, km, num, position, withoutCeiling } from "./evidence";
import { useWorkspace } from "./context";

export function Caution({
  children = "small sample",
}: {
  children?: ReactNode;
}) {
  return (
    <span className="caution">
      <TriangleAlert size={12} />
      {children}
    </span>
  );
}
export function StateCard({
  title,
  children,
  loading = false,
  error = false,
  retry,
}: {
  title: string;
  children?: ReactNode;
  loading?: boolean;
  error?: boolean;
  retry?: () => void;
}) {
  return (
    <section
      className={`state-card ${error ? "state-error" : ""}`}
      role={error ? "alert" : loading ? "status" : undefined}
      aria-busy={loading}
    >
      {loading ? (
        <RefreshCw className="spin" size={24} />
      ) : error ? (
        <TriangleAlert size={24} />
      ) : null}
      <h2>{title}</h2>
      {children}
      {retry && (
        <button className="outline" onClick={retry}>
          <RefreshCw size={16} />
          Retry
        </button>
      )}
    </section>
  );
}
export function QueryState({
  query,
  label,
}: {
  query: {
    isPending: boolean;
    isError: boolean;
    error: Error | null;
    refetch: () => unknown;
  };
  label: string;
}) {
  if (query.isPending) return <StateCard title={`Loading ${label}…`} loading />;
  if (query.isError)
    return (
      <StateCard
        title={`Could not load ${label}`}
        error
        retry={() => query.refetch()}
      >
        <p>{query.error?.message}</p>
      </StateCard>
    );
  return null;
}
export function FilterPanel({
  kind,
  filters,
  onApply,
  models,
}: {
  kind: string;
  filters: Filters;
  onApply: (f: Filters) => void;
  models: Model[];
}) {
  const [draft, setDraft] = useState(filters);
  const navigate = useNavigate();
  const { href } = useWorkspace();
  useEffect(() => setDraft(filters), [filters]);
  const more = [
    draft.mileage_min || draft.mileage_max,
    draft.fuel,
    draft.transmission,
    draft.days !== 365,
  ].filter(Boolean).length;
  const count = [
    draft.price_max,
    kind === "analysis" ? true : draft.category,
    kind === "analysis" ? true : false,
    true,
    ...[
      draft.mileage_min || draft.mileage_max,
      draft.fuel,
      draft.transmission,
      draft.days !== 365,
    ],
  ].filter(Boolean).length;
  const valid = (f: Filters) =>
    f.year_min <= f.year_max &&
    (!f.mileage_min ||
      !f.mileage_max ||
      Number(f.mileage_min) <= Number(f.mileage_max));
  function change(p: Partial<Filters>) {
    const f = { ...draft, ...p };
    setDraft(f);
    if (kind === "compare" && valid(f)) onApply(withoutCeiling(f));
  }
  function submit(f = draft) {
    if (!valid(f)) return;
    onApply(kind === "market" ? f : withoutCeiling(f));
    if (kind === "analysis" && f.make && f.model)
      navigate(
        href(
          `/models/${encodeURIComponent(f.make)}/${encodeURIComponent(f.model)}`,
        ),
      );
  }
  function remove(p: Partial<Filters>) {
    const f = { ...draft, ...p };
    setDraft(f);
    onApply(kind === "market" ? f : withoutCeiling(f));
  }
  const makes = [...new Set(models.map((s) => s.make))].sort();
  if (draft.make && !makes.includes(draft.make)) makes.push(draft.make);
  const modelNames = [
    ...new Set(models.filter((s) => s.make === draft.make).map((s) => s.model)),
  ].sort();
  if (draft.model && !modelNames.includes(draft.model))
    modelNames.push(draft.model);
  const years = Array.from(
    { length: 2027 - 1990 },
    (_, i) => 1990 + i,
  ).reverse();
  return (
    <form
      className="filter-panel"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <div className="filter-title">
        <h2>
          {kind === "compare" ? "Shared filters" : "Filters"}
          <span className="count-badge">{count}</span>
        </h2>
        <button
          type="button"
          className="text-button muted"
          onClick={() => {
            const f =
              kind === "market"
                ? defaultFilters
                : withoutCeiling(defaultFilters);
            setDraft(f);
            onApply(f);
          }}
        >
          Reset
        </button>
      </div>
      {kind === "market" && (
        <>
          <label>
            OLX asking-price ceiling · KM
            <input
              aria-label="OLX asking-price ceiling"
              required
              type="number"
              min="1"
              step="1"
              value={draft.price_max}
              onChange={(e) => change({ price_max: e.target.value })}
            />
          </label>
          <p className="filter-hint">
            Your OLX selling-price position — not the EU purchase budget.
          </p>
        </>
      )}
      {kind === "analysis" ? (
        <>
          <label>
            Make
            <div className="select-wrap">
              <select
                value={draft.make}
                required
                onChange={(e) => {
                  const make = e.target.value;
                  change({
                    make,
                    model: models.find((m) => m.make === make)?.model ?? "",
                  });
                }}
              >
                <option value="">Choose make</option>
                {makes.map((m) => (
                  <option key={m}>{m}</option>
                ))}
              </select>
              <ChevronDown size={15} />
            </div>
          </label>
          <label>
            Model
            <div className="select-wrap">
              <select
                value={draft.model}
                required
                onChange={(e) => change({ model: e.target.value })}
              >
                <option value="">Choose model</option>
                {modelNames.map((m) => (
                  <option key={m}>{m}</option>
                ))}
              </select>
              <ChevronDown size={15} />
            </div>
          </label>
        </>
      ) : (
        <label>
          Category
          <div className="select-wrap">
            <select
              value={draft.category}
              onChange={(e) => change({ category: e.target.value })}
            >
              <option value="">All categories</option>
              {[
                ["suv", "SUV"],
                ["sedan", "Sedan"],
                ["hatchback", "Hatchback"],
                ["estate", "Estate"],
              ].map(([v, t]) => (
                <option key={v} value={v}>
                  {t}
                </option>
              ))}
            </select>
            <ChevronDown size={15} />
          </div>
        </label>
      )}
      <div className="year-inputs">
        {(["year_min", "year_max"] as const).map((key, i) => (
          <label key={key}>
            {i ? "Year to" : "Year from"}
            <div className="select-wrap">
              <select
                value={draft[key]}
                onChange={(e) => change({ [key]: Number(e.target.value) })}
              >
                {years.map((y) => (
                  <option key={y}>{y}</option>
                ))}
              </select>
              <ChevronDown size={15} />
            </div>
          </label>
        ))}
      </div>
      {!valid(draft) && <Caution>Check year and mileage ranges.</Caution>}
      <details className="more-filters">
        <summary>
          <SlidersHorizontal size={16} />
          More filters{more > 0 ? ` · ${more} applied` : ""}
          <ChevronDown size={15} />
        </summary>
        <div className="extra-fields">
          <div className="year-inputs">
            <label>
              Mileage from
              <input
                type="number"
                min="0"
                value={draft.mileage_min}
                onChange={(e) => change({ mileage_min: e.target.value })}
              />
            </label>
            <label>
              Mileage to
              <input
                type="number"
                min="0"
                value={draft.mileage_max}
                onChange={(e) => change({ mileage_max: e.target.value })}
              />
            </label>
          </div>
          <label>
            Fuel
            <select
              value={draft.fuel}
              onChange={(e) => change({ fuel: e.target.value })}
            >
              <option value="">All fuels</option>
              {["Diesel", "Petrol", "Hybrid", "Electric"].map((f) => (
                <option key={f} value={f.toLowerCase()}>
                  {f}
                </option>
              ))}
            </select>
          </label>
          <label>
            Gearbox
            <select
              value={draft.transmission}
              onChange={(e) => change({ transmission: e.target.value })}
            >
              <option value="">All gearboxes</option>
              <option value="automatic">Automatic</option>
              <option value="manual">Manual</option>
            </select>
          </label>
          {kind === "analysis" && (
            <label>
              Category
              <select
                value={draft.category}
                onChange={(e) => change({ category: e.target.value })}
              >
                <option value="">All categories</option>
                {["SUV", "Sedan", "Hatchback", "Estate"].map((v) => (
                  <option value={v.toLowerCase()} key={v}>
                    {v}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label>
            Observation window
            <select
              value={draft.days}
              onChange={(e) => change({ days: Number(e.target.value) })}
            >
              {[7, 14, 30, 90, 365].map((d) => (
                <option key={d} value={d}>
                  {d} days
                </option>
              ))}
            </select>
          </label>
        </div>
      </details>
      <div className="filter-chips">
        {(draft.mileage_min || draft.mileage_max) && (
          <Chip
            label={`${draft.mileage_min ? num(Number(draft.mileage_min)) : "0"}–${draft.mileage_max ? num(Number(draft.mileage_max)) : "any"} km`}
            onRemove={() => remove({ mileage_min: "", mileage_max: "" })}
          />
        )}{" "}
        {draft.fuel && (
          <Chip
            label={draft.fuel[0].toUpperCase() + draft.fuel.slice(1)}
            onRemove={() => remove({ fuel: "" })}
          />
        )}{" "}
        {draft.transmission && (
          <Chip
            label={
              draft.transmission[0].toUpperCase() + draft.transmission.slice(1)
            }
            onRemove={() => remove({ transmission: "" })}
          />
        )}{" "}
        {draft.days !== 365 && (
          <Chip
            label={`${draft.days}-day window`}
            onRemove={() => remove({ days: 365 })}
          />
        )}
      </div>
      {kind === "compare" ? (
        <div className="auto-note">
          <RefreshCw size={17} />
          <span>Same for every column · updates automatically</span>
        </div>
      ) : (
        <button className="primary filter-submit" disabled={!valid(draft)}>
          {kind === "analysis" ? "Analyze" : "Search models"}
        </button>
      )}
    </form>
  );
}
export function Chip({
  label,
  onRemove,
}: {
  label: string;
  onRemove: () => void;
}) {
  return (
    <span className="chip">
      {label}
      <button type="button" aria-label={`Remove ${label}`} onClick={onRemove}>
        <X size={12} />
      </button>
    </span>
  );
}
export function Axis({
  scale,
  ticks = 6,
  omitValue,
}: {
  scale: [number, number];
  ticks?: number;
  omitValue?: number;
}) {
  return (
    <div className="price-axis">
      {Array.from({ length: ticks }, (_, i) => {
        const value = scale[0] + ((scale[1] - scale[0]) * i) / (ticks - 1);
        return (
          <span
            key={i}
            style={value === omitValue ? { visibility: "hidden" } : undefined}
          >
            {num(value / 1000)}k
          </span>
        );
      })}
    </div>
  );
}
export function PriceBand({
  evidence,
  scale,
  label = false,
  track = false,
}: {
  evidence: Pick<Evidence, "median" | "p25" | "p75">;
  scale: [number, number];
  label?: boolean;
  track?: boolean;
}) {
  const { p25, p75, median } = evidence;
  if (p25 == null || p75 == null || median == null)
    return <div className="price-plot unavailable">No priced ads</div>;
  const start = position(p25, scale),
    end = position(p75, scale),
    mid = position(median, scale);
  return (
    <div
      className={`price-plot ${track ? "with-track" : ""}`}
      role="img"
      aria-label={`Median ${km(median)}, middle 50% ${km(p25)} to ${km(p75)}`}
    >
      <span
        className="range-band"
        style={{ left: `${start}%`, width: `${Math.max(end - start, 0.4)}%` }}
      />
      <span className="median-dot" style={{ left: `${mid}%` }} />
      {label && (
        <span
          className={`band-label ${end > 93 ? "label-left" : ""}`}
          style={{ left: `${end > 93 ? start : end}%` }}
        >
          {num(median)}
        </span>
      )}
    </div>
  );
}
export function CompareTray() {
  const { selected, toggle, marketFilters, setCompareFilters, href } =
    useWorkspace();
  return selected.length ? (
    <div className="compare-tray">
      <span className="muted">{selected.length} selected</span>
      <div className="tray-chips">
        {selected.map((s) => (
          <Chip
            key={`${s.make}|${s.model}`}
            label={`${s.make} ${s.model}`}
            onRemove={() => toggle(s)}
          />
        ))}
      </div>
      <Link
        className="primary"
        to={href("/compare")}
        onClick={() => setCompareFilters(withoutCeiling(marketFilters))}
      >
        Compare {selected.length} models
      </Link>
    </div>
  ) : null;
}
