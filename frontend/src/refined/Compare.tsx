import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Columns3, X } from "lucide-react";
import type { Evidence } from "../pages/dealerShared";
import { data } from "./data";
import { BlueBand, PageHeader, StaleNotice } from "./Shell";
import { useWorkspace } from "./context";
import { Axis, Caution, PriceBand, QueryState, StateCard } from "./shared";
import {
  displayDate,
  km,
  name,
  percent,
  range,
  scaleFor,
  segmentKey,
  years,
  small,
} from "./evidence";
export default function Compare() {
  const {
    selected,
    toggle,
    add,
    compareFilters: filters,
    overview,
    analyzeHref,
    href,
  } = useWorkspace();
  const [adding, setAdding] = useState(false);
  const query = useQuery({
    queryKey: ["comparison", selected, filters],
    queryFn: () => data.compare(selected, filters),
    enabled: selected.length > 0,
    retry: false,
  });
  const items = query.data?.segments ?? [],
    scale = scaleFor(items, [30000, 50000], 5000);
  const metrics: [string, string, (s: Evidence) => string | number][] = [
    ["Typical asking price", "median", (s) => km(s.median)],
    ["Middle-50% range", "25th–75th percentile", (s) => range(s)],
    ["Priced ads", "each ad counted once", (s) => s.priced_sample],
    [
      "Distinct ads with cuts",
      "not cut events",
      (s) =>
        `${s.price_cut_listings} · ${percent(s.price_cut_listings, s.priced_sample)}`,
    ],
    ["Actual represented years", "within shared filter", (s) => years(s)],
    ["Last observation", "", (s) => displayDate(s.newest_observation)],
  ];
  return (
    <>
      <PageHeader
        title="Compare"
        scope={`${filters.category.toUpperCase() || "All categories"} · ${filters.year_min} – ${filters.year_max} · ${filters.fuel || "all fuels"} · ${filters.transmission || "all gearboxes"} · ${filters.mileage_min ? Number(filters.mileage_min) / 1000 + "k" : "0"}–${filters.mileage_max ? Number(filters.mileage_max) / 1000 + "k" : "any"} km · last ${filters.days} days · no ceiling`}
      />
      <StaleNotice asOf={query.data?.as_of} />
      <BlueBand>
        <div className="band-heading">
          <h2>
            <Columns3 size={19} />
            Comparing {selected.length} of 3
          </h2>
          <span>
            {selected.length === 3
              ? "Remove one to add another"
              : "Choose up to 3 models"}
          </span>
          <button
            className="outline add-model"
            disabled={selected.length >= 3}
            onClick={() => setAdding(!adding)}
          >
            + Add model
          </button>
        </div>
        {adding && selected.length < 3 && (
          <label className="add-model-picker">
            Choose model
            <select
              aria-label="Add model to comparison"
              value=""
              onChange={(e) => {
                const s = overview.data?.models.find(
                  (m) => segmentKey(m) === e.target.value,
                );
                if (s) {
                  add({ make: s.make, model: s.model });
                  setAdding(false);
                }
              }}
            >
              <option value="">Select an observed model</option>
              {overview.data?.models
                .filter(
                  (m) => !selected.some((s) => segmentKey(s) === segmentKey(m)),
                )
                .map((m) => (
                  <option key={segmentKey(m)} value={segmentKey(m)}>
                    {name(m)}
                  </option>
                ))}
            </select>
          </label>
        )}
        <div className="band-items">
          {selected.map((s, i) => (
            <article className="band-item selection-item" key={segmentKey(s)}>
              <div className="band-item-title">
                <h3>
                  <span className="number-badge">{i + 1}</span>
                  {name(s)}
                </h3>
                <button
                  aria-label={`Remove ${name(s)} from comparison`}
                  className="icon-button"
                  onClick={() => toggle(s)}
                >
                  <X size={16} />
                </button>
              </div>
              <p>
                {items.find((m) => segmentKey(s) === segmentKey(m))
                  ? years(items.find((m) => segmentKey(s) === segmentKey(m))!)
                  : "Calculating years"}{" "}
                represented
              </p>
              <Link to={analyzeHref(s, filters)}>Analyze →</Link>
            </article>
          ))}
        </div>
      </BlueBand>
      {!selected.length ? (
        <StateCard title="Choose models to compare">
          <p>Add up to 3 models using the button above or browse the market.</p>
          <Link to={href("/market")} className="primary">
            Browse market
          </Link>
        </StateCard>
      ) : (
        <>
          <QueryState query={query} label="comparison" />
          {query.data &&
            !query.isError &&
            (items.every((s) => s.priced_sample === 0) ? (
              <StateCard title="No comparable priced ads">
                <p>
                  Widen the shared year range or remove a specification filter.
                </p>
              </StateCard>
            ) : (
              <section className="surface-card comparison-card">
                <div className="comparison-price-row table-heading">
                  <h2>Price position · KM</h2>
                  <Axis scale={scale} ticks={5} />
                </div>
                {items.map((s, i) => (
                  <div className="comparison-price-row" key={segmentKey(s)}>
                    <span className="comparison-model">
                      <span className="number-badge">{i + 1}</span>
                      {name(s)}
                      {small(s) && <Caution />}
                    </span>
                    <PriceBand evidence={s} scale={scale} label track />
                  </div>
                ))}
                <div
                  className="comparison-evidence"
                  style={
                    { "--columns": selected.length } as React.CSSProperties
                  }
                >
                  <div className="evidence-row evidence-heading">
                    <h2>Evidence</h2>
                    {items.map((s, i) => (
                      <div key={segmentKey(s)}>
                        <span className="number-badge">{i + 1}</span>
                        {name(s)}
                        {small(s) && <Caution />}
                      </div>
                    ))}
                  </div>
                  {metrics.map(([label, hint, get], i) => (
                    <div
                      className={`evidence-row ${i === 0 ? "typical-row" : ""}`}
                      key={label}
                    >
                      <div>
                        {label}
                        <small>{hint}</small>
                      </div>
                      {items.map((s) => (
                        <div key={segmentKey(s)} data-label={name(s)}>
                          {get(s)}
                          {label === "Priced ads" && small(s) && <Caution />}
                        </div>
                      ))}
                    </div>
                  ))}
                  <div className="evidence-row evidence-actions">
                    <span />
                    {items.map((s) => (
                      <div key={segmentKey(s)}>
                        <Link className="outline" to={analyzeHref(s, filters)}>
                          Analyze →
                        </Link>
                      </div>
                    ))}
                  </div>
                </div>
              </section>
            ))}
        </>
      )}
      <p className="page-footnote">
        Calculated automatically when models or filters change. Full price
        distributions, no asking-price ceiling. No ranking implied.
      </p>
    </>
  );
}
