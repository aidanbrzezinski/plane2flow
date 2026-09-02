"""Core data model: work items, edges, and the graph that holds them."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

# Aidan's SOP state machine. Anything Plane reports that isn't in here gets
# mapped by its state *group* (backlog/unstarted/started/completed/cancelled).
CANON_STATES = ["Backlog", "Ready", "In Progress", "Blocked", "Review",
                "Done", "Dropped"]

_STATE_ALIASES = {
    "backlog": "Backlog",
    "todo": "Ready",
    "to do": "Ready",
    "ready": "Ready",
    "unstarted": "Ready",
    "in progress": "In Progress",
    "inprogress": "In Progress",
    "started": "In Progress",
    "doing": "In Progress",
    "blocked": "Blocked",
    "on hold": "Blocked",
    "onhold": "Blocked",
    "stuck": "Blocked",
    "review": "Review",
    "in review": "Review",
    "qa": "Review",
    "done": "Done",
    "completed": "Done",
    "complete": "Done",
    "closed": "Done",
    "dropped": "Dropped",
    "cancelled": "Dropped",
    "canceled": "Dropped",
    "wontfix": "Dropped",
    "duplicate": "Dropped",
}

# [MODULE] prefix convention from the EV27 Plane SOP.
MODULE_PREFIXES = {
    "ELEC": "Electrical",
    "PWT": "Powertrain",
    "DRV": "Drivetrain",
    "STR": "Structural & Aero",
    "VD": "Vehicle Dynamics",
    "INT": "Integration & DAQ",
}
MODULE_ORDER = ["Electrical", "Powertrain", "Drivetrain",
                "Structural & Aero", "Vehicle Dynamics", "Integration & DAQ"]

_PREFIX_RE = re.compile(r"^\s*\[\s*([A-Za-z&/ \-]{2,24})\s*\]\s*(.*)$")


def normalize_state(raw: str | None, group: str | None = None) -> str:
    """Map a Plane state name (or group) onto the canonical SOP state."""
    if raw:
        hit = _STATE_ALIASES.get(raw.strip().lower())
        if hit:
            return hit
    if group:
        hit = _STATE_ALIASES.get(group.strip().lower())
        if hit:
            return hit
    return raw.strip() if raw else "Backlog"


def split_module_prefix(title: str) -> tuple[str | None, str]:
    """'[ELEC] Inertia switch' -> ('Electrical', 'Inertia switch')."""
    m = _PREFIX_RE.match(title or "")
    if not m:
        return None, (title or "").strip()
    tag, rest = m.group(1).strip().upper(), m.group(2).strip()
    return MODULE_PREFIXES.get(tag, tag.title()), rest or title.strip()


@dataclass
class Item:
    uid: str                      # stable internal key (Plane uuid, or CSV id)
    key: str = ""                 # human key, e.g. "EV27-42"
    title: str = ""
    state: str = "Backlog"
    module: str = "Unassigned"
    cycle: str = ""
    assignees: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    priority: str = ""
    estimate: str = ""
    level: str = ""               # Top / Mid / Low, if derivable
    parent: str | None = None
    target_date: str = ""
    start_date: str = ""
    url: str = ""
    description: str = ""

    # filled in by the graph
    blocked_by: list[str] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)
    relates_to: list[str] = field(default_factory=list)
    children: list[str] = field(default_factory=list)
    layer: int = 0
    row: int = 0

    @property
    def done(self) -> bool:
        """Finished as far as anything downstream is concerned. Dropped counts:
        nothing waits on work that is never happening."""
        return self.state in ("Done", "Dropped", "Cancelled")

    @property
    def label(self) -> str:
        return self.key or self.uid[:8]


@dataclass
class Edge:
    src: str        # blocker  (must finish first)
    dst: str        # blocked  (waits on src)
    kind: str = "blocks"   # blocks | parent | relates
    back: bool = False     # reversed to break a cycle
    rollup: bool = False   # derived from parent/child, not set by hand in Plane

    def key(self) -> tuple:
        return (self.src, self.dst, self.kind)


class Graph:
    def __init__(self, items: Iterable[Item] = (), edges: Iterable[Edge] = ()):
        self.items: dict[str, Item] = {i.uid: i for i in items}
        self.stamps: dict[str, str] = {}
        self.edges: list[Edge] = []
        for e in edges:
            self.add_edge(e)

    # ---- construction -------------------------------------------------
    def add_item(self, item: Item) -> None:
        self.items[item.uid] = item

    def add_edge(self, edge: Edge) -> None:
        if edge.src == edge.dst:
            return
        if edge.src not in self.items or edge.dst not in self.items:
            return
        if any(e.key() == edge.key() for e in self.edges):
            return
        self.edges.append(edge)

    def add_rollup_edges(self) -> int:
        """Treat parent/child as an ordering constraint: a parent cannot be
        finished until every one of its children is.

        Plane's temporal relation types (start_after / finish_before) express
        this directly, but not every build has them -- and where the structure
        is built from sub-issues, the nesting IS the sequencing. So the edge is
        derived here rather than demanded of the data: child -> parent, marked
        rollup so it can be told apart from a link somebody set by hand."""
        added = 0
        for e in list(self.edges):
            if e.kind != "parent":
                continue
            before = len(self.edges)
            self.add_edge(Edge(e.dst, e.src, "blocks", rollup=True))
            added += len(self.edges) - before
        return added

    def finalize(self) -> "Graph":
        """Recompute adjacency lists and the module roster."""
        for it in self.items.values():
            it.blocked_by, it.blocks = [], []
            it.relates_to, it.children = [], []
        for e in self.edges:
            if e.kind == "blocks":
                self.items[e.dst].blocked_by.append(e.src)
                self.items[e.src].blocks.append(e.dst)
            elif e.kind == "relates":
                self.items[e.src].relates_to.append(e.dst)
            elif e.kind == "parent":
                self.items[e.src].children.append(e.dst)
        return self

    def child_progress(self, uid: str) -> tuple[int, int]:
        kids = self.items[uid].children
        return sum(1 for k in kids if self.items[k].done), len(kids)

    def schedule_conflicts(self) -> list[tuple[str, str]]:
        """(blocker, blocked) pairs where the blocker is due AFTER the thing
        waiting on it. The plan says these can both hold; the calendar says
        they cannot."""
        out = []
        for e in self.edges:
            if e.kind != "blocks":
                continue
            a, b = self.items.get(e.src), self.items.get(e.dst)
            if not a or not b or a.done:
                continue
            if a.target_date and b.target_date and a.target_date > b.target_date:
                out.append((e.src, e.dst))
        return out

    # ---- queries ------------------------------------------------------
    @property
    def modules(self) -> list[str]:
        seen = {i.module for i in self.items.values()}
        ordered = [m for m in MODULE_ORDER if m in seen]
        ordered += sorted(m for m in seen if m not in MODULE_ORDER)
        return ordered

    def blocking_edges(self) -> list[Edge]:
        return [e for e in self.edges if e.kind == "blocks"]

    def is_blocked(self, uid: str) -> bool:
        """True when something this item waits on is not finished."""
        it = self.items[uid]
        if it.done:
            return False
        return any(not self.items[b].done for b in it.blocked_by
                   if b in self.items)

    def ready_now(self, uid: str) -> bool:
        it = self.items[uid]
        return not it.done and not self.is_blocked(uid)

    def stats(self) -> dict:
        by_state = {s: 0 for s in CANON_STATES}
        for it in self.items.values():
            by_state[it.state] = by_state.get(it.state, 0) + 1
        return {
            "items": len(self.items),
            "edges": len(self.blocking_edges()),
            "modules": len(self.modules),
            "blocked": sum(1 for u in self.items if self.is_blocked(u)),
            "ready": sum(1 for u in self.items if self.ready_now(u)),
            "rollup": sum(1 for e in self.edges if e.rollup),
            "conflicts": len(self.schedule_conflicts()),
            "by_state": by_state,
        }
