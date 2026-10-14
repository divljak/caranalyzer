import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ChevronRight, Info, Target } from "lucide-react";
import { data } from "./data";
import { BlueBand, PageHeader, StaleNotice } from "./Shell";
import { useWorkspace } from "./context";
import {
  Axis,
  Caution,
  CompareTray,
  PriceBand,
  QueryState,
  StateCard,
} from "./shared";
import {
  displayDate,
  isSuggested,
  km,
  name,
  percent,
  position,
  scaleFor,
  segmentKey,
  SIGNALS,
  signals,
  sortedModels,
  years,
  type Sort,
  small,
} from "./evidence";
export default function Market() {
  const {
    marketFilters: filters,
    selected,
    toggle,
    analyzeHref,
  } = useWorkspace();
  const query = useQuery({
    queryKey: ["market", filters],
    queryFn: () => data.discovery(filters),
    retry: false,
  });
  const [sort, setSort] = useState<Sort>("signals"),
    [limit, setLimit] = useState(14);
  const items = sortedModels(query.data?.items ?? [], sort),
    suggested = items.filter(isSuggested);
  const scale = scaleFor(items, [20000, 70000]);
  const ceiling = Number(filters.price_max);
  return (
    <>
      <PageHeader
        title="Market"
        scope={`${filters.category.toUpperCase() || "All categories"} · ${filters.year_min} – ${filters.year_max} · ceiling ${km(ceiling)} · last ${filters.days} days`}
      />
      <StaleNotice asOf={query.data?.as_of} />
      <BlueBand>
        <div className="band-heading">
          <h2>
            <Target size={19} />
            Suggested to investigate
          </h2>
          <span>
            {query.isPending
              ? "Checking signals…"
              : query.isError
                ? "Suggestions unavailable"
                : `${suggested.length} models meet all 4 signals`}
          </span>
          <details className="suggestion-rules">
            <summary>
              <Info size={16} />
              How suggestions work
              <span className="disclosure-chevron" />
            </summary>
            <div className="disclosure-body">
              <p>All four signals must be met in this filtered cohort:</p>
              <ul>
                <li>
                  At least {SIGNALS.withinCeiling * 100}% of priced ads fit your
                  ceiling.
                </li>
                <li>At most {SIGNALS.cuts * 100}% have recorded cuts.</li>
                <li>At least {SIGNALS.pricedAds} priced ads.</li>
                <li>
                  Middle-50% width is at most {SIGNALS.range * 100}% of the
                  median.
                </li>
              </ul>
              <p>Signals guide investigation; they do not predict sales.</p>
            </div>
          </details>
        </div>
        <div className="band-items">
          {suggested.slice(0, 3).map((s) => (
            <article className="band-item" key={segmentKey(s)}>
              <div className="band-item-title">
                <h3>{name(s)}</h3>
                <span>{km(s.median)}</span>
              </div>
              <p>
                {percent(s.within_budget, s.priced_sample)} of ads under your
                ceiling · {percent(s.price_cut_listings, s.priced_sample)} cut ·{" "}
                {s.priced_sample} ads
              </p>
              <Link to={analyzeHref(s)}>Analyze →</Link>
            </article>
          ))}
          {!suggested.length && (
            <p>
              {query.isPending
                ? "Checking the filtered cohort…"
                : query.isError
                  ? "Suggestions unavailable."
                  : "No models meet all 4 signals in this scope."}
            </p>
          )}
        </div>
      </BlueBand>
      <QueryState query={query} label="market evidence" />
      {!query.isPending &&
        !query.isError &&
        (items.length ? (
          <>
            <section
              className="market-table surface-card"
              aria-label="Model asking-price distributions"
            >
              <div className="market-row table-heading">
                <div className="model-column">
                  <span>Model</span>
                  <label className="sort-control">
                    <span className="sr-only">Sort models</span>
                    <select
                      value={sort}
                      onChange={(e) => setSort(e.target.value as Sort)}
                      aria-label="Sort models"
                    >
                      <option value="signals">Signals met</option>
                      <option value="observed">Most observed ads</option>
                      <option value="price">Lowest typical price</option>
                      <option value="recent">Latest observation</option>
                      <option value="cuts">Most price cuts</option>
                    </select>
                  </label>
                </div>
                <div className="axis-cell">
                  <Axis scale={scale} omitValue={ceiling} />
                  {ceiling >= scale[0] && ceiling <= scale[1] && (
                    <span
                      className="ceiling-label"
                      style={{ left: `${position(ceiling, scale)}%` }}
                    >
                      {ceiling / 1000}k ceiling
                    </span>
                  )}
                </div>
                <span>Under</span>
                <span>Cut</span>
                <span>Ads</span>
                <span />
              </div>
              {items.slice(0, limit).map((s) => {
                const meets = signals(s);
                return (
                  <div className="market-row" key={segmentKey(s)}>
                    <div className="model-column">
                      <input
                        type="checkbox"
                        aria-label={`Select ${name(s)} for comparison`}
                        checked={selected.some(
                          (x) => segmentKey(x) === segmentKey(s),
                        )}
                        onChange={() => toggle(s)}
                      />
                      <div>
                        <Link className="model-name" to={analyzeHref(s)}>
                          {name(s)}
                        </Link>
                        <small>
                          {years(s, true)} ·{" "}
                          {small(s) ? (
                            <Caution />
                          ) : (
                            `seen ${displayDate(s.newest_observation, false)}`
                          )}
                        </small>
                      </div>
                    </div>
                    <div className="distribution-cell">
                      <PriceBand evidence={s} scale={scale} label />
                      {ceiling >= scale[0] && ceiling <= scale[1] && (
                        <span
                          className="ceiling-line"
                          style={{ left: `${position(ceiling, scale)}%` }}
                        />
                      )}
                    </div>
                    <span
                      className={`stat ${meets.ceiling ? "bright" : ""}`}
                      data-label="Under"
                    >
                      {percent(s.within_budget, s.priced_sample)}
                    </span>
                    <span
                      className={`stat ${meets.steady ? "bright" : ""}`}
                      data-label="Cut"
                    >
                      {percent(s.price_cut_listings, s.priced_sample)}
                    </span>
                    <span
                      className={`stat ${meets.enough ? "bright" : ""}`}
                      data-label="Ads"
                    >
                      {s.priced_sample}
                    </span>
                    <Link
                      className="icon-button"
                      aria-label={`Analyze ${name(s)}`}
                      to={analyzeHref(s)}
                    >
                      <ChevronRight size={18} />
                    </Link>
                  </div>
                );
              })}
            </section>
            <footer className="table-footer">
              <p>
                Band = middle 50% · dot = median · bright values meet a signal
                (≥{SIGNALS.withinCeiling * 100}% under ceiling, ≤
                {SIGNALS.cuts * 100}% cut, ≥{SIGNALS.pricedAds} ads)
              </p>
              {items.length > limit && (
                <button
                  className="outline"
                  onClick={() => setLimit((n) => n + 10)}
                >
                  Show {Math.min(10, items.length - limit)} more
                </button>
              )}
            </footer>
          </>
        ) : (
          <StateCard title="No models in this scope">
            <p>Try a wider year range or remove a specification filter.</p>
          </StateCard>
        ))}
      <CompareTray />
    </>
  );
}
