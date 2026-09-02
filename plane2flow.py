#!/usr/bin/env python3
"""plane2flow -- Plane work items -> interactive, printable dependency flowchart.

Nodes come from a CSV export or straight from the API; blocking relations come
from the API (Plane's CSV export has never carried them). Output is one
self-contained HTML file: pan/zoom and filter on screen, Letter-portrait
multi-page when you hit Print.

  # CSV only -- nodes and any "blocked by EV27-12" written in descriptions
  ./plane2flow.py --csv export.csv -o board.html

  # nodes from CSV, real relations pulled from Plane
  ./plane2flow.py --csv export.csv --relations-from-api -o board.html

  # everything live from Plane
  ./plane2flow.py --api -o board.html

Credentials come from the environment (or a .env beside this file):
  PLANE_BASE_URL, PLANE_API_KEY, PLANE_WORKSPACE, PLANE_PROJECT

Stdlib only. No pip install.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pf import __version__
from pf.model import Edge, Graph
from pf.render import render
from pf.sources_csv import edges_from_descriptions, load_csv


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def cache_path_for(a) -> str | None:
    if a.no_cache:
        return None
    return a.cache or str(Path(__file__).resolve().parent /
                          ".plane2flow-cache.json")


def history_progress(done: int, total: int, cached: int) -> None:
    bar = int(28 * done / max(1, total))
    sys.stderr.write(
        f"\r  history   {done}/{total} [{'#' * bar}{'.' * (28 - bar)}] "
        f"{cached} cached")
    sys.stderr.flush()
    if done >= total:
        sys.stderr.write("\n")


def progress_line(done: int, total: int, cached: int) -> None:
    bar = int(28 * done / max(1, total))
    sys.stderr.write(
        f"\r  relations {done}/{total} [{'#' * bar}{'.' * (28 - bar)}] "
        f"{cached} cached")
    sys.stderr.flush()
    if done >= total:
        sys.stderr.write("\n")


def merge_relations(graph: Graph, api_graph: Graph, key_index: dict) -> int:
    """Graft API relations onto CSV-sourced nodes, matching on the human key."""
    by_key = {v.upper(): k for k, v in
              {u: i.label for u, i in graph.items.items()}.items()}
    by_key.update({k.upper(): v for k, v in key_index.items()})
    remap = {}
    for uid, it in api_graph.items.items():
        target = uid if uid in graph.items else by_key.get(it.label.upper())
        if target:
            remap[uid] = target
    added = 0
    for ed in api_graph.edges:
        s, d = remap.get(ed.src), remap.get(ed.dst)
        if s and d:
            before = len(graph.edges)
            graph.add_edge(Edge(s, d, ed.kind))
            added += len(graph.edges) - before
    graph.finalize()
    return added


def probe(a, parser) -> int:
    """Answer the only question that matters when the chart comes out empty:
    is Plane refusing to tell us about relations, or are there none?"""
    import json as _json
    from pf.sources_api import PlaneClient, PlaneError

    missing = [n for n, v in (("--base-url", a.base_url),
                              ("--api-key", a.api_key),
                              ("--workspace", a.workspace),
                              ("--project", a.project)) if not v]
    if missing:
        parser.error("--probe-relations needs " + ", ".join(missing))

    c = PlaneClient(a.base_url, a.api_key, a.workspace,
                    insecure=a.insecure, workers=a.workers)
    try:
        proj = c.resolve_project(a.project)
        items = c.items(proj["id"])
    except PlaneError as exc:
        print(f"plane2flow: {exc}", file=sys.stderr)
        return 2
    if not items:
        print("no work items in that project", file=sys.stderr)
        return 1

    ident = proj.get("identifier") or ""
    want = (a.probe_relations or "").strip().upper()
    target = None
    if want:
        for r in items:
            key = f"{ident}-{r.get('sequence_id')}".upper()
            if key == want or str(r.get("sequence_id")) == want.lstrip(ident + "-"):
                target = r
                break
        if target is None:
            parser.error(f"no work item {want} in {proj.get('name')}")
    else:
        target = items[0]

    key = f"{ident}-{target.get('sequence_id')}"
    print(f"project : {proj.get('name')} ({ident})")
    print(f"item    : {key}  {target.get('name', '')[:60]}")
    print(f"id      : {target['id']}\n")

    any_ok = False
    for row in c.probe_relations(proj["id"], target["id"]):
        print(f"  {row['status']:<4} {row['path']}")
        if row.get("error"):
            print(f"       {row['error']}")
        else:
            any_ok = True
            print(f"       {row['shape']}")
            body = _json.dumps(row["body"])
            print(f"       {body[:400]}{'...' if len(body) > 400 else ''}")
            print(f"       has relations: {row['nonempty']}")
        print()

    print("-" * 68)
    if not any_ok:
        print("Every relation endpoint failed. The reader cannot see relations")
        print("on this instance -- this is an endpoint problem, not an empty")
        print("project. Send me the status codes above.")
    else:
        print("An endpoint answered. If 'has relations' is False here and on a")
        print("couple of other items you know are linked, then the relations")
        print("are genuinely not set in Plane and the chart is telling the")
        print("truth. Sub-issues and modules are NOT relations.")
    return 0


def scan(a, parser) -> int:
    """Walk the whole project and report what relation data actually exists.
    One command that settles 'are they set?' and 'what shape are they?'."""
    import json as _json
    from collections import Counter
    from concurrent.futures import ThreadPoolExecutor

    from pf.sources_api import REL_TYPES, PlaneClient, PlaneError, _rel_rows

    missing = [n for n, v in (("--base-url", a.base_url),
                              ("--api-key", a.api_key),
                              ("--workspace", a.workspace),
                              ("--project", a.project)) if not v]
    if missing:
        parser.error("--scan-relations needs " + ", ".join(missing))

    c = PlaneClient(a.base_url, a.api_key, a.workspace,
                    insecure=a.insecure, workers=a.workers, rate=a.rate)
    try:
        proj = c.resolve_project(a.project)
        items = c.items(proj["id"])
    except PlaneError as exc:
        print(f"plane2flow: {exc}", file=sys.stderr)
        return 2

    mins = len(items) / max(1.0, a.rate)
    if mins > 0.5:
        print(f"scanning {len(items)} items at {a.rate:.0f} req/min "
              f"(~{mins:.1f} min -- Plane's limit is 60/min)\n",
              file=sys.stderr)
    ident = proj.get("identifier") or ""
    ids = {r["id"]: f"{ident}-{r.get('sequence_id')}" for r in items}
    print(f"project : {proj.get('name')} ({ident})")
    print(f"items   : {len(items)}")

    # does the list payload even carry parent / sub-issue information?
    have_parent = sum(1 for r in items if r.get("parent"))
    parent_keys = sorted({k for r in items for k in r
                          if "parent" in k or "sub_issue" in k})
    print(f"parent  : {have_parent} items have a non-null parent "
          f"(fields present: {parent_keys or 'none'})")
    print()

    counts = Counter()
    per_item = Counter()
    samples: list[tuple[str, dict]] = []
    unknown_targets = 0

    def fetch(r):
        return r["id"], c.relations(proj["id"], r["id"])

    n_done = [0]
    with ThreadPoolExecutor(max_workers=c.workers) as ex:
        for uid, blob in ex.map(fetch, items):
            n_done[0] += 1
            if sys.stderr.isatty() and n_done[0] % 10 == 0:
                progress_line(n_done[0], len(items), 0)
            hit = 0
            for want in REL_TYPES:
                rows = _rel_rows(blob, want)
                if rows:
                    counts[want] += len(rows)
                    hit += len(rows)
                    for row in rows:
                        if isinstance(row, dict):
                            got = [v for k, v in row.items()
                                   if isinstance(v, str) and v in ids]
                            if not got:
                                unknown_targets += 1
            if hit:
                per_item[uid] = hit
                if len(samples) < 3:
                    samples.append((ids.get(uid, uid), blob))

    if sys.stderr.isatty():
        progress_line(len(items), len(items), 0)
    print(f"endpoint used     : {'ok' if c.rel_ok else 'NONE ANSWERED'}"
          f"  ({c.rel_ok} ok / {c.rel_fail} failed)")
    if c.throttle.throttled:
        print(f"rate limiting     : {c.throttle.throttled} pauses, "
              f"{c.throttle.waited:.0f}s waiting -- lower --rate if this "
              f"keeps happening")
    if c.rel_error:
        print(f"first error       : {c.rel_error}")
    print(f"items w/ relations: {len(per_item)} of {len(items)}")
    print(f"relation rows     : {sum(counts.values())}")
    for want in REL_TYPES:
        if counts[want]:
            print(f"    {want:<14} {counts[want]}")
    if unknown_targets:
        print(f"!! {unknown_targets} rows had NO field matching a work-item id "
              f"in this project -- that is the shape problem; the dump below "
              f"shows what the rows actually contain")
    print()

    if not samples:
        print("Nothing to dump: every item came back with all relation lists "
              "empty. Whatever is linking your items, it is not stored as a "
              "work-item relation.")
        return 0

    print("=" * 70)
    print("VERBATIM PAYLOADS (send me these)")
    print("=" * 70)
    for key, blob in samples:
        print(f"\n--- {key} ---")
        print(_json.dumps(blob, indent=2)[:2500])
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="plane2flow", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    src = p.add_argument_group("source")
    src.add_argument("--csv", metavar="FILE",
                     help="Plane CSV export (supplies the boxes)")
    src.add_argument("--api", action="store_true",
                     help="pull everything from the Plane API")
    src.add_argument("--relations-from-api", action="store_true",
                     help="boxes from --csv, arrows from the API")
    src.add_argument("--parent-mode", choices=("block", "contain", "ignore"),
                     default="block",
                     help="what parent/child means. block: a parent cannot "
                          "finish until every child is done (default). "
                          "contain: draw nesting but don't sequence it. "
                          "ignore: drop it")
    src.add_argument("--no-history", action="store_true",
                     help="skip the 'Done without passing Review' check, which "
                          "reads the activity feed of each Done item")
    src.add_argument("--no-description-edges", action="store_true",
                     help="don't scrape 'blocked by EV27-12' out of descriptions")
    src.add_argument("--probe-relations", metavar="KEY", nargs="?", const="",
                     help="diagnose an empty chart: hit every relation endpoint "
                          "for one work item (default: the first) and print "
                          "exactly what each one replies")
    src.add_argument("--scan-relations", action="store_true",
                     help="walk EVERY work item, count relations by type, and "
                          "dump the first non-empty payload verbatim")

    api = p.add_argument_group("plane api")
    api.add_argument("--base-url", default=os.getenv("PLANE_BASE_URL", ""))
    api.add_argument("--api-key", default=os.getenv("PLANE_API_KEY", ""))
    api.add_argument("--workspace", default=os.getenv("PLANE_WORKSPACE", ""))
    api.add_argument("--project", default=os.getenv("PLANE_PROJECT", ""))
    api.add_argument("--insecure", action="store_true",
                     help="skip TLS verification (self-signed homelab certs)")
    api.add_argument("--workers", type=int, default=4,
                     help="parallel requests (the rate limit governs, not this)")
    api.add_argument("--rate", type=float,
                     default=float(os.getenv("PLANE_RATE", "55")),
                     help="requests per minute; Plane allows 60 (default: 55)")
    api.add_argument("--cache", metavar="FILE", default="",
                     help="relation cache (default: .plane2flow-cache.json "
                          "beside this script)")
    api.add_argument("--no-cache", action="store_true",
                     help="ignore and do not write the relation cache")

    out = p.add_argument_group("output")
    out.add_argument("-o", "--out", default="board.html")
    out.add_argument("--title", default="EV27 dependency flow")
    out.add_argument("--subtitle", default="")
    out.add_argument("--explain-csv", action="store_true",
                     help="print the detected column mapping and exit")
    out.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--version", action="version", version=__version__)

    a = p.parse_args(argv)
    load_dotenv(Path(__file__).resolve().parent / ".env")
    for attr, env in (("base_url", "PLANE_BASE_URL"), ("api_key", "PLANE_API_KEY"),
                      ("workspace", "PLANE_WORKSPACE"), ("project", "PLANE_PROJECT")):
        if not getattr(a, attr):
            setattr(a, attr, os.getenv(env, ""))

    if a.probe_relations is not None:
        return probe(a, p)
    if a.scan_relations:
        return scan(a, p)

    if not a.csv and not a.api:
        p.error("give me --csv FILE or --api (or both)")

    note = ""
    graph: Graph
    key_index: dict = {}

    if a.csv:
        graph, report = load_csv(a.csv, explain=a.explain_csv or a.verbose)
        key_index = report["keyIndex"]
        note = f"CSV: {Path(a.csv).name} ({report['rows']} rows)"
        if a.explain_csv:
            return 0
        if not a.no_description_edges:
            n = edges_from_descriptions(graph, key_index)
            if n and a.verbose:
                print(f"  {n} edges scraped from descriptions")
    else:
        graph = Graph()

    if a.api or a.relations_from_api:
        from pf.sources_api import PlaneError, load_api
        missing = [n for n, v in (("--base-url", a.base_url),
                                  ("--api-key", a.api_key),
                                  ("--workspace", a.workspace),
                                  ("--project", a.project)) if not v]
        if missing:
            p.error("API mode needs " + ", ".join(missing) +
                    " (flag or environment variable)")
        try:
            live = load_api(a.base_url, a.api_key, a.workspace, a.project,
                            insecure=a.insecure, workers=a.workers,
                            verbose=a.verbose, rate=a.rate,
                            cache_path=cache_path_for(a),
                            progress=progress_line if sys.stderr.isatty()
                            else None)
        except PlaneError as exc:
            print(f"plane2flow: {exc}", file=sys.stderr)
            if a.csv:
                print("  keeping the CSV-only view", file=sys.stderr)
                live = None
            else:
                return 2
        if live is not None:
            if a.api and not a.csv:
                graph = live
                note = f"live: {a.base_url}"
            else:
                n = merge_relations(graph, live, key_index)
                note += f" + {n} live relations"

    if not graph.items:
        print("plane2flow: no work items found", file=sys.stderr)
        return 1

    graph.finalize()
    if a.parent_mode == "ignore":
        graph.edges = [e for e in graph.edges if e.kind != "parent"]
    elif a.parent_mode == "block":
        n = graph.add_rollup_edges()
        if n:
            note += f" · {n} sub-item rollups"
    graph.finalize()

    # ---- the review-skip check needs work-item history, which is one extra
    # request per Done item. Only Done items are asked about, and the answers
    # are cached like relations are.
    rules, hnote = None, ""
    if not a.no_history and a.base_url and a.api_key and a.workspace and a.project:
        from pf.model import normalize_state
        from pf.sources_api import PlaneError, load_history
        done_uids = [u for u, it in graph.items.items() if it.state == "Done"]
        if done_uids:
            try:
                hist, why = load_history(
                    a.base_url, a.api_key, a.workspace, a.project, done_uids,
                    insecure=a.insecure, workers=a.workers, rate=a.rate,
                    cache_path=cache_path_for(a), stamps=graph.stamps,
                    progress=history_progress if sys.stderr.isatty() else None)
            except PlaneError as exc:
                hist, why = {}, str(exc)
            if why:
                hnote = ("The &ldquo;Done without passing Review&rdquo; check is "
                         "off: this Plane instance did not return work-item "
                         "history. Everything else on this page is unaffected.")
            else:
                skipped = set()
                for u in done_uids:
                    seen = hist.get(u) or set()
                    if not seen:
                        continue      # no history at all: no evidence either way
                    if not any(normalize_state(x) == "Review" for x in seen):
                        skipped.add(u)
                from pf import hygiene as H
                rules = H.run(graph, review_skipped=skipped)
    elif not a.no_history:
        hnote = ("The &ldquo;Done without passing Review&rdquo; check needs the "
                 "Plane API; this board was built from a CSV, which carries no "
                 "history.")

    html = render(graph, a.title, a.subtitle, note, rules=rules,
                  hygiene_note=hnote)
    Path(a.out).write_text(html, encoding="utf-8")
    s = graph.stats()
    print(f"{a.out}  ·  {s['items']} items, {s['edges']} dependencies "
          f"({s['edges'] - s['rollup']} set in Plane + {s['rollup']} sub-item "
          f"rollups), {s['blocked']} blocked, {s['ready']} ready, "
          f"{s['modules']} modules")
    if s["conflicts"]:
        print(f"  !! {s['conflicts']} schedule conflicts: a blocker is due "
              f"after the thing waiting on it -- see the last printed page")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
