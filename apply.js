/* Apply as officer: election -> position filter, live counters, platform image preview. */
(function () {
  var MAX_BYTES = 5 * 1024 * 1024;

  // ---- election / position / rules ----
  var election = document.getElementById("election_id");
  var position = document.getElementById("position_id");
  if (election && position) {
    var rules = document.querySelectorAll(".rules");
    var sync = function () {
      var id = election.value, keep = false;
      Array.prototype.forEach.call(position.options, function (o) {
        if (!o.dataset.election) return;
        var show = o.dataset.election === id;
        o.hidden = !show; o.disabled = !show;
        if (show && o.selected) keep = true;
      });
      if (!keep) position.selectedIndex = 0;
      Array.prototype.forEach.call(rules, function (r) { r.hidden = r.dataset.election !== id; });
    };
    election.addEventListener("change", sync);
    sync();
  }

  // ---- counters (minimum only, no maximum) ----
  document.querySelectorAll("textarea[data-min]").forEach(function (box) {
    var min = parseInt(box.dataset.min, 10);
    var out = document.querySelector('[data-counter="' + box.id + '"]');
    if (!out) return;
    function update() {
      var n = box.value.trim().length;
      var ok = n >= min;
      out.textContent = ok ? n + " characters ✓" : n + " / " + min + " minimum";
      out.classList.toggle("ok", ok);
    }
    box.addEventListener("input", update);
    update();
  });

  // ---- platform image ----
  var input = document.getElementById("platform_image");
  if (!input) return;
  var pick = document.getElementById("pf-pick");
  var preview = document.getElementById("pf-preview");
  var img = document.getElementById("pf-img");
  var nameEl = document.getElementById("pf-name");
  var err = document.getElementById("pf-error");
  var dialog = document.getElementById("pf-dialog");
  var full = document.getElementById("pf-full");
  var url = null;

  function show(msg) { err.textContent = msg; err.hidden = !msg; }
  function clear() {
    input.value = "";
    if (url) { URL.revokeObjectURL(url); url = null; }
    preview.hidden = true; pick.hidden = false; img.removeAttribute("src");
  }
  pick.addEventListener("click", function () { input.click(); });
  document.getElementById("pf-change").addEventListener("click", function () { input.click(); });
  document.getElementById("pf-remove").addEventListener("click", function () { clear(); show(""); });
  input.addEventListener("change", function () {
    var f = input.files[0];
    show("");
    if (!f) { clear(); return; }
    if (["image/png", "image/jpeg"].indexOf(f.type) === -1) { clear(); show("Use a PNG or JPG image."); return; }
    if (f.size > MAX_BYTES) { clear(); show("Choose an image smaller than 5 MB."); return; }
    if (url) URL.revokeObjectURL(url);
    url = URL.createObjectURL(f);
    img.src = url; nameEl.textContent = f.name;
    preview.hidden = false; pick.hidden = true;
  });
  document.getElementById("pf-view").addEventListener("click", function () {
    full.src = url || img.src;
    if (dialog.showModal) dialog.showModal(); else window.open(full.src, "_blank");
  });
  document.getElementById("pf-close").addEventListener("click", function () { dialog.close(); });
  dialog.addEventListener("click", function (e) { if (e.target === dialog) dialog.close(); });

  // ---- stop a too-short submit early (the server checks again) ----
  document.getElementById("apply-form").addEventListener("submit", function (e) {
    var bad = null;
    document.querySelectorAll("textarea[data-min]").forEach(function (b) {
      if (!bad && b.value.trim().length < parseInt(b.dataset.min, 10)) bad = b;
    });
    if (bad) { e.preventDefault(); bad.focus(); bad.scrollIntoView({ block: "center", behavior: "smooth" }); }
  });
})();
