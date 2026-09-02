"""Plane REST client -- stdlib only, read-only.

Self-hosted Plane CE lags the cloud API docs, so every call here tries the
current path first and falls back to the legacy one:

  /work-items/                      ->  /issues/
  /work-items/{id}/dependencies/    ->  /issues/{id}/relations/

That second fallback is the one that matters: on CE 1.3.1 the /dependencies/
endpoint 404s and only the older /relations/ endpoint returns blocking,
blocked_by, duplicate and relates_to. (makeplane/plane-mcp-server#185)

Auth is X-API-Key. Nothing here issues a write.
"""
from __future__ import annotations

import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .cache import RelationCache
from .model import Edge, Graph, Item, normalize_state, split_module_prefix
from .ratelimit import Throttle, retry_after


class PlaneError(RuntimeError):
    pass


class PlaneClient:
    def __init__(self, base_url: str, api_key: str, workspace: str,
                 timeout: int = 30, insecure: bool = False,
                 workers: int = 4, verbose: bool = False,
                 rate: float = 55.0, retries: int = 4):
        self.base = base_url.rstrip("/")
        self.key = api_key
        self.ws = workspace
        self.timeout = timeout
        self.workers = max(1, workers)
        self.verbose = verbose
        self.ctx = ssl._create_unverified_context() if insecure else None
        self.rel_ok = 0          # relation lookups that reached an endpoint
        self.rel_fail = 0        # relation lookups where every endpoint failed
        self.rel_error = ""      # first failure, for the report
        self.rel_unresolved = 0  # rows whose target id matched no item
        self.hist_fail = 0
        self.hist_error = ""
        self.throttle = Throttle(rate)
        self.retries = max(0, retries)
        self.requests = 0

    # ---- plumbing -----------------------------------------------------
    def _get(self, path: str, params: dict | None = None) -> Any:
        url = f"{self.base}/api/v1{path}"
        if params:
            url += "?" + urllib.parse.urlencode(
                {k: v for k, v in params.items() if v is not None})
        req = urllib.request.Request(url, headers={
            "X-API-Key": self.key,
            "Accept": "application/json",
            "User-Agent": "plane2flow/1.0",
        })
        backoff = 2.0
        for attempt in range(self.retries + 1):
            self.throttle.take()
            try:
                with urllib.request.urlopen(req, timeout=self.timeout,
                                            context=self.ctx) as r:
                    self.requests += 1
                    data = json.loads(r.read().decode("utf-8") or "null")
                    # Slow the whole pool down before we hit the wall, not after
                    try:
                        left = int(r.headers.get("X-RateLimit-Remaining", "999"))
                        if left <= 3:
                            self.throttle.pause(
                                retry_after(r.headers, 5.0), count=False)
                    except (TypeError, ValueError):
                        pass
                    return data
            except urllib.error.HTTPError as e:
                self.requests += 1
                if e.code in (429, 502, 503, 504) and attempt < self.retries:
                    wait = retry_after(e.headers, backoff)
                    self.throttle.pause(min(wait, 90.0))
                    backoff = min(backoff * 2, 60.0)
                    continue
                body = e.read().decode("utf-8", "replace")[:300]
                raise PlaneError(f"{e.code} {path} :: {body}") from None
            except urllib.error.URLError as e:
                if attempt < self.retries:
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 30.0)
                    continue
                raise PlaneError(
                    f"cannot reach {self.base}: {e.reason}") from None
        raise PlaneError(f"gave up on {path} after {self.retries} retries")

    def _try(self, paths: list[str], params: dict | None = None) -> Any:
        last: PlaneError | None = None
        for p in paths:
            try:
                return self._get(p, params)
            except PlaneError as e:
                if " 404 " in f" {e} " or str(e).startswith("404"):
                    last = e
                    continue
                raise
        raise last or PlaneError("no endpoint matched")

    def _paged(self, paths: list[str], params: dict | None = None) -> list[dict]:
        out: list[dict] = []
        cursor = None
        while True:
            p = dict(params or {})
            p["per_page"] = 100
            if cursor:
                p["cursor"] = cursor
            data = self._try(paths, p)
            if isinstance(data, list):
                return data
            out.extend(data.get("results") or [])
            if not data.get("next_page_results"):
                break
            cursor = data.get("next_cursor")
            if not cursor:
                break
        return out

    # ---- resources ----------------------------------------------------
    def projects(self) -> list[dict]:
        return self._paged([f"/workspaces/{self.ws}/projects/"])

    def resolve_project(self, ref: str) -> dict:
        projs = self.projects()
        for p in projs:
            if ref in (p.get("id"), p.get("identifier"), p.get("name")):
                return p
        for p in projs:
            if ref.lower() in (p.get("name") or "").lower():
                return p
        names = ", ".join(f"{p.get('identifier')}={p.get('name')}"
                          for p in projs)
        raise PlaneError(f"project '{ref}' not found. available: {names}")

    def _pp(self, pid: str, tail: str) -> list[str]:
        base = f"/workspaces/{self.ws}/projects/{pid}"
        return [f"{base}/work-items/{tail}", f"{base}/issues/{tail}"]

    def items(self, pid: str) -> list[dict]:
        return self._paged(self._pp(pid, ""))

    def states(self, pid: str) -> dict[str, dict]:
        rows = self._paged([f"/workspaces/{self.ws}/projects/{pid}/states/"])
        return {s["id"]: s for s in rows}

    def members(self, pid: str) -> dict[str, str]:
        try:
            rows = self._paged(
                [f"/workspaces/{self.ws}/projects/{pid}/members/",
                 f"/workspaces/{self.ws}/members/"])
        except PlaneError:
            return {}
        out = {}
        for m in rows:
            u = m.get("member") if isinstance(m.get("member"), dict) else m
            uid = u.get("id") or m.get("member_id") or m.get("id")
            name = (u.get("display_name") or u.get("first_name")
                    or u.get("email") or "")
            if uid:
                out[uid] = name
        return out

    def _grouping(self, pid: str, kind: str) -> dict[str, str]:
        """kind: 'modules' or 'cycles' -> {item_id: group name}"""
        try:
            groups = self._paged(
                [f"/workspaces/{self.ws}/projects/{pid}/{kind}/"])
        except PlaneError:
            return {}
        tail = "module-issues" if kind == "modules" else "cycle-issues"
        alt = "work-items" if kind == "modules" else "work-items"
        out: dict[str, str] = {}

        def one(g: dict) -> None:
            gid, name = g.get("id"), g.get("name") or ""
            base = f"/workspaces/{self.ws}/projects/{pid}/{kind}/{gid}"
            try:
                rows = self._paged([f"{base}/{tail}/", f"{base}/{alt}/"])
            except PlaneError:
                return
            for r in rows:
                iid = (r.get("issue") if isinstance(r.get("issue"), str)
                       else (r.get("issue", {}) or {}).get("id")
                       if isinstance(r.get("issue"), dict) else None)
                iid = iid or r.get("issue_id") or r.get("id")
                if iid:
                    out[iid] = name

        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            list(ex.map(one, groups))
        return out

    def relation_paths(self, pid: str, item_id: str) -> list[str]:
        base = f"/workspaces/{self.ws}/projects/{pid}"
        return [
            f"{base}/work-items/{item_id}/dependencies/",
            f"{base}/issues/{item_id}/relations/",
            f"{base}/work-items/{item_id}/relations/",
        ]

    def relations(self, pid: str, item_id: str) -> dict:
        """Returns the relation blob. Records WHY it came back empty -- an
        endpoint that 404s across the board and an item with genuinely no
        relations are different problems and must not look alike."""
        try:
            blob = self._try(self.relation_paths(pid, item_id)) or {}
            self.rel_ok += 1
            return blob
        except PlaneError as exc:
            self.rel_fail += 1
            if not self.rel_error:
                self.rel_error = str(exc)
            return {}

    def activities(self, pid: str, item_id: str) -> Any:
        base = f"/workspaces/{self.ws}/projects/{pid}"
        try:
            return self._try([
                f"{base}/work-items/{item_id}/activities/",
                f"{base}/issues/{item_id}/activities/",
                f"{base}/work-items/{item_id}/history/",
            ]) or {}
        except PlaneError as exc:
            self.hist_fail += 1
            if not self.hist_error:
                self.hist_error = str(exc)
            return {}

    def probe_relations(self, pid: str, item_id: str) -> list[dict]:
        """Hit every candidate endpoint for one work item and report exactly
        what each one said. This is the tool for 'why are there no arrows'."""
        out = []
        for path in self.relation_paths(pid, item_id):
            row: dict = {"path": path}
            try:
                blob = self._get(path)
                row["status"] = 200
                row["shape"] = (f"dict keys: {sorted(blob)}"
                                if isinstance(blob, dict)
                                else f"list of {len(blob)}"
                                if isinstance(blob, list) else type(blob).__name__)
                row["body"] = blob
                row["nonempty"] = bool(
                    any(blob.get(k) for k in
                        ("blocking", "blocked_by", "relates_to", "duplicate"))
                    if isinstance(blob, dict) else blob)
            except PlaneError as exc:
                row["status"] = str(exc).split(" ", 1)[0]
                row["error"] = str(exc)[:200]
            out.append(row)
        return out


# Every key a Plane build has used to point at the other work item in a
# relation row. Order matters only as a tiebreak -- the caller resolves each
# row against the items it actually knows about, so a relation record whose
# own "id" is the relation uuid can't hijack the edge any more.
_REL_ID_KEYS = ("related_issue", "related_work_item", "issue", "work_item",
                "issue_id", "work_item_id", "entity_identifier", "id")

REL_TYPES = ("blocking", "blocked_by", "relates_to", "duplicate",
             "start_after", "start_before", "finish_after", "finish_before")


def entered_states(blob: Any) -> set[str]:
    """Every state this work item has ever been moved INTO, from its activity
    feed. Used to catch items that reached Done without passing Review."""
    seen: set[str] = set()
    rows = blob.get("results") if isinstance(blob, dict) else blob
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if (row.get("field") or "").lower() != "state":
            continue
        for k in ("new_value", "new_identifier_value", "new_state"):
            v = row.get(k)
            if isinstance(v, str) and v.strip():
                seen.add(v.strip())
    return seen


def _rel_rows(blob: Any, want: str) -> list[Any]:
    """Pull the rows for one relation type out of whichever shape came back."""
    if isinstance(blob, dict):
        rows = blob.get(want)
        if rows is None and isinstance(blob.get("results"), list):
            return _rel_rows(blob["results"], want)
        return list(rows or [])
    if isinstance(blob, list):
        return [r for r in blob if isinstance(r, dict)
                and (r.get("relation_type") or r.get("relation")) == want]
    return []


def _rel_candidates(blob: Any, want: str) -> list[list[str]]:
    """One candidate-id list per relation row, best guess first.

    Returning candidates instead of a single id is the whole point: some Plane
    builds hand back the work item itself, others hand back a relation record
    whose `id` belongs to the relation, not the item. The caller keeps whichever
    candidate resolves to a work item it has actually loaded."""
    out: list[list[str]] = []
    for row in _rel_rows(blob, want):
        if isinstance(row, str):
            out.append([row])
        elif isinstance(row, dict):
            cands: list[str] = []
            for k in _REL_ID_KEYS:
                v = row.get(k)
                if isinstance(v, str) and v:
                    cands.append(v)
                elif isinstance(v, dict) and isinstance(v.get("id"), str):
                    cands.append(v["id"])
            if cands:
                out.append(cands)
    return out


def _rel_ids(blob: Any, want: str) -> list[str]:
    """Back-compat: first candidate per row."""
    return [c[0] for c in _rel_candidates(blob, want) if c]


def load_api(base_url: str, api_key: str, workspace: str, project: str,
             insecure: bool = False, workers: int = 4,
             verbose: bool = False, with_relations: bool = True,
             rate: float = 55.0, cache_path: str | None = None,
             progress=None) -> Graph:
    c = PlaneClient(base_url, api_key, workspace, insecure=insecure,
                    workers=workers, verbose=verbose, rate=rate)
    cache = RelationCache(cache_path)
    proj = c.resolve_project(project)
    pid, ident = proj["id"], proj.get("identifier") or ""
    if verbose:
        print(f"  project {proj.get('name')} ({ident})")

    states = c.states(pid)
    people = c.members(pid)
    mods = c._grouping(pid, "modules")
    cycs = c._grouping(pid, "cycles")
    raw = c.items(pid)
    if verbose:
        print(f"  {len(raw)} work items, {len(states)} states, "
              f"{len(mods)} module links")

    g = Graph()
    for r in raw:
        st = r.get("state")
        st_row = states.get(st) if isinstance(st, str) else (st or {})
        st_row = st_row or {}
        title_raw = r.get("name") or ""
        mod_from_title, clean = split_module_prefix(title_raw)
        seq = r.get("sequence_id")
        g.add_item(Item(
            uid=r["id"],
            key=f"{ident}-{seq}" if ident and seq else (str(seq) if seq else r["id"][:8]),
            title=clean or title_raw,
            state=normalize_state(st_row.get("name"), st_row.get("group")),
            module=mods.get(r["id"]) or mod_from_title or "Unassigned",
            cycle=cycs.get(r["id"], ""),
            assignees=[people.get(a, "") for a in (r.get("assignees") or [])
                       if people.get(a)],
            labels=[str(x) for x in (r.get("labels") or []) if isinstance(x, str)],
            priority=r.get("priority") or "",
            estimate=str(r.get("estimate_point") or ""),
            parent=r.get("parent") if isinstance(r.get("parent"), str) else None,
            target_date=r.get("target_date") or "",
            start_date=r.get("start_date") or "",
            url=f"{c.base}/{workspace}/projects/{pid}/issues/{r['id']}",
            description=(r.get("description_stripped") or "")[:4000],
        ))

    for it in g.items.values():
        if it.parent and it.parent in g.items:
            g.add_edge(Edge(it.parent, it.uid, "parent"))

    # kept for the history cache: an item's activity feed only changes when
    # the item does
    g.stamps = {r["id"]: str(r.get("updated_at") or "") for r in raw}

    if with_relations:
        ids = list(g.items)
        stamps = {r["id"]: str(r.get("updated_at") or "") for r in raw}
        cache.prune(set(ids))
        done = [0]
        total = len(ids)

        def fetch(i: str) -> tuple[str, dict]:
            hit = cache.get(i, stamps.get(i, ""))
            if hit is None:
                blob = c.relations(pid, i)
                if blob or c.rel_fail == 0:
                    cache.put(i, stamps.get(i, ""), blob)
            else:
                blob = hit
                c.rel_ok += 1
            done[0] += 1
            if progress and (done[0] % 10 == 0 or done[0] == total):
                progress(done[0], total, cache.hits)
            return i, blob

        def resolve(cands: list[str]) -> str | None:
            for cid in cands:
                if cid in g.items and cid != "":
                    return cid
            return None

        unresolved = 0
        with ThreadPoolExecutor(max_workers=c.workers) as ex:
            for uid, blob in ex.map(fetch, ids):
                # "this item blocks X" -> edge uid -> X.
                # "finish_before" is the temporal spelling of the same thing on
                # newer builds; treat both as ordering constraints.
                for want, fwd in (("blocking", True), ("finish_before", True),
                                  ("blocked_by", False), ("finish_after", False),
                                  ("start_after", False)):
                    for cands in _rel_candidates(blob, want):
                        other = resolve(cands)
                        if other is None:
                            unresolved += 1
                            continue
                        g.add_edge(Edge(uid, other, "blocks") if fwd
                                   else Edge(other, uid, "blocks"))
                for cands in _rel_candidates(blob, "relates_to"):
                    other = resolve(cands)
                    if other:
                        g.add_edge(Edge(uid, other, "relates"))
        c.rel_unresolved = unresolved
        cache.save()
        c.cache_hits, c.cache_misses = cache.hits, cache.misses
        if verbose or c.rel_fail:
            n = len(g.blocking_edges())
            print(f"  {n} blocking relations "
                  f"({cache.hits} cached, {cache.misses} fetched)")
            if c.throttle.throttled:
                print(f"  paced: {c.throttle.throttled} rate-limit pauses, "
                      f"{c.throttle.waited:.0f}s of waiting summed across "
                      f"{workers} workers -- lower --rate to avoid them")
            if c.rel_fail:
                print(f"  !! {c.rel_fail}/{c.rel_fail + c.rel_ok} relation "
                      f"lookups FAILED -- every endpoint errored")
                print(f"     first error: {c.rel_error}")
                print(f"     run --probe-relations to see each endpoint's reply")
            elif n == 0 and not c.rel_unresolved:
                print(f"     all {c.rel_ok} lookups succeeded and came back "
                      f"empty -- the relations are genuinely not set in Plane")
            if c.rel_unresolved:
                print(f"  !! {c.rel_unresolved} relation rows pointed at ids "
                      f"not in this project -- run --scan-relations")

    return g.finalize()


def env_client_args() -> dict:
    return {
        "base_url": os.getenv("PLANE_BASE_URL", ""),
        "api_key": os.getenv("PLANE_API_KEY", ""),
        "workspace": os.getenv("PLANE_WORKSPACE", ""),
        "project": os.getenv("PLANE_PROJECT", ""),
    }


def load_history(base_url: str, api_key: str, workspace: str, project: str,
                 uids: list[str], insecure: bool = False, workers: int = 4,
                 rate: float = 55.0, cache_path: str | None = None,
                 stamps: dict[str, str] | None = None,
                 progress=None) -> tuple[dict[str, set[str]], str]:
    """States each of `uids` has ever been moved into.

    Only worth calling for items that are already Done -- the question is
    whether they passed Review on the way, and the activity feed is one request
    each against a 60/minute budget. Returns ({}, reason) when the instance has
    no activities endpoint, so the caller can say 'unavailable' rather than
    quietly reporting that nothing skipped review."""
    c = PlaneClient(base_url, api_key, workspace, insecure=insecure,
                    workers=workers, rate=rate)
    proj = c.resolve_project(project)
    pid = proj["id"]
    cache = RelationCache(cache_path)
    out: dict[str, set[str]] = {}
    stamps = stamps or {}
    done = [0]

    def one(uid: str) -> tuple[str, set[str]]:
        key = "hist:" + uid
        hit = cache.get(key, stamps.get(uid, ""))
        if hit is None:
            blob = c.activities(pid, uid)
            states = sorted(entered_states(blob))
            if blob:
                cache.put(key, stamps.get(uid, ""), {"states": states})
        else:
            states = hit.get("states", [])
        done[0] += 1
        if progress:
            progress(done[0], len(uids), cache.hits)
        return uid, set(states)

    with ThreadPoolExecutor(max_workers=c.workers) as ex:
        for uid, states in ex.map(one, uids):
            out[uid] = states
    cache.save()
    if c.hist_fail >= max(1, len(uids)):
        return {}, (c.hist_error or "no activities endpoint on this instance")
    return out, ""
