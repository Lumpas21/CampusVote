// Show / hide password for pages that use the normal site shell (settings).
// The auth screens already do this inline in auth_base.html.
document.querySelectorAll("[data-eye]").forEach(function (btn) {
  btn.addEventListener("click", function () {
    var input = document.getElementById(btn.dataset.eye);
    if (input) input.type = input.type === "password" ? "text" : "password";
  });
});
