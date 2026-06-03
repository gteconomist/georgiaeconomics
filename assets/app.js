/* georgiaeconomics.com — shared front-end runtime (Phase 5, WS1)
 *
 * One global namespace, `window.GE`, holding the brand palette, number/date
 * formatters, and small data/chart helpers that EVERY page used to re-declare
 * inline. Pages now reference GE.* instead of declaring their own `const BRAND`
 * / `fmt*` — which is what caused the global-redeclaration collisions that broke
 * charts.js / maps.js (see reference_georgiaeconomics_page_gotchas).
 *
 * Loaded as a plain <script defer src="/assets/app.js"> BEFORE a page's inline
 * script. Do not add page-specific logic here.
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

  // Highlight the nav link matching the current path (so the shared header
  // partial needs no per-page "active" markup).
  function markActiveNav() {
    var path = location.pathname.replace(/index\.html$/, "");
    if (path.length > 1) path = path.replace(/\/$/, "") + "/";
    var links = document.querySelectorAll(".site-header nav a");
    var best = null, bestLen = -1;
    links.forEach(function (a) {
      var href = a.getAttribute("href") || "";
      if (href === "/" ) { if (path === "/" && bestLen < 0) { best = a; bestLen = 0; } return; }
      if (path.indexOf(href) === 0 && href.length > bestLen) { best = a; bestLen = href.length; }
    });
    if (best) best.classList.add("active");
  }

  window.GE = { BRAND: BRAND, fmt: fmt, data: data, setYear: setYear, show: show, hide: hide, text: text, axes: axes };

  document.addEventListener("DOMContentLoaded", function () { setYear(); markActiveNav(); });
})();
