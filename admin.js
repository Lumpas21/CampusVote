/* Admin panel behaviour: confirm dialogs, clickable rows, election form. */
(function () {
  // Ask before risky actions (close / delete / reset / unlock ...)
  document.addEventListener("submit", function (e) {
    var msg = e.target.getAttribute && e.target.getAttribute("data-confirm");
    if (msg && !window.confirm(msg)) e.preventDefault();
  });

  // Click a table row to open it
  document.querySelectorAll("tr[data-href]").forEach(function (tr) {
    tr.addEventListener("click", function (e) {
      if (e.target.closest("a, button, form")) return;
      window.location.href = tr.getAttribute("data-href");
    });
  });

  // Show / hide password
  document.querySelectorAll("[data-eye]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var input = document.getElementById(btn.dataset.eye);
      if (input) input.type = input.type === "password" ? "text" : "password";
    });
  });

  // ---- Election form ----
  var typeSel = document.getElementById("type");
  if (typeSel) {
    var scopes = document.querySelectorAll(".scope[data-type]");
    var box = document.getElementById("pos-box");
    var list = box ? box.querySelector(".pos-list") : null;
    var addBtn = document.getElementById("pos-add");
    var newInp = document.getElementById("pos-new");
    var defaults = {};
    try { defaults = JSON.parse(box.getAttribute("data-defaults") || "{}"); } catch (err) {}

    function showScope() {
      scopes.forEach(function (s) { s.hidden = s.getAttribute("data-type") !== typeSel.value; });
    }
    function chip(name) {
      var c = document.createElement("span");
      c.className = "pos-chip";
      var h = document.createElement("input");
      h.type = "hidden"; h.name = "positions"; h.value = name;
      var t = document.createElement("span"); t.textContent = name;
      var x = document.createElement("button");
      x.type = "button"; x.setAttribute("aria-label", "Remove " + name); x.innerHTML = "&times;";
      c.appendChild(h); c.appendChild(t); c.appendChild(x);
      return c;
    }
    function names() {
      return Array.prototype.map.call(list.querySelectorAll("input[name=positions]"), function (i) { return i.value.toLowerCase(); });
    }
    function touch() { box.setAttribute("data-touched", "1"); }

    typeSel.addEventListener("change", function () {
      showScope();
      // Until the admin edits the list, it follows the election type.
      if (box.getAttribute("data-touched") === "0" && defaults[typeSel.value]) {
        list.innerHTML = "";
        defaults[typeSel.value].forEach(function (n) { list.appendChild(chip(n)); });
      }
    });
    list.addEventListener("click", function (e) {
      var b = e.target.closest("button");
      if (b) { b.parentNode.remove(); touch(); }
    });
    addBtn.addEventListener("click", function () {
      newInp.hidden = false; newInp.focus();
    });
    function commit() {
      var v = newInp.value.trim();
      if (v && names().indexOf(v.toLowerCase()) === -1) { list.appendChild(chip(v)); touch(); }
      newInp.value = ""; newInp.hidden = true;
    }
    newInp.addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); commit(); }
      if (e.key === "Escape") { newInp.value = ""; newInp.hidden = true; }
    });
    newInp.addEventListener("blur", commit);
    showScope();
  }
})();
