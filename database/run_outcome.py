"""One reading of a collector run's outcome, shared by the exit codes, the bot, the digest and health.

The stored ``status`` stays exactly as the collector writes it (``partial`` still means "not all
discovery pages were read"), so nothing that reads the table changes meaning. What changes is how a
run is *judged*: a run that stopped only because the day's search-page budget was used up, with no
errors and no rejected pages, is ``budget_limited``. That is expected and healthy, so it exits 0 and
is shown as OK. A run with errors, rejected pages or an exception is still a real ``partial``.
"""

import json

BUDGET_LIMITED_REASONS = frozenset({"discovery_budget_incomplete"})
HEALTHY = frozenset({"ok", "budget_limited"})


def stopped_reason(report):
    """``stopped_reason`` from a run's stored report (JSON text or dict); None if unreadable."""
    if isinstance(report, dict):
        return report.get("stopped_reason")
    if not isinstance(report, str) or not report.lstrip().startswith("{"):
        return None
    try:
        data = json.loads(report)
    except ValueError:
        return None
    return data.get("stopped_reason") if isinstance(data, dict) else None


def _count(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def classify(run):
    """Return ok | budget_limited | partial | failed | paused | budget_exhausted | running | none."""
    if not run:
        return "none"
    status = str(run.get("status") or "").lower()
    errors = _count(run.get("errors"))
    rejected = _count(run.get("rejected"))
    if isinstance(run.get("report"), (str, dict)):
        report = run["report"]
        reason = stopped_reason(report)
        if isinstance(report, str) and report.lstrip().startswith("{"):
            try:
                rejected = rejected or _count(json.loads(report).get("rejected"))
            except ValueError:
                pass
    else:
        reason = run.get("stopped_reason")
    if status == "completed":
        return "ok" if not errors else "partial"
    if status == "partial" and not errors and not rejected and reason in BUDGET_LIMITED_REASONS:
        return "budget_limited"
    if status in {"partial", "failed", "paused", "budget_exhausted", "running"}:
        return status
    return status or "none"


def is_healthy(run):
    return classify(run) in HEALTHY


def exit_code(report):
    """Process exit code for a finished collector run: 0 for a clean or only budget-limited run."""
    return 0 if is_healthy(report) else 1
