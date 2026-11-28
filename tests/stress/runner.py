"""Run the stress cases in ``bot_cases.yaml`` against whatever branch is checked out.

    python -m tests.stress.runner                 # table of results, exit 0
    python -m tests.stress.runner --json out.json # also write the machine-readable result
    python -m tests.stress.runner --strict        # exit 1 when any case fails (for CI on a fixed branch)
    python -m tests.stress.runner -k budget       # only case ids / categories containing "budget"
    python -m tests.stress.runner --original      # only the first 100 cases (ids without the "v2-" prefix)
    python -m tests.stress.runner --new           # only the v2 cases (ids starting "v2-")

Needs PyYAML (``pip install pyyaml``). Everything is offline: see ``harness.py``.
"""

from collections import Counter, OrderedDict
from datetime import datetime, timezone
import json
import pathlib
import sys

import yaml

from telegram_intent import fold
from tests.stress.harness import Session

CASES_FILE = pathlib.Path(__file__).with_name("bot_cases.yaml")
# The v2 cases (ids "v2-...") live in their own file so the first 100 stay exactly as written.
# The exact-year rule cases (GAR-8, ids "v2-yr-...") are in a third file for the same reason.
CASE_FILES = (CASES_FILE, pathlib.Path(__file__).with_name("bot_cases_v2.yaml"),
              pathlib.Path(__file__).with_name("bot_cases_year_rule.yaml"),
              pathlib.Path(__file__).with_name("bot_cases_filler_budget.yaml"))
FILTER_KEYS = ("search", "budget", "price_min", "mileage_max", "mileage_min", "year_min", "year_max", "fuel",
               "transmission", "new_ok", "family", "low_km")


def load_cases(paths=CASE_FILES):
    cases = []
    for path in [paths] if isinstance(paths, (str, pathlib.Path)) else paths:
        if not pathlib.Path(path).exists():
            continue
        with open(path, encoding="utf-8") as handle:
            cases.extend(yaml.safe_load(handle)["cases"])
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), "duplicate case ids"
    return cases


def _year_value(value):
    """"Y-10" / "Y" -> this year minus 10 / this year; anything else unchanged."""
    if isinstance(value, str) and value.startswith("Y"):
        return datetime.now(timezone.utc).year + int(value[1:] or 0)
    return value


def _check_alternative(result, expect):
    """Return a list of problems for one alternative of an expectation (empty list = it holds)."""
    problems = []
    intent = result["intent"]
    if result.get("error"):
        return [f"crashed: {result['error']}"]
    kinds = expect.get("intent")
    if kinds is not None:
        kinds = [kinds] if isinstance(kinds, str) else list(kinds)
        actual = intent.kind if intent else f"(no answer: {result.get('reason')})"
        if actual not in kinds:
            problems.append(f"intent {actual!r}, expected {'/'.join(kinds)}")
    if "note" in expect and (intent.note if intent else None) != expect["note"]:
        problems.append(f"note {intent.note if intent else None!r}, expected {expect['note']!r}")
    for key, want in (expect.get("filters") or {}).items():
        have = getattr(intent.filters, key, None) if intent else None
        want = _year_value(want)
        if key == "search":
            have, want = fold(have or ""), fold(want or "")
            ok = have == want
        elif want is None or want is False:
            ok = not have
        else:
            ok = have == want
        if not ok:
            problems.append(f"filters.{key} = {have!r}, expected {want!r}")
    for word in expect.get("search_has", []):
        have = fold(intent.filters.search if intent else "")
        if fold(word) not in have:
            problems.append(f"filters.search {have!r} lacks {word!r}")
    for word in expect.get("search_lacks", []):
        if intent and fold(word) in fold(intent.filters.search).split():
            problems.append(f"filters.search {intent.filters.search!r} wrongly contains {word!r}")
    if "sort" in expect and result["sort"] != expect["sort"]:
        problems.append(f"sort {result['sort']!r}, expected {expect['sort']!r}")
    text = fold(result["text"])
    for word in expect.get("reply_has", []):
        if fold(word) not in text:
            problems.append(f"reply lacks {word!r}")
    if expect.get("reply_has_any") and not any(fold(w) in text for w in expect["reply_has_any"]):
        problems.append(f"reply has none of {expect['reply_has_any']}")
    for word in expect.get("reply_lacks", []):
        if fold(word) in text:
            problems.append(f"reply wrongly contains {word!r}")
    buttons = " | ".join(fold(b) for b in result["buttons"])
    if "buttons_min" in expect and len(result["buttons"]) < expect["buttons_min"]:
        problems.append(f"{len(result['buttons'])} buttons, expected at least {expect['buttons_min']}")
    for word in expect.get("buttons_have", []):
        if fold(word) not in buttons:
            problems.append(f"buttons lack {word!r}")
    if expect.get("buttons_have_any") and not any(fold(w) in buttons for w in expect["buttons_have_any"]):
        problems.append(f"buttons have none of {expect['buttons_have_any']}")
    if "footer_chars_max" in expect:
        used = footer_chars(result["text"])
        if used > expect["footer_chars_max"]:
            problems.append(f"footer is {used} characters, expected at most {expect['footer_chars_max']}")
    if "handled" in expect and bool(result["handled"]) != expect["handled"]:
        problems.append(f"handled={result['handled']}, expected {expect['handled']}")
    for key, bound in (expect.get("lt") or {}).items():  # "a jeftinije?": the new cap is below the old one
        have = getattr(intent.filters, key, None) if intent else None
        if have is None or not have < _year_value(bound):
            problems.append(f"filters.{key} = {have!r}, expected below {_year_value(bound)!r}")
    for key, bound in (expect.get("gt") or {}).items():  # "a novije?": the year floor moves up
        have = getattr(intent.filters, key, None) if intent else None
        if have is None or not have > _year_value(bound):
            problems.append(f"filters.{key} = {have!r}, expected above {_year_value(bound)!r}")
    if "days" in expect and (intent.days if intent else None) != expect["days"]:
        problems.append(f"days {intent.days if intent else None!r}, expected {expect['days']!r}")
    labels = intent.labels if intent else ()
    for label in expect.get("labels_has", []):
        if label not in labels:
            problems.append(f"labels {labels!r} lack {label!r}")
    for label in expect.get("labels_lacks", []):
        if label in labels:
            problems.append(f"labels {labels!r} wrongly contain {label!r}")
    if "reply_chars_max" in expect and len(result["text"]) > expect["reply_chars_max"]:
        problems.append(f"reply is {len(result['text'])} characters, expected at most {expect['reply_chars_max']}")
    return problems


def footer_chars(text):
    """Characters in the flush-left lines after the last listing row (rows start with a bullet, their detail
    lines are indented): the notes and the disclaimer under the results."""
    lines = text.split("\n")
    last = max((i for i, line in enumerate(lines) if line.startswith("•")), default=None)
    if last is None:
        return 0
    return sum(len(line) for line in lines[last + 1:] if line.strip() and not line.startswith(" "))


def check_turn(result, expect):
    """Problems for one turn. ``any_of`` lists alternatives; the turn passes when one of them holds."""
    alternatives = expect.get("any_of")
    if not alternatives:
        return _check_alternative(result, expect)
    shared = {k: v for k, v in expect.items() if k != "any_of"}
    attempts = [_check_alternative(result, {**shared, **alt}) for alt in alternatives]
    if any(not a for a in attempts):
        return []
    best = min(attempts, key=len)
    return [f"none of {len(attempts)} acceptable readings matched; closest: " + "; ".join(best)]


def run_case(case):
    session = Session(chat=case.get("chat", "private"), dataset=case.get("dataset", "rows"))
    turns = []
    for turn in case["turns"]:
        result = session.say(turn["say"], reply=bool(turn.get("reply")), untagged=bool(turn.get("untagged")),
                             user_id=turn.get("user"))
        problems = check_turn(result, turn.get("expect") or {})
        intent = result["intent"]
        turns.append({"say": turn["say"], "problems": problems, "covers": turn.get("covers", []),
                      "intent": intent.kind if intent else None,
                      "note": intent.note if intent else None,
                      "filters": ({k: getattr(intent.filters, k) for k in FILTER_KEYS
                                   if getattr(intent.filters, k)} if intent else {}),
                      "labels": list(intent.labels) if intent else [], "gemini": result["gemini"],
                      "handled": result["handled"],
                      "sort": result["sort"], "buttons": result["buttons"],
                      "reply": result["text"][:400], "error": result["error"]})
    return {"id": case["id"], "category": case["category"], "title": case.get("title", ""),
            "impact": case.get("impact", ""),
            "passed": all(not t["problems"] for t in turns), "turns": turns}


NEW_PREFIX = "v2-"


def run_all(selector=None, only=None):
    """``only``: None (all), "original" (the first 100) or "new" (ids starting with ``NEW_PREFIX``)."""
    results = []
    for case in load_cases():
        if selector and selector not in case["id"] and selector not in case["category"]:
            continue
        if only and (only == "new") != case["id"].startswith(NEW_PREFIX):
            continue
        results.append(run_case(case))
    return results


def by_category(results):
    table = OrderedDict()
    for r in results:
        row = table.setdefault(r["category"], Counter())
        row["pass" if r["passed"] else "fail"] += 1
    return table


def summary_lines(results):
    lines = [f"{'category':<22}{'pass':>6}{'fail':>6}{'total':>7}"]
    total = Counter()
    for category, row in by_category(results).items():
        lines.append(f"{category:<22}{row['pass']:>6}{row['fail']:>6}{row['pass'] + row['fail']:>7}")
        total.update(row)
    lines.append(f"{'TOTAL':<22}{total['pass']:>6}{total['fail']:>6}{total['pass'] + total['fail']:>7}")
    return lines


def failure_lines(results):
    out = []
    for r in results:
        if r["passed"]:
            continue
        out.append(f"FAIL {r['id']} [{r['category']}] {r['title']}")
        for t in r["turns"]:
            if t["problems"]:
                covers = f" (planned fix {','.join(map(str, t['covers']))})" if t["covers"] else " (NEW)"
                route = " [Gemini first in prod]" if t.get("gemini") else " [parser-only]"
                out.append(f"   > {t['say']!r}{covers}{route}")
                out.extend(f"       - {p}" for p in t["problems"])
    return out


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    selector = argv[argv.index("-k") + 1] if "-k" in argv else None
    only = "original" if "--original" in argv else "new" if "--new" in argv else None
    results = run_all(selector, only)
    print("\n".join(summary_lines(results)))
    print()
    print("\n".join(failure_lines(results)))
    if "--json" in argv:
        pathlib.Path(argv[argv.index("--json") + 1]).write_text(json.dumps(results, ensure_ascii=False, indent=1))
    return 1 if "--strict" in argv and any(not r["passed"] for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
