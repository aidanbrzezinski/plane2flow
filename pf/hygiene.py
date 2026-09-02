"""Hygiene checks -- things in the project that are probably mistakes.

Every rule here answers "someone should go fix this in Plane", not "this is
interesting". A rule that fires on healthy data is worse than no rule, because
it teaches people to ignore the report, so each one is written to fire only on
a real contradiction or a real SOP violation.

Rules split into two groups: structural ones that need only the current state,
and the review-skip rule, which needs work-item history and is therefore
optional (see sources_api.entered_states).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .model import MODULE_ORDER, Graph

SEV_ORDER = {"serious": 0, "warning": 1, "info": 2}


@dataclass
class Rule:
    code: str
    label: str
    severity: str
    why: str
    items: list[tuple[str, str]] = field(default_factory=list)  # (uid, detail)


def _has_prefix(title: str, raw_title: str) -> bool:
    return raw_title.strip().startswith("[")


def run(g: Graph, review_skipped: set[str] | None = None,
        history_available: bool = True) -> list[Rule]:
    rules: list[Rule] = []

    def rule(code, label, sev, why):
        r = Rule(code, label, sev, why)
        rules.append(r)
        return r

    # ---- contradictions: the data disagrees with itself ------------------
    r = rule("done_open_children", "Closed with open sub-items", "serious",
             "The parent is marked Done but work inside it is not. Either the "
             "sub-items are stale or the parent was closed early.")
    for uid, it in g.items.items():
        if it.state != "Done" or not it.children:
            continue
        open_kids = [k for k in it.children if not g.items[k].done]
        if open_kids:
            r.items.append((uid, f"{len(open_kids)} of {len(it.children)} "
                                 f"sub-items still open: " +
                                 ", ".join(g.items[k].label for k in open_kids[:6])))

    r = rule("done_blocked", "Done, but its blockers are not", "serious",
             "This finished before something it depends on. One of the two "
             "states is wrong, or the dependency is.")
    for uid, it in g.items.items():
        if not it.done:
            continue
        open_b = [b for b in it.blocked_by if b in g.items
                  and not g.items[b].done]
        if open_b:
            r.items.append((uid, "waiting on " +
                            ", ".join(g.items[b].label for b in open_b[:6])))

    r = rule("date_conflict", "Blocker due after the work it blocks", "serious",
             "Both target dates cannot hold. One of them needs moving.")
    for a, b in g.schedule_conflicts():
        r.items.append((b, f"due {g.items[b].target_date[:10]}, blocked by "
                            f"{g.items[a].label} due "
                            f"{g.items[a].target_date[:10]}"))

    r = rule("loop", "Dependency loop", "serious",
             "Two or more items each wait on the other. Nothing in the loop "
             "can ever start.")
    for e in g.edges:
        if e.kind == "blocks" and e.back:
            r.items.append((e.dst, f"loops back through {g.items[e.src].label}"))

    if review_skipped is not None:
        r = rule("skipped_review", "Done without passing Review", "serious",
                 "The SOP state machine is Backlog to Ready to In Progress to "
                 "Review to Done. These reached Done without ever entering "
                 "Review, so nobody checked them.")
        for uid in sorted(review_skipped, key=lambda u: g.items[u].label):
            if uid in g.items:
                r.items.append((uid, "no Review step in this item's history"))

    r = rule("blocked_state_clear", "Marked Blocked, but nothing blocks it",
             "warning",
             "The item sits in the Blocked state while every upstream item is "
             "finished. Either it is waiting on something nobody linked, or it "
             "was unblocked and never moved on.")
    for uid, it in g.items.items():
        if it.state == "Blocked" and not g.is_blocked(uid):
            n = len(it.blocked_by)
            r.items.append((uid, "no unfinished blockers" +
                            (f" ({n} linked, all done)" if n
                             else " and nothing linked at all")))

    # ---- in-flight problems ---------------------------------------------
    r = rule("blocked_in_progress", "Being worked on while blocked", "warning",
             "Someone is spending time on this while an upstream item is "
             "unfinished. Either the dependency is wrong or the work is.")
    for uid, it in g.items.items():
        if it.state in ("In Progress", "Review") and g.is_blocked(uid):
            open_b = [b for b in it.blocked_by if not g.items[b].done]
            r.items.append((uid, "waiting on " +
                            ", ".join(g.items[b].label for b in open_b[:4])))

    # ---- SOP conformance -------------------------------------------------
    r = rule("no_module", "Not assigned to a module", "warning",
             "The SOP puts every work item under exactly one of the six "
             "module leads. These belong to nobody.")
    known = set(MODULE_ORDER)
    for uid, it in g.items.items():
        if it.module == "Unassigned" or it.module not in known:
            r.items.append((uid, f"module: {it.module or 'none'}"))

    r = rule("no_owner", "No assignee", "warning",
             "Unowned work does not get done. Worst on anything already "
             "started.")
    for uid, it in g.items.items():
        if not it.assignees and not it.done:
            r.items.append((uid, f"{it.state}, in {it.module}"))

    r = rule("no_estimate", "No estimate", "info",
             "The SOP uses T-shirt estimates at every level, with XXL "
             "reserved for top level.")
    for uid, it in g.items.items():
        if not it.estimate and not it.done:
            r.items.append((uid, it.state))

    r = rule("no_target", "No target date", "info",
             "Without a date this item cannot participate in schedule checks "
             "and never shows up as late.")
    for uid, it in g.items.items():
        if not it.target_date and not it.done:
            r.items.append((uid, it.state))

    r = rule("no_cycle", "Not in a cycle", "info",
             "Work outside a cycle is invisible to the 2-week planning "
             "rhythm.")
    for uid, it in g.items.items():
        if not it.cycle and not it.done:
            r.items.append((uid, it.state))

    r = rule("xxl_not_top", "XXL estimate below top level", "info",
             "The SOP reserves XXL for top-level items. An XXL with a parent "
             "is either mis-sized or mis-nested.")
    for uid, it in g.items.items():
        if it.estimate.strip().upper() == "XXL" and it.parent:
            r.items.append((uid, f"XXL under {g.items[it.parent].label}"
                                 if it.parent in g.items else "XXL with a parent"))

    # ---- isolation -------------------------------------------------------
    r = rule("isolated", "No dependencies and no nesting", "info",
             "Nothing waits on it, it waits on nothing, and it is not part of "
             "anything. Usually means the links were never set.")
    for uid, it in g.items.items():
        if it.done:
            continue
        if not (it.blocked_by or it.blocks or it.children or it.parent):
            r.items.append((uid, f"{it.state}, in {it.module}"))

    for r in rules:
        r.items.sort(key=lambda t: g.items[t[0]].label)
    rules = [r for r in rules if r.items]
    rules.sort(key=lambda r: (SEV_ORDER.get(r.severity, 9), -len(r.items)))
    return rules


def summary(rules: list[Rule]) -> dict:
    flagged = {uid for r in rules for uid, _ in r.items}
    return {
        "rules": len(rules),
        "findings": sum(len(r.items) for r in rules),
        "items": len(flagged),
        "serious": sum(len(r.items) for r in rules if r.severity == "serious"),
        "warning": sum(len(r.items) for r in rules if r.severity == "warning"),
    }
