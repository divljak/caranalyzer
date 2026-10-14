import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { ChevronDown, ChevronRight, RefreshCw } from "lucide-react";
import type { Filters } from "../pages/dealerShared";
import { data, demo, type Job, type Month } from "./data";
import { BlueBand, PageHeader, StaleNotice } from "./Shell";
import { useWorkspace } from "./context";
import { Axis, Caution, PriceBand, QueryState, StateCard } from "./shared";
import {
  displayDate,
  fingerprint,
  km,
  name,
  num,
  percent,
  position,
  range,
  scaleFor,
  SIGNALS,
  years,
  small,
} from "./evidence";

export default function ModelAnalysis() {
  const { make = "", model = "" } = useParams();
  const {
    analysisFilters,
    setAnalysisFilters,
    add,
    setCompareFilters,
    href,
    notice,
  } = useWorkspace();
  const location = useLocation(),
    navigate = useNavigate(),
    client = useQueryClient();
  const filters = { ...analysisFilters, make, model, price_max: "" };
  const query = useQuery({
    queryKey: ["analysis", filters],
    queryFn: () => data.analysis(filters),
    retry: false,
  });
  const followed = useQuery({
    queryKey: ["following"],
    queryFn: data.following,
    retry: false,
  });
  const [savedRange, setSavedRange] = useState<[number, number] | null>(null),
    [saving, setSaving] = useState(false);
  useEffect(() => {
    const p = new URLSearchParams(location.search),
      next = { ...analysisFilters, make, model, price_max: "" };
    (["year_min", "year_max", "days"] as const).forEach((k) => {
      if (p.has(k)) next[k] = Number(p.get(k));
    });
    (
      [
        "fuel",
        "transmission",
        "category",
        "mileage_min",
        "mileage_max",
      ] as const
    ).forEach((k) => {
      if (p.has(k)) next[k] = p.get(k)!;
    });
    setAnalysisFilters(next);
    setSavedRange(null);
    // Route changes seed the exact scope; subsequent sidebar edits stay in workspace state.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [make, model, location.search]);
  const summary = query.data?.summary;
  const yearRows = query.data?.years ?? [],
    scale = scaleFor(yearRows, [30000, 55000], 5000);
  const isFollowed = followed.data?.some(
    (s) => fingerprint(s.filters) === fingerprint(filters),
  );
  async function follow() {
    if (!summary) return;
    setSaving(true);
    try {
      await data.follow(filters, summary);
      await client.invalidateQueries({ queryKey: ["following"] });
      notice(`Following ${name(filters)} with these exact filters.`);
    } catch (e) {
      notice((e as Error).message);
    } finally {
      setSaving(false);
    }
  }
  const compare = () => {
    if (add({ make, model })) {
      setCompareFilters(filters);
      navigate(href("/compare"));
    }
  };
  return (
    <>
      <PageHeader
        title="Model analysis"
        back
        scope={`${name(filters)} · ${filters.year_min} – ${filters.year_max} · ${filters.fuel ? filters.fuel[0].toUpperCase() + filters.fuel.slice(1) : "all fuels"} · ${filters.transmission ? filters.transmission[0].toUpperCase() + filters.transmission.slice(1) : "all gearboxes"} · ${filters.mileage_min ? num(Number(filters.mileage_min) / 1000) + "k" : "0"}–${filters.mileage_max ? num(Number(filters.mileage_max) / 1000) + "k" : "any"} km · last ${filters.days} days`}
      />
      <StaleNotice asOf={query.data?.as_of} />
      <BlueBand className="summary-band">
        <div className="summary-top">
          <div>
            <h2>{name(filters)}</h2>
            <p>
              {summary
                ? years(summary)
                : `${filters.year_min} – ${filters.year_max}`}{" "}
              represented in this filtered sample
            </p>
          </div>
          <div className="summary-actions">
            <button className="outline" onClick={compare}>
              + Compare
            </button>
            <button
              className="outline"
              onClick={follow}
              disabled={saving || isFollowed || !summary?.priced_sample}
            >
              {saving ? "Saving…" : isFollowed ? "Following" : "Follow"}
            </button>
          </div>
        </div>
        <div className="summary-metrics">
          <div className="summary-price">
            <small>Typical asking price</small>
            <strong>{summary ? km(summary.median) : "—"}</strong>
          </div>
          <div>
            <small>Middle 50%</small>
            <span>{summary ? range(summary, true) : "—"}</span>
          </div>
          <div>
            <small>Priced ads</small>
            <span>{summary?.priced_sample ?? "—"}</span>
            {summary && small(summary) && <Caution />}
          </div>
          <div>
            <small>Ads with price cuts</small>
            <span>
              {summary?.cuts_available === false
                ? "Unavailable"
                : summary
                  ? `${summary.price_cut_listings} · ${percent(summary.price_cut_listings, summary.priced_sample)}`
                  : "—"}
            </span>
          </div>
          <div>
            <small>Last observation</small>
            <span>{displayDate(summary?.newest_observation)}</span>
          </div>
        </div>
      </BlueBand>
      <QueryState query={query} label="model evidence" />
      {!query.isPending &&
        !query.isError &&
        (summary?.priced_sample ? (
          <>
            <section className="surface-card year-table">
              <div className="year-row table-heading">
                <h2>By year</h2>
                <Axis scale={scale} />
                <span>Typical</span>
                <span>Middle 50%</span>
                <span>Priced ads</span>
                <span />
              </div>
              {savedRange && (
                <button
                  className="text-button back-range"
                  onClick={() => {
                    setAnalysisFilters({
                      ...filters,
                      year_min: savedRange[0],
                      year_max: savedRange[1],
                    });
                    setSavedRange(null);
                  }}
                >
                  ← Back to {savedRange[0]} – {savedRange[1]}
                </button>
              )}
              {yearRows.map((y) => (
                <button
                  className="year-row"
                  key={y.year}
                  aria-label={`Analyze year ${y.year}, ${y.priced_sample} priced ads${small(y) ? ", small sample" : ""}`}
                  onClick={() => {
                    if (!savedRange)
                      setSavedRange([filters.year_min, filters.year_max]);
                    setAnalysisFilters({
                      ...filters,
                      year_min: y.year,
                      year_max: y.year,
                    });
                  }}
                >
                  <span className="year-name">
                    {y.year}
                    {small(y) && (
                      <Caution>
                        <span className="sr-only">small sample</span>
                      </Caution>
                    )}
                  </span>
                  <PriceBand evidence={y} scale={scale} track />
                  <span className="year-typical" data-label="Typical">
                    {num(y.median)}
                  </span>
                  <span className="year-range" data-label="Middle 50%">
                    {range(y)}
                  </span>
                  <span
                    className={small(y) ? "caution" : "year-ads"}
                    data-label="Priced ads"
                  >
                    {y.priced_sample}
                    {small(y) && " · small"}
                  </span>
                  <ChevronRight size={18} />
                </button>
              ))}
              <p className="year-footnote">
                <Caution>
                  Fewer than {SIGNALS.smallSample} priced ads — small sample.
                </Caution>{" "}
                Select a year to drill in.
              </p>
            </section>
            <MonthlyChart
              months={query.data?.monthly ?? []}
              asOf={query.data?.as_of ?? ""}
            />
            <ComparableAds filters={filters} count={summary.priced_sample} />
            <GearboxDetails
              filters={filters}
              paused={query.data?.transmission_resume_at ?? null}
            />
          </>
        ) : (
          <StateCard title="No priced ads in this scope">
            <p>Widen the year range or remove a specification filter.</p>
            {savedRange && (
              <button
                className="outline"
                onClick={() => {
                  setAnalysisFilters({
                    ...filters,
                    year_min: savedRange[0],
                    year_max: savedRange[1],
                  });
                  setSavedRange(null);
                }}
              >
                Back to {savedRange[0]} – {savedRange[1]}
              </button>
            )}
          </StateCard>
        ))}
    </>
  );
}
function monthLabel(m: string, full = false) {
  return new Intl.DateTimeFormat("en-GB", {
    month: "short",
    ...(full ? { year: "numeric" as const } : {}),
    timeZone: "UTC",
  })
    .format(new Date(m.length === 7 ? `${m}-01T12:00:00Z` : m))
    .replace("Sept", "Sep");
}
export function MonthlyChart({
  months,
  asOf,
}: {
  months: Month[];
  asOf: string;
}) {
  const usable = months
    .filter((m) => m.median != null && m.p25 != null && m.p75 != null)
    .slice(-12);
  const [index, setIndex] = useState(Math.max(0, usable.length - 1));
  useEffect(
    () => setIndex(Math.max(0, usable.length - 1)),
    [months, usable.length],
  );
  const active = usable[Math.min(index, usable.length - 1)];
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const scale = scaleFor(usable, [34000, 50000], 2000),
    maxCount = Math.max(...usable.map((m) => m.priced_sample), 1);
  return (
    <section className="monthly-card surface-card">
      <div className="chart-heading">
        <div>
          <h2>Monthly asking prices · KM</h2>
          <p>
            {usable.length
              ? `${monthLabel(usable[0].month, true)} – ${monthLabel(usable.at(-1)!.month, true)} · last recorded price per ad per month`
              : "Last 12 months"}
          </p>
        </div>
        <div className="chart-legend">
          <span>
            <i className="legend-dot" />
            Median
          </span>
          <span>
            <i className="legend-band" />
            Middle 50%
          </span>
          <span>
            <i className="legend-count" />
            Priced ads
          </span>
        </div>
      </div>
      {!usable.length ? (
        <StateCard title="No monthly history">
          <p>
            Monthly evidence will appear after usable price observations are
            collected.
          </p>
        </StateCard>
      ) : (
        <>
          {usable.length === 1 && (
            <p className="single-month">
              One month of evidence — insufficient history for a trend.
            </p>
          )}
          <div className="monthly-plot">
            <div className="vertical-axis">
              {[scale[1], (scale[0] + scale[1]) / 2, scale[0]].map((v) => (
                <span key={v}>{num(v / 1000)}k</span>
              ))}
            </div>
            <div
              className="month-columns"
              style={{
                gridTemplateColumns: `repeat(${usable.length}, minmax(44px,1fr))`,
                minWidth: usable.length * 44,
              }}
            >
              {usable.map((m, i) => {
                const bottom = position(m.p25!, scale),
                  top = position(m.p75!, scale);
                return (
                  <button
                    key={m.month}
                    ref={(el) => {
                      refs.current[i] = el;
                    }}
                    className={`month-column ${i === index ? "selected" : ""}`}
                    aria-label={`${monthLabel(m.month, true)}, median ${km(m.median)}, ${m.priced_sample} priced ads`}
                    aria-pressed={i === index}
                    onClick={() => setIndex(i)}
                    onKeyDown={(e) => {
                      let next = i;
                      if (e.key === "ArrowLeft") next = Math.max(0, i - 1);
                      else if (e.key === "ArrowRight")
                        next = Math.min(usable.length - 1, i + 1);
                      else if (e.key === "Home") next = 0;
                      else if (e.key === "End") next = usable.length - 1;
                      else return;
                      e.preventDefault();
                      setIndex(next);
                      refs.current[next]?.focus();
                    }}
                  >
                    <span className="month-price-region">
                      <span
                        className="month-range"
                        style={{
                          bottom: `${bottom}%`,
                          height: `${Math.max(top - bottom, 0.5)}%`,
                        }}
                      />
                      <span
                        className="month-median"
                        style={{ bottom: `${position(m.median!, scale)}%` }}
                      />
                    </span>
                    <span className="month-count-region">
                      <span
                        className="month-count"
                        style={{
                          height: `${(m.priced_sample / maxCount) * 34}px`,
                        }}
                      />
                    </span>
                    <span className="month-ad-count">{m.priced_sample}</span>
                    <span className="month-label">{monthLabel(m.month)}</span>
                  </button>
                );
              })}
            </div>
          </div>
          <div className="month-detail" aria-live="polite">
            <strong>{monthLabel(active.month, true)}</strong>
            <span>Median {km(active.median)}</span>
            <span>Middle 50% {range(active, true)}</span>
            <span>{active.priced_sample} priced ads</span>
            {(active.partial ||
              active.month.slice(0, 7) === asOf.slice(0, 7)) && (
              <small>Partial month · through {displayDate(asOf, false)}</small>
            )}
          </div>
        </>
      )}
      <p className="chart-footnote">
        Repeated snapshots add no weight. Changing car mix can change the median
        — this is not a matched-car price index.
      </p>
    </section>
  );
}
function ComparableAds({
  filters,
  count,
}: {
  filters: Filters;
  count: number;
}) {
  const [open, setOpen] = useState(false),
    [page, setPage] = useState(1);
  const query = useQuery({
    queryKey: ["ads", filters, page],
    queryFn: () => data.listings(filters, page),
    enabled: open,
    retry: false,
  });
  return (
    <details
      className="wide-disclosure"
      onToggle={(e) => setOpen(e.currentTarget.open)}
    >
      <summary>
        <strong>Comparable ads</strong>
        <span>{count} ads · availability unverified</span>
        <ChevronDown size={18} />
      </summary>
      {open && (
        <div className="wide-body">
          <QueryState query={query} label="comparable ads" />
          {query.data && !query.data.items.length && (
            <p className="muted">
              {demo
                ? "Individual ads are not included in this illustrative export."
                : "No observed ads match these filters."}
            </p>
          )}
          {!!query.data?.items.length && (
            <>
              <div className="ads-table-wrap">
                <table className="ads-table">
                  <thead>
                    <tr>
                      {[
                        "Original title",
                        "Asking price",
                        "Year",
                        "Fuel",
                        "Mileage",
                        "Gearbox",
                        "Observed",
                        "History",
                        "OLX",
                      ].map((t) => (
                        <th key={t}>{t}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {query.data.items.map((c) => (
                      <tr key={c.listing_id}>
                        <td data-label="Original title">
                          {c.description || "Title unavailable"}
                        </td>
                        <td data-label="Asking price">
                          {c.price == null ? "Price on request" : km(c.price)}
                        </td>
                        <td data-label="Year">{c.year ?? "Unknown"}</td>
                        <td data-label="Fuel">{c.fuel_type ?? "Unknown"}</td>
                        <td data-label="Mileage">
                          {c.mileage == null
                            ? "Unknown"
                            : `${num(c.mileage)} km`}
                        </td>
                        <td data-label="Gearbox">
                          {c.transmission ?? "Unknown"}
                          <small>
                            {c.transmission_source === "olx_attribute"
                              ? "OLX field"
                              : c.transmission_source === "ad_title"
                                ? "Inferred from title"
                                : "Missing"}
                          </small>
                        </td>
                        <td data-label="Observed">
                          {displayDate(c.last_verified_at, false)}
                        </td>
                        <td data-label="History">
                          <AdHistory id={c.listing_id} />
                        </td>
                        <td data-label="OLX">
                          {c.listing_url?.startsWith("https://olx.ba/") ? (
                            <a
                              className="text-button"
                              href={c.listing_url}
                              target="_blank"
                              rel="noreferrer"
                            >
                              View ↗
                            </a>
                          ) : (
                            "Unavailable"
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="ad-pagination">
                <button
                  className="outline"
                  disabled={page === 1}
                  onClick={() => setPage((p) => p - 1)}
                >
                  Previous
                </button>
                <span>
                  Page {page} · {query.data.total} observed ads
                </span>
                <button
                  className="outline"
                  disabled={page * 20 >= query.data.total}
                  onClick={() => setPage((p) => p + 1)}
                >
                  Next
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </details>
  );
}
function AdHistory({ id }: { id: string }) {
  const [open, setOpen] = useState(false);
  const query = useQuery({
    queryKey: ["history", id],
    queryFn: () => data.history(id),
    enabled: open,
    retry: false,
  });
  const items =
    query.data?.filter(
      (o, i, all) =>
        o.asking_price != null &&
        (i === 0 || o.asking_price !== all[i - 1].asking_price),
    ) ?? [];
  return (
    <details onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary className="text-button">Prices</summary>
      {open && (
        <>
          <QueryState query={query} label="price history" />
          {items.map((h, i) => (
            <p key={i}>
              {displayDate(h.observed_at, false)} · {km(h.asking_price)}
            </p>
          ))}
          {query.data && !items.length && <p>No usable recorded prices.</p>}
        </>
      )}
    </details>
  );
}
function GearboxDetails({
  filters,
  paused,
}: {
  filters: Filters;
  paused: string | null;
}) {
  const [job, setJob] = useState<Job | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const client = useQueryClient();
  const running = job?.status === "queued" || job?.status === "running";
  const sourcePaused = paused && Date.parse(paused) > Date.now();
  useEffect(() => {
    if (!running || !job) return;
    const timeout = setTimeout(() => {
      data
        .job(job.id)
        .then((j) => {
          setJob(j);
          if (j.status === "completed" || j.status === "partial") {
            client.invalidateQueries({ queryKey: ["analysis"] });
            client.invalidateQueries({ queryKey: ["ads"] });
            client.invalidateQueries({ queryKey: ["market"] });
          }
        })
        .catch((e) => {
          setError(e.message);
          setJob(null);
        });
    }, 1500);
    return () => clearTimeout(timeout);
  }, [job, running, client]);
  useEffect(() => {
    setJob(null);
    setError("");
  }, [filters.make, filters.model]);
  async function refresh() {
    setBusy(true);
    setError("");
    try {
      setJob(await data.refreshGearbox(filters));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <details className="wide-disclosure">
      <summary>
        <strong>Gearbox details</strong>
        <span>Check known ad gearbox fields</span>
        <ChevronDown size={18} />
      </summary>
      <div className="wide-body">
        <p>
          Collected OLX fields and title inferences are identified separately.
          Unknown is not evidence.
        </p>
        {sourcePaused && (
          <p role="status">
            <Caution>Source paused · try after {displayDate(paused)}.</Caution>
          </p>
        )}
        {error && (
          <p role="alert">
            <Caution>{error}</Caution>
          </p>
        )}
        {(busy || running) && (
          <p role="status">
            <RefreshCw className="spin" size={16} />
            Checking gearbox fields
            {job ? ` · ${job.checked} of ${job.total}` : "…"}
          </p>
        )}
        {job && !running && (
          <p role="status">
            {job.status === "completed" && !job.errors
              ? "Done"
              : "Lookup incomplete"}{" "}
            · {job.checked} checked · {job.enriched} OLX fields collected.{" "}
            {job.message}
          </p>
        )}
        {demo && (
          <p className="muted">
            Gearbox refresh requires the local database; no ad IDs are included
            in the demo.
          </p>
        )}
        {import.meta.env.VITE_HOSTED === 'true' && !demo && (
          <p className="muted">
            Saved gearbox details are available. New source checks run in the
            collector workspace.
          </p>
        )}
        <button
          className="outline"
          disabled={demo || import.meta.env.VITE_HOSTED === 'true' || busy || running || !!sourcePaused}
          onClick={refresh}
        >
          <RefreshCw size={16} />
          {error ? "Retry gearbox refresh" : "Refresh gearbox details"}
        </button>
      </div>
    </details>
  );
}
