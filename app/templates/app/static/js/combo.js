/* Searchable dropdown: turns <select data-combo> into a type-to-search box.
   The real <select> stays in the page (hidden) so the form still submits normally. */
(function () {
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function Combo(select) {
    var wrap = el("div", "combo");
    var input = el("input", "combo-input");
    input.type = "text";
    input.autocomplete = "off";
    input.spellcheck = false;
    input.id = select.id + "-combo";
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-expanded", "false");
    input.setAttribute("aria-autocomplete", "list");

    var arrow = el("span", "combo-arrow");
    arrow.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>';
    var list = el("ul", "combo-list");
    list.hidden = true;
    list.setAttribute("role", "listbox");

    select.parentNode.insertBefore(wrap, select);
    wrap.appendChild(select);
    wrap.appendChild(input);
    wrap.appendChild(arrow);
    wrap.appendChild(list);
    select.classList.add("combo-hidden");
    select.tabIndex = -1;

    var label = document.querySelector('label[for="' + select.id + '"]');
    if (label) label.setAttribute("for", input.id);

    var items = [];
    var active = -1;
    var isOpen = false;

    function placeholder() {
      var o = select.options[0];
      return o && o.value === "" ? o.text : "Select";
    }
    function selectedText() {
      var o = select.options[select.selectedIndex];
      return o && o.value !== "" ? o.text : "";
    }
    function sync() {
      input.value = selectedText();
      input.placeholder = placeholder();
    }
    function readOptions() {
      var out = [];
      Array.prototype.forEach.call(select.children, function (node) {
        if (node.tagName === "OPTGROUP") {
          Array.prototype.forEach.call(node.children, function (o) {
            out.push({ value: o.value, text: o.text, group: node.label });
          });
        } else if (node.tagName === "OPTION" && node.value !== "") {
          out.push({ value: node.value, text: node.text, group: null });
        }
      });
      return out;
    }
    function setActive(i) {
      items.forEach(function (li) { li.classList.remove("active"); });
      active = i;
      if (i >= 0 && items[i]) {
        items[i].classList.add("active");
        items[i].scrollIntoView({ block: "nearest" });
      }
    }
    function render(q) {
      q = (q || "").trim().toLowerCase();
      list.innerHTML = "";
      items = [];
      var lastGroup = null;
      var selectedIdx = -1;
      readOptions().forEach(function (o) {
        // match the option text, or the college code (the part before " - " in the group name)
        var hay = (o.text + " " + (o.group ? o.group.split(" - ")[0] : "")).toLowerCase();
        if (q && hay.indexOf(q) === -1) return;
        if (o.group !== lastGroup) {
          if (o.group) list.appendChild(el("li", "combo-group", o.group));
          lastGroup = o.group;
        }
        var li = el("li", "combo-item", o.text);
        li.setAttribute("role", "option");
        li.dataset.value = o.value;
        if (o.value === select.value) { li.classList.add("selected"); selectedIdx = items.length; }
        list.appendChild(li);
        items.push(li);
      });
      if (!items.length) list.appendChild(el("li", "combo-empty", "No results"));
      setActive(items.length ? (q || selectedIdx < 0 ? 0 : selectedIdx) : -1);
    }
    function openList(filter) {
      render(filter || "");
      list.hidden = false;
      isOpen = true;
      input.setAttribute("aria-expanded", "true");
    }
    function closeList() {
      list.hidden = true;
      isOpen = false;
      input.setAttribute("aria-expanded", "false");
      sync();
    }
    function choose(value) {
      select.value = value;
      select.dispatchEvent(new Event("change", { bubbles: true }));
      closeList();
    }

    input.addEventListener("focus", function () { openList(""); input.select(); });
    input.addEventListener("click", function () { if (!isOpen) openList(""); });
    input.addEventListener("input", function () {
      if (!isOpen) { list.hidden = false; isOpen = true; input.setAttribute("aria-expanded", "true"); }
      render(input.value);
    });
    input.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        if (!isOpen) openList(""); else setActive(Math.min(active + 1, items.length - 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        if (isOpen) setActive(Math.max(active - 1, 0));
      } else if (e.key === "Enter") {
        if (isOpen && active >= 0 && items[active]) {
          e.preventDefault();
          choose(items[active].dataset.value);
        }
      } else if (e.key === "Escape") {
        closeList();
      } else if (e.key === "Tab") {
        closeList();
      }
    });
    input.addEventListener("blur", closeList);
    list.addEventListener("mousedown", function (e) {
      e.preventDefault(); // keep focus in the input
      var li = e.target.closest(".combo-item");
      if (li) choose(li.dataset.value);
    });

    select._combo = { sync: sync };
    sync();
  }

  document.querySelectorAll("select[data-combo]").forEach(function (s) { if (!s._combo) new Combo(s); });
})();
