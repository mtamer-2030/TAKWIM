/* مِحَكّ — مسار التلميذ (CLAUDE.md §13، §15).
   لا نموذج يعمل هنا؛ التصحيح يقيني على الخادم. لا نقطة، لا وقت مرئي، لا تلعيب. */
(function () {
  "use strict";

  var sessions = JSON.parse(document.getElementById("sessions-data").textContent || "[]");

  // قفل الجهاز: رمز عشوائي ثابت لهذا الجهاز/المتصفّح.
  var deviceToken = localStorage.getItem("mihakk_device");
  if (!deviceToken) {
    deviceToken = "d-" + Math.random().toString(36).slice(2) + Date.now().toString(36);
    localStorage.setItem("mihakk_device", deviceToken);
  }

  var state = {
    session: null, attemptId: null, revealMode: "immediate", kind: null,
    questions: [], answers: {}, idx: 0,
    firstSeen: {}, msSpent: {}, revision: {}, startedAt: Date.now()
  };

  function $(id) { return document.getElementById(id); }
  function show(name) {
    ["pick", "code", "confirm", "quiz", "done", "none"].forEach(function (n) {
      $("screen-" + n).classList.toggle("active", n === name);
    });
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  async function postJSON(url, body) {
    var r = await fetch(url, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    return r.json();
  }

  // ————— الدخول —————
  function startFlow() {
    if (sessions.length === 0) { show("none"); return; }
    if (sessions.length === 1) { chooseSession(sessions[0]); return; }
    var list = $("pick-list"); list.innerHTML = "";
    sessions.forEach(function (s) {
      var b = document.createElement("button");
      b.className = "btn big"; b.style.margin = ".4rem 0";
      b.textContent = s.class_label + " — " + s.title;
      b.onclick = function () { chooseSession(s); };
      list.appendChild(b);
    });
    show("pick");
  }
  function chooseSession(s) {
    state.session = s;
    $("code-session-label").textContent = s.class_label + " · " + s.title;
    show("code"); setTimeout(function () { $("code").focus(); }, 100);
  }

  $("btn-lookup").onclick = async function () {
    var typed = $("code").value.trim();
    var err = $("code-error"); err.hidden = true;
    if (!typed) { return; }
    var res = await postJSON("/api/student/lookup", {
      session_id: state.session.id, typed: typed, device_token: deviceToken
    });
    if (!res.ok) { err.textContent = res.error; err.hidden = false; return; }
    $("confirm-name").textContent = res.full_name;
    $("confirm-rid").textContent = res.roster_id;
    show("confirm");
  };
  $("code").addEventListener("keydown", function (e) {
    if (e.key === "Enter") { $("btn-lookup").click(); }
  });
  $("btn-back-code").onclick = function () { show("code"); };

  $("btn-start").onclick = async function () {
    var typed = $("code").value.trim();
    var res = await postJSON("/api/student/start", {
      session_id: state.session.id, typed: typed, device_token: deviceToken
    });
    if (!res.ok) {
      $("code-error").textContent = res.error; $("code-error").hidden = false;
      show("code"); return;
    }
    state.attemptId = res.attempt_id;
    state.revealMode = res.reveal_mode;
    state.kind = res.kind;
    state.questions = res.questions;
    state.answers = res.answers || {};
    state.idx = 0;
    show("quiz");
    renderQuestion();
  };

  // ————— عرض الأسئلة —————
  function currentQ() { return state.questions[state.idx]; }

  function renderQuestion() {
    var q = currentQ();
    if (!state.firstSeen[q.id]) { state.firstSeen[q.id] = Date.now(); }
    $("progress").textContent = (state.idx + 1) + " من " + state.questions.length;
    var host = $("qcard");
    var html = "";
    if (q.stimulus_text) { html += '<div class="stimulus">' + esc(q.stimulus_text) + "</div>"; }
    html += '<div class="prompt">' + esc(q.prompt) + "</div>";
    html += '<div id="answer-area"></div>';
    host.innerHTML = html;
    renderAnswerArea(q);
    $("btn-prev").disabled = state.idx === 0;
    var last = state.idx === state.questions.length - 1;
    $("btn-next").hidden = last;
    $("submit-row").hidden = !last;
    $("saveflag").textContent = "";
  }

  function renderAnswerArea(q) {
    var area = $("answer-area");
    var saved = state.answers[q.id] || null;
    var p = q.payload || {};
    if (q.type === "mcq_single") {
      (p.options || []).forEach(function (opt, i) {
        var b = document.createElement("button");
        b.className = "option" + (saved && saved.choice === i ? " selected" : "");
        b.textContent = opt;
        b.onclick = function () {
          setAnswer(q.id, { choice: i });
          Array.prototype.forEach.call(area.children, function (c) { c.classList.remove("selected"); });
          b.classList.add("selected");
        };
        area.appendChild(b);
      });
    } else if (q.type === "mcq_multi") {
      var chosen = (saved && saved.choices) ? saved.choices.slice() : [];
      (p.options || []).forEach(function (opt, i) {
        var b = document.createElement("button");
        b.className = "option" + (chosen.indexOf(i) >= 0 ? " selected" : "");
        b.textContent = opt;
        b.onclick = function () {
          var k = chosen.indexOf(i);
          if (k >= 0) { chosen.splice(k, 1); b.classList.remove("selected"); }
          else { chosen.push(i); b.classList.add("selected"); }
          setAnswer(q.id, { choices: chosen.slice() });
        };
        area.appendChild(b);
      });
    } else if (q.type === "classify") {
      var assigns = (saved && saved.assignments) ? saved.assignments.slice() : [];
      (p.items || []).forEach(function (it, i) {
        var row = document.createElement("div"); row.className = "field";
        row.innerHTML = '<div style="font-weight:600">' + esc(it.text) + "</div>";
        var sel = document.createElement("select");
        sel.innerHTML = '<option value="">— اختر —</option>' +
          (p.categories || []).map(function (c, ci) {
            return '<option value="' + ci + '">' + esc(c) + "</option>";
          }).join("");
        if (assigns[i] != null) { sel.value = assigns[i]; }
        sel.onchange = function () {
          assigns[i] = sel.value === "" ? null : parseInt(sel.value, 10);
          setAnswer(q.id, { assignments: assigns.slice() });
        };
        row.appendChild(sel); area.appendChild(row);
      });
    } else if (q.type === "order") {
      var order = (saved && saved.order) ? saved.order.slice()
        : shuffle((p.items || []).map(function (_, i) { return i; }));
      setAnswer(q.id, { order: order.slice() }, true);
      var listEl = document.createElement("div");
      function redraw() {
        listEl.innerHTML = "";
        order.forEach(function (itemIdx, pos) {
          var row = document.createElement("div");
          row.className = "rowflex"; row.style.margin = ".35rem 0";
          row.innerHTML = '<span style="flex:1;padding:.6rem;border:1.5px solid var(--line);' +
            'border-radius:10px;background:#fff">' + esc(p.items[itemIdx]) + "</span>";
          var up = document.createElement("button"); up.className = "btn ghost"; up.textContent = "▲";
          var dn = document.createElement("button"); dn.className = "btn ghost"; dn.textContent = "▼";
          up.disabled = pos === 0; dn.disabled = pos === order.length - 1;
          up.onclick = function () { swap(order, pos, pos - 1); commit(); };
          dn.onclick = function () { swap(order, pos, pos + 1); commit(); };
          row.appendChild(up); row.appendChild(dn); listEl.appendChild(row);
        });
      }
      function commit() { setAnswer(q.id, { order: order.slice() }); redraw(); }
      redraw(); area.appendChild(listEl);
    } else if (q.type === "short_text" || q.type === "long_text") {
      var ta = document.createElement("textarea");
      if (q.type === "long_text") { ta.style.minHeight = "220px"; }
      if (p.max_chars) { ta.maxLength = p.max_chars; }
      ta.value = (saved && saved.text) || "";
      ta.oninput = function () { setAnswer(q.id, { text: ta.value }); };
      area.appendChild(ta);
      if (p.scaffold && p.scaffold.length) {
        var s = document.createElement("div"); s.className = "muted small";
        s.textContent = "استرشد بـ: " + p.scaffold.join(" · ");
        area.appendChild(s);
      }
      if (p.max_chars) {
        var counter = document.createElement("div"); counter.className = "muted small";
        function upd() { counter.textContent = ta.value.length + " / " + p.max_chars; }
        ta.addEventListener("input", upd); upd(); area.appendChild(counter);
      }
    } else if (q.type === "grid") {
      var rows = p.rows || 1, cols = (p.columns || []).length || 1;
      var cells = (saved && saved.cells) ? saved.cells : [];
      var tbl = document.createElement("table");
      var thead = "<tr>" + (p.columns || []).map(function (c) {
        return "<th>" + esc(c) + "</th>";
      }).join("") + "</tr>";
      tbl.innerHTML = "<thead>" + thead + "</thead>";
      var tb = document.createElement("tbody");
      for (var r = 0; r < rows; r++) {
        var tr = document.createElement("tr");
        cells[r] = cells[r] || [];
        for (var c = 0; c < cols; c++) {
          (function (r, c) {
            var td = document.createElement("td");
            var inp = document.createElement("textarea");
            inp.style.minHeight = "60px";
            if (p.max_chars_per_cell) { inp.maxLength = p.max_chars_per_cell; }
            inp.value = cells[r][c] || "";
            inp.oninput = function () {
              cells[r][c] = inp.value; setAnswer(q.id, { cells: cells });
            };
            td.appendChild(inp); tr.appendChild(td);
          })(r, c);
        }
        tb.appendChild(tr);
      }
      tbl.appendChild(tb);
      var wrap = document.createElement("div"); wrap.className = "table-wrap";
      wrap.appendChild(tbl); area.appendChild(wrap);
    }
  }

  function shuffle(a) {
    for (var i = a.length - 1; i > 0; i--) {
      var j = Math.floor(Math.random() * (i + 1));
      var t = a[i]; a[i] = a[j]; a[j] = t;
    }
    return a;
  }
  function swap(a, i, j) { var t = a[i]; a[i] = a[j]; a[j] = t; }

  function setAnswer(qid, val, silent) {
    var prev = state.answers[qid];
    if (prev !== undefined && JSON.stringify(prev) !== JSON.stringify(val)) {
      state.revision[qid] = (state.revision[qid] || 0) + 1;
    }
    state.answers[qid] = val;
    if (!silent) { $("saveflag").textContent = ""; }
  }

  // ————— حفظ فوري عند كل انتقال (§4/6) —————
  async function saveCurrent() {
    var q = currentQ();
    var raw = state.answers[q.id];
    if (raw === undefined) { return; }
    var since = state.firstSeen[q.id] || Date.now();
    state.msSpent[q.id] = (state.msSpent[q.id] || 0) + (Date.now() - since);
    state.firstSeen[q.id] = Date.now();
    $("saveflag").textContent = "…جارٍ الحفظ";
    var res = await postJSON("/api/student/answer", {
      attempt_id: state.attemptId, device_token: deviceToken, question_id: q.id,
      raw: raw, ms_spent: state.msSpent[q.id], revision_count: state.revision[q.id] || 0
    });
    $("saveflag").textContent = res.ok ? "✓ حُفِظ" : "تعذّر الحفظ — أعد المحاولة";
    return res.ok;
  }

  $("btn-next").onclick = async function () {
    await saveCurrent();
    if (state.idx < state.questions.length - 1) { state.idx++; renderQuestion(); }
  };
  $("btn-prev").onclick = async function () {
    await saveCurrent();
    if (state.idx > 0) { state.idx--; renderQuestion(); }
  };

  $("btn-submit").onclick = async function () {
    await saveCurrent();
    var res = await postJSON("/api/student/submit", {
      attempt_id: state.attemptId, device_token: deviceToken,
      total_ms: Date.now() - state.startedAt
    });
    renderFeedback(res);
    show("done");
  };

  // ————— التغذية الراجعة (§13): بلا نقطة، بوصف الخطأ لا كشف الجواب —————
  function renderFeedback(res) {
    var host = $("feedback");
    if (!res || res.reveal_mode === "none" || !res.feedback || !res.feedback.length) {
      host.innerHTML = "";  // في الفرض: «تمّ التسليم» فقط
      return;
    }
    var html = "";
    res.feedback.forEach(function (f) {
      html += '<div class="fb-item">';
      html += '<b>السؤال ' + f.position + "</b> ";
      if (f.verdict === "blank") {
        html += '<span class="fb-wait">— لم تُجب</span>';
      } else if (f.is_closed) {
        if (f.verdict === "correct") { html += '<span class="fb-ok">✓ صحيح</span>'; }
        else if (f.verdict === "partial") { html += '<span class="fb-bad">◑ جزئي</span>'; }
        else { html += '<span class="fb-bad">✗</span>'; }
        (f.error_labels || []).forEach(function (lbl) {
          html += '<div class="muted small">— ' + esc(lbl) + "</div>";
        });
      } else {
        (f.met_indicators || []).forEach(function (t) {
          html += '<div class="fb-ok">✓ ' + esc(t) + "</div>";
        });
        if (f.pending_count) {
          html += '<div class="fb-wait">· ' + f.pending_count +
            " مؤشّر(ات) تنتظر تصحيح الأستاذ</div>";
        }
      }
      html += "</div>";
    });
    host.innerHTML = html;
  }

  startFlow();
})();
