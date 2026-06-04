/* ge-chart-overrides.js — Georgia Economics only.
 *
 * The shared charts.js (loaded from the economicsguru repo via jsDelivr)
 * registers a global Chart.js plugin, "creamBg", that paints a parchment
 * (#fbf5dc) fill behind every chart. GA diverged to the Modern Editorial
 * look (charts on white), so we disable that plugin here — WITHOUT editing
 * charts.js, which would also restyle economicsguru.com.
 *
 * Load order (all in <head>): app.js (sync) → chart.umd (defer) →
 * charts.js (defer) → THIS (defer). Deferred scripts run in document order
 * before DOMContentLoaded, so creamBg is registered by charts.js and then
 * removed here, before each page's inline script creates its charts.
 */
(function () {
  function disableCream() {
    if (typeof Chart === "undefined") { return setTimeout(disableCream, 0); }
    // 1) belt: make the plugin's resolved options false so the core skips it
    try {
      Chart.defaults.plugins = Chart.defaults.plugins || {};
      Chart.defaults.plugins.creamBg = false;
    } catch (e) {}
    // 2) suspenders: unregister it outright if present
    try {
      var p = Chart.registry.getPlugin("creamBg");
      if (p) { Chart.unregister(p); }
    } catch (e) {}
  }
  disableCream();
})();
