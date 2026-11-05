/* Client-side inactivity timeout, synchronized across tabs.
 *
 * Mirrors the server's sliding timeout (app.before_request). Activity in any
 * tab resets the timer in every tab via a shared localStorage key. When the
 * idle limit is reached, or another tab logs out / times out, every tab is
 * sent to the login page with the session-expired notice.
 *
 * The authoritative check is still server-side; this only gives the user a
 * prompt redirect instead of leaving a stale page open.
 */
(function () {
    "use strict";
    var root = document.body;
    var minutes = parseInt(root.getAttribute("data-inactivity-minutes") || "15", 10);
    if (!minutes || minutes < 1) { minutes = 15; }
    var IDLE_MS = minutes * 60 * 1000;
    var ACTIVITY_KEY = "docman-activity";
    var LOGOUT_KEY = "docman-logout";
    var WRITE_THROTTLE_MS = 5000;   // don't hammer localStorage on every mousemove
    var CHECK_EVERY_MS = 15000;

    function now() { return Date.now(); }
    function ls(fn, dflt) { try { return fn(); } catch (e) { return dflt; } }

    var lastActivity = now();
    var lastWrite = 0;

    function recordActivity() {
        var t = now();
        lastActivity = t;
        if (t - lastWrite >= WRITE_THROTTLE_MS) {
            lastWrite = t;
            ls(function () { localStorage.setItem(ACTIVITY_KEY, String(t)); });
        }
    }

    // Seed from any existing shared timestamp so a fresh tab inherits the real
    // idle age instead of resetting it.
    var seeded = ls(function () { return localStorage.getItem(ACTIVITY_KEY); }, null);
    if (seeded) {
        var s = parseInt(seeded, 10);
        if (!isNaN(s) && s <= now()) { lastActivity = s; }
    }
    ls(function () { localStorage.removeItem(LOGOUT_KEY); });

    ["mousemove", "mousedown", "keydown", "scroll", "touchstart", "click"].forEach(function (ev) {
        window.addEventListener(ev, recordActivity, { passive: true });
    });

    window.addEventListener("storage", function (e) {
        if (e.key === ACTIVITY_KEY && e.newValue) {
            var v = parseInt(e.newValue, 10);
            if (!isNaN(v) && v > lastActivity) { lastActivity = v; }
        } else if (e.key === LOGOUT_KEY && e.newValue) {
            // Another tab logged out or timed out.
            window.location.href = "/login?expired=1";
        }
    });

    var fired = false;
    function expire() {
        if (fired) { return; }
        fired = true;
        ls(function () { localStorage.setItem(LOGOUT_KEY, String(now())); });
        window.location.href = "/session-timeout";
    }

    setInterval(function () {
        if (now() - lastActivity >= IDLE_MS) { expire(); }
    }, CHECK_EVERY_MS);

    // Broadcast manual logout so other tabs follow immediately.
    var logoutForm = document.querySelector('form[action$="/logout"]');
    if (logoutForm) {
        logoutForm.addEventListener("submit", function () {
            ls(function () { localStorage.setItem(LOGOUT_KEY, String(now())); });
        });
    }
})();
