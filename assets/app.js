/* georgiaeconomics.com — shared front-end runtime (Phase 5, WS1–WS3)
 *
 * One global namespace, `window.GE`, holding the brand palette, number/date
 * formatters, small data/chart helpers (WS1), plus the grouped-nav, breadcrumb,
 * client-side search and metro-navigation map behaviours (WS2/WS3) that the
 * shared chrome relies on. Pages reference GE.* instead of re-declaring their
 * own globals — see reference_georgiaeconomics_page_gotchas.
 *
 * Loaded as a plain <script src="/assets/app.js"> (NOT deferred) BEFORE a page's
 * inline script, so GE.* is defined when that inline script runs during parse.
 * Browser-feature work that needs the DOM is deferred to DOMContentLoaded below.
 */
(function () {
  "use strict";

  // Brand palette — mirrors the CSS custom properties in /styles.css.
  var BRAND = {
    navy:      "#1a3a5c",
    navySoft:  "#2e5984",
    mustard:   "#d4a017",
    teal:      "#3a8d8d",
    tealLight: "#5fb8b8",
    coral:     "#d4624a",
    green:     "#6b8e3d",
    peach:     "#e8a87c",
    peachDeep: "#c46b3a",
    peachPale: "#faead9",
    grid:      "#b6ad8d",
    ink:       "#1a1a1a",
    inkSoft:   "#6b7280",
    border:    "#d9d3b8",
    chartBg:   "#fbf5dc",
  };

  // ---- formatters (the union of what pages used; all null-safe) ----
  function _n(v) { return (v === null || v === undefined || (typeof v === "number" && !isFinite(v))) ? null : Number(v); }
  var fmt = {
    pct:       function (v, d) { v = _n(v); return v === null ? "—" : v.toFixed(d == null ? 1 : d) + "%"; },
    signedPct: function (v)    { v = _n(v); return v === null ? "—" : (v >= 0 ? "+" : "") + v.toFixed(1) + "%"; },
    signedPp:  function (v)    { v = _n(v); return v === null ? "—" : (v >= 0 ? "+" : "") + v.toFixed(1) + " pp"; },
    signedK:   function (v)    { v = _n(v); return v === null ? "—" : (v >= 0 ? "+" : "") + v.toFixed(1); },
    signed:    function (v)    { v = _n(v); return v === null ? "—" : (v >= 0 ? "+" : "") + Math.round(v).toLocaleString(); },
    signed1:   function (v)    { v = _n(v); return v === null ? "—" : (v >= 0 ? "+" : "") + v.toFixed(1); },
    num:       function (v, d) { v = _n(v); return v === null ? "—" : v.toLocaleString("en-US", { maximumFractionDigits: d == null ? 1 : d }); },
    num0:      function (v)    { v = _n(v); return v === null ? "—" : Math.round(v).toLocaleString(); },
    k:         function (v)    { v = _n(v); return v === null ? "—" : v.toLocaleString("en-US", { maximumFractionDigits: 1 }); },
    usd0:      function (v)    { v = _n(v); return v === null ? "—" : "$" + Math.round(v).toLocaleString(); },
    bn:        function (v)    { v = _n(v); return v === null ? "—" : "$" + v.toLocaleString("en-US", { maximumFractionDigits: 1 }) + "bn"; },
    month:     function (s)    { if (!s) return "—"; var p = String(s).split("-").map(Number); return new Date(p[0], p[1] - 1, 1).toLocaleString("en-US", { month: "short", year: "2-digit" }); },
    monthLong: function (s)    { if (!s) return "—"; var p = String(s).split("-").map(Number); return new Date(p[0], p[1] - 1, 1).toLocaleString("en-US", { month: "long", year: "numeric" }); },
  };

  // ---- data fetch with a daily cache-bust ----
  function data(name) {
    var v = new Date().toISOString().slice(0, 10);
    return fetch("/data/" + name + ".json?v=" + v).then(function (r) {
      if (!r.ok) throw new Error("fetch " + name + ".json -> " + r.status);
      return r.json();
    });
  }

  // ---- tiny DOM conveniences pages repeated ----
  function setYear(id) {
    var el = document.getElementById(id || "yr");
    if (el) el.textContent = new Date().getFullYear();
  }
  function show(id) { var el = document.getElementById(id); if (el) el.style.display = ""; }
  function hide(id) { var el = document.getElementById(id); if (el) el.style.display = "none"; }
  function text(id, v) { var el = document.getElementById(id); if (el) el.textContent = v; }

  // Chart.js cartesian-axis defaults in brand styling (opt-in helper).
  function axes(opts) {
    opts = opts || {};
    return {
      x: { grid: { display: opts.xgrid === true }, ticks: { color: BRAND.navy, font: { size: 11 }, maxRotation: 45, autoSkip: true, maxTicksLimit: opts.xTicks || 12 } },
      y: { grid: { color: BRAND.grid, borderDash: [3, 4] }, ticks: { color: BRAND.navy, font: { size: 11 } }, beginAtZero: !!opts.zero },
    };
  }

  /* =================================================================
   * WS2 — information architecture: grouped nav, breadcrumbs, search.
   * ================================================================= */

  // Static page registry (path -> {label, axis}). Used for active-nav,
  // breadcrumbs, and as the base of the client-side search index. Metro
  // report pages (/msa/<slug>/) are handled dynamically by slug.
  var AXES = { places: "Places", topics: "Topics", industries: "Industries", site: null };
  var PAGES = [
    { path: "/counties/",                 label: "Counties",          axis: "places" },
    { path: "/msa/",                       label: "Metros",            axis: "places" },
    { path: "/population/",                label: "Population",        axis: "topics" },
    { path: "/labor/",                     label: "Labor",             axis: "topics" },
    { path: "/housing/",                   label: "Housing",           axis: "topics" },
    { path: "/gdp/",                       label: "GDP",               axis: "topics" },
    { path: "/migration/",                 label: "Migration",         axis: "topics" },
    { path: "/inflation/",                 label: "Inflation",         axis: "topics" },
    { path: "/trade/",                     label: "Trade & Logistics", axis: "topics" },
    { path: "/outlook/",                   label: "Outlook",           axis: "topics" },
    { path: "/industries/agriculture/",   label: "Agriculture",       axis: "industries" },
    { path: "/industries/automotive/",    label: "Automotive & EV",   axis: "industries" },
    { path: "/industries/data-centers/",  label: "Data Centers",      axis: "industries" },
    { path: "/industries/film/",          label: "Film",              axis: "industries" },
    { path: "/directory/",                 label: "Directory",         axis: "site" },
    { path: "/about/",                     label: "About",             axis: "site" },
  ];

  function curPath() {
    var p = location.pathname.replace(/index\.html$/, "");
    if (p.length > 1) p = p.replace(/\/$/, "") + "/";
    return p;
  }

  // Highlight the nav link (and its parent group button) matching the current
  // path, so the shared header partial needs no per-page "active" markup.
  function markActiveNav() {
    var path = curPath();
    var links = document.querySelectorAll(".site-header .site-nav a");
    var best = null, bestLen = -1;
    links.forEach(function (a) {
      var href = a.getAttribute("href") || "";
      if (href === "/") return;
      if (path.indexOf(href) === 0 && href.length > bestLen) { best = a; bestLen = href.length; }
    });
    if (best) {
      best.classList.add("active");
      var grp = best.closest(".nav-group");
      if (grp) { var btn = grp.querySelector(".nav-top"); if (btn) btn.classList.add("active"); }
    }
  }

  // Grouped-nav dropdowns: hover opens on desktop (CSS); click/keyboard opens
  // on touch + for accessibility. Outside-click and Escape close everything.
  function initNav() {
    var groups = Array.prototype.slice.call(document.querySelectorAll(".site-header .nav-group"));
    if (!groups.length) return;
    function closeAll(except) {
      groups.forEach(function (g) {
        if (g === except) return;
        g.classList.remove("open");
        var b = g.querySelector(".nav-top"); if (b) b.setAttribute("aria-expanded", "false");
      });
    }
    groups.forEach(function (g) {
      var btn = g.querySelector(".nav-top");
      if (!btn) return;
      btn.addEventListener("click", function (e) {
        e.preventDefault();
        var willOpen = !g.classList.contains("open");
        closeAll(g);
        g.classList.toggle("open", willOpen);
        btn.setAttribute("aria-expanded", willOpen ? "true" : "false");
      });
    });
    document.addEventListener("click", function (e) {
      if (!e.target.closest(".site-header .nav-group")) closeAll(null);
    });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeAll(null); });
  }

  // Breadcrumbs: injected at the top of <main> on every page except the home
  // page, derived from the path + PAGES registry (no per-page markup needed).
  function titleCaseSlug(s) {
    return s.replace(/-/g, " ").replace(/\b\w/g, function (c) { return c.toUpperCase(); });
  }
  function crumbsFor(path) {
    if (path === "/") return null;
    var trail = [{ label: "Home", href: "/" }];
    var m = path.match(/^\/msa\/([^/]+)\/$/);
    if (m && m[1] !== "") {
      trail.push({ label: "Places", href: null });
      trail.push({ label: "Metros", href: "/msa/" });
      trail.push({ label: titleCaseSlug(m[1]), href: null });
      return trail;
    }
    var page = PAGES.filter(function (p) { return p.path === path; })[0];
    if (page) {
      if (page.axis && AXES[page.axis]) {
        // industries axis also has a real landing concept; topics/places don't,
        // so the axis crumb is a non-link label.
        trail.push({ label: AXES[page.axis], href: null });
      }
      trail.push({ label: page.label, href: null });
      return trail;
    }
    return null; // unknown page → no breadcrumb (don't guess)
  }
  function initBreadcrumbs() {
    var main = document.querySelector("main");
    if (!main || document.querySelector(".breadcrumbs")) return;
    var trail = crumbsFor(curPath());
    if (!trail) return;
    var nav = document.createElement("nav");
    nav.className = "breadcrumbs";
    nav.setAttribute("aria-label", "Breadcrumb");
    nav.innerHTML = trail.map(function (c, i) {
      var sep = i ? '<span class="sep" aria-hidden="true">›</span>' : "";
      var node = c.href ? '<a href="' + c.href + '">' + c.label + "</a>"
                        : '<span aria-current="page">' + c.label + "</span>";
      return sep + node;
    }).join("");
    main.insertBefore(nav, main.firstChild);
  }

  // Client-side search over /data/search-index.json (built at deploy time).
  function initSearch() {
    var input = document.getElementById("site-search");
    var box = document.getElementById("site-search-results");
    if (!input || !box) return;
    var index = [], active = -1, ready = false;

    function load() {
      if (ready) return;
      ready = true;
      data("search-index").then(function (j) { index = (j && j.items) || j || []; })
        .catch(function () { index = PAGES.map(function (p) { return { title: p.label, url: p.path, kind: p.axis || "page" }; }); });
    }
    function score(q, it) {
      var t = (it.title || "").toLowerCase();
      if (t === q) return 100;
      if (t.indexOf(q) === 0) return 80;
      if (t.indexOf(q) >= 0) return 60;
      var k = (it.keywords || "").toLowerCase();
      if (k.indexOf(q) >= 0) return 40;
      return 0;
    }
    function render(items) {
      if (!items.length) { box.hidden = true; input.setAttribute("aria-expanded", "false"); return; }
      box.innerHTML = items.map(function (it, i) {
        return '<a class="sr-item" role="option" href="' + it.url + '" data-i="' + i + '">' +
               '<span class="sr-title">' + it.title + "</span>" +
               (it.kind ? '<span class="sr-kind">' + it.kind + "</span>" : "") + "</a>";
      }).join("");
      box.hidden = false; input.setAttribute("aria-expanded", "true"); active = -1;
    }
    function query() {
      var q = input.value.trim().toLowerCase();
      if (q.length < 2) { box.hidden = true; input.setAttribute("aria-expanded", "false"); return; }
      var hits = index.map(function (it) { return { it: it, s: score(q, it) }; })
        .filter(function (x) { return x.s > 0; })
        .sort(function (a, b) { return b.s - a.s || a.it.title.length - b.it.title.length; })
        .slice(0, 8).map(function (x) { return x.it; });
      render(hits);
    }
    input.addEventListener("focus", load);
    input.addEventListener("input", query);
    input.addEventListener("keydown", function (e) {
      var items = box.querySelectorAll(".sr-item");
      if (e.key === "ArrowDown" && items.length) { e.preventDefault(); active = Math.min(active + 1, items.length - 1); }
      else if (e.key === "ArrowUp" && items.length) { e.preventDefault(); active = Math.max(active - 1, 0); }
      else if (e.key === "Enter") { if (active >= 0 && items[active]) { location.href = items[active].getAttribute("href"); } return; }
      else if (e.key === "Escape") { box.hidden = true; input.blur(); return; }
      else return;
      items.forEach(function (el, i) { el.classList.toggle("active", i === active); });
    });
    document.addEventListener("click", function (e) {
      if (!e.target.closest(".nav-search")) { box.hidden = true; input.setAttribute("aria-expanded", "false"); }
    });
  }

  /* =================================================================
   * WS3 — map as navigation: a clickable Georgia metro choropleth where
   * clicking a county opens its metro's report page. Built on maps.js
   * (window.gaMaps) + Plotly, which the host page must load.
   * ================================================================= */

  function slugify(name) { return String(name).toLowerCase().replace(/&/g, "and").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, ""); }

  var _metroLookup = null; // promise -> { byFips: {fips:{cbsa,slug,name}}, msas: {...} }
  function metroLookup() {
    if (_metroLookup) return _metroLookup;
    _metroLookup = data("ga_msa_counties").then(function (j) {
      var msas = (j && j.msas) || {};
      var byFips = {};
      Object.keys(msas).forEach(function (cbsa) {
        var m = msas[cbsa], slug = slugify(m.short_name);
        (m.counties || []).forEach(function (f) { byFips[f] = { cbsa: cbsa, slug: slug, name: m.short_name, full: m.full_name }; });
      });
      return { byFips: byFips, msas: msas };
    });
    return _metroLookup;
  }

  // Attach a click handler to an already-rendered Plotly choropleth so that
  // clicking a county navigates to its metro report. No-ops for non-metro
  // counties (no page yet) and if Plotly/maps.js aren't present.
  function attachMetroNav(elId) {
    var el = document.getElementById(elId);
    if (!el || typeof el.on !== "function") return false;
    metroLookup().then(function (lk) {
      el.on("plotly_click", function (ev) {
        var pt = ev && ev.points && ev.points[0];
        if (!pt) return;
        var hit = lk.byFips[pt.location];
        if (hit) location.href = "/msa/" + hit.slug + "/";
      });
      el.style.cursor = "pointer";
    });
    return true;
  }

  // Render a clickable "explore the metros" choropleth (e.g. the home hero):
  // all metro counties shaded by their metro's unemployment, click -> metro page.
  function metroMap(elId, opts) {
    opts = opts || {};
    var el = document.getElementById(elId);
    if (!el || !window.gaMaps || !window.Plotly) return Promise.resolve(false);
    return Promise.all([metroLookup(), data("msa")]).then(function (res) {
      var lk = res[0], msa = res[1];
      var byCbsa = {};
      ((msa && msa.msas) || []).forEach(function (m) { byCbsa[m.cbsa] = m; });
      var points = [], vals = [];
      Object.keys(lk.byFips).forEach(function (fips) {
        var hit = lk.byFips[fips], m = byCbsa[hit.cbsa] || {};
        var ur = m.metrics && m.metrics.unemployment_rate;
        if (ur != null) vals.push(ur);
        points.push({
          fips: fips,
          value: (ur == null ? null : ur),
          label: hit.name,
          hoverText: "<b>" + (hit.full || hit.name) + "</b><br>Unemployment: " +
                     (ur == null ? "—" : ur.toFixed(1) + "%") + "<br><i>click to open report →</i>",
        });
      });
      // Fully-saturated teal→mustard→coral scale with a fixed range, so every
      // metro reads clearly and none blends into the cream background (the old
      // "inverse" scale put mid values at cream — Valdosta disappeared).
      var lo = vals.length ? Math.min.apply(null, vals) : 0;
      var hi = vals.length ? Math.max.apply(null, vals) : 1;
      if (hi - lo < 0.5) { hi = lo + 0.5; } // avoid a flat scale when metros cluster
      return window.gaMaps.drawGAChoropleth(elId, points, {
        metricLabel: opts.metricLabel || "Metro unemployment",
        unit: "%",
        colorscale: [[0, BRAND.teal], [0.5, BRAND.mustard], [1, BRAND.coral]],
        zmin: lo, zmax: hi,
      }).then(function () { attachMetroNav(elId); return true; });
    }).catch(function (e) { if (window.console) console.warn("metroMap failed:", e); return false; });
  }

  // Auto-wire: render any [data-ge-metromap] container, and attach metro-nav
  // click to the standing /msa/ choropleth once it has rendered (it draws
  // asynchronously, so poll briefly for the Plotly graph div).
  function autoWireMaps() {
    var hero = document.querySelector("[data-ge-metromap]");
    if (hero && hero.id) metroMap(hero.id, { metricLabel: hero.getAttribute("data-metric-label") });
    if (document.getElementById("msa-choropleth")) {
      var tries = 0;
      var t = setInterval(function () {
        if (attachMetroNav("msa-choropleth") || ++tries > 40) clearInterval(t);
      }, 250);
    }
  }

  window.GE = {
    BRAND: BRAND, fmt: fmt, data: data,
    setYear: setYear, show: show, hide: hide, text: text, axes: axes,
    // WS2/WS3 helpers (also run automatically on DOMContentLoaded):
    markActiveNav: markActiveNav, metroMap: metroMap, attachMetroNav: attachMetroNav,
    slugify: slugify, PAGES: PAGES,
  };

  document.addEventListener("DOMContentLoaded", function () {
    setYear();
    markActiveNav();
    initNav();
    initBreadcrumbs();
    initSearch();
    autoWireMaps();
  });
})();
