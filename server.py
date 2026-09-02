#!/usr/bin/env python3
"""plane2flow server -- read-only. Polls Plane, serves the board and a JSON API.

Nothing here writes to Plane. The only state is an on-disk cache so a restart
(or a Plane outage) still serves the last good picture instead of a blank page.

  GET /                  the interactive board (HTML)
  GET /board.html        same file, as a download
  GET /api/graph         everything: items, edges, stats
  GET /api/items         list, filterable by module/state/assignee/cycle
  GET /api/items/{key}   one work item plus its chains
  GET /api/blocked       what is waiting on something unfinished
  GET /api/ready         what could be started today
  GET /api/critical-path longest dependency chain to each leaf
  GET /api/health        freshness, for Uptime Kuma
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.responses import HTMLResponse, JSONResponse

from pf.model import Graph
from pf.render import render
from pf.sources_api import PlaneError, load_api
from pf.sources_csv import edges_from_descriptions, load_csv

DATA = Path(os.getenv("PF_DATA_DIR", "/data"))
REFRESH = int(os.getenv("PF_REFRESH_SECONDS", "300"))
TITLE = os.getenv("PF_TITLE", "EV27 dependency flow")
SUBTITLE = os.getenv("PF_SUBTITLE", "")
FALLBACK_CSV = os.getenv("PF_FALLBACK_CSV", "")
REFRESH_TOKEN = os.getenv("PF_REFRESH_TOKEN", "")
# block: a parent cannot finish until every child is done. contain / ignore.
PARENT_MODE = os.getenv("PF_PARENT_MODE", "block")

CFG = {
    "base_url": os.getenv("PLANE_BASE_URL", ""),
    "api_key": os.getenv("PLANE_API_KEY", ""),
    "workspace": os.getenv("PLANE_WORKSPACE", ""),
    "project": os.getenv("PLANE_PROJECT", ""),
    "insecure": os.getenv("PLANE_INSECURE", "").lower() in ("1", "true", "yes"),
    "workers": int(os.getenv("PLANE_WORKERS", "4")),
    # Plane allows 60 requests/minute. Relations are one request per work item,
    # so the first poll of a large project is paced and slow; the on-disk cache
    # means every poll after that only re-reads work items that changed.
    "rate": float(os.getenv("PLANE_RATE", "55")),
}


class State:
    graph: Graph | None = None
    html: str = ""
    payload: dict = {}
    fetched_at: float = 0.0
    source: str = "none"
    last_error: str = ""
    refreshing: bool = False


S = State()


# ------------------------------------------------------------- building ----
def _payload(g: Graph) -> dict:
    items = []
    for uid, it in g.items.items():
        items.append({
            "id": uid, "key": it.label, "title": it.title, "state": it.state,
            "module": it.module, "cycle": it.cycle, "assignees": it.assignees,
            "labels": it.labels, "priority": it.priority,
            "estimate": it.estimate, "target_date": it.target_date,
            "url": it.url, "layer": it.layer,
            "blocked_by": [g.items[b].label for b in it.blocked_by if b in g.items],
            "blocks": [g.items[b].label for b in it.blocks if b in g.items],
            "blocked": g.is_blocked(uid),
            "ready": g.ready_now(uid),
        })
    items.sort(key=lambda r: (r["module"], r["key"]))
    edges = [{"from": g.items[e.src].label, "to": g.items[e.dst].label,
              "kind": e.kind,
              "cross_module": g.items[e.src].module != g.items[e.dst].module}
             for e in g.edges if e.src in g.items and e.dst in g.items]
    return {"stats": g.stats(), "items": items, "edges": edges,
            "modules": g.modules}


def _cache_write(g: Graph, html: str, payload: dict, source: str) -> None:
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        (DATA / "board.html").write_text(html, encoding="utf-8")
        (DATA / "cache.json").write_text(json.dumps(
            {"payload": payload, "fetched_at": time.time(), "source": source}),
            encoding="utf-8")
    except OSError as exc:
        S.last_error = f"cache write failed: {exc}"


def _cache_read() -> bool:
    try:
        html = (DATA / "board.html").read_text(encoding="utf-8")
        blob = json.loads((DATA / "cache.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    S.html, S.payload = html, blob.get("payload", {})
    S.fetched_at = blob.get("fetched_at", 0.0)
    S.source = blob.get("source", "cache") + " (cached)"
    return True


def build() -> None:
    """Fetch, render, cache. Never raises -- a failure leaves the last good
    board in place and records the reason on /api/health."""
    if S.refreshing:
        return
    S.refreshing = True
    try:
        missing = [k for k in ("base_url", "api_key", "workspace", "project")
                   if not CFG[k]]
        g: Graph | None = None
        source = ""
        if not missing:
            try:
                g = load_api(CFG["base_url"], CFG["api_key"], CFG["workspace"],
                             CFG["project"], insecure=CFG["insecure"],
                             workers=CFG["workers"], rate=CFG["rate"],
                             cache_path=str(DATA / "relations-cache.json"))
                source = f"plane:{CFG['workspace']}/{CFG['project']}"
                S.last_error = ""
            except PlaneError as exc:
                S.last_error = str(exc)
        else:
            # No Plane config at all means CSV mode was chosen on purpose --
            # that is a configuration, not a failure.
            S.last_error = ("" if FALLBACK_CSV else
                            "missing config: " + ", ".join(missing))

        if g is None and FALLBACK_CSV and Path(FALLBACK_CSV).exists():
            g, report = load_csv(FALLBACK_CSV)
            edges_from_descriptions(g, report["keyIndex"])
            source = f"csv:{Path(FALLBACK_CSV).name}"

        if g is None or not g.items:
            return

        g.finalize()
        if PARENT_MODE == "ignore":
            g.edges = [e for e in g.edges if e.kind != "parent"]
        elif PARENT_MODE == "block":
            g.add_rollup_edges()
        g.finalize()
        html = render(g, TITLE, SUBTITLE, source)
        payload = _payload(g)
        S.graph, S.html, S.payload = g, html, payload
        S.fetched_at, S.source = time.time(), source
        _cache_write(g, html, payload, source)
    finally:
        S.refreshing = False


async def _loop() -> None:
    while True:
        await asyncio.to_thread(build)
        await asyncio.sleep(max(30, REFRESH))


@asynccontextmanager
async def lifespan(app: FastAPI):
    _cache_read()
    task = asyncio.create_task(_loop())
    yield
    task.cancel()


app = FastAPI(title="plane2flow", version="1.0.0",
              description="Read-only dependency view of a Plane project.",
              lifespan=lifespan)


def need() -> dict:
    if not S.payload:
        raise HTTPException(503, detail={
            "error": "no data yet",
            "last_error": S.last_error or "first poll has not finished"})
    return S.payload


def _meta() -> dict:
    return {"fetched_at": S.fetched_at, "source": S.source,
            "age_seconds": round(time.time() - S.fetched_at, 1)
            if S.fetched_at else None}


# ---------------------------------------------------------------- routes ---
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def board() -> HTMLResponse:
    if not S.html:
        raise HTTPException(503, "no board rendered yet")
    return HTMLResponse(S.html, headers={"Cache-Control": "no-cache"})


@app.get("/board.html", include_in_schema=False)
def download() -> Response:
    if not S.html:
        raise HTTPException(503, "no board rendered yet")
    return Response(S.html, media_type="text/html", headers={
        "Content-Disposition": 'attachment; filename="ev27-board.html"'})


@app.get("/api/graph")
def graph() -> dict:
    return {**_meta(), **need()}


@app.get("/api/items")
def items(module: str | None = None, state: str | None = None,
          assignee: str | None = None, cycle: str | None = None,
          blocked: bool | None = None, ready: bool | None = None,
          q: str | None = Query(None, description="substring of key or title")
          ) -> dict:
    rows = need()["items"]

    def keep(r: dict) -> bool:
        if module and r["module"] != module: return False
        if state and r["state"] != state: return False
        if cycle and r["cycle"] != cycle: return False
        if assignee and assignee not in r["assignees"]: return False
        if blocked is not None and r["blocked"] != blocked: return False
        if ready is not None and r["ready"] != ready: return False
        if q and q.lower() not in (r["key"] + " " + r["title"]).lower():
            return False
        return True

    out = [r for r in rows if keep(r)]
    return {**_meta(), "count": len(out), "items": out}


@app.get("/api/items/{key}")
def item(key: str) -> dict:
    rows = need()["items"]
    hit = next((r for r in rows if r["key"].lower() == key.lower()), None)
    if not hit:
        raise HTTPException(404, f"no work item {key}")
    by_key = {r["key"]: r for r in rows}

    def walk(start: str, field: str) -> list[str]:
        seen, stack = set(), list(by_key[start][field])
        while stack:
            k = stack.pop()
            if k in seen or k not in by_key:
                continue
            seen.add(k)
            stack.extend(by_key[k][field])
        return sorted(seen)

    return {**_meta(), "item": hit,
            "upstream": walk(hit["key"], "blocked_by"),
            "downstream": walk(hit["key"], "blocks")}


@app.get("/api/blocked")
def blocked_items() -> dict:
    rows = [r for r in need()["items"] if r["blocked"]]
    return {**_meta(), "count": len(rows), "items": rows}


@app.get("/api/ready")
def ready_items() -> dict:
    rows = [r for r in need()["items"] if r["ready"]]
    return {**_meta(), "count": len(rows), "items": rows}


@app.get("/api/critical-path")
def critical_path() -> dict:
    """Longest unfinished dependency chain ending at each leaf, longest first.
    This is where the schedule actually lives."""
    rows = need()["items"]
    by_key = {r["key"]: r for r in rows}
    memo: dict[str, list[str]] = {}

    def longest(k: str, seen: frozenset = frozenset()) -> list[str]:
        if k in memo:
            return memo[k]
        if k in seen:
            return []
        best: list[str] = []
        for p in by_key.get(k, {}).get("blocked_by", []):
            cand = longest(p, seen | {k})
            if len(cand) > len(best):
                best = cand
        memo[k] = best + [k]
        return memo[k]

    chains = []
    for r in rows:
        if not r["blocks"]:
            path = longest(r["key"])
            open_ = [k for k in path if by_key[k]["state"] not in
                     ("Done", "Cancelled")]
            chains.append({"leaf": r["key"], "length": len(path),
                           "open_length": len(open_), "path": path,
                           "module": r["module"]})
    chains.sort(key=lambda c: (-c["open_length"], -c["length"]))
    return {**_meta(), "chains": chains[:25]}


@app.get("/api/health")
def health() -> JSONResponse:
    age = time.time() - S.fetched_at if S.fetched_at else None
    stale = age is None or age > REFRESH * 3
    body = {"ok": not stale and bool(S.payload), "stale": stale,
            "items": len((S.payload or {}).get("items", [])),
            "last_error": S.last_error or None, **_meta()}
    return JSONResponse(body, status_code=200 if body["ok"] else 503)


@app.post("/api/refresh", include_in_schema=False)
async def refresh(token: str = "") -> dict:
    """Off unless PF_REFRESH_TOKEN is set. Still read-only toward Plane."""
    if not REFRESH_TOKEN:
        raise HTTPException(404)
    if token != REFRESH_TOKEN:
        raise HTTPException(403, "bad token")
    await asyncio.to_thread(build)
    return {"refreshed": True, **_meta()}
