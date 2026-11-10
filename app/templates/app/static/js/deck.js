/* Landing page: one screen at a time. Clicking a menu link swaps the screen - no page scrolling. */
(function () {
  var slides = Array.prototype.slice.call(document.querySelectorAll(".screen"));
  if (!slides.length) return;
  var byId = {};
  slides.forEach(function (s) { byId[s.id] = s; });
  var links = document.querySelectorAll("[data-go]");

  function show(id, push) {
    if (!byId[id]) id = "top";
    slides.forEach(function (s) { s.classList.toggle("active", s.id === id); });
    byId[id].scrollTop = 0;
    document.body.setAttribute("data-slide", id);
    links.forEach(function (a) {
      if (a.classList.contains("btn") || a.classList.contains("brand")) return;
      a.classList.toggle("active", a.getAttribute("data-go") === id);
    });
    if (push) history.pushState(null, "", id === "top" ? location.pathname : "#" + id);
  }

  document.body.classList.add("deck-on");
  links.forEach(function (a) {
    a.addEventListener("click", function (e) {
      e.preventDefault();
      show(a.getAttribute("data-go"), true);
    });
  });
  window.addEventListener("popstate", function () { show(location.hash.slice(1), false); });
  show(location.hash.slice(1), false);

  // fade out any message (for example "You have been logged out.")
  var fl = document.querySelector(".flashes");
  if (fl) setTimeout(function () { fl.classList.add("fade"); }, 3500);
})();
