// Send JavaScript errors to the server log, so a phone that can't run the
// staff page shows up there instead of failing silently. Loaded before
// staff.js, and written in old-style JavaScript so it runs on any browser.
(function () {
  function send(message) {
    try {
      var x = new XMLHttpRequest();
      x.open("POST", "/api/client-log");
      x.setRequestHeader("Content-Type", "application/json");
      x.send(JSON.stringify({ page: location.pathname, message: String(message).slice(0, 500) }));
    } catch (e) { /* nothing more we can do */ }
  }
  window.addEventListener("error", function (e) {
    send((e.message || "error") + " at " + (e.filename || "?") + ":" + (e.lineno || "?"));
  });
  window.addEventListener("unhandledrejection", function (e) {
    var r = e.reason;
    send("unhandled rejection: " + ((r && r.stack) || r));
  });
})();
