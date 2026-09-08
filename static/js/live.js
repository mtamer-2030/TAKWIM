/* مِحَكّ — المتابعة المباشرة (§16). polling كل ثانيتين، بلا أي نموذج. */
(function () {
  "use strict";
  var sid = window.MIHAKK_SESSION;
  var STAT = {
    "in_progress": { cls: "s-in_progress", label: "يعمل" },
    "submitted": { cls: "s-submitted", label: "سلّم" },
    "present": { cls: "s-present", label: "حاضر" },
    "absent": { cls: "s-absent", label: "غائب" }
  };
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  var lastOk = 0;
  function markFresh() {
    lastOk = Date.now();
    var el = document.getElementById("live-status");
    if (el) { el.textContent = "مباشر ✓ (تحديث كل ثانيتين)"; el.style.color = "var(--ok)"; }
  }
  function setStale() {
    var el = document.getElementById("live-status");
    if (!el) { return; }
    var secs = lastOk ? Math.round((Date.now() - lastOk) / 1000) : 0;
    el.textContent = lastOk ? ("انقطع التحديث منذ " + secs + " ث… يُعاد") : "تعذّر الاتصال… يُعاد";
    el.style.color = "var(--warn)";
  }
  async function unlock(studentId) {
    await fetch("/teacher/sessions/" + sid + "/unlock", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ student_id: studentId })
    });
    tick();
  }
  window.__unlock = unlock;

  async function fetchJSON(url, timeoutMs) {
    var ctrl = new AbortController();
    var to = setTimeout(function () { ctrl.abort(); }, timeoutMs || 4000);
    try {
      var r = await fetch(url, { signal: ctrl.signal, cache: "no-store" });
      return await r.json();
    } finally { clearTimeout(to); }
  }

  async function tick() {
    var res;
    try { res = await fetchJSON("/teacher/sessions/" + sid + "/live.json", 4000); }
    catch (e) { setStale(); return; }   // مهلة/انقطاع ← نُظهر أنّ التحديث تأخّر ونعيد بسرعة
    if (!res || !res.ok) { return; }
    markFresh();
    document.getElementById("stat-total-q").textContent = res.total_q;
    document.getElementById("stat-submitted").textContent = res.submitted;
    var present = 0, active = 0;
    var g = document.getElementById("grid");
    g.innerHTML = "";
    res.grid.forEach(function (c) {
      if (c.present) { present++; }
      if (c.status === "in_progress") { active++; }
      var st = STAT[c.status] || STAT.present;
      var div = document.createElement("div");
      div.className = "cell " + st.cls;
      var prog = c.status === "in_progress" || c.status === "submitted"
        ? '<div class="small">' + c.answered + " / " + res.total_q + " جواب</div>" : "";
      var lock = c.locked && c.status === "in_progress"
        ? '<button class="btn warn" style="min-height:30px;padding:.1rem .5rem;margin-top:.3rem" ' +
          'onclick="__unlock(' + c.student_id + ')">فكّ القفل</button>' : "";
      div.innerHTML = '<div class="who">' + esc(c.name) + "</div>" +
        '<div class="rid">' + esc(c.roster_id) + "</div>" +
        '<div class="small">' + st.label + "</div>" + prog + lock;
      g.appendChild(div);
    });
    document.getElementById("stat-present").textContent = present;
    document.getElementById("stat-active").textContent = active;

    var rej = document.getElementById("rejections");
    var LBL = {
      "rejected_locked": "جهاز آخر", "rejected_absent": "غائب",
      "rejected_unknown": "رمز مجهول"
    };
    rej.innerHTML = (res.rejections || []).map(function (r) {
      return '<div class="rej">⛔ رفض (' + (LBL[r.event] || r.event) + ") — " +
        esc(r.login_code || "") + " · " + esc(r.at) + "</div>";
    }).join("");
  }

  tick();
  setInterval(tick, 2000);
})();
