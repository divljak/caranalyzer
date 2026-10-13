import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  BrowserRouter,
  Link,
  Navigate,
  NavLink,
  Route,
  Routes,
  useLocation,
} from "react-router-dom";
import {
  CarFront,
  ChartLine,
  Columns3,
  Database,
  Eye,
  LayoutGrid,
  Menu,
  X,
} from "lucide-react";
import type { Segment } from "../pages/dealerShared";
import { data, demo } from "./data";
import {
  defaultFilters,
  displayDate,
  segmentKey,
  withoutCeiling,
} from "./evidence";
import { FilterPanel, StateCard } from "./shared";
import Market from "./Market";
import ModelAnalysis from "./ModelAnalysis";
import Compare from "./Compare";
import Following from "./Following";
import "./refined.css";

const selectionStorage = `car-analyzer-selection-${demo ? "demo" : "local"}-v1`;
function initialSelection(): Segment[] {
  try {
    const saved = sessionStorage.getItem(selectionStorage);
    if (saved) return JSON.parse(saved).slice(0, 3);
  } catch {
    /* ignore unavailable storage */
  }
  if (!demo) return [];
  return window.location.pathname === "/compare"
    ? [
        { make: "Škoda", model: "Karoq" },
        { make: "Volkswagen", model: "T-Roc" },
        { make: "Peugeot", model: "2008" },
      ]
    : [
        { make: "Peugeot", model: "2008" },
        { make: "Peugeot", model: "3008" },
      ];
}
import { Context } from "./context";
export default function Workspace() {
  return (
    <div className="refined-app">
      <BrowserRouter>
        <ContextBody />
        <div className="sr-only" aria-live="polite">
          Car Analyzer
        </div>
      </BrowserRouter>
    </div>
  );
  function ContextBody() {
    const location = useLocation();
    const [marketFilters, setMarketFilters] = useState(defaultFilters);
    const [analysisFilters, setAnalysisFilters] = useState(
      withoutCeiling(defaultFilters),
    );
    const [compareFilters, setCompareFilters] = useState(
      withoutCeiling(defaultFilters),
    );
    const [selected, setSelected] = useState(initialSelection);
    const [message, setMessage] = useState("");
    const [drawer, setDrawer] = useState(false);
    const overview = useQuery({
      queryKey: ["overview", demo],
      queryFn: data.overview,
      retry: false,
    });
    const href = (path: string) =>
      `${path}${demo ? (path.includes("?") ? "&" : "?") + "demo=1" : ""}`;
    const analyzeHref = (s: Segment, f = marketFilters) =>
      href(
        `/models/${encodeURIComponent(s.make)}/${encodeURIComponent(s.model)}?${new URLSearchParams(
          Object.entries(withoutCeiling(f))
            .filter(([k]) => !["make", "model", "price_max"].includes(k))
            .map(([k, v]) => [k, String(v)]),
        )}`,
      );
    const add = (s: Segment) => {
      if (selected.some((x) => segmentKey(s) === segmentKey(x))) return true;
      if (selected.length === 3) {
        setMessage(
          "Up to 3 models. Remove one from comparison to add another.",
        );
        return false;
      }
      setSelected((prev) => [...prev, s]);
      return true;
    };
    const toggle = (s: Segment) => {
      if (selected.some((x) => segmentKey(x) === segmentKey(s)))
        setSelected((prev) =>
          prev.filter((x) => segmentKey(x) !== segmentKey(s)),
        );
      else add(s);
    };
    useEffect(() => {
      sessionStorage.setItem(selectionStorage, JSON.stringify(selected));
    }, [selected]);
    useEffect(() => {
      setDrawer(false);
      setMessage("");
      window.scrollTo({ top: 0 });
    }, [location.pathname]);
    const page =
      location.pathname === "/compare"
        ? "compare"
        : location.pathname === "/following"
          ? "following"
          : location.pathname.startsWith("/models/")
            ? "analysis"
            : "market";
    const filters =
      page === "compare"
        ? compareFilters
        : page === "analysis"
          ? analysisFilters
          : marketFilters;
    const apply =
      page === "compare"
        ? setCompareFilters
        : page === "analysis"
          ? setAnalysisFilters
          : setMarketFilters;
    const nav = [
      ["/market", "Market", LayoutGrid],
      ["/models/Škoda/Karoq", "Model analysis", ChartLine],
      ["/compare", "Compare", Columns3],
      ["/following", "Following", Eye],
    ] as const;
    return (
      <Context.Provider
        value={{
          marketFilters,
          setMarketFilters,
          analysisFilters,
          setAnalysisFilters,
          compareFilters,
          setCompareFilters,
          selected,
          toggle,
          add,
          overview,
          notice: setMessage,
          href,
          analyzeHref,
        }}
      >
        <div className={`workspace page-${page}`}>
          <div className="mobile-top">
            <div className="brand">
              <span className="brand-mark">
                <CarFront size={20} />
              </span>
              Car Analyzer
            </div>
            <button
              aria-label="Toggle navigation and filters"
              aria-expanded={drawer}
              onClick={() => setDrawer(!drawer)}
            >
              {drawer ? <X /> : <Menu />}
            </button>
          </div>
          <aside className={`floating-sidebar ${drawer ? "is-open" : ""}`}>
            <div className="brand">
              <span className="brand-mark">
                <CarFront size={20} />
              </span>
              <div>
                Car Analyzer<small>OLX.ba · Bosnia</small>
              </div>
            </div>
            <nav aria-label="Workspace navigation">
              {nav.map(([path, label, Icon]) => (
                <NavLink
                  key={label}
                  to={href(path)}
                  className={() =>
                    `nav-item ${(label === "Model analysis" ? page === "analysis" : page === label.toLowerCase()) ? "active" : ""}`
                  }
                  aria-current={
                    (
                      label === "Model analysis"
                        ? page === "analysis"
                        : page === label.toLowerCase()
                    )
                      ? "page"
                      : undefined
                  }
                >
                  <Icon size={19} />
                  {label}
                </NavLink>
              ))}
            </nav>
            {page === "following" ? (
              <div className="saved-note">
                <h2>Saved searches</h2>
                <p>
                  A saved search is a model plus its exact years, mileage, fuel,
                  gearbox, category and window — not an individual car.
                </p>
                <p className="muted">
                  No automatic notifications. Open this page to see changes
                  after a new collection.
                </p>
              </div>
            ) : (
              <FilterPanel
                key={page}
                kind={page}
                filters={filters}
                onApply={apply}
                models={overview.data?.models ?? []}
              />
            )}
            <div className="last-updated">
              <Database size={18} />
              <div>
                <small>Last clean collection</small>
                <span>{displayDate(overview.data?.as_of)}</span>
              </div>
            </div>
          </aside>
          <main className="main-column">
            {message && (
              <div className="notice" role="status">
                <span>{message}</span>
                <button
                  aria-label="Dismiss message"
                  onClick={() => setMessage("")}
                >
                  <X size={16} />
                </button>
              </div>
            )}
            <Routes>
              <Route
                path="/"
                element={<Navigate to={href("/market")} replace />}
              />
              <Route path="/market" element={<Market />} />
              <Route path="/models/:make/:model" element={<ModelAnalysis />} />
              <Route path="/compare" element={<Compare />} />
              <Route path="/following" element={<Following />} />
              <Route
                path="*"
                element={
                  <StateCard title="Page not found">
                    <Link className="primary" to={href("/market")}>
                      Browse market
                    </Link>
                  </StateCard>
                }
              />
            </Routes>
          </main>
        </div>
      </Context.Provider>
    );
  }
}
