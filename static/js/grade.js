/* مِحَكّ — حفظ تصحيح الأستاذ (§16). ai_score مقترح لا يدخل تقريراً قبل التصديق. */
(function () {
  "use strict";
  document.querySelectorAll("[data-answer]").forEach(function (card) {
    var id = card.getAttribute("data-answer");
    var btn = card.querySelector(".g-save");
    var flag = card.querySelector(".g-flag");
    btn.addEventListener("click", async function () {
      var score = card.querySelector(".g-score").value;
      var body = {
        manual_score: score === "" ? null : parseFloat(score),
        teacher_note: card.querySelector(".g-note").value || null,
        teacher_confirmed: card.querySelector(".g-confirm").checked
      };
      flag.textContent = "…";
      var res = await fetch("/teacher/answers/" + id + "/grade", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      });
      var js = await res.json();
      flag.textContent = js.ok ? "✓ حُفِظ" : "تعذّر الحفظ";
    });
  });
})();
