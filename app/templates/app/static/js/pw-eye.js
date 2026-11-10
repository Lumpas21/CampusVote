document.querySelectorAll("[data-eye]").forEach(function (btn) {
  var input = document.getElementById(btn.dataset.eye);
  if (!input) return;

  function syncButton() {
    var visible = input.type !== "password";
    btn.setAttribute("aria-pressed", String(visible));
    btn.setAttribute("aria-label", (visible ? "Hide " : "Show ") + "password");
  }

  syncButton();
  btn.addEventListener("click", function () {
    input.type = input.type === "password" ? "text" : "password";
    syncButton();
  });
});
