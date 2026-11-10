/* Ballot: one position per screen, with Back / Next and a platform viewer. */
(function () {
  var form = document.getElementById("ballot-form");
  if (!form) return;
  var steps = Array.prototype.slice.call(form.querySelectorAll(".vt-step"));
  var back = document.getElementById("vt-back");
  var next = document.getElementById("vt-next");
  var dots = document.getElementById("vt-dots");
  var err = document.getElementById("vt-error");
  var i = 0;

  steps.forEach(function () { dots.appendChild(document.createElement("span")); });
  form.classList.add("js");

  function chosen(step) { return !!step.querySelector("input[type=radio]:checked"); }

  function show(n) {
    i = n;
    steps.forEach(function (s, k) { s.hidden = k !== i; });
    Array.prototype.forEach.call(dots.children, function (d, k) {
      d.className = k === i ? "now" : (chosen(steps[k]) ? "done" : "");
    });
    back.style.visibility = i === 0 ? "hidden" : "visible";
    next.textContent = i === steps.length - 1 ? "Review my choices" : "Next";
    err.hidden = true;
  }

  next.addEventListener("click", function () {
    if (!chosen(steps[i])) {
      err.textContent = "Choose a candidate, or choose Abstain, to continue.";
      err.hidden = false; return;
    }
    if (i === steps.length - 1) form.submit(); else show(i + 1);
  });
  back.addEventListener("click", function () { if (i > 0) show(i - 1); });
  form.addEventListener("change", function () { show(i); });
  form.addEventListener("submit", function (e) {
    var missing = steps.findIndex(function (s) { return !chosen(s); });
    if (missing !== -1) {
      e.preventDefault(); show(missing);
      err.textContent = "Choose a candidate, or choose Abstain, to continue.";
      err.hidden = false;
    }
  });

  /* platform viewer */
  var dlg = document.getElementById("vt-dialog");
  var dn = document.getElementById("vt-dname"), dt = document.getElementById("vt-dtext"), di = document.getElementById("vt-dimg");
  form.addEventListener("click", function (e) {
    var b = e.target.closest(".vt-more");
    if (!b) return;
    e.preventDefault(); e.stopPropagation();
    dn.textContent = b.dataset.name;
    dt.textContent = b.dataset.platform || "This candidate did not write a platform.";
    if (b.dataset.image) { di.src = b.dataset.image; di.hidden = false; } else { di.removeAttribute("src"); di.hidden = true; }
    if (dlg.showModal) dlg.showModal();
  });
  document.getElementById("vt-close").addEventListener("click", function () { dlg.close(); });
  dlg.addEventListener("click", function (e) { if (e.target === dlg) dlg.close(); });

  show(0);
})();
