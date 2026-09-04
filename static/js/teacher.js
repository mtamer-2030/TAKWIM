/* مِحَكّ — شاشة التأليف (§2، §16). إنشاء وتعديل الأسئلة بلا محرّر نصوص،
   مع معاينة كما تظهر على الهاتف. التحقّق الصارم يبقى على الخادم. */
(function () {
  "use strict";
  var DATA = JSON.parse(document.getElementById("assess-data").textContent);
  var TYPES = JSON.parse(document.getElementById("types-data").textContent);
  var AID = DATA.id;
  var COMP = DATA.competencies;
  var CODES = DATA.error_codes || {};
  var STIMULI = DATA.stimuli || [];
  var TYPE_LABELS = {
    mcq_single: "اختيار من متعدّد (جواب واحد)", mcq_multi: "اختيار من متعدّد (عدّة)",
    classify: "تصنيف", order: "ترتيب", short_text: "جواب قصير",
    grid: "جدول", long_text: "فقرة إنشائية"
  };
  var OPEN = ["short_text", "grid", "long_text"];

  var editor = document.getElementById("editor");
  var editing = null;   // معرّف السؤال قيد التحرير أو null

  function el(tag, attrs, kids) {
    var e = document.createElement(tag);
    attrs = attrs || {};
    Object.keys(attrs).forEach(function (k) {
      if (k === "class") { e.className = attrs[k]; }
      else if (k === "html") { e.innerHTML = attrs[k]; }
      else { e.setAttribute(k, attrs[k]); }
    });
    (kids || []).forEach(function (c) { e.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return e;
  }
  function opt(v, label, sel) {
    var o = el("option", { value: v }, [label]); if (sel) { o.selected = true; } return o;
  }

  // ————— نموذج السؤال (state) —————
  var model = blank();
  function blank() {
    return {
      id: null, type: "mcq_single", competency: Object.keys(COMP)[0], prompt: "",
      max_score: 1, stimulus: "",
      options: ["", "", "", ""], correct: 0, correctMulti: [], diagnostics: {},
      categories: ["", ""], items: [{ text: "", category: 0 }],
      orderItems: ["", ""], partial: true,
      max_chars: 0, scaffold: "", columns: ["", ""], rows: 3, cellChars: 0,
      indicators: [], penalties: []
    };
  }

  function render() {
    editor.innerHTML = "";
    // نوع + كفاية + نقطة
    var head = el("div", { class: "rowflex" });
    var typeSel = el("select"); TYPES.forEach(function (t) { typeSel.appendChild(opt(t, TYPE_LABELS[t], t === model.type)); });
    typeSel.onchange = function () { model.type = typeSel.value; render(); preview(); };
    var compSel = el("select");
    Object.keys(COMP).forEach(function (c) { compSel.appendChild(opt(c, COMP[c], c === model.competency)); });
    compSel.onchange = function () { model.competency = compSel.value; preview(); };
    var score = el("input", { type: "number", step: "0.25", style: "width:90px" });
    score.value = model.max_score;
    score.oninput = function () { model.max_score = parseFloat(score.value) || 0; };
    head.appendChild(wrap("النوع", typeSel));
    head.appendChild(wrap("الكفاية", compSel));
    head.appendChild(wrap("النقطة القصوى", score));
    editor.appendChild(head);

    if (STIMULI.length) {
      var stimSel = el("select"); stimSel.appendChild(opt("", "— بلا نصّ —", !model.stimulus));
      STIMULI.forEach(function (s) { stimSel.appendChild(opt(s.id, s.id + ": " + (s.text || "").slice(0, 30), s.id === model.stimulus)); });
      stimSel.onchange = function () { model.stimulus = stimSel.value; preview(); };
      editor.appendChild(wrap("نصّ الانطلاق", stimSel));
    }

    var prompt = el("textarea"); prompt.value = model.prompt;
    prompt.oninput = function () { model.prompt = prompt.value; preview(); };
    editor.appendChild(wrap("نصّ السؤال", prompt));

    editor.appendChild(el("hr"));
    renderTypeFields();
    if (OPEN.indexOf(model.type) >= 0) { renderIndicators(); }
    preview();
  }

  function wrap(label, node) {
    var d = el("div", { class: "field", style: "flex:1" });
    d.appendChild(el("label", {}, [label])); d.appendChild(node); return d;
  }

  function listEditor(arr, placeholder, onchange, allowErrCode, correctCtl) {
    var box = el("div");
    function redraw() {
      box.innerHTML = "";
      arr.forEach(function (v, i) {
        var row = el("div", { class: "rowflex", style: "margin:.3rem 0" });
        if (correctCtl) { row.appendChild(correctCtl(i)); }
        var inp = el("input", { type: "text", placeholder: placeholder });
        inp.value = typeof v === "string" ? v : (v.text || "");
        inp.oninput = function () {
          if (typeof arr[i] === "string") { arr[i] = inp.value; } else { arr[i].text = inp.value; }
          onchange(); preview();
        };
        row.appendChild(inp);
        if (allowErrCode) {
          var sel = el("select", { style: "max-width:190px" });
          sel.appendChild(opt("", "— رمز خطأ —", !model.diagnostics[i]));
          Object.keys(CODES).forEach(function (c) { sel.appendChild(opt(c, CODES[c], model.diagnostics[i] === c)); });
          sel.onchange = function () {
            if (sel.value) { model.diagnostics[i] = sel.value; } else { delete model.diagnostics[i]; }
          };
          sel.disabled = correctCtl && model.correct === i;
          row.appendChild(sel);
        }
        var del = el("button", { class: "btn bad", style: "min-height:34px;padding:.1rem .6rem" }, ["×"]);
        del.onclick = function () { arr.splice(i, 1); if (allowErrCode) { model.diagnostics = {}; } redraw(); onchange(); preview(); };
        row.appendChild(del);
        box.appendChild(row);
      });
      var add = el("button", { class: "btn ghost", style: "min-height:34px" }, ["+ عنصر"]);
      add.onclick = function () { arr.push(typeof arr[0] === "object" ? { text: "", category: 0 } : ""); redraw(); onchange(); preview(); };
      box.appendChild(add);
    }
    redraw();
    return box;
  }

  function renderTypeFields() {
    var t = model.type;
    if (t === "mcq_single") {
      editor.appendChild(el("label", {}, ["الخيارات (اختر الصحيح، وأسند رمز خطأ للبدائل)"]));
      editor.appendChild(listEditor(model.options, "نصّ الخيار", function () { }, true, function (i) {
        var r = el("input", { type: "radio", name: "correct" }); r.checked = model.correct === i;
        r.onclick = function () { model.correct = i; render(); preview(); };
        return r;
      }));
    } else if (t === "mcq_multi") {
      editor.appendChild(el("label", {}, ["الخيارات (علّم كل الصحيحة)"]));
      editor.appendChild(listEditor(model.options, "نصّ الخيار", function () { }, false, function (i) {
        var c = el("input", { type: "checkbox" }); c.checked = model.correctMulti.indexOf(i) >= 0;
        c.onclick = function () {
          var k = model.correctMulti.indexOf(i);
          if (k >= 0) { model.correctMulti.splice(k, 1); } else { model.correctMulti.push(i); }
        };
        return c;
      }));
    } else if (t === "classify") {
      editor.appendChild(el("label", {}, ["الفئات"]));
      editor.appendChild(listEditor(model.categories, "اسم الفئة", function () { renderClassifyItems(); }));
      editor.appendChild(el("label", {}, ["العناصر (وفئة كلٍّ منها)"]));
      var host = el("div", { id: "classify-items" }); editor.appendChild(host); renderClassifyItems();
    } else if (t === "order") {
      editor.appendChild(el("label", {}, ["العناصر بالترتيب الصحيح (يُعرض للتلميذ مبعثراً)"]));
      editor.appendChild(listEditor(model.orderItems, "خطوة/جملة", function () { }));
      var pc = el("label", { class: "rowflex", style: "font-weight:400" });
      var cb = el("input", { type: "checkbox" }); cb.checked = model.partial;
      cb.onclick = function () { model.partial = cb.checked; };
      pc.appendChild(cb); pc.appendChild(el("span", {}, ["تنقيط جزئي"])); editor.appendChild(pc);
    } else if (t === "short_text" || t === "long_text") {
      var mc = el("input", { type: "number", style: "width:120px" }); mc.value = model.max_chars || "";
      mc.oninput = function () { model.max_chars = parseInt(mc.value, 10) || 0; };
      editor.appendChild(wrap("أقصى عدد محارف (اختياري)", mc));
      if (t === "long_text") {
        var sc = el("input", { type: "text" }); sc.value = model.scaffold;
        sc.oninput = function () { model.scaffold = sc.value; };
        editor.appendChild(wrap("سقالة (مفصولة بـ ·)", sc));
      }
    } else if (t === "grid") {
      editor.appendChild(el("label", {}, ["الأعمدة"]));
      editor.appendChild(listEditor(model.columns, "عنوان العمود", function () { }));
      var rr = el("input", { type: "number", style: "width:90px" }); rr.value = model.rows;
      rr.oninput = function () { model.rows = parseInt(rr.value, 10) || 1; };
      editor.appendChild(wrap("عدد الصفوف", rr));
      var cc = el("input", { type: "number", style: "width:120px" }); cc.value = model.cellChars || "";
      cc.oninput = function () { model.cellChars = parseInt(cc.value, 10) || 0; };
      editor.appendChild(wrap("أقصى محارف للخلية (اختياري)", cc));
    }
  }

  function renderClassifyItems() {
    var host = document.getElementById("classify-items"); if (!host) { return; }
    host.innerHTML = "";
    model.items.forEach(function (it, i) {
      var row = el("div", { class: "rowflex", style: "margin:.3rem 0" });
      var inp = el("input", { type: "text", placeholder: "نصّ العنصر" }); inp.value = it.text;
      inp.oninput = function () { it.text = inp.value; preview(); };
      var sel = el("select", { style: "max-width:200px" });
      model.categories.forEach(function (c, ci) { sel.appendChild(opt(ci, c || ("فئة " + (ci + 1)), it.category === ci)); });
      sel.onchange = function () { it.category = parseInt(sel.value, 10); };
      var del = el("button", { class: "btn bad", style: "min-height:34px;padding:.1rem .6rem" }, ["×"]);
      del.onclick = function () { model.items.splice(i, 1); renderClassifyItems(); preview(); };
      row.appendChild(inp); row.appendChild(sel); row.appendChild(del); host.appendChild(row);
    });
    var add = el("button", { class: "btn ghost", style: "min-height:34px" }, ["+ عنصر"]);
    add.onclick = function () { model.items.push({ text: "", category: 0 }); renderClassifyItems(); preview(); };
    host.appendChild(add);
  }

  function renderIndicators() {
    editor.appendChild(el("hr"));
    editor.appendChild(el("label", {}, ["عناصر الجواب (المؤشّرات) — مجموع نقاطها = النقطة القصوى"]));
    var host = el("div");
    function redraw() {
      host.innerHTML = "";
      model.indicators.forEach(function (ind, i) {
        var card = el("div", { class: "card", style: "margin:.4rem 0;padding:.7rem" });
        var r1 = el("div", { class: "rowflex" });
        var idi = el("input", { type: "text", placeholder: "id (i1)", style: "max-width:90px" }); idi.value = ind.id;
        idi.oninput = function () { ind.id = idi.value; };
        var txt = el("input", { type: "text", placeholder: "نصّ المؤشّر" }); txt.value = ind.text;
        txt.oninput = function () { ind.text = txt.value; };
        var pts = el("input", { type: "number", step: "0.25", style: "max-width:80px" }); pts.value = ind.points;
        pts.oninput = function () { ind.points = parseFloat(pts.value) || 0; };
        var del = el("button", { class: "btn bad", style: "min-height:34px;padding:.1rem .6rem" }, ["×"]);
        del.onclick = function () { model.indicators.splice(i, 1); redraw(); };
        r1.appendChild(idi); r1.appendChild(txt); r1.appendChild(pts); r1.appendChild(del);
        card.appendChild(r1);
        // check_rule
        var r2 = el("div", { class: "rowflex", style: "margin-top:.3rem" });
        var rsel = el("select", { style: "max-width:160px" });
        [["", "بلا قاعدة (يقرّرها الأستاذ)"], ["any_of", "any_of"], ["all_of", "all_of"],
         ["none_of", "none_of"], ["min_chars", "min_chars"], ["max_chars", "max_chars"],
         ["regex", "regex"]].forEach(function (o) {
          rsel.appendChild(opt(o[0], o[1], ind.check_rule && ind.check_rule.type === o[0]));
        });
        var param = el("input", { type: "text", placeholder: "أنماط مفصولة بـ | أو رقم" });
        if (ind.check_rule) {
          param.value = ind.check_rule.patterns ? ind.check_rule.patterns.join("|")
            : (ind.check_rule.value != null ? ind.check_rule.value
              : (ind.check_rule.pattern || ""));
        }
        function syncRule() {
          var tp = rsel.value;
          if (!tp) { ind.check_rule = null; param.disabled = true; return; }
          param.disabled = false;
          if (tp === "min_chars" || tp === "max_chars") {
            ind.check_rule = { type: tp, value: parseInt(param.value, 10) || 0 };
          } else if (tp === "regex") {
            ind.check_rule = { type: tp, pattern: param.value };
          } else {
            ind.check_rule = { type: tp, patterns: param.value.split("|").map(function (s) { return s.trim(); }).filter(Boolean) };
          }
        }
        rsel.onchange = syncRule; param.oninput = syncRule; syncRule();
        r2.appendChild(rsel); r2.appendChild(param); card.appendChild(r2);
        host.appendChild(card);
      });
      var add = el("button", { class: "btn ghost", style: "min-height:34px" }, ["+ مؤشّر"]);
      add.onclick = function () {
        model.indicators.push({ id: "i" + (model.indicators.length + 1), text: "", points: 0, check_rule: null });
        redraw();
      };
      host.appendChild(add);
    }
    redraw(); editor.appendChild(host);
  }

  // ————— بناء JSON للسؤال —————
  function buildQuestion() {
    var q = { type: model.type, competency: model.competency, prompt: model.prompt,
      max_score: model.max_score };
    if (model.id) { q.id = model.id; }
    if (model.stimulus) { q.stimulus = model.stimulus; }
    var p = {};
    if (model.type === "mcq_single") {
      p.options = model.options.slice(); p.correct = model.correct;
      var diag = {}; Object.keys(model.diagnostics).forEach(function (k) { diag[k] = model.diagnostics[k]; });
      if (Object.keys(diag).length) { p.diagnostics = diag; }
    } else if (model.type === "mcq_multi") {
      p.options = model.options.slice(); p.correct = model.correctMulti.slice().sort(function (a, b) { return a - b; });
    } else if (model.type === "classify") {
      p.categories = model.categories.slice();
      p.items = model.items.map(function (it) { return { text: it.text, category: it.category }; });
    } else if (model.type === "order") {
      p.items = model.orderItems.slice();
      p.correct_order = model.orderItems.map(function (_, i) { return i; });
      p.partial_credit = model.partial;
    } else if (model.type === "short_text" || model.type === "long_text") {
      if (model.max_chars) { p.max_chars = model.max_chars; }
      if (model.type === "long_text" && model.scaffold) {
        p.scaffold = model.scaffold.split("·").map(function (s) { return s.trim(); }).filter(Boolean);
      }
    } else if (model.type === "grid") {
      p.columns = model.columns.slice(); p.rows = model.rows;
      if (model.cellChars) { p.max_chars_per_cell = model.cellChars; }
    }
    q.payload = p;
    if (OPEN.indexOf(model.type) >= 0 && model.indicators.length) {
      q.indicators = model.indicators.map(function (ind) {
        var o = { id: ind.id, text: ind.text, points: ind.points };
        if (ind.check_rule) { o.check_rule = ind.check_rule; }
        return o;
      });
    }
    return q;
  }

  // ————— المعاينة (كما تظهر على الهاتف) —————
  function preview() {
    var host = document.getElementById("preview");
    var q = buildQuestion();
    var h = "";
    if (q.stimulus) {
      var st = STIMULI.filter(function (s) { return s.id === q.stimulus; })[0];
      if (st) { h += '<div class="stimulus">' + esc(st.text) + "</div>"; }
    }
    h += '<div class="prompt">' + esc(q.prompt || "—") + "</div>";
    var p = q.payload;
    if (q.type === "mcq_single" || q.type === "mcq_multi") {
      (p.options || []).forEach(function (o) { h += '<div class="option">' + esc(o) + "</div>"; });
    } else if (q.type === "classify") {
      (p.items || []).forEach(function (it) {
        h += '<div style="font-weight:600;margin-top:.5rem">' + esc(it.text) + "</div>" +
          '<select>' + (p.categories || []).map(function (c) { return "<option>" + esc(c) + "</option>"; }).join("") + "</select>";
      });
    } else if (q.type === "order") {
      (p.items || []).forEach(function (o) { h += '<div class="option">↕ ' + esc(o) + "</div>"; });
    } else if (q.type === "short_text" || q.type === "long_text") {
      h += '<textarea placeholder="جواب التلميذ"' + (q.type === "long_text" ? ' style="min-height:120px"' : "") + "></textarea>";
    } else if (q.type === "grid") {
      h += '<table><tr>' + (p.columns || []).map(function (c) { return "<th>" + esc(c) + "</th>"; }).join("") + "</tr>";
      for (var r = 0; r < (p.rows || 1); r++) {
        h += "<tr>" + (p.columns || []).map(function () { return "<td><input></td>"; }).join("") + "</tr>";
      }
      h += "</table>";
    }
    host.innerHTML = h;
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  // ————— حفظ —————
  document.getElementById("save-q").addEventListener("click", async function () {
    var q = buildQuestion();
    var errBox = document.getElementById("editor-errors"); errBox.hidden = true;
    var res = await fetch("/teacher/assessments/" + AID + "/question", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(q)
    });
    var js = await res.json();
    if (!js.ok) {
      errBox.innerHTML = "<b>لم يُقبل السؤال:</b><ul class='errs'>" +
        (js.errors || ["خطأ"]).map(function (e) { return "<li>" + esc(e) + "</li>"; }).join("") + "</ul>";
      errBox.hidden = false; return;
    }
    location.reload();
  });
  document.getElementById("reset-q").addEventListener("click", function () {
    model = blank(); editing = null;
    document.getElementById("editor-title").textContent = "إضافة سؤال";
    render();
  });

  // تحرير سؤال موجود
  document.querySelectorAll(".edit-q").forEach(function (b) {
    b.addEventListener("click", function () {
      var q = JSON.parse(b.getAttribute("data-q"));
      model = fromQuestion(q);
      document.getElementById("editor-title").textContent = "تحرير سؤال #" + q.position;
      render();
      document.getElementById("editor").scrollIntoView({ behavior: "smooth" });
    });
  });

  function fromQuestion(q) {
    var m = blank();
    m.id = q.id; m.type = q.type; m.competency = q.competency; m.prompt = q.prompt;
    m.max_score = q.max_score; m.stimulus = q.stimulus || "";
    var p = q.payload || {};
    if (q.type === "mcq_single") {
      m.options = (p.options || []).slice(); m.correct = p.correct || 0;
      m.diagnostics = p.diagnostics || {};
    } else if (q.type === "mcq_multi") {
      m.options = (p.options || []).slice(); m.correctMulti = (p.correct || []).slice();
    } else if (q.type === "classify") {
      m.categories = (p.categories || []).slice();
      m.items = (p.items || []).map(function (it) { return { text: it.text, category: it.category }; });
    } else if (q.type === "order") {
      m.orderItems = (p.items || []).slice(); m.partial = !!p.partial_credit;
    } else if (q.type === "short_text" || q.type === "long_text") {
      m.max_chars = p.max_chars || 0; m.scaffold = (p.scaffold || []).join(" · ");
    } else if (q.type === "grid") {
      m.columns = (p.columns || []).slice(); m.rows = p.rows || 1; m.cellChars = p.max_chars_per_cell || 0;
    }
    m.indicators = (q.indicators || []).map(function (i) {
      return { id: i.id, text: i.text, points: i.points, check_rule: i.check_rule || null };
    });
    return m;
  }

  // ————— نصوص الانطلاق —————
  var stimHost = document.getElementById("stimuli-list");
  function drawStim() {
    stimHost.innerHTML = "";
    STIMULI.forEach(function (s, i) {
      var row = el("div", { class: "rowflex", style: "margin:.3rem 0" });
      var idi = el("input", { type: "text", style: "max-width:90px", placeholder: "s1" }); idi.value = s.id;
      idi.oninput = function () { s.id = idi.value; };
      var txt = el("input", { type: "text", placeholder: "نصّ الانطلاق" }); txt.value = s.text;
      txt.oninput = function () { s.text = txt.value; };
      var del = el("button", { class: "btn bad", style: "min-height:34px;padding:.1rem .6rem" }, ["×"]);
      del.onclick = function () { STIMULI.splice(i, 1); drawStim(); };
      row.appendChild(idi); row.appendChild(txt); row.appendChild(del); stimHost.appendChild(row);
    });
  }
  document.getElementById("add-stim").addEventListener("click", function () {
    STIMULI.push({ id: "s" + (STIMULI.length + 1), text: "" }); drawStim();
  });
  document.getElementById("save-stim").addEventListener("click", async function () {
    await fetch("/teacher/assessments/" + AID + "/stimuli", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ stimuli: STIMULI })
    });
    location.reload();
  });
  drawStim();

  render();
})();
