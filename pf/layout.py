"""Layered DAG layout (Sugiyama-lite) with module banding.

Screen layout: x is a GLOBAL dependency layer, so "further right = later in the
build" holds across the whole car, and modules are stacked as horizontal bands.
A cross-module arrow therefore always reads left-to-right, which is the whole
point of drawing it.

Print layout: each module is re-laid-out on its own, module-locally, so it fits
one portrait page.
"""
from __future__ import annotations

from collections import defaultdict

from .model import Edge, Graph, Item

NODE_W = 216
NODE_H = 84
LAYER_GAP = 88          # horizontal space between layers
ROW_GAP = 16            # vertical space between nodes in a layer
BAND_PAD = 34           # padding inside a module band
BAND_GAP = 30           # space between module bands
BAND_LABEL_W = 132      # left gutter holding the module name
MARGIN = 28


# ---------------------------------------------------------------- cycles ----
def break_cycles(items: dict[str, Item], edges: list[Edge]) -> list[Edge]:
    """Depth-first back-edge removal. Returns the edges marked, not dropped --
    a real dependency cycle is a planning bug and should stay visible."""
    color: dict[str, int] = defaultdict(int)   # 0 white, 1 gray, 2 black
    out: dict[str, list[Edge]] = defaultdict(list)
    for e in edges:
        out[e.src].append(e)

    def visit(u: str) -> None:
        color[u] = 1
        for e in out[u]:
            c = color[e.dst]
            if c == 1:
                e.back = True          # closes a cycle
            elif c == 0:
                visit(e.dst)
        color[u] = 2

    for uid in items:
        if color[uid] == 0:
            visit(uid)
    return edges


# --------------------------------------------------------------- layering ---
def assign_layers(items: dict[str, Item], edges: list[Edge]) -> dict[str, int]:
    """Longest-path layering over forward edges only."""
    fwd = [e for e in edges if not e.back]
    preds: dict[str, list[str]] = defaultdict(list)
    succs: dict[str, list[str]] = defaultdict(list)
    for e in fwd:
        preds[e.dst].append(e.src)
        succs[e.src].append(e.dst)

    indeg = {u: len(preds[u]) for u in items}
    layer = {u: 0 for u in items}
    queue = [u for u in items if indeg[u] == 0]
    seen = 0
    while queue:
        u = queue.pop()
        seen += 1
        for v in succs[u]:
            layer[v] = max(layer[v], layer[u] + 1)
            indeg[v] -= 1
            if indeg[v] == 0:
                queue.append(v)
    if seen < len(items):      # defensive: leftovers from a nasty cycle
        for u in items:
            if u not in layer:
                layer[u] = 0
    return layer


def order_within(groups: dict[tuple, list[str]], edges: list[Edge],
                 layer: dict[str, int], passes: int = 4) -> None:
    """Barycenter sweeps to cut edge crossings. Mutates the lists in place."""
    nbr_up: dict[str, list[str]] = defaultdict(list)
    nbr_dn: dict[str, list[str]] = defaultdict(list)
    for e in edges:
        if e.back:
            continue
        nbr_up[e.dst].append(e.src)
        nbr_dn[e.src].append(e.dst)

    pos: dict[str, int] = {}

    def reindex() -> None:
        for members in groups.values():
            for i, u in enumerate(members):
                pos[u] = i

    reindex()
    for p in range(passes):
        table = nbr_up if p % 2 == 0 else nbr_dn
        for members in groups.values():
            if len(members) < 2:
                continue
            def bary(u: str) -> float:
                ns = [pos[n] for n in table[u] if n in pos]
                return sum(ns) / len(ns) if ns else pos[u]
            members.sort(key=lambda u: (bary(u), pos[u]))
        reindex()


# --------------------------------------------------------------- geometry ---
def layout_banded(graph: Graph) -> dict:
    """Whole-project layout: global layers on x, module bands on y."""
    items = graph.items
    edges = break_cycles(items, [e for e in graph.edges if e.kind == "blocks"])
    layer = assign_layers(items, edges)
    for uid, lv in layer.items():
        items[uid].layer = lv

    modules = graph.modules
    groups: dict[tuple, list[str]] = defaultdict(list)
    for uid, it in items.items():
        groups[(it.module, layer[uid])].append(uid)
    for members in groups.values():
        members.sort(key=lambda u: (items[u].state, items[u].label))
    order_within(groups, edges, layer)

    n_layers = max(layer.values(), default=0) + 1
    col_x = [MARGIN + BAND_LABEL_W + i * (NODE_W + LAYER_GAP)
             for i in range(n_layers)]

    nodes: dict[str, dict] = {}
    bands: list[dict] = []
    y = MARGIN
    for mod in modules:
        rows = max((len(groups.get((mod, i), [])) for i in range(n_layers)),
                   default=0)
        band_h = max(NODE_H + 2 * BAND_PAD,
                     rows * NODE_H + (rows - 1) * ROW_GAP + 2 * BAND_PAD)
        for i in range(n_layers):
            members = groups.get((mod, i), [])
            block_h = len(members) * NODE_H + max(0, len(members) - 1) * ROW_GAP
            top = y + (band_h - block_h) / 2
            for k, uid in enumerate(members):
                items[uid].row = k
                nodes[uid] = {
                    "x": col_x[i], "y": top + k * (NODE_H + ROW_GAP),
                    "w": NODE_W, "h": NODE_H, "layer": i, "module": mod,
                }
        bands.append({"module": mod, "y": y, "h": band_h})
        y += band_h + BAND_GAP

    width = (col_x[-1] + NODE_W + MARGIN) if col_x else 800
    height = y - BAND_GAP + MARGIN
    return {
        "nodes": nodes,
        "bands": bands,
        "edges": [{"src": e.src, "dst": e.dst, "back": e.back,
                   "rollup": e.rollup} for e in edges],
        "width": width, "height": height,
        "layers": n_layers,
        "bandLabelW": BAND_LABEL_W, "margin": MARGIN,
    }


def layout_compact(graph: Graph, uids: list[str]) -> dict:
    """Module-local layout for one printed page."""
    sub = {u: graph.items[u] for u in uids}
    edges = [Edge(e.src, e.dst, e.kind, e.back, e.rollup)
             for e in graph.edges
             if e.kind == "blocks" and e.src in sub and e.dst in sub]
    break_cycles(sub, edges)
    layer = assign_layers(sub, edges)

    groups: dict[tuple, list[str]] = defaultdict(list)
    for uid in sub:
        groups[("m", layer[uid])].append(uid)
    for members in groups.values():
        members.sort(key=lambda u: (sub[u].state, sub[u].label))
    order_within(groups, edges, layer)

    n_layers = max(layer.values(), default=0) + 1
    col_x = [MARGIN + i * (NODE_W + LAYER_GAP) for i in range(n_layers)]
    nodes: dict[str, dict] = {}
    max_h = 0
    for i in range(n_layers):
        members = groups.get(("m", i), [])
        for k, uid in enumerate(members):
            nodes[uid] = {"x": col_x[i], "y": MARGIN + k * (NODE_H + ROW_GAP),
                          "w": NODE_W, "h": NODE_H, "layer": i}
        block_h = len(members) * (NODE_H + ROW_GAP) - ROW_GAP
        max_h = max(max_h, block_h)
    return {
        "nodes": nodes,
        "edges": [{"src": e.src, "dst": e.dst, "back": e.back,
                   "rollup": e.rollup} for e in edges],
        "width": (col_x[-1] + NODE_W + MARGIN) if col_x else 400,
        "height": max_h + 2 * MARGIN,
    }
