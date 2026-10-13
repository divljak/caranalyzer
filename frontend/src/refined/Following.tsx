import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { ChevronRight, Columns3, EyeOff, History } from "lucide-react";
import type { SavedSearch } from "../pages/dealerShared";
import { data } from "./data";
import { BlueBand, PageHeader, StaleNotice } from "./Shell";
import { useWorkspace } from "./context";
import { Caution, QueryState, StateCard } from "./shared";
import {
  baselineChange,
  displayDate,
  km,
  name,
  num,
  range,
  small,
} from "./evidence";
function specs(s: SavedSearch) {
  const f = s.filters;
  return [
    `${f.year_min}–${String(f.year_max).slice(-2)}`,
    f.fuel && f.fuel[0].toUpperCase() + f.fuel.slice(1),
    f.transmission && f.transmission[0].toUpperCase() + f.transmission.slice(1),
    f.mileage_min || f.mileage_max
      ? `${f.mileage_min ? Number(f.mileage_min) / 1000 + "k" : "0"}–${f.mileage_max ? Number(f.mileage_max) / 1000 + "k" : "any"} km`
      : "all mileage",
    f.category.toUpperCase(),
    `${f.days} d`,
  ]
    .filter(Boolean)
    .join(" · ");
}
function difference(v: number) {
  return `${v > 0 ? "+" : "−"}${km(Math.abs(v))}`;
}
export default function Following() {
  const query = useQuery({
    queryKey: ["following"],
    queryFn: data.following,
    retry: false,
  });
  const client = useQueryClient(),
    navigate = useNavigate();
  const { add, setCompareFilters, analyzeHref, href, notice } = useWorkspace();
  const [removing, setRemoving] = useState<string | null>(null);
  const searches = query.data ?? [],
    newCollections = searches.filter((s) => s.new_data),
    changed = newCollections.filter((s) => baselineChange(s) != null);
  async function unfollow(id: string) {
    setRemoving(id);
    try {
      await data.unfollow(id);
      await client.invalidateQueries({ queryKey: ["following"] });
    } catch (e) {
      notice((e as Error).message);
    } finally {
      setRemoving(null);
    }
  }
  return (
    <>
      <PageHeader
        title="Following"
        scope={`${query.isPending || query.isError ? "Saved model searches" : searches.length + " saved model searches"} · full price distributions, no ceiling`}
      />
      <StaleNotice asOf={searches[0]?.as_of} />
      <BlueBand>
        <div className="band-heading">
          <h2>
            <History size={19} />
            Since you saved
          </h2>
          <span>
            {query.isPending
              ? "Checking later collections…"
              : query.isError
                ? "Changes unavailable"
                : `${newCollections.length} searches have a new collection · ${changed.length} ${changed.length === 1 ? "median" : "medians"} changed`}
          </span>
        </div>
        <div className="band-items">
          {newCollections.slice(0, 3).map((s) => {
            const delta = baselineChange(s);
            return (
              <article className="band-item followed-highlight" key={s.id}>
                <div className="band-item-title">
                  <h3>{name(s.filters)}</h3>
                  <span>{km(s.current.median)}</span>
                </div>
                {delta != null ? (
                  <>
                    <p className="baseline-difference">
                      {difference(delta)} vs {km(s.baseline.median)} when saved
                    </p>
                    <p>Car mix can change the median.</p>
                  </>
                ) : (
                  <p>New collection · median unchanged</p>
                )}
                <Link to={analyzeHref(s.filters, s.filters)}>Analyze →</Link>
              </article>
            );
          })}
          {!newCollections.length && (
            <p>
              {query.isPending
                ? "Checking saved searches…"
                : query.isError
                  ? "Saved-search changes unavailable."
                  : "No collection since your searches were saved."}
            </p>
          )}
        </div>
      </BlueBand>
      <QueryState query={query} label="saved searches" />
      {!query.isPending &&
        !query.isError &&
        (searches.length ? (
          <>
            <section
              className="surface-card following-table"
              aria-label="Saved model searches"
            >
              <div className="following-row table-heading">
                <span>Saved search</span>
                <span>Median</span>
                <span>Middle 50%</span>
                <span>Ads · cuts</span>
                <span>Last seen</span>
                <span>Since saved</span>
                <span />
              </div>
              {searches.map((s) => {
                const delta = baselineChange(s);
                return (
                  <div className="following-row" key={s.id}>
                    <div className="following-search">
                      <Link to={analyzeHref(s.filters, s.filters)}>
                        {name(s.filters)}
                      </Link>
                      <small>{specs(s)}</small>
                    </div>
                    <span className="following-median" data-label="Median">
                      {num(s.current.median)}
                    </span>
                    <span className="following-range" data-label="Middle 50%">
                      {range(s.current)}
                    </span>
                    <span data-label="Ads · cuts">
                      {s.current.priced_sample} · {s.current.price_cut_listings}
                      {small(s.current) && <Caution />}
                    </span>
                    <span className="following-seen" data-label="Last seen">
                      {displayDate(s.current.newest_observation, false)}
                    </span>
                    <div className="since-saved" data-label="Since saved">
                      {s.new_data ? (
                        <>
                          <span>New collection</span>
                          {delta != null ? (
                            <>
                              <small className="changed-median">
                                {difference(delta)} vs saved
                              </small>
                              <small>Car mix can change the median.</small>
                            </>
                          ) : (
                            <small>Median unchanged</small>
                          )}
                        </>
                      ) : (
                        <span className="muted">No collection since saved</span>
                      )}
                    </div>
                    <div className="following-actions">
                      <button
                        className="icon-button"
                        aria-label={`Compare ${name(s.filters)}`}
                        onClick={() => {
                          if (add(s.filters)) {
                            setCompareFilters(s.filters);
                            navigate(href("/compare"));
                          }
                        }}
                      >
                        <Columns3 size={18} />
                      </button>
                      <button
                        className="icon-button"
                        aria-label={`Unfollow ${name(s.filters)}`}
                        disabled={removing === s.id}
                        onClick={() => unfollow(s.id)}
                      >
                        <EyeOff size={18} />
                      </button>
                      <Link
                        className="icon-button"
                        aria-label={`Analyze ${name(s.filters)}`}
                        to={analyzeHref(s.filters, s.filters)}
                      >
                        <ChevronRight size={18} />
                      </Link>
                    </div>
                  </div>
                );
              })}
            </section>
            <p className="page-footnote">
              Saving an equivalent search again keeps the original baseline. A
              difference appears only after a later collection and only if the
              median changed.
            </p>
          </>
        ) : (
          <StateCard title="No followed models">
            <p>
              Save an exact model search from Model analysis to return to its
              evidence.
            </p>
            <Link className="primary" to={href("/market")}>
              Browse market
            </Link>
          </StateCard>
        ))}
    </>
  );
}
