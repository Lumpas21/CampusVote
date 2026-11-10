// Six-digit code boxes used by the new pages (forgot password, change email).
// Same behaviour as the inline script in auth/verify_email.html.
// Optional parts: #timer (data-seconds) + #verify-btn, and #resend-btn (data-wait) + #resend-note.
(function () {
  var boxes = Array.prototype.slice.call(document.querySelectorAll(".otp-box"));
  var hidden = document.getElementById("code");
  var form = document.getElementById("otp-form");
  if (!boxes.length || !hidden || !form) return;

  function join() { hidden.value = boxes.map(function (b) { return b.value; }).join(""); }

  boxes.forEach(function (box, i) {
    box.addEventListener("input", function () {
      box.value = box.value.replace(/\D/g, "").slice(0, 1);
      if (box.value && i < boxes.length - 1) boxes[i + 1].focus();
      join();
    });
    box.addEventListener("keydown", function (e) {
      if (e.key === "Backspace" && !box.value && i > 0) boxes[i - 1].focus();
      if (e.key === "ArrowLeft" && i > 0) boxes[i - 1].focus();
      if (e.key === "ArrowRight" && i < boxes.length - 1) boxes[i + 1].focus();
    });
    box.addEventListener("paste", function (e) {
      var text = (e.clipboardData || window.clipboardData).getData("text").replace(/\D/g, "").slice(0, 6);
      if (!text) return;
      e.preventDefault();
      text.split("").forEach(function (ch, k) { boxes[k].value = ch; });
      boxes[Math.min(text.length, 5)].focus();
      join();
    });
  });
  form.addEventListener("submit", join);

  // expiry countdown (only when the page provides a real number)
  var timer = document.getElementById("timer");
  var verifyBtn = document.getElementById("verify-btn");
  if (timer && timer.dataset.seconds) {
    var left = parseInt(timer.dataset.seconds, 10) || 0;
    var pad = function (n) { return (n < 10 ? "0" : "") + n; };
    var tick = function () {
      if (left <= 0) {
        timer.textContent = "Code expired. Request a new one.";
        timer.classList.add("expired");
        if (verifyBtn) { verifyBtn.disabled = true; verifyBtn.classList.add("disabled"); }
        return false;
      }
      timer.textContent = "Code expires in " + pad(Math.floor(left / 60)) + ":" + pad(left % 60);
      left -= 1; return true;
    };
    if (tick()) { var t = setInterval(function () { if (!tick()) clearInterval(t); }, 1000); }
  }

  // resend cooldown
  var rb = document.getElementById("resend-btn");
  var note = document.getElementById("resend-note");
  if (rb) {
    var wait = parseInt(rb.dataset.wait, 10) || 0;
    var rtick = function () {
      if (wait <= 0) { rb.disabled = false; if (note) note.textContent = ""; return false; }
      rb.disabled = true; if (note) note.textContent = "in " + wait + "s"; wait -= 1; return true;
    };
    if (rtick()) { var r = setInterval(function () { if (!rtick()) clearInterval(r); }, 1000); }
  }
})();
