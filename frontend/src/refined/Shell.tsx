import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { Database, Info } from "lucide-react";
import { demo, previewState } from "./data";
import { displayDate } from "./evidence";
import { useWorkspace } from "./context";
export function PageHeader({
  title,
  scope,
  back = false,
}: {
  title: string;
  scope: string;
  back?: boolean;
}) {
  const { href } = useWorkspace();
  return (
    <header className="page-header">
      <div>
        {back && (
          <Link className="back-link" to={href("/market")}>
            ← Market
          </Link>
        )}
        <h1>{title}</h1>
        <p className="meta">
          <span>Sample · asking prices in KM</span>
          <span className="meta-dot">·</span>
          {scope}
        </p>
      </div>
      <details className="about-data">
        <summary>
          <Info size={16} />
          About the data
          <span className="disclosure-chevron" />
        </summary>
        <div className="disclosure-body">
          {demo && (
            <p>
              <strong>Illustrative demo.</strong> These numbers are made up for
              the design. Aggregate fixtures support the exported scope; other
              scopes may have no fixture evidence.
            </p>
          )}
          <p>
            The collector attempts a bounded sample of the newest OLX car results.
            This is incomplete market coverage. This database copy does not
            automatically receive future collections.
          </p>
          <p>
            Asking prices are not transaction prices. Leaving the collected
            window is not a sale. Availability is unverified; there is no sales,
            demand or selling-speed evidence.
          </p>
          <p>
            Typical price is the median. Middle 50% is the 25th–75th percentile.
            Missing prices are excluded and each priced ad counts once. The
            Market ceiling decides eligibility and the within-ceiling count;
            price bands use the full filtered cohort.
          </p>
          <p>
            Cuts count distinct ads with recorded reductions, not cut events.
            Monthly history uses the final usable price per ad per UTC month,
            once, over the last 12 months. Changing car mix can change the
            median.
          </p>
          <p>
            Make, model and category are inferred labels. Attribute filters
            exclude unknowns. Gearbox sources distinguish OLX fields, title
            inference and missing values. Unknown specifications are not
            verified evidence.
          </p>
        </div>
      </details>
    </header>
  );
}
export function StaleNotice({ asOf }: { asOf?: string | null }) {
  const stale =
    previewState === "stale" ||
    (asOf && Date.now() - Date.parse(asOf) > 3 * 86400000);
  return stale ? (
    <div className="stale-notice" role="status">
      <Database size={16} />
      <span>
        Data through {displayDate(asOf)}. No newer collection is available.
      </span>
    </div>
  ) : null;
}
export function BlueBand({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return <section className={`blue-band ${className}`}>{children}</section>;
}
