"""Read a Plane CSV export.

Plane's export column set has moved around across versions, and self-hosted CE
lags the cloud docs, so nothing here matches a header literally: headers are
normalized (lowercased, non-alphanumerics stripped) and matched against a list
of candidates. `--explain-csv` prints what matched what, which is the fastest
way to find out that your export calls it "Work item ID" this month.

Relations are NOT in any Plane CSV export. The CSV gives nodes; edges come from
the API (sources_api) or, as a fallback, from IDs referenced in descriptions.
"""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path

from .model import Edge, Graph, Item, normalize_state, split_module_prefix

FIELDS = {
    "key":        ["id", "workitemid", "issueid", "key", "sequenceid",
                   "sequence", "workitemkey", "issuekey"],
    "title":      ["name", "title", "workitemname", "issuename", "summary"],
    "state":      ["state", "status", "statename", "workflowstate"],
    "stategroup": ["stategroup", "statusgroup", "group"],
    "module":     ["module", "modulename", "modules"],
    "cycle":      ["cycle", "cyclename", "sprint", "iteration"],
    "assignee":   ["assignee", "assignees", "assignedto", "owner"],
    "labels":     ["labels", "label", "tags"],
    "priority":   ["priority"],
    "estimate":   ["estimate", "estimatepoint", "points", "estimatepoints",
                   "tshirt", "tshirtsize"],
    "parent":     ["parent", "parentid", "parentissue", "parentworkitem",
                   "parentname"],
    "target":     ["targetdate", "duedate", "enddate", "target"],
    "start":      ["startdate", "start"],
    "url":        ["url", "link", "permalink"],
    "desc":       ["description", "descriptionstripped", "descriptionhtml",
                   "details", "body"],
    "project":    ["project", "projectname", "projectidentifier"],
    "uuid":       ["uuid", "workitemuuid", "issueuuid", "internalid"],
}

_norm = lambda s: re.sub(r"[^a-z0-9]", "", (s or "").lower())
_SPLIT = re.compile(r"\s*[;,|]\s*")


def _map_headers(headers: list[str]) -> dict[str, str]:
    """field -> actual header. First candidate wins, exact before prefix."""
    seen = {_norm(h): h for h in headers if h}
    out: dict[str, str] = {}
    for field, cands in FIELDS.items():
        for c in cands:
            if c in seen:
                out[field] = seen[c]
                break
        else:
            for c in cands:
                hit = next((orig for n, orig in seen.items()
                            if n.startswith(c) or c in n), None)
                if hit:
                    out[field] = hit
                    break
    return out


def _multi(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [p.strip() for p in _SPLIT.split(raw) if p.strip()]


def _level_from(estimate: str, title: str) -> str:
    """Top/Mid/Low per the SOP: XXL is reserved for top level."""
    e = (estimate or "").strip().upper()
    if e in ("XXL", "5", "XXLARGE"):
        return "Top"
    if e in ("XL", "L", "4", "3"):
        return "Mid"
    if e:
        return "Low"
    return ""


def load_csv(path: str | Path, explain: bool = False) -> tuple[Graph, dict]:
    text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    headers = reader.fieldnames or []
    cols = _map_headers(headers)

    graph = Graph()
    by_key: dict[str, str] = {}
    rows = 0
    for r in reader:
        get = lambda f: (r.get(cols[f], "") or "").strip() if f in cols else ""
        key = get("key")
        uid = get("uuid") or key or f"row{rows}"
        if not key and not get("title"):
            continue
        rows += 1
        title_raw = get("title")
        mod_from_title, clean_title = split_module_prefix(title_raw)
        module = get("module") or mod_from_title or "Unassigned"
        module = _multi(module)[0] if _multi(module) else "Unassigned"
        est = get("estimate")
        it = Item(
            uid=uid,
            key=key or uid,
            title=clean_title or title_raw,
            state=normalize_state(get("state"), get("stategroup")),
            module=module,
            cycle=get("cycle"),
            assignees=_multi(get("assignee")),
            labels=_multi(get("labels")),
            priority=get("priority"),
            estimate=est,
            level=_level_from(est, title_raw),
            parent=get("parent") or None,
            target_date=get("target"),
            start_date=get("start"),
            url=get("url"),
            description=get("desc")[:4000],
        )
        graph.add_item(it)
        if key:
            by_key[key.upper()] = uid

    # Parent links, if the export carried them, become containment edges.
    for it in graph.items.values():
        if it.parent:
            pid = by_key.get(it.parent.upper()) or (
                it.parent if it.parent in graph.items else None)
            if pid:
                graph.add_edge(Edge(pid, it.uid, "parent"))

    report = {
        "headers": headers,
        "mapped": cols,
        "unmapped": [h for h in headers if h not in cols.values()],
        "rows": rows,
        "keyIndex": by_key,
    }
    if explain:
        print("CSV columns detected:")
        for f in FIELDS:
            print(f"  {f:<10} <- {cols.get(f, '(none)')}")
        if report["unmapped"]:
            print("  unused columns:", ", ".join(report["unmapped"]))
        print(f"  {rows} rows parsed")
    return graph.finalize(), report


# Fallback edge source: "blocked by ELEC-12" written into a description.
_MENTION = re.compile(
    r"(blocked\s*by|depends\s*on|after|waiting\s*on|blocks|before)\s*[:\-]?\s*"
    r"((?:[A-Z][A-Z0-9]*-\d+[\s,;/]*)+)", re.I)
_TOKEN = re.compile(r"[A-Z][A-Z0-9]*-\d+", re.I)


def edges_from_descriptions(graph: Graph, key_index: dict[str, str]) -> int:
    added = 0
    for it in graph.items.values():
        for verb, blob in _MENTION.findall(it.description or ""):
            v = verb.lower().replace(" ", "")
            for tok in _TOKEN.findall(blob):
                other = key_index.get(tok.upper())
                if not other or other == it.uid:
                    continue
                e = (Edge(other, it.uid, "blocks")
                     if v in ("blockedby", "dependson", "after", "waitingon")
                     else Edge(it.uid, other, "blocks"))
                before = len(graph.edges)
                graph.add_edge(e)
                added += len(graph.edges) - before
    graph.finalize()
    return added
