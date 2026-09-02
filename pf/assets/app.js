/* plane2flow -- interaction layer.
   The SVG geometry is rendered server-side, so the chart is complete and
   printable with JS disabled. Everything here is enhancement: pan/zoom,
   filtering, and dependency-chain highlighting. */
(function () {
  "use strict";

  var DATA = JSON.parse(document.getElementById("pf-data").textContent);
  var items = DATA.items, edges = DATA.edges;
  var svg = document.getElementById("graph");
  var vp = document.getElementById("viewport");
  var stage = document.getElementById("stage");
  var panel = document.getElementById("panel");

  /* ---- adjacency ------------------------------------------------------ */
  var conflicts = {};
  (DATA.conflicts || []).forEach(function (c) {
    (conflicts[c.blocked] = conflicts[c.blocked] || []).push(c.blocker);
  });
  var up = {}, dn = {}, kids = {}, upSet = {}, dnSet = {};
  Object.keys(items).forEach(function (k) {
    up[k] = []; dn[k] = []; kids[k] = []; upSet[k] = []; dnSet[k] = [];
  });
  edges.forEach(function (e) {
    if (e.kind === "parent" && kids[e.src]) kids[e.src].push(e.dst);
  });
  edges.forEach(function (e) {
    if (e.kind !== "blocks") return;
    if (dn[e.src]) dn[e.src].push(e.dst);
    if (up[e.dst]) up[e.dst].push(e.src);
    // links a human set in Plane, as opposed to ones derived from nesting --
    // the panel lists these separately so a parent does not show its own
    // children twice
    if (!e.rollup) {
      if (dnSet[e.src]) dnSet[e.src].push(e.dst);
      if (upSet[e.dst]) upSet[e.dst].push(e.src);
    }
  });

  function reach(start, table) {
    var seen = {}, stack = [start];
    while (stack.length) {
      var u = stack.pop();
      (table[u] || []).forEach(function (v) {
        if (!seen[v]) { seen[v] = 1; stack.push(v); }
      });
    }
    return seen;
  }

  /* ---- pan & zoom ----------------------------------------------------- */
  var view = { x: 0, y: 0, k: 1 };
  function apply() {
    vp.setAttribute("transform",
      "translate(" + view.x + "," + view.y + ") scale(" + view.k + ")");
  }
  function fit() {
    var r = stage.getBoundingClientRect();
    var k = Math.min(r.width / (DATA.width + 40),
                     r.height / (DATA.height + 40), 1.4);
    view.k = k > 0 ? k : 1;
    view.x = (r.width - DATA.width * view.k) / 2;
    view.y = 24;
    apply();
  }
  stage.addEventListener("wheel", function (ev) {
    ev.preventDefault();
    var r = stage.getBoundingClientRect();
    var mx = ev.clientX - r.left, my = ev.clientY - r.top;
    var f = Math.exp(-ev.deltaY * 0.0016);
    var nk = Math.min(3, Math.max(0.08, view.k * f));
    view.x = mx - (mx - view.x) * (nk / view.k);
    view.y = my - (my - view.y) * (nk / view.k);
    view.k = nk;
    apply();
  }, { passive: false });

  var drag = null;
  stage.addEventListener("pointerdown", function (ev) {
    if (ev.target.closest(".node") || ev.target.closest("#panel")) return;
    drag = { x: ev.clientX - view.x, y: ev.clientY - view.y };
    stage.classList.add("grabbing");
    stage.setPointerCapture(ev.pointerId);
  });
  stage.addEventListener("pointermove", function (ev) {
    if (!drag) return;
    view.x = ev.clientX - drag.x;
    view.y = ev.clientY - drag.y;
    apply();
  });
  ["pointerup", "pointercancel"].forEach(function (t) {
    stage.addEventListener(t, function () {
      drag = null; stage.classList.remove("grabbing");
    });
  });

  /* ---- selection / chain highlight ------------------------------------ */
  var selected = null;
  function clearSel() {
    selected = null;
    svg.classList.remove("has-sel");
    svg.querySelectorAll(".on").forEach(function (n) {
      n.classList.remove("on");
    });
    svg.querySelectorAll(".node.sel").forEach(function (n) {
      n.classList.remove("sel");
    });
    panel.hidden = true;
  }
  function select(uid) {
    if (selected === uid) { clearSel(); return; }
    clearSel();
    selected = uid;
    var u = reach(uid, up), d = reach(uid, dn);
    var on = {}; on[uid] = 1;
    Object.keys(u).forEach(function (k) { on[k] = 1; });
    Object.keys(d).forEach(function (k) { on[k] = 1; });
    Object.keys(on).forEach(function (k) {
      var el = document.getElementById("n-" + k);
      if (el) el.classList.add("on");
    });
    svg.querySelectorAll(".edge").forEach(function (e) {
      var s = e.getAttribute("data-src"), t = e.getAttribute("data-dst");
      if (on[s] && on[t]) e.classList.add("on");
    });
    var me = document.getElementById("n-" + uid);
    if (me) me.classList.add("sel");
    svg.classList.add("has-sel");
    showPanel(uid, Object.keys(u).length, Object.keys(d).length);
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function chipFor(state) {
    var dark = matchMedia("(prefers-color-scheme: dark)").matches &&
      document.documentElement.getAttribute("data-theme") !== "light";
    if (document.documentElement.getAttribute("data-theme") === "dark") dark = true;
    var bg = (dark ? DATA.theme.rampDark : DATA.theme.rampLight)[state] || "#888";
    var ink = (dark ? DATA.theme.inkDark : DATA.theme.inkLight)[state] || "#fff";
    return '<span class="pill" style="background:' + bg + ';color:' + ink +
      '">' + esc(DATA.theme.glyph[state] || "") + " " + esc(state) + "</span>";
  }
  function linkList(ids) {
    if (!ids.length) return "<dd>&mdash;</dd>";
    return "<dd><ul>" + ids.map(function (i) {
      var it = items[i];
      if (!it) return "";
      return '<li><a href="#" data-goto="' + i + '"><code>' + esc(it.key) +
        "</code></a> " + esc(it.title) +
        (it.state === "Done" ? "" : " <em>(" + esc(it.state) + ")</em>") +
        "</li>";
    }).join("") + "</ul></dd>";
  }
  function showPanel(uid, nUp, nDn) {
    var it = items[uid];
    var blocked = up[uid].some(function (b) {
      return items[b] && items[b].state !== "Done" && items[b].state !== "Cancelled";
    });
    panel.innerHTML =
      '<button class="close" aria-label="Close">&times;</button>' +
      '<div class="k">' + esc(it.key) + " &middot; " + esc(it.module) + "</div>" +
      "<h3>" + esc(it.title) + "</h3>" +
      '<div style="margin-top:8px">' + chipFor(it.state) +
      (blocked ? ' <span class="pill" style="background:' +
        DATA.theme.status.critical + ';color:#fff">&#9940; blocked</span>' :
        (it.state === "Done" || it.state === "Cancelled" ? "" :
          ' <span class="pill" style="background:' + DATA.theme.status.good +
          ';color:#fff">&#9679; ready</span>')) + "</div>" +
      "<dl>" +
      (it.assignees.length ? "<dt>Owner</dt><dd>" +
        esc(it.assignees.join(", ")) + "</dd>" : "") +
      (it.cycle ? "<dt>Cycle</dt><dd>" + esc(it.cycle) + "</dd>" : "") +
      (it.estimate ? "<dt>Estimate</dt><dd>" + esc(it.estimate) + "</dd>" : "") +
      (it.target_date ? "<dt>Target</dt><dd>" + esc(it.target_date) + "</dd>" : "") +
      (it.labels.length ? "<dt>Labels</dt><dd>" + esc(it.labels.join(", ")) +
        "</dd>" : "") +
      "<dt>Blocked by</dt>" + linkList(upSet[uid]) +
      "<dt>Blocks</dt>" + linkList(dnSet[uid]) +
      (it.parent && items[it.parent]
        ? "<dt>Parent</dt>" + linkList([it.parent]) : "") +
      (kids[uid].length
        ? "<dt>Sub-items</dt><dd>" + it.kidsDone + " of " + kids[uid].length +
          " done" + (it.kidsDone < kids[uid].length
            ? " &mdash; this cannot finish until the rest are" : "") + "</dd>" +
          "<dt></dt>" + linkList(kids[uid])
        : "") +
      (conflicts[uid] ? "<dt>&#9888; Dates</dt><dd>Due " +
        esc(it.target_date) + ", but blocked by " +
        conflicts[uid].map(function (b) {
          return esc(items[b].key) + " (due " + esc(items[b].target_date) + ")";
        }).join(", ") + ". Both cannot hold.</dd>" : "") +
      "<dt>Chain</dt><dd>" + nUp + " upstream &middot; " + nDn +
      " downstream</dd>" +
      (it.url ? '<dt>Plane</dt><dd><a href="' + esc(it.url) +
        '" target="_blank" rel="noopener">open &#8599;</a></dd>' : "") +
      "</dl>";
    panel.hidden = false;
  }
  panel.addEventListener("click", function (ev) {
    if (ev.target.classList.contains("close")) { clearSel(); return; }
    var g = ev.target.closest("[data-goto]");
    if (g) { ev.preventDefault(); select(g.getAttribute("data-goto")); }
  });
  svg.addEventListener("click", function (ev) {
    var n = ev.target.closest(".node");
    if (n) select(n.getAttribute("data-uid"));
    else clearSel();
  });
  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape") { clearSel(); clearCrit(); }
    if (ev.key === "f" && !/input|select/i.test(ev.target.tagName)) fit();
  });

  /* ---- filtering ------------------------------------------------------ */
  var f = {
    module: "", state: "", assignee: "", cycle: "", q: "",
    blockedOnly: false, hideDone: false
  };
  function visible(uid) {
    var it = items[uid];
    if (f.module && it.module !== f.module) return false;
    if (f.state && it.state !== f.state) return false;
    if (f.cycle && it.cycle !== f.cycle) return false;
    if (f.assignee && it.assignees.indexOf(f.assignee) < 0) return false;
    if (f.hideDone && (it.state === "Done" || it.state === "Cancelled")) return false;
    if (f.blockedOnly) {
      var b = up[uid].some(function (x) {
        return items[x] && items[x].state !== "Done" && items[x].state !== "Cancelled";
      });
      if (!b || it.state === "Done" || it.state === "Cancelled") return false;
    }
    if (f.q) {
      var hay = (it.key + " " + it.title + " " + it.module + " " +
        it.labels.join(" ") + " " + it.assignees.join(" ")).toLowerCase();
      if (hay.indexOf(f.q) < 0) return false;
    }
    return true;
  }
  function refilter() {
    var shown = 0, vis = {};
    Object.keys(items).forEach(function (uid) {
      var ok = visible(uid);
      vis[uid] = ok;
      if (ok) shown++;
      var el = document.getElementById("n-" + uid);
      if (el) el.classList.toggle("hide", !ok);
    });
    svg.querySelectorAll(".edge").forEach(function (e) {
      var s = e.getAttribute("data-src"), t = e.getAttribute("data-dst");
      e.classList.toggle("hide", !(vis[s] && vis[t]));
    });
    document.getElementById("shown").textContent = shown;
    if (selected && !vis[selected]) clearSel();
  }

  function bind(id, key, transform) {
    var el = document.getElementById(id);
    if (!el) return;
    el.addEventListener(el.tagName === "INPUT" && el.type === "search"
      ? "input" : "change", function () {
      f[key] = transform ? transform(el) : el.value;
      refilter();
    });
  }
  bind("f-module", "module");
  bind("f-state", "state");
  bind("f-assignee", "assignee");
  bind("f-cycle", "cycle");
  bind("f-q", "q", function (el) { return el.value.trim().toLowerCase(); });

  function toggle(id, fn) {
    var b = document.getElementById(id);
    if (!b) return;
    b.addEventListener("click", function () {
      var on = b.getAttribute("aria-pressed") !== "true";
      b.setAttribute("aria-pressed", on ? "true" : "false");
      fn(on);
    });
  }
  toggle("t-blocked", function (on) { f.blockedOnly = on; refilter(); });
  toggle("t-hidedone", function (on) { f.hideDone = on; refilter(); });
  toggle("t-dimdone", function (on) { svg.classList.toggle("dim-done", on); });
  toggle("t-relates", function (on) {
    svg.querySelectorAll(".edge.rel").forEach(function (e) {
      e.style.display = on ? "" : "none";
    });
  });
  toggle("t-parent", function (on) {
    svg.querySelectorAll(".edge.par").forEach(function (e) {
      e.style.display = on ? "" : "none";
    });
  });

  /* ---- critical path ------------------------------------------------- */
  var critOn = false;
  function longestChains() {
    var memo = {};
    function longest(k, seen) {
      if (memo[k]) return memo[k];
      if (seen[k]) return [];
      seen[k] = 1;
      var best = [];
      (up[k] || []).forEach(function (p) {
        var c = longest(p, seen);
        if (c.length > best.length) best = c;
      });
      delete seen[k];
      memo[k] = best.concat([k]);
      return memo[k];
    }
    var best = null;
    Object.keys(items).forEach(function (k) {
      if (dn[k].length) return;              // only chain ends
      var path = longest(k, {});
      var open = path.filter(function (x) {
        return items[x].state !== "Done" && items[x].state !== "Cancelled";
      }).length;
      if (!best || open > best.open || (open === best.open &&
          path.length > best.path.length)) {
        best = { path: path, open: open };
      }
    });
    return best;
  }
  function clearCrit() {
    critOn = false;
    svg.classList.remove("has-crit");
    svg.querySelectorAll(".crit").forEach(function (n) {
      n.classList.remove("crit");
    });
    document.getElementById("b-critical").setAttribute("aria-pressed", "false");
  }
  document.getElementById("b-critical").addEventListener("click", function () {
    if (critOn) { clearCrit(); return; }
    clearSel();
    var best = longestChains();
    if (!best || !best.path.length) return;
    var on = {};
    best.path.forEach(function (k) {
      on[k] = 1;
      var el = document.getElementById("n-" + k);
      if (el) el.classList.add("crit");
    });
    for (var i = 0; i < best.path.length - 1; i++) {
      var a = best.path[i], b = best.path[i + 1];
      svg.querySelectorAll('.edge[data-src="' + a + '"][data-dst="' + b + '"]')
        .forEach(function (e) { e.classList.add("crit"); });
    }
    svg.classList.add("has-crit");
    critOn = true;
    this.setAttribute("aria-pressed", "true");
    panel.innerHTML =
      '<button class="close" aria-label="Close">&times;</button>' +
      "<h3>Critical path</h3>" +
      '<div class="k">' + best.path.length + " items, " + best.open +
      " still open</div>" +
      "<ul>" + best.path.map(function (k) {
        var it = items[k];
        var done = it.state === "Done" || it.state === "Cancelled";
        return '<li><a href="#" data-goto="' + k + '"><code>' +
          esc(it.key) + "</code></a> " + esc(it.title) +
          (done ? " <em>(done)</em>" : " <strong>(" + esc(it.state) +
           ")</strong>") + "</li>";
      }).join("") + "</ul>" +
      '<p style="font-size:12px;color:var(--muted);margin-top:10px">' +
      "Nothing downstream of this chain can finish sooner than it does.</p>";
    panel.hidden = false;
  });

  /* ---- tabs ------------------------------------------------------------ */
  var tabs = document.querySelectorAll('.tabs [role="tab"]');
  function showPane(name) {
    tabs.forEach(function (t) {
      t.setAttribute("aria-selected",
        t.getAttribute("data-pane") === name ? "true" : "false");
    });
    ["board", "standup", "hygiene"].forEach(function (n) {
      var el = document.getElementById("pane-" + n);
      if (el) el.hidden = n !== name;
    });
    document.body.setAttribute("data-tab", name);
    try { localStorage.setItem("pf-tab", name); } catch (e) {}
    if (name === "board") fit();
  }
  tabs.forEach(function (t) {
    t.addEventListener("click", function () {
      showPane(t.getAttribute("data-pane"));
    });
  });

  document.getElementById("b-fit").addEventListener("click", fit);
  document.getElementById("b-print").addEventListener("click", function () {
    window.print();
  });
  document.getElementById("b-theme").addEventListener("click", function () {
    var cur = document.documentElement.getAttribute("data-theme");
    var next = cur === "dark" ? "light" : cur === "light" ? "dark"
      : (matchMedia("(prefers-color-scheme: dark)").matches ? "light" : "dark");
    document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem("pf-theme", next); } catch (e) {}
    if (selected) select(selected), select(selected);
  });
  try {
    var saved = localStorage.getItem("pf-theme");
    if (saved) document.documentElement.setAttribute("data-theme", saved);
  } catch (e) {}

  /* relates-to and parent edges start hidden -- they are not ordering
     constraints and they clutter the read until asked for */
  svg.querySelectorAll(".edge.rel, .edge.par").forEach(function (e) {
    e.style.display = "none";
  });

  window.addEventListener("resize", function () {
    if (view.k === 1 && view.x === 0) fit();
  });
  fit();
  refilter();
  try {
    var t = localStorage.getItem("pf-tab");
    if (t && t !== "board") showPane(t);
  } catch (e) {}
})();
