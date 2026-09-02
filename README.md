# plane2flow

Turns an EV27 Plane project into a dependency flowchart you can click through
on screen and hand out on paper.

Boxes are work items, coloured by state. Arrows read **left to right: a box must
finish before the boxes it points to can start.** Click any box and the whole
chain it sits on — everything upstream and everything downstream — lights up.

Two pieces:

| | what it is | needs |
|---|---|---|
| `plane2flow.py` | CLI. Data in, one self-contained `.html` out. | Python 3.10+, **no pip install** |
| `server.py` + Docker | Read-only API that polls Plane and serves the same board live. | FastAPI, a container host |

---

## 1. The script

```bash
# boxes from a CSV export; arrows scraped from "Blocked by EV27-12" in descriptions
./plane2flow.py --csv export.csv -o board.html

# boxes from the CSV, real relations pulled from Plane
./plane2flow.py --csv export.csv --relations-from-api -o board.html

# everything live
./plane2flow.py --api -o board.html
```

Open `board.html` in anything. Nothing is fetched at view time — the file is
complete, so it works offline, survives being emailed, and can be committed as a
snapshot of where the car stood on a given day.

### Where the arrows come from

**Plane's CSV export does not contain relations.** It never has. The export gives
you the boxes; the arrows have to come from somewhere else, and there are three
options in falling order of trustworthiness:

1. `--relations-from-api` / `--api` — real `blocked_by` / `blocking` relations
   set in Plane. This is the honest version.
2. Description scraping (on by default with `--csv`) — picks up
   `blocked by EV27-12`, `depends on EV27-3`, `blocks EV27-40` written in a work
   item's description. Disable with `--no-description-edges`.
3. Parent / sub-issue links, if your export carries them. Drawn as containment,
   not as blocking.

### Column detection

Plane has renamed export columns more than once and self-hosted CE lags the
cloud, so nothing is matched literally — headers are normalised and matched
against a candidate list. To see what matched what:

```bash
./plane2flow.py --csv export.csv --explain-csv
```

If a field says `(none)` and you know the column exists, add its header to
`FIELDS` in `pf/sources_csv.py`.

### When the chart comes out empty

```bash
./plane2flow.py --probe-relations           # or --probe-relations EV27-42
```

Hits every candidate relation endpoint for one work item and prints exactly what
each replies. There are only two answers and they need different fixes:

- **Every endpoint errored** — the reader can't see relations on this instance.
  An endpoint problem, not an empty project.
- **An endpoint returned 200 with empty lists** — that *item* has no relations.
  Probing one item proves nothing about the project; use `--scan-relations`.

```bash
./plane2flow.py --scan-relations
```

Walks every work item, counts relations by type, reports how many items have a
parent, and dumps the first non-empty payload verbatim. This is the one to run
when the endpoint is healthy but the chart is still bare — it distinguishes
"none are set" from "they're set in a shape the reader mis-parsed", and the
verbatim dump is what makes the second one fixable.

### Sub-items as ordering

By default **a parent cannot finish until every one of its children is done.**
Each parent/child pair becomes a derived `child -> parent` dependency, marked as
a rollup so it stays distinguishable from a link somebody set by hand: thin
arrow, hollow head, and listed under *Sub-items* in the detail panel rather than
mixed into *Blocked by*.

This matters where the structure is built from nesting. Newer Plane builds have
temporal relation types (`start_after`, `finish_before`) that say this directly;
where a build doesn't have them, the nesting *is* the sequencing, so the edge is
derived rather than demanded of the data.

```bash
--parent-mode block      # default: children must finish first
--parent-mode contain    # draw the nesting, don't sequence it
--parent-mode ignore     # drop it entirely
```

(`PF_PARENT_MODE` in the container.) A parent node shows `3/7 sub` and its panel
says how many children are outstanding. The `Nesting` toggle draws the raw
parent/child links as dotted lines when you want to see the tree itself.

### Schedule conflicts

Any dependency where **the blocker is due after the thing waiting on it** gets
flagged: a ⚠ on the node, a count in the toolbar, and a table on its own printed
page. Both dates cannot hold; one of them is wrong. Rollups are checked too — a
parent due before a child it contains is the same contradiction.

### Three tabs

**Board** — the flowchart.

**Standup** — open work grouped by owner: what each person has in flight, what
they're blocked on and by what, and what's free to pick up. Unassigned work gets
its own card, which is usually the biggest one and is meant to be.

**Hygiene** — everything in the project that is probably a mistake, grouped by
rule, each row linking straight to the work item in Plane so it can be fixed.
The tab badge shows the serious count.

Rules, in the order they're shown:

| | |
|---|---|
| Closed with open sub-items | parent Done, children not |
| Done, but its blockers are not | finished before something it depends on |
| Blocker due after the work it blocks | both dates can't hold |
| Dependency loop | nothing in the loop can start |
| Done without passing Review | reached Done with no Review step in its history |
| Being worked on while blocked | In Progress or Review with an unfinished blocker |
| Not assigned to a module | the SOP puts everything under one of the six leads |
| No assignee / no estimate / no target / no cycle | |
| XXL estimate below top level | SOP reserves XXL for top level |
| No dependencies and no nesting | isolated; the links were probably never set |

A rule that fires on healthy data is worse than no rule, so each one is written
to fire only on a real contradiction or a real SOP violation.

**The review check needs history.** State alone can't tell you an item skipped
Review, so it reads the activity feed of each *Done* item (only Done items —
that's the question being asked) and looks for a state change into Review. One
extra request per Done item, cached like relations. If the instance has no
activities endpoint the check is dropped and the page *says so* rather than
quietly reporting nothing skipped. An item with no history at all isn't flagged
— absence of evidence isn't evidence. `--no-history` skips the whole thing.

### Printing follows the tab

Print from **Board** and you get the full report. Print from **Standup** and you
get one sheet of open work by owner, soonest target first — the thing to bring
to the meeting. Print from **Hygiene** and you get the fix-list.

### Critical path

The `Critical path` button highlights the longest unfinished dependency chain
and lists it in the panel. Nothing downstream of that chain can finish sooner
than it does, so it is the schedule whether or not anyone planned it.

A normal run now says which of the two it hit rather than reporting a silent
zero.

### Credentials

Flags, environment variables, or a `.env` beside the script:

```
PLANE_BASE_URL=https://plane.lokislair.com
PLANE_API_KEY=...          # profile > Settings > API tokens
PLANE_WORKSPACE=2026-formula-electric
PLANE_PROJECT=EV27         # id, identifier, or name
```

Add `--insecure` if the instance is still on a self-signed cert.

### Printing

`Print` in the toolbar, or Ctrl-P. The screen view is hidden and a paginated
report is printed instead — **Letter portrait**, one module per page:

1. Cover — counts, legend, everything blocked right now, everything ready to start
2. One page per module — the module's own flowchart plus a table of its items
3. Cross-module dependencies — where one lead is waiting on another

Set margins to 0.5in and turn on background graphics.

---

## 2. The container

```bash
cp .env.example .env      # fill in PLANE_API_KEY
docker compose up -d --build
curl -s localhost:8412/api/health | jq
```

Polls Plane every `PF_REFRESH_SECONDS` (default 300), renders the board, caches
it to a volume.

The served board carries a **sync indicator** and a **Refresh** button. The same
HTML works either way and works out which it is: opened over http it asks
`/api/health`, shows how long ago Plane was last read, and offers Refresh;
opened straight off disk it just says how old the snapshot is and hides the
button. If the server re-polls while you have the page open, a *"Plane has newer
data — Reload"* banner appears rather than the view changing under you.

Refresh only *re-reads* Plane, so it is open by default and throttled rather
than locked: `PF_REFRESH_MIN_SECONDS` (default 20) is the minimum gap between
manual refreshes, which is what stops the button from eating the 60/min API
budget. Set `PF_REFRESH_TOKEN` to require a token as well. If Plane is down or the token expires it keeps serving the last
good board and says so on `/api/health` — the board never goes blank.

### Endpoints — all read-only

| | |
|---|---|
| `GET /` | the interactive board |
| `GET /board.html` | same, as a download |
| `GET /api/graph` | items, edges, stats |
| `GET /api/items` | filter by `module`, `state`, `assignee`, `cycle`, `blocked`, `ready`, `q` |
| `GET /api/items/{key}` | one item plus its full upstream/downstream chains |
| `GET /api/blocked` | waiting on something unfinished |
| `GET /api/ready` | could be started today |
| `GET /api/critical-path` | longest dependency chains, longest open first |
| `GET /api/health` | freshness — point Uptime Kuma here |
| `GET /docs` | OpenAPI |

Nothing in the container writes to Plane. `POST /api/refresh` exists but returns
404 unless `PF_REFRESH_TOKEN` is set, and even then it only re-reads.

The port binds to `127.0.0.1:8412` so the reverse proxy is the only way in. Point
Nginx Proxy Manager at it and it lands under `*.lokislair.com` like everything
else.

### Offline fallback

Uncomment the `./fallback` mount and set `PF_FALLBACK_CSV=/fallback/export.csv`.
If the API can't be reached the container falls back to that CSV instead of
serving nothing.

---

## Reading the chart

- **Colour = state, using the EV27 workspace's own state colours**, so a box on
  the board reads the same as the item does in Plane: Backlog and Ready grey,
  In Progress amber, Blocked crimson, Review pale blue, Done green, Dropped
  slate.
- **Every box also carries its state as text and a glyph**, so nothing depends on
  colour alone. That is now load-bearing rather than belt-and-braces — see below.
- **Red left bar + ⛔ = blocked**: something it waits on isn't Done. This is
  **computed, and separate from the Blocked state** somebody set in Plane. An
  item can sit in Blocked with nothing actually blocking it (there is a hygiene
  rule for exactly that), or sit in In Progress while genuinely blocked. Where
  the chip and the flag disagree, the disagreement is the finding.
- **Green ● = ready now**: no unfinished upstream work.
- **Dashed red arrow = a dependency loop.** Two items waiting on each other.
  Nothing is hidden to make the layout tidy; a loop is a planning bug and it
  stays on the chart until someone fixes it.
- Done items are dimmed by default — toggle with `Dim done`, remove with
  `Hide done`.

Keyboard: `f` fits to window, `Esc` clears a selection. Scroll to zoom, drag to pan.

### Two consequences of matching Plane's colours

Both deliberate, both worth knowing:

- **Backlog and Ready are the same hex in Plane** (`#60646C`). Two states that
  paint identically cannot be told apart on a wall chart, so Ready is lightened
  within the same grey. Set `READY_MATCHES_PLANE = True` in `pf/theme.py` to use
  the exact workspace hex instead.
- **Black-and-white printing no longer separates every state by shade.** On the
  0–255 grey scale, Blocked (91) sits beside Backlog (100), and Dropped (164)
  beside In Progress (167). The written state name and the glyph carry the
  meaning on paper; colour alone does not. The previous single-hue ramp printed
  as seven distinct greys; these are the workspace's hues, and hues do not.

Dark mode lightens only the greys — `#60646C` sits at 2.93:1 on the dark
surface, under the 3:1 floor, so it steps up until it clears. The other five are
used exactly as given. Ink on each chip is picked by measured contrast, not by
eye.

---

## Layout

Horizontal position is a **global dependency layer** computed across the whole
car, not per module — so a cross-module arrow always points rightward, and how
far right something sits is how deep in the build it is. Modules are the
horizontal bands.

Cycles are broken for layout by marking back edges, never by deleting them.

## Files

```
plane2flow.py        CLI
server.py            FastAPI read-only server
pf/model.py          items, edges, state normalisation, [MODULE] prefixes
pf/sources_csv.py    tolerant CSV reader + description scraping
pf/sources_api.py    Plane REST client (work-items/ -> issues/,
                     dependencies/ -> relations/ fallbacks for CE)
pf/layout.py         cycle breaking, layering, crossing reduction
pf/render.py         SVG + HTML emitter, screen and print
pf/theme.py          the state ramp
pf/assets/           app.css, app.js (inlined into the output)
sample/              a fake EV27 export to try it on
```

## Notes on self-hosted CE

Community Edition 1.3.1 does not expose `/work-items/{id}/dependencies/` — it
404s, and relations live at the older `/issues/{id}/relations/` with `issues`
instead of `work_item_ids`. The client tries the new path first and falls back,
so it works on both (makeplane/plane-mcp-server#185).

## Rate limiting

**Plane allows 60 requests per minute per API key**, and relations cost one
request per work item. A 175-item project is therefore ~3 minutes on a cold
run, and anything that ignores this gets a 429 (`error_code 5900`) partway
through and silently returns a fraction of the graph.

The client paces itself with a shared token bucket, so `--workers` changes
latency but never the request rate. On a 429 it honours `Retry-After`, backs
the whole pool off, and retries — nothing is dropped. `--rate` (default 55/min)
is the dial if your instance is stricter.

The cold run is paid once. Every relation blob is cached against the work
item's `updated_at`, so later runs re-fetch only what changed:

```
19 blocking relations (0 cached, 60 fetched)     # first run
19 blocking relations (60 cached, 0 fetched)     # second run, ~1s
```

Cache lives at `.plane2flow-cache.json` beside the script (`/data/` in the
container). Delete it any time — the only cost is waiting again. `--no-cache`
skips it entirely.

## Relation payload shapes

Different Plane builds return different things from `/relations/`. Seen so far:

```json
"blocked_by": [{"project_id": "...", "issue_id": "..."}]   // CE, id under issue_id
"blocked_by": [{"id": "...", "name": "...", ...}]          // the work item itself
"blocked_by": [{"id": "<relation-uuid>", "related_issue": "..."}]  // relation record
```

The reader collects every candidate id in a row and keeps whichever one resolves
to a work item it loaded, so all three work and a new shape is unlikely to break
it. If one does, `--scan-relations` dumps the payload verbatim and the fix is one
entry in `_REL_ID_KEYS`.

Newer builds also expose temporal relations (`start_after`, `finish_before`);
they are read as ordering constraints where present and ignored where the build
doesn't have them.
