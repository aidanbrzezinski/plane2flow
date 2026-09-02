"""Emit one self-contained HTML file: interactive on screen, paginated on paper.

The SVG geometry is written here, server-side, so the document is complete
without JavaScript -- which is what makes File > Print work reliably and what
makes the output safe to archive. JS only adds pan/zoom, filtering and
chain highlighting on top of finished markup.
"""
from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from . import theme as T
from .layout import NODE_H, NODE_W, layout_banded, layout_compact
from .model import CANON_STATES, Graph

ASSETS = Path(__file__).parent / "assets"

# Print page budget at Letter portrait, 0.5in margins, 96dpi.
PRINT_W = 720
PRINT_H = 470


def e(s) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-") or "x"


def wrap(text: str, width: int, lines: int) -> list[str]:
    words, out, cur = (text or "").split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if len(trial) <= width:
            cur = trial
        else:
            if cur:
                out.append(cur)
            cur = w
            if len(out) == lines:
                break
        if len(out) == lines:
            break
    if cur and len(out) < lines:
        out.append(cur)
    if not out:
        return [""]
    if len(out) == lines:
        joined = " ".join(words)
        shown = len(" ".join(out))
        if shown < len(joined):
            out[-1] = out[-1][:max(0, width - 1)].rstrip() + "…"
    return out


# ------------------------------------------------------------------ svg ----
_CONFLICT_CACHE: dict[int, set] = {}


def _conflict_ids(g: Graph) -> set:
    """Items on the blocked side of a date conflict, cached per graph."""
    key = id(g)
    if key not in _CONFLICT_CACHE:
        _CONFLICT_CACHE[key] = {d for _, d in g.schedule_conflicts()}
    return _CONFLICT_CACHE[key]


def _node(uid: str, it, g: Graph, geo: dict, small: bool = False) -> str:
    x, y, w, h = geo["x"], geo["y"], geo["w"], geo["h"]
    cls = ["node", "st-" + slug(it.state)]
    if it.done:
        cls.append("done")
    blocked = g.is_blocked(uid)
    if blocked:
        cls.append("blocked")
    glyph = T.STATE_GLYPH.get(it.state, "")
    title_lines = wrap(it.title, 33, 2)
    meta_bits = []
    if it.assignees:
        meta_bits.append(it.assignees[0])
    if it.estimate:
        meta_bits.append(it.estimate)
    if it.cycle:
        meta_bits.append(it.cycle)
    if it.target_date:
        meta_bits.append(it.target_date[:10])
    meta = " · ".join(meta_bits)[:28 if it.children else 44]

    parts = [
        f'<g id="n-{e(uid)}" class="{" ".join(cls)}" data-uid="{e(uid)}" '
        f'transform="translate({x:.1f},{y:.1f})">',
        f'<rect class="box" width="{w}" height="{h}"/>',
        # header strip: clipped to the top with a second rect over the fold
        f'<path class="hdr" d="M0,7 A7,7 0 0 1 7,0 L{w-7},0 '
        f'A7,7 0 0 1 {w},7 L{w},19 L0,19 Z"/>',
        f'<text class="hdrtxt" x="8" y="13.5">{e(glyph)} {e(it.state.upper())}</text>',
        f'<text class="key" x="8" y="35">{e(it.label)}</text>',
    ]
    if blocked:
        parts.append(
            f'<rect class="blockbar" x="0" y="19" width="3.5" height="{h-19}"/>')
        parts.append(
            f'<text class="flag" x="{w-8}" y="35" text-anchor="end">'
            f'⛔ BLOCKED</text>')
    elif not it.done:
        parts.append(
            f'<text class="flag rdy" x="{w-8}" y="35" text-anchor="end">'
            f'● READY</text>')
    ybase = 51
    for i, line in enumerate(title_lines):
        parts.append(f'<text class="title" x="8" y="{ybase + i*13}">'
                     f'{e(line)}</text>')
    if meta:
        parts.append(f'<text class="meta" x="8" y="{h-8}">{e(meta)}</text>')
    kdone, ktotal = g.child_progress(uid)
    if ktotal:
        pct = f"{kdone}/{ktotal} sub"
        parts.append(f'<text class="meta kids" x="{w-8}" y="{h-8}" '
                     f'text-anchor="end">{e(pct)}</text>')
    if uid in _conflict_ids(g):
        parts.append(f'<text class="warn" x="{w-8}" y="13.5" '
                     f'text-anchor="end">⚠</text>')
    tip = f"{it.label} — {it.title} [{it.state}]"
    if ktotal:
        tip += f" · {kdone}/{ktotal} sub-items done"
    parts.append("<title>" + e(tip) + "</title>")
    parts.append("</g>")
    return "".join(parts)


def _edge(a: dict, b: dict, back: bool, kind: str, src: str, dst: str,
          rollup: bool = False) -> str:
    cls = ("edge" + (" back" if back else "")
           + (" rel" if kind == "relates" else "")
           + (" par" if kind == "parent" else "")
           + (" roll" if rollup else ""))
    ay, by = a["y"] + a["h"] / 2, b["y"] + b["h"] / 2
    if b["x"] > a["x"]:
        x1, x2 = a["x"] + a["w"], b["x"]
        dx = max(26.0, (x2 - x1) * 0.45)
        d = f"M{x1:.1f},{ay:.1f} C{x1+dx:.1f},{ay:.1f} {x2-dx:.1f},{by:.1f} {x2:.1f},{by:.1f}"
    else:
        x1, x2 = a["x"], b["x"] + b["w"]
        drop = max(abs(ay - by) * 0.35, 46.0)
        d = (f"M{x1:.1f},{ay:.1f} C{x1-70:.1f},{ay+drop:.1f} "
             f"{x2+70:.1f},{by+drop:.1f} {x2:.1f},{by:.1f}")
    return (f'<path class="{cls}" d="{d}" data-src="{e(src)}" '
            f'data-dst="{e(dst)}"/>')


def _defs() -> str:
    return (
        '<defs>'
        '<marker id="arrow" viewBox="0 0 10 10" refX="9.4" refY="5" '
        'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        '<path d="M0,1 L10,5 L0,9 z" class="mk"/></marker>'
        '<marker id="arrow-crit" viewBox="0 0 10 10" refX="9.4" refY="5" '
        'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        '<path d="M0,1 L10,5 L0,9 z" class="mkc"/></marker>'
        '<marker id="arrow-roll" viewBox="0 0 10 10" refX="9.4" refY="5" '
        'markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
        '<path d="M0,2 L9,5 L0,8" class="mkr"/></marker>'
        '</defs>')


def screen_svg(g: Graph, lay: dict) -> str:
    out = [f'<svg id="graph" class="graph dim-done" width="{lay["width"]}" '
           f'height="{lay["height"]}" viewBox="0 0 {lay["width"]} '
           f'{lay["height"]}" xmlns="http://www.w3.org/2000/svg">',
           _defs(), '<g id="viewport">']
    # bands behind everything
    out.append('<g class="bands">')
    for b in lay["bands"]:
        n = sum(1 for i in g.items.values() if i.module == b["module"])
        out.append(
            f'<g class="band"><rect x="6" y="{b["y"]:.1f}" '
            f'width="{lay["width"]-12}" height="{b["h"]:.1f}" rx="10"/>'
            f'<text x="20" y="{b["y"]+24:.1f}">{e(b["module"])}</text>'
            f'<text class="cnt" x="20" y="{b["y"]+40:.1f}">{n} items</text>'
            f'</g>')
    out.append("</g>")
    nodes = lay["nodes"]
    out.append('<g class="edges">')
    for ed in lay["edges"]:
        a, b = nodes.get(ed["src"]), nodes.get(ed["dst"])
        if a and b:
            out.append(_edge(a, b, ed["back"], "blocks", ed["src"], ed["dst"],
                              ed.get("rollup", False)))
    for ed in g.edges:
        if ed.kind not in ("relates", "parent"):
            continue
        a, b = nodes.get(ed.src), nodes.get(ed.dst)
        if a and b:
            out.append(_edge(a, b, False, ed.kind, ed.src, ed.dst))
    out.append("</g>")
    out.append('<g class="nodes">')
    for uid, geo in nodes.items():
        out.append(_node(uid, g.items[uid], g, geo))
    out.append("</g></g></svg>")
    return "".join(out)


def print_svg(g: Graph, uids: list[str]) -> str:
    lay = layout_compact(g, uids)
    W, H = lay["width"], lay["height"]
    s = min(PRINT_W / W, PRINT_H / H, 1.0)
    out = [f'<svg class="graph" width="{W*s:.0f}" height="{H*s:.0f}" '
           f'viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">',
           _defs()]
    for ed in lay["edges"]:
        a, b = lay["nodes"].get(ed["src"]), lay["nodes"].get(ed["dst"])
        if a and b:
            out.append(_edge(a, b, ed["back"], "blocks", ed["src"], ed["dst"],
                              ed.get("rollup", False)))
    for uid, geo in lay["nodes"].items():
        out.append(_node(uid, g.items[uid], g, geo))
    out.append("</svg>")
    return "".join(out)


# ----------------------------------------------------------------- html ----
def state_css(states: list[str]) -> str:
    rows = [":root{"]
    for s in states:
        rows.append(f"--st-{slug(s)}:{T.STATE_RAMP_LIGHT.get(s, '#a9a7a0')};")
        rows.append(f"--sti-{slug(s)}:{T.STATE_INK_LIGHT.get(s, '#0b0b0b')};")
    rows.append("}")
    dark = "".join(
        f"--st-{slug(s)}:{T.STATE_RAMP_DARK.get(s, '#6a6862')};"
        f"--sti-{slug(s)}:{T.STATE_INK_DARK.get(s, '#ffffff')};"
        for s in states)
    rows.append("@media (prefers-color-scheme:dark){:root:not([data-theme=light]){"
                + dark + "}}")
    rows.append(":root[data-theme=dark]{" + dark + "}")
    for s in states:
        k = slug(s)
        rows.append(f".st-{k} .hdr{{fill:var(--st-{k});}}")
        rows.append(f".st-{k} .hdrtxt{{fill:var(--sti-{k});}}")
        rows.append(f".sw-{k}{{background:var(--st-{k});color:var(--sti-{k});}}")
    rows.append(".blockbar{fill:var(--critical);}")
    rows.append(".flag{fill:var(--critical);}.flag.rdy{fill:var(--good);}")
    rows.append(".mk{fill:var(--edge);}.mkc{fill:var(--critical);}"
                ".mkr{fill:none;stroke:var(--edge);stroke-width:1.4;}")
    # Print: force the light ramp and solid ink regardless of screen theme.
    light = "".join(
        f"--st-{slug(s)}:{T.STATE_RAMP_LIGHT.get(s, '#a9a7a0')};"
        f"--sti-{slug(s)}:{T.STATE_INK_LIGHT.get(s, '#0b0b0b')};"
        for s in states)
    rows.append("@media print{:root{" + light +
                "--node-bg:#fff;--ink:#000;--ink2:#333;--muted:#555;"
                "--border:#c3c2b7;--edge:#666;--band-bg:#f4f3f0;}"
                ".graph{ -webkit-print-color-adjust:exact;"
                "print-color-adjust:exact;}}")
    rows.append(".graph{-webkit-print-color-adjust:exact;print-color-adjust:exact;}")
    return "".join(rows)


def safe_json(obj) -> str:
    """JSON for embedding inside <script>. A title containing "</script>" would
    otherwise close the element and break (or hijack) the page."""
    return (json.dumps(obj)
            .replace("<", "\\u003c")
            .replace(">", "\\u003e")
            .replace("&", "\\u0026")
            .replace("\u2028", "\\u2028")
            .replace("\u2029", "\\u2029"))


def _sel(id_: str, label: str, values: list[str]) -> str:
    opts = "".join(f'<option value="{e(v)}">{e(v)}</option>' for v in values
                   if v)
    return (f'<select id="{id_}" aria-label="{e(label)}">'
            f'<option value="">{e(label)}</option>{opts}</select>')


def _table(g: Graph, uids: list[str]) -> str:
    rows = []
    for uid in uids:
        it = g.items[uid]
        blockers = [g.items[b].label for b in it.blocked_by if b in g.items
                    and not g.items[b].done]
        blocks = [g.items[b].label for b in it.blocks if b in g.items]
        flag = ('<span class="blk">blocked</span>' if g.is_blocked(uid)
                else '<span class="rdy">ready</span>' if g.ready_now(uid)
                else '<span class="fin">&mdash;</span>')
        rows.append(
            f'<tr><td class="k">{e(it.label)}</td><td>{e(it.title)}</td>'
            f'<td class="nw">{e(T.STATE_GLYPH.get(it.state, ""))} '
            f'{e(it.state)}</td><td class="nw">{flag}</td>'
            f'<td class="k">{e(", ".join(blockers)) or "—"}</td>'
            f'<td class="k">{e(", ".join(blocks)) or "—"}</td>'
            f'<td>{e(", ".join(it.assignees))or "—"}</td>'
            f'<td class="nw">{e(it.target_date[:10]) or "—"}</td></tr>')
    return (
        '<table class="pt"><colgroup>'
        '<col style="width:8%"><col style="width:27%">'
        '<col style="width:10%"><col style="width:7%">'
        '<col style="width:13%"><col style="width:19%">'
        '<col style="width:8%"><col style="width:8%">'
        '</colgroup><thead><tr><th>ID</th><th>Work item</th>'
        "<th>State</th><th>Now</th><th>Waiting on</th><th>Blocks</th>"
        "<th>Owner</th><th>Target</th></tr></thead><tbody>" + "".join(rows) +
        "</tbody></table>")


def _legend(states: list[str]) -> str:
    sw = "".join(
        f'<span class="sw sw-{slug(s)}">{e(T.STATE_GLYPH.get(s, ""))} {e(s)}</span>'
        for s in states)
    return ('<div class="legend">' + sw +
            '<span class="sw" style="background:#d03b3b;color:#fff">'
            '⛔ blocked</span>'
            '<span class="sw" style="background:#0ca30c;color:#fff">'
            '● ready now</span>'
            '<span class="sw" style="border-style:dashed">'
            '⇠ dashed red = dependency loop</span>'
            '<span class="sw">↦ thin arrow = sub-item rolling up to its '
            'parent</span>'
            '<span class="sw">⚠ = blocker due after the thing it blocks</span>'
            '</div>')


def _item_link(g: Graph, uid: str, extra: str = "") -> str:
    it = g.items[uid]
    key = e(it.label)
    label = f'<code>{key}</code>' if not it.url else (
        f'<a href="{e(it.url)}" target="_blank" rel="noopener">'
        f'<code>{key}</code></a>')
    return f'{label} {e(it.title)}{extra}'


def standup_html(g: Graph) -> str:
    """Who is working on what, who is stuck, and what is free to pick up."""
    people: dict[str, list[str]] = {}
    for uid, it in g.items.items():
        if it.done:
            continue
        for who in (it.assignees or ["Unassigned"]):
            people.setdefault(who, []).append(uid)

    order = sorted((p for p in people if p != "Unassigned"),
                   key=lambda p: -len(people[p]))
    if "Unassigned" in people:
        order.append("Unassigned")

    def li(uid: str, show_blockers: bool) -> str:
        it = g.items[uid]
        bits = [f'<li>{_item_link(g, uid)}']
        meta = [x for x in (it.module, it.cycle, it.target_date[:10]) if x]
        if meta:
            bits.append(f'<br><code>{e(" · ".join(meta))}</code>')
        if show_blockers:
            names = [g.items[b].label for b in it.blocked_by
                     if b in g.items and not g.items[b].done]
            if names:
                bits.append(f'<br><span class="on-hold">waiting on '
                            f'{e(", ".join(names[:5]))}</span>')
        bits.append("</li>")
        return "".join(bits)

    cards = []
    for who in order:
        uids = people[who]
        doing = [u for u in uids if g.items[u].state in ("In Progress", "Review")
                 and not g.is_blocked(u)]
        stuck = [u for u in uids if g.is_blocked(u)]
        free = [u for u in uids if g.ready_now(u)
                and g.items[u].state not in ("In Progress", "Review")]
        for lst in (doing, stuck, free):
            lst.sort(key=lambda u: (g.items[u].target_date or "9999",
                                    g.items[u].label))

        def block(head: str, lst: list[str], blockers: bool,
                  empty: str) -> str:
            if not lst:
                return f'<h4>{e(head)}</h4><p class="empty">{e(empty)}</p>'
            return (f'<h4>{e(head)} ({len(lst)})</h4><ul class="tasklist">'
                    + "".join(li(u, blockers) for u in lst) + "</ul>")

        cards.append(
            '<div class="card">'
            f'<h3>{e(who)}<span class="n">{len(uids)} open</span></h3>'
            + block("In flight", doing, False, "nothing in progress")
            + block("Blocked", stuck, True, "nothing blocked")
            + block("Free to start", free, False, "nothing queued")
            + "</div>")

    if not cards:
        return '<div class="allclear">Nothing open. Everything is Done.</div>'
    return '<div class="su-grid">' + "".join(cards) + "</div>"


def hygiene_html(g: Graph, rules, note: str = "") -> str:
    from . import hygiene as H
    summ = H.summary(rules)
    head = (
        '<div class="hy-head">'
        f'<div><div class="big">{summ["findings"]}</div>'
        '<div class="lbl">findings</div></div>'
        f'<div><div class="big">{summ["items"]}</div>'
        '<div class="lbl">work items affected</div></div>'
        f'<div><div class="big">{summ["serious"]}</div>'
        '<div class="lbl">serious</div></div>'
        f'<div><div class="big">{summ["warning"]}</div>'
        '<div class="lbl">worth fixing</div></div>'
        "</div>")
    if note:
        head += f'<p class="note">{note}</p>'
    if not rules:
        return head + ('<div class="allclear">Nothing flagged. Every check '
                       "passed.</div>")

    out = [head]
    for r in rules:
        rows = "".join(
            f'<tr><td class="k">{_item_link(g, uid)}</td>'
            f'<td>{e(g.items[uid].state)}</td>'
            f"<td>{e(g.items[uid].module)}</td>"
            f'<td>{e(", ".join(g.items[uid].assignees)) or "&mdash;"}</td>'
            f"<td>{e(detail)}</td></tr>"
            for uid, detail in r.items)
        out.append(
            f'<details class="rule"{" open" if r.severity == "serious" else ""}>'
            f'<summary><span class="sev {r.severity}">{e(r.severity)}</span>'
            f'{e(r.label)}<span class="count">{len(r.items)}</span></summary>'
            f'<p class="why">{e(r.why)}</p>'
            '<table class="hy"><thead><tr><th>Work item</th><th>State</th>'
            "<th>Module</th><th>Owner</th><th>What is wrong</th></tr></thead>"
            f"<tbody>{rows}</tbody></table></details>")
    return "".join(out)


def hygiene_print(g: Graph, rules) -> str:
    if not rules:
        return ('<section class="psec"><h2>Hygiene</h2>'
                "<p>Nothing flagged.</p></section>")
    secs = []
    for r in rules:
        rows = "".join(
            f'<tr><td class="k">{e(g.items[uid].label)}</td>'
            f"<td>{e(g.items[uid].title)}</td>"
            f'<td class="nw">{e(g.items[uid].state)}</td>'
            f"<td>{e(', '.join(g.items[uid].assignees)) or '—'}</td>"
            f"<td>{e(detail)}</td></tr>"
            for uid, detail in r.items)
        secs.append(
            f"<h2>{e(r.label)}</h2>"
            f'<p class="sub">{e(r.severity.upper())} · {len(r.items)} items · '
            f"{e(r.why)}</p>"
            '<table class="pt"><thead><tr><th>ID</th><th>Work item</th>'
            "<th>State</th><th>Owner</th><th>What is wrong</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>")
    return '<section class="psec">' + "".join(secs) + "</section>"


def standup_print(g: Graph) -> str:
    people: dict[str, list[str]] = {}
    for uid, it in g.items.items():
        if it.done:
            continue
        for who in (it.assignees or ["Unassigned"]):
            people.setdefault(who, []).append(uid)
    order = sorted((p for p in people if p != "Unassigned"),
                   key=lambda p: -len(people[p]))
    if "Unassigned" in people:
        order.append("Unassigned")
    rows = []
    for who in order:
        for uid in sorted(people[who],
                          key=lambda u: (g.items[u].target_date or "9999",
                                         g.items[u].label)):
            it = g.items[uid]
            if g.is_blocked(uid):
                status = ('<span class="blk">blocked</span> — '
                          + e(", ".join(g.items[b].label
                                        for b in it.blocked_by
                                        if not g.items[b].done)[:60]))
            elif it.state in ("In Progress", "Review"):
                status = "in flight"
            else:
                status = '<span class="rdy">free to start</span>'
            rows.append(
                f'<tr><td>{e(who)}</td><td class="k">{e(it.label)}</td>'
                f"<td>{e(it.title)}</td>"
                f'<td class="nw">{e(it.state)}</td><td>{status}</td>'
                f'<td class="nw">{e(it.target_date[:10]) or "—"}</td></tr>')
    return ('<section class="psec"><h2>Standup</h2>'
            '<p class="sub">Open work by owner, soonest target first.</p>'
            '<table class="pt"><thead><tr><th>Owner</th><th>ID</th>'
            "<th>Work item</th><th>State</th><th>Now</th><th>Target</th>"
            "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></section>")


def render(g: Graph, title: str, subtitle: str = "",
           source_note: str = "", rules=None, hygiene_note: str = "") -> str:
    lay = layout_banded(g)
    st = g.stats()
    states = [s for s in CANON_STATES if st["by_state"].get(s)]
    states = states or CANON_STATES
    now = datetime.now(timezone.utc).astimezone()

    payload = {
        "items": {
            uid: {
                "key": it.label, "title": it.title, "state": it.state,
                "module": it.module, "cycle": it.cycle,
                "assignees": it.assignees, "labels": it.labels,
                "estimate": it.estimate, "target_date": it.target_date,
                "url": it.url, "parent": it.parent or "",
                "children": it.children,
                "kidsDone": g.child_progress(uid)[0],
            } for uid, it in g.items.items()},
        "edges": [{"src": e_.src, "dst": e_.dst, "kind": e_.kind,
                   "rollup": e_.rollup} for e_ in g.edges],
        "conflicts": [{"blocker": a, "blocked": b}
                      for a, b in g.schedule_conflicts()],
        "width": lay["width"], "height": lay["height"],
        "theme": T.theme_payload(),
    }

    assignees = sorted({a for i in g.items.values() for a in i.assignees})
    cycles = sorted({i.cycle for i in g.items.values() if i.cycle})

    blocked = [u for u in g.items if g.is_blocked(u)]
    blocked.sort(key=lambda u: (g.items[u].module, g.items[u].label))
    ready = [u for u in g.items if g.ready_now(u)]
    ready.sort(key=lambda u: (g.items[u].module, g.items[u].label))

    cross = [ed for ed in g.blocking_edges()
             if g.items[ed.src].module != g.items[ed.dst].module]

    # ---- print sections
    psecs = [
        '<section class="psec cover">'
        f"<h1>{e(title)}</h1>"
        f'<p class="sub">{e(subtitle)}{" · " if subtitle else ""}'
        f'Printed {e(now.strftime("%Y-%m-%d %H:%M %Z"))}'
        f'{" · " + e(source_note) if source_note else ""}</p>'
        '<div class="kpis">'
        f'<div class="kpi"><div class="n">{st["items"]}</div>'
        '<div class="l">work items</div></div>'
        f'<div class="kpi"><div class="n">{st["edges"]}</div>'
        f'<div class="l">dependencies</div></div>'
        f'<div class="kpi"><div class="n">{st["conflicts"]}</div>'
        '<div class="l">date conflicts</div></div>'
        f'<div class="kpi"><div class="n">{st["blocked"]}</div>'
        '<div class="l">blocked</div></div>'
        f'<div class="kpi"><div class="n">{st["ready"]}</div>'
        '<div class="l">ready now</div></div>'
        f'<div class="kpi"><div class="n">{st["modules"]}</div>'
        '<div class="l">modules</div></div>'
        "</div>" + _legend(states) +
        "<h2 style=\"margin-top:18pt\">Blocked right now</h2>"
        f'<p class="sub">Waiting on something that is not Done.</p>'
        + (_table(g, blocked) if blocked else "<p>Nothing is blocked.</p>") +
        "<h2 style=\"margin-top:16pt\">Ready to start</h2>"
        f'<p class="sub">No unfinished upstream dependencies.</p>'
        + (_table(g, ready) if ready else "<p>Nothing is ready.</p>") +
        "</section>"
    ]
    for mod in g.modules:
        uids = [u for u, i in g.items.items() if i.module == mod]
        uids.sort(key=lambda u: (g.items[u].layer, g.items[u].label))
        n_blk = sum(1 for u in uids if g.is_blocked(u))
        psecs.append(
            f'<section class="psec"><h2>{e(mod)}</h2>'
            f'<p class="sub">{len(uids)} work items · {n_blk} blocked · '
            "arrows read left to right: a box must finish before the boxes it "
            "points to can start</p>"
            + print_svg(g, uids) + _table(g, uids) + "</section>")
    conflicts = g.schedule_conflicts()
    if conflicts:
        rows = "".join(
            f'<tr><td class="k">{e(g.items[a].label)}</td>'
            f"<td>{e(g.items[a].title)}</td>"
            f'<td class="nw">{e(g.items[a].target_date[:10])}</td>'
            f'<td class="k">{e(g.items[b].label)}</td>'
            f"<td>{e(g.items[b].title)}</td>"
            f'<td class="nw blk">{e(g.items[b].target_date[:10])}</td></tr>'
            for a, b in conflicts)
        psecs.append(
            '<section class="psec"><h2>Schedule conflicts</h2>'
            '<p class="sub">The blocker is due <em>after</em> the thing waiting '
            "on it. Both dates cannot hold; one of them is wrong.</p>"
            '<table class="pt"><thead><tr><th>Blocker</th><th></th>'
            "<th>Due</th><th>Blocks</th><th></th><th>Due</th></tr></thead>"
            "<tbody>" + rows + "</tbody></table></section>")

    if cross:
        rows = "".join(
            f'<tr><td class="k">{e(g.items[ed.src].label)}</td>'
            f"<td>{e(g.items[ed.src].module)}</td>"
            f'<td class="k">{e(g.items[ed.dst].label)}</td>'
            f"<td>{e(g.items[ed.dst].module)}</td>"
            f"<td>{e(g.items[ed.src].state)}</td></tr>"
            for ed in cross)
        psecs.append(
            '<section class="psec"><h2>Cross-module dependencies</h2>'
            '<p class="sub">Where one lead is waiting on another. These are '
            "the ones that slip.</p>"
            '<table class="pt"><thead><tr><th>Blocker</th><th>Owned by</th>'
            "<th>Blocks</th><th>Owned by</th><th>Blocker state</th></tr>"
            "</thead><tbody>" + rows + "</tbody></table></section>")

    from . import hygiene as H
    rules = H.run(g) if rules is None else rules
    hsum = H.summary(rules)

    css = (ASSETS / "app.css").read_text() + state_css(states)
    js = (ASSETS / "app.js").read_text()
    badge_cls = "badge" if hsum["serious"] else "badge quiet"
    badge_n = hsum["serious"] or hsum["findings"]

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<style>{css}</style>
</head>
<body data-tab="board">
<header class="bar screenonly">
  <span class="brand">{e(title)}<small>{e(subtitle)}</small></span>
  <span class="ctl board-only">
    {_sel("f-module", "All modules", g.modules)}
    {_sel("f-state", "All states", states)}
    {_sel("f-assignee", "Anyone", assignees)}
    {_sel("f-cycle", "All cycles", cycles)}
    <input id="f-q" type="search" placeholder="Search id, title, label…">
    <button id="t-blocked" aria-pressed="false">Blocked only</button>
    <button id="t-hidedone" aria-pressed="false">Hide done</button>
    <button id="t-dimdone" aria-pressed="true">Dim done</button>
    <button id="t-relates" aria-pressed="false">Relates-to</button>
    <button id="t-parent" aria-pressed="false" title="Show the parent/child nesting itself as dotted lines">Nesting</button>
    <button id="b-critical" title="Highlight the longest unfinished dependency chain">Critical path</button>
  </span>
  <span class="spacer"></span>
  <span class="counts board-only"><b id="shown">{st["items"]}</b>/{st["items"]} shown ·
    <span class="warn">{st["blocked"]} blocked</span> ·
    {st["ready"]} ready{f' · <span class="warn">{st["conflicts"]} date conflicts</span>' if st["conflicts"] else ""}</span>
  <button id="b-fit" class="board-only" title="Fit to window (f)">Fit</button>
  <button id="b-theme" title="Toggle theme">◐</button>
  <button id="b-print">Print</button>
</header>

<nav class="tabs screenonly" role="tablist">
  <button role="tab" data-pane="board" aria-selected="true">Board</button>
  <button role="tab" data-pane="standup" aria-selected="false">Standup</button>
  <button role="tab" data-pane="hygiene" aria-selected="false">Hygiene<span
    class="{badge_cls}">{badge_n}</span></button>
</nav>

<main class="screenonly">
  <section class="pane" id="pane-board">
    <div id="wrap">
      <div id="stage">{screen_svg(g, lay)}</div>
      <aside id="panel" hidden></aside>
    </div>
  </section>
  <section class="pane scroll" id="pane-standup" hidden>
    <div class="inner">{standup_html(g)}</div>
  </section>
  <section class="pane scroll" id="pane-hygiene" hidden>
    <div class="inner">{hygiene_html(g, rules, hygiene_note)}</div>
  </section>
</main>

<div class="printonly print-board">{"".join(psecs)}</div>
<div class="printonly print-standup">{standup_print(g)}</div>
<div class="printonly print-hygiene">{hygiene_print(g, rules)}</div>

<script type="application/json" id="pf-data">{safe_json(payload)}</script>
<script>{js}</script>
</body>
</html>"""
