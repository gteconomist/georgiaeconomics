/* georgiaeconomics.com — maps.js
 * Plotly choropleth wrappers for Georgia state, county, and MSA visualizations.
 * Pairs with charts.js (loaded from economicsguru via jsDelivr).
 * Requires Plotly.js loaded BEFORE this file:
 *   <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
 */

const GA_STATE_FIPS = '13';

/* ---------- Brand tokens (mirror BRAND in charts.js, plus GA accents) ---------- */
const BRAND_MAP = {
  navy: '#1a3a5c',
  mustard: '#d4a017',
  teal: '#3a8d8d',
  coral: '#d4624a',
  peach: '#e8a87c',
  peachDeep: '#c46b3a',
  peachPale: '#faead9',
  cream: '#fbf5dc',
  ink: '#1a1a1a',
  inkSoft: '#6b7280',
  border: '#d9d3b8',
};

/* ---------- Color scales ---------- */
/* Sequential — for one-sided metrics (unemployment, poverty, prices). Cream -> peach -> navy. */
const SCALE_SEQUENTIAL = [
  [0.00, BRAND_MAP.peachPale],
  [0.25, BRAND_MAP.peach],
  [0.55, BRAND_MAP.peachDeep],
  [0.80, BRAND_MAP.coral],
  [1.00, BRAND_MAP.navy],
];

/* Diverging — for change metrics (population change, MoM unemployment delta). Teal (down) <-> coral (up). */
const SCALE_DIVERGING = [
  [0.00, BRAND_MAP.teal],
  [0.25, '#a4d4d4'],
  [0.50, BRAND_MAP.cream],
  [0.75, BRAND_MAP.peach],
  [1.00, BRAND_MAP.coral],
];

/* Sequential reverse — for "higher is worse" metrics where you want navy at the high end. */
const SCALE_INVERSE = [
  [0.00, BRAND_MAP.navy],
  [0.25, BRAND_MAP.teal],
  [0.55, BRAND_MAP.cream],
  [0.80, BRAND_MAP.peach],
  [1.00, BRAND_MAP.coral],
];

/* ---------- GeoJSON URL (Plotly's hosted US counties, FIPS-keyed) ---------- */
const US_COUNTIES_GEOJSON = 'https://raw.githubusercontent.com/plotly/datasets/master/geojson-counties-fips.json';

/* ---------- Cached geojson fetch ---------- */
let _geojsonPromise = null;
function loadUSCountiesGeoJSON() {
  if (!_geojsonPromise) {
    _geojsonPromise = fetch(US_COUNTIES_GEOJSON).then(r => r.json());
  }
  return _geojsonPromise;
}

/* ---------- Georgia state outline ----------
 * A single GA boundary polygon, fetched from the same raw.githubusercontent.com
 * host as the counties geojson and cached. Used to draw a black state border
 * and to drop the neighboring-state subunit lines. Fails soft: if the fetch is
 * ever unavailable, the map simply renders without the outline. */
const GA_OUTLINE_GEOJSON = 'https://raw.githubusercontent.com/glynnbird/usstatesgeojson/master/georgia.geojson';
let _gaOutlinePromise = null;
function loadGAOutline() {
  if (!_gaOutlinePromise) {
    _gaOutlinePromise = fetch(GA_OUTLINE_GEOJSON)
      .then(r => (r.ok ? r.json() : Promise.reject(new Error('outline ' + r.status))))
      .then(feat => ({ type: 'FeatureCollection', features: [{ type: 'Feature', id: 'GA', properties: {}, geometry: feat.geometry }] }))
      .catch(() => null);   // fail soft — no outline, map still draws
  }
  return _gaOutlinePromise;
}

/* A transparent-fill choropleth whose only job is to draw a black GA border. */
function _gaOutlineTrace(outline) {
  return {
    type: 'choropleth', locationmode: 'geojson-id', geojson: outline, featureidkey: 'id',
    locations: ['GA'], z: [0], showscale: false,
    colorscale: [[0, 'rgba(0,0,0,0)'], [1, 'rgba(0,0,0,0)']],
    marker: { line: { color: '#1a1a1a', width: 1.6 } },
    hoverinfo: 'skip',
  };
}

/* ---------- Narrow-viewport (phone) detection + brand colorbar ----------
 * On a phone the choropleth is portrait and a vertical colorbar steals most of
 * the width, shrinking Georgia to a sliver. On narrow screens we lay the
 * colorbar out horizontally so the map can use the full width. */
function _isNarrow() {
  return typeof window !== 'undefined' && (window.innerWidth || 999) < 700;
}
function _brandColorbar(label, unit, narrow, pos) {
  var cb = {
    tickfont: { family: 'Source Sans Pro, Arial, sans-serif', size: 11, color: BRAND_MAP.navy },
    title: { text: (label || '') + (unit ? ' (' + unit + ')' : ''), font: { size: 12, color: BRAND_MAP.navy } },
  };
  if (narrow) {
    cb.orientation = 'h'; cb.thickness = 10; cb.len = 0.92; cb.x = 0.5; cb.xanchor = 'center';
    if (pos === 'top') { cb.y = 1.0; cb.yanchor = 'bottom'; cb.title.side = 'top'; }
    else { cb.y = -0.02; cb.yanchor = 'top'; cb.title.side = 'bottom'; }
  } else {
    cb.thickness = 12; cb.len = 0.8; cb.x = 1.02;
  }
  return cb;
}

/* ---------- Format helpers ---------- */
function fmtPct(v, digits) {
  if (v == null || isNaN(v)) return '—';
  return Number(v).toFixed(digits == null ? 1 : digits) + '%';
}
function fmtNum(v, digits) {
  if (v == null || isNaN(v)) return '—';
  return Number(v).toLocaleString('en-US', { maximumFractionDigits: digits == null ? 0 : digits });
}
function fmtMoney(v) {
  if (v == null || isNaN(v)) return '—';
  return '$' + Number(v).toLocaleString('en-US', { maximumFractionDigits: 0 });
}

/* ---------- drawGAChoropleth ----------
 * Render a static Georgia county-level choropleth.
 *
 * elId: DOM id of the container div
 * dataPoints: array of { fips: '13121', value: 4.2, label: 'Fulton' }
 * opts: {
 *   title: string,
 *   metricLabel: 'Unemployment rate',
 *   unit: '%' | '$' | '' (default ''),
 *   colorscale: 'sequential' | 'diverging' | 'inverse' (default 'sequential'),
 *   zmin, zmax: optional fixed color range,
 *   valueFormatter: fn(v) -> string (overrides unit-based default)
 * }
 */
async function drawGAChoropleth(elId, dataPoints, opts) {
  opts = opts || {};
  const geojson = await loadUSCountiesGeoJSON();
  const outline = await loadGAOutline();

  const scale = Array.isArray(opts.colorscale) ? opts.colorscale
              : opts.colorscale === 'diverging' ? SCALE_DIVERGING
              : opts.colorscale === 'inverse'   ? SCALE_INVERSE
              :                                   SCALE_SEQUENTIAL;

  const unit = opts.unit || '';
  const narrow = _isNarrow();
  const fmt  = opts.valueFormatter || (v => {
    if (unit === '%') return fmtPct(v, 1);
    if (unit === '$') return fmtMoney(v);
    return fmtNum(v);
  });

  const locations = dataPoints.map(d => d.fips);
  const z         = dataPoints.map(d => d.value);
  const labels    = dataPoints.map(d => d.label || '');
  // A dataPoint may supply its own `hoverText` (used e.g. when counties are
  // shaded by an MSA-level value, where "<name> County" would be wrong).
  const text      = dataPoints.map(d => d.hoverText
    || `<b>${d.label || d.fips} County</b><br>${opts.metricLabel || 'Value'}: ${fmt(d.value)}`);

  // Color range. For a diverging scale over signed data (e.g. net migration),
  // default to an explicit range centered on zero so 0 maps to the scale's
  // midpoint and negative/positive read symmetrically. Plotly's auto-range can
  // render nothing for a custom diverging scale spanning negatives, so set it.
  let zmin = opts.zmin, zmax = opts.zmax;
  if (opts.colorscale === 'diverging' && (zmin == null || zmax == null)) {
    const finite = z.filter(v => typeof v === 'number' && isFinite(v));
    const m = finite.length ? Math.max.apply(null, finite.map(Math.abs)) : 0;
    if (m > 0) {
      if (zmin == null) zmin = -m;
      if (zmax == null) zmax = m;
    }
  }

  const trace = {
    type: 'choropleth',
    locationmode: 'geojson-id',
    geojson: geojson,
    featureidkey: 'id',
    locations,
    z,
    text,
    hovertemplate: '%{text}<extra></extra>',
    colorscale: scale,
    zmin: zmin,
    zmax: zmax,
    marker: { line: { width: 0.4, color: '#ffffff' } },
    colorbar: _brandColorbar(opts.metricLabel, unit, narrow, 'bottom'),
  };

  const layout = {
    title: opts.title ? { text: opts.title, font: { family: 'Source Sans Pro', size: 16, color: BRAND_MAP.navy } } : undefined,
    geo: {
      scope: 'usa',
      fitbounds: 'locations',
      visible: false,
      showsubunits: false,   // hide neighboring states — Georgia only
      bgcolor: BRAND_MAP.cream,
    },
    dragmode: false,         // lock the view to Georgia (no pan)
    paper_bgcolor: BRAND_MAP.cream,
    plot_bgcolor: BRAND_MAP.cream,
    // On phones the colorbar moves below the map, so leave room at the bottom.
    margin: narrow ? { t: opts.title ? 36 : 8, l: 8, r: 8, b: 52 }
                   : { t: opts.title ? 36 : 8, l: 8, r: 8, b: 8 },
    font: { family: 'Source Sans Pro, Arial, sans-serif', color: BRAND_MAP.navy },
  };

  const traces = outline ? [trace, _gaOutlineTrace(outline)] : [trace];
  return Plotly.newPlot(elId, traces, layout, { responsive: true, displayModeBar: false, scrollZoom: false });
}

/* ---------- drawGATimeChoropleth ----------
 * Animated choropleth: drag-through-time slider over GA counties.
 *
 * elId: DOM id
 * framesByDate: array of { date: '2026-03', points: [{fips, value, label}, ...] }
 *               ordered chronologically. Each frame should have the SAME fips set.
 * opts: same as drawGAChoropleth, plus:
 *   frameDuration: ms per frame on play (default 600)
 */
async function drawGATimeChoropleth(elId, framesByDate, opts) {
  opts = opts || {};
  if (!framesByDate || !framesByDate.length) return;
  const geojson = await loadUSCountiesGeoJSON();
  const outline = await loadGAOutline();

  const scale = opts.colorscale === 'diverging' ? SCALE_DIVERGING
              : opts.colorscale === 'inverse'   ? SCALE_INVERSE
              :                                   SCALE_SEQUENTIAL;

  const unit = opts.unit || '';
  const narrow = _isNarrow();
  const fmt  = opts.valueFormatter || (v => {
    if (unit === '%') return fmtPct(v, 1);
    if (unit === '$') return fmtMoney(v);
    return fmtNum(v);
  });

  // Compute global zmin/zmax across all frames so colors stay comparable.
  let zmin = opts.zmin, zmax = opts.zmax;
  if (zmin == null || zmax == null) {
    let lo = Infinity, hi = -Infinity;
    framesByDate.forEach(f => f.points.forEach(p => {
      if (p.value < lo) lo = p.value;
      if (p.value > hi) hi = p.value;
    }));
    if (zmin == null) zmin = lo;
    if (zmax == null) zmax = hi;
  }

  function traceFor(frame) {
    return {
      type: 'choropleth',
      locationmode: 'geojson-id',
      geojson: geojson,
      featureidkey: 'id',
      locations: frame.points.map(d => d.fips),
      z:         frame.points.map(d => d.value),
      text:      frame.points.map(d => `<b>${d.label || d.fips} County</b><br>${opts.metricLabel || 'Value'}: ${fmt(d.value)}<br>${frame.date}`),
      hovertemplate: '%{text}<extra></extra>',
      colorscale: scale,
      zmin, zmax,
      marker: { line: { width: 0.4, color: '#ffffff' } },
      colorbar: _brandColorbar(opts.metricLabel, unit, narrow, 'top'),
    };
  }

  const initial = traceFor(framesByDate[framesByDate.length - 1]); // start at most recent
  const frames  = framesByDate.map(f => ({ name: f.date, data: [traceFor(f)] }));

  const sliderSteps = framesByDate.map(f => ({
    label: f.date,
    method: 'animate',
    args: [[f.date], { mode: 'immediate', frame: { duration: 0, redraw: true }, transition: { duration: 0 } }],
  }));

  const layout = {
    title: opts.title ? { text: opts.title, font: { family: 'Source Sans Pro', size: 16, color: BRAND_MAP.navy } } : undefined,
    geo: {
      scope: 'usa', fitbounds: 'locations', visible: false,
      showsubunits: false, bgcolor: BRAND_MAP.cream,
    },
    dragmode: false,
    paper_bgcolor: BRAND_MAP.cream,
    plot_bgcolor: BRAND_MAP.cream,
    // On phones the colorbar sits above the map (the slider owns the bottom),
    // so reserve a little extra headroom.
    margin: { t: narrow ? 44 : (opts.title ? 36 : 8), l: 8, r: 8, b: 80 },
    font: { family: 'Source Sans Pro, Arial, sans-serif', color: BRAND_MAP.navy },
    sliders: [{
      active: framesByDate.length - 1,
      currentvalue: { prefix: 'Month: ', font: { size: 13, color: BRAND_MAP.navy, family: 'Source Sans Pro' } },
      pad: { t: 30 },
      steps: sliderSteps,
      bgcolor: BRAND_MAP.peachPale,
      bordercolor: BRAND_MAP.peachDeep,
      borderwidth: 1,
      tickcolor: BRAND_MAP.navy,
      font: { size: 10, color: BRAND_MAP.navy },
    }],
    updatemenus: [{
      type: 'buttons',
      x: 0.02, y: -0.08, xanchor: 'left', yanchor: 'top',
      direction: 'left', pad: { t: 4, r: 6 },
      bgcolor: BRAND_MAP.cream, bordercolor: BRAND_MAP.navy,
      font: { color: BRAND_MAP.navy, family: 'Source Sans Pro', size: 12 },
      buttons: [
        { label: '▶ Play', method: 'animate', args: [null, { mode: 'immediate', fromcurrent: true, frame: { duration: opts.frameDuration || 600, redraw: true }, transition: { duration: 0 } }] },
        { label: '❚❚ Pause', method: 'animate', args: [[null], { mode: 'immediate', frame: { duration: 0, redraw: false }, transition: { duration: 0 } }] },
      ],
    }],
  };

  const initialTraces = outline ? [initial, _gaOutlineTrace(outline)] : [initial];
  await Plotly.newPlot(elId, initialTraces, layout, { responsive: true, displayModeBar: false, scrollZoom: false });
  return Plotly.addFrames(elId, frames);
}

/* ---------- Best & worst leaderboards ---------- */
/* Helper to render a top-N / bottom-N table next to a map.
 * containerId: id of the parent grid container, expected to have two .leaderboard children:
 *   <div class="leaderboard" data-role="best">  <table><tbody></tbody></table> </div>
 *   <div class="leaderboard" data-role="worst"> <table><tbody></tbody></table> </div>
 */
function renderLeaderboards(containerId, dataPoints, opts) {
  opts = opts || {};
  const n = opts.topN || 5;
  const direction = opts.lowerIsBetter ? 1 : -1; // -1: high = best (e.g. income); 1: low = best (e.g. unemp)
  const sorted = dataPoints.slice().sort((a, b) => direction * (a.value - b.value));

  const best  = sorted.slice(0, n);
  const worst = sorted.slice(-n).reverse();

  const unit = opts.unit || '';
  const fmt = opts.valueFormatter || (v => {
    if (unit === '%') return fmtPct(v, 1);
    if (unit === '$') return fmtMoney(v);
    return fmtNum(v);
  });

  const container = document.getElementById(containerId);
  if (!container) return;
  const fill = (role, rows) => {
    const tbody = container.querySelector(`[data-role="${role}"] tbody`);
    if (!tbody) return;
    tbody.innerHTML = rows.map(r => `<tr><td>${r.label || r.fips}</td><td>${fmt(r.value)}</td></tr>`).join('');
  };
  fill('best',  best);
  fill('worst', worst);
}

/* Export to window for use in pages */
window.gaMaps = {
  drawGAChoropleth,
  drawGATimeChoropleth,
  renderLeaderboards,
  loadUSCountiesGeoJSON,
  BRAND_MAP,
  SCALE_SEQUENTIAL,
  SCALE_DIVERGING,
  SCALE_INVERSE,
  GA_STATE_FIPS,
};
