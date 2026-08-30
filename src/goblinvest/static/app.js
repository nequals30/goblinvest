/* All of Goblinvest's JavaScript. Three small things, no dependencies:
 *
 *   1. sortable, optionally paginated tables
 *   2. selects that submit their form on change
 *   3. multi-selects shown as checkbox dropdowns
 *
 * Everything here is progressive enhancement: with JS off or broken, pages
 * still render and every control still works (the pickers just need their
 * submit button, which this script hides).
 */

/* --- 1. tables ------------------------------------------------------------
 *
 * The server renders a complete <table> as ordinary HTML. This script only
 * reorders and shows/hides rows that are already in the document; it never
 * fetches, never builds a row, and never re-renders. With no JavaScript the
 * table still renders, in whatever order the server sorted it.
 *
 * Markup contract:
 *   <table class="grid sortable" data-page-size="100">
 *     <th data-type="text|number|date">   sortable column (omit to opt out)
 *     <td data-sort="-2000.00">           machine-readable value; falls back
 *                                         to the cell's text
 *
 * data-page-size is optional; the pager hides itself when everything fits.
 *
 * **Opening a page sorts it the way the server sent it** — for Month View, by
 * date. The chosen sort is carried across exactly one page load, and only when
 * the load came from a control *inside* the table: a row's form (categorizing)
 * or a link in a cell (the ✎ pencil). Those are one gesture that happens to be
 * a round trip, and losing the sort mid-way made working down a sorted column
 * impossible. Anything else — the nav, the month picker, the filter bar, typing
 * the URL — is arriving at the page, and arrives sorted by date (user's call,
 * 2026-08-27).
 *
 * So the state is *written* when such a control navigates, and read-and-deleted
 * on the next render. Keyed per table: data-sort-key if it has one (Month View
 * sets `month`, so the 400-error re-render at /month/categorize is still the
 * same table), otherwise the path plus the table's position on it.
 */
(function () {
  "use strict";

  var STORE = "gv:sort:";

  function take(key) {
    try {
      var raw = sessionStorage.getItem(STORE + key);
      sessionStorage.removeItem(STORE + key); // one load only
      return JSON.parse(raw);
    } catch (e) {
      return null; // private mode, storage off, or something else's key
    }
  }

  function hold(key, state) {
    try {
      if (state) sessionStorage.setItem(STORE + key, JSON.stringify(state));
      else sessionStorage.removeItem(STORE + key);
    } catch (e) {
      /* not being able to remember is not a reason to stop sorting */
    }
  }

  function cellValue(row, index, type) {
    var cell = row.cells[index];
    if (!cell) return type === "number" ? 0 : "";
    var raw = cell.dataset.sort !== undefined ? cell.dataset.sort : cell.textContent.trim();
    if (type === "number") {
      var n = parseFloat(raw);
      return isNaN(n) ? -Infinity : n;
    }
    return type === "date" ? raw : raw.toLowerCase();
  }

  function setup(table, position) {
    var body = table.tBodies[0];
    if (!body) return;
    var key = table.dataset.sortKey || location.pathname + ":" + position;
    var rows = Array.prototype.slice.call(body.rows);
    // Remembered so an unsorted state and equal keys both stay stable.
    rows.forEach(function (row, i) {
      row._i = i;
    });

    var pageSize = parseInt(table.dataset.pageSize, 10) || 0;
    var page = 0;
    var pager = null;
    if (pageSize > 0 && rows.length > pageSize) {
      pager = document.createElement("div");
      pager.className = "pager";
      pager.innerHTML =
        '<button type="button" data-step="-1">‹ prev</button>' +
        '<span class="pager-status"></span>' +
        '<button type="button" data-step="1">next ›</button>';
      table.parentNode.insertBefore(pager, table.nextSibling);
      pager.addEventListener("click", function (e) {
        var step = e.target.dataset && e.target.dataset.step;
        if (!step) return;
        var last = Math.ceil(rows.length / pageSize) - 1;
        page = Math.min(Math.max(page + parseInt(step, 10), 0), last);
        draw();
      });
    }

    function draw() {
      var start = pager ? page * pageSize : 0;
      var end = pager ? start + pageSize : rows.length;
      var fragment = document.createDocumentFragment();
      rows.forEach(function (row, i) {
        row.hidden = i < start || i >= end;
        fragment.appendChild(row);
      });
      body.appendChild(fragment);

      if (pager) {
        var last = Math.ceil(rows.length / pageSize) - 1;
        pager.querySelector(".pager-status").textContent =
          start + 1 + "–" + Math.min(end, rows.length) + " of " + rows.length;
        pager.querySelectorAll("button").forEach(function (b) {
          b.disabled = b.dataset.step === "-1" ? page === 0 : page >= last;
        });
      }
    }

    var headers = table.tHead ? table.tHead.rows[0].cells : [];

    function sortBy(index, ascending) {
      var th = headers[index];
      if (!th || !th.dataset.type) return false;
      Array.prototype.forEach.call(headers, function (other) {
        if (other.dataset.type) other.setAttribute("aria-sort", "none");
      });
      th.setAttribute("aria-sort", ascending ? "ascending" : "descending");

      var direction = ascending ? 1 : -1;
      rows.sort(function (a, b) {
        var x = cellValue(a, index, th.dataset.type);
        var y = cellValue(b, index, th.dataset.type);
        if (x < y) return -direction;
        if (x > y) return direction;
        return a._i - b._i;
      });
      page = 0;
      draw();
      return true;
    }

    Array.prototype.forEach.call(headers, function (th, index) {
      var type = th.dataset.type;
      if (!type) return;

      // A button rather than a click handler on the <th>, so the column is
      // reachable and operable from the keyboard for free.
      var button = document.createElement("button");
      button.type = "button";
      button.className = "sort-btn";
      button.innerHTML = th.innerHTML;
      th.innerHTML = "";
      th.appendChild(button);
      th.setAttribute("aria-sort", "none");
      // Lets the stylesheet drop the cell's own padding so the button can fill
      // it — the whole header cell becomes the click target.
      th.classList.add("is-sortable");

      button.addEventListener("click", function () {
        var ascending = th.getAttribute("aria-sort") !== "ascending";
        sortBy(index, ascending);
        sorted = { index: index, ascending: ascending };
      });
    });

    // What the table is sorted by right now, or null for the server's order.
    // Nothing is stored until something inside the table navigates.
    var sorted = take(key);
    // A carried-over column can be gone — a table's shape is the server's to
    // change — so fall back to the order it was rendered in.
    if (!sorted || !sortBy(sorted.index, sorted.ascending !== false)) {
      sorted = null;
      if (pager) draw();
    }

    // Submitting a row's form or following a link in a cell is one gesture
    // that happens to reload the page — the sort goes with it. Both events
    // bubble from inside the table, so this is the only place that knows.
    function keep() {
      hold(key, sorted);
    }

    table.addEventListener("submit", keep);
    table.addEventListener("click", function (e) {
      if (e.target.closest && e.target.closest("a")) keep();
    });
  }

  document.querySelectorAll("table.sortable").forEach(setup);
})();

/* --- 2. self-submitting selects -------------------------------------------
 *
 * <select data-autosubmit> picks a month without a second click. The form's
 * fallback button is marked data-nojs and hidden here, so the no-JavaScript
 * path keeps working.
 */
(function () {
  "use strict";

  document.querySelectorAll("select[data-autosubmit]").forEach(function (select) {
    select.addEventListener("change", function () {
      select.form.submit();
    });
  });

  document.querySelectorAll("[data-nojs]").forEach(function (el) {
    el.hidden = true;
  });
})();

/* --- 3. checkbox comboboxes ------------------------------------------------
 *
 * <select multiple data-combo="category"> is drawn as a button and a dropdown
 * of checkboxes. Ticking a box applies it: the form submits after a short
 * pause, so a run of ticks is still one page load, and the menu re-opens on
 * the way back — the table updates underneath it and you carry on ticking.
 * Applying only once the menu was dismissed was the first cut, and it read as
 * nothing happening at all (user, 2026-08-27).
 *
 * The <select> itself stays in the form, hidden, and is what actually carries
 * the values: ticking a box selects its <option>. So with JavaScript off the
 * page keeps the native multi-select and its data-nojs submit button, and the
 * server sees the same request either way.
 *
 * Nothing ticked means no filter, and so does everything ticked — they are the
 * same request. "all" and "none" are still both offered, because "all" is how
 * you say "everything except this one" without ticking fourteen boxes.
 */
(function () {
  "use strict";

  var SETTLE = 250; // ms of quiet before the page load; batches a run of ticks
  var OPEN = "gv:combo:";
  var open = null; // at most one menu is open at a time

  // Which menu was open when the page submitted itself, so it can be put back
  // where it was instead of closing under the user mid-gesture.
  function openKey(select) {
    return OPEN + location.pathname + ":" + select.name;
  }

  function remember(select, isOpen) {
    try {
      if (isOpen) sessionStorage.setItem(openKey(select), "1");
      else sessionStorage.removeItem(openKey(select));
    } catch (e) {
      /* no storage: the menu just doesn't reopen */
    }
  }

  function wasOpen(select) {
    try {
      return sessionStorage.getItem(openKey(select)) === "1";
    } catch (e) {
      return false;
    }
  }

  function picked(select) {
    return Array.prototype.filter.call(select.options, function (option) {
      return option.selected;
    });
  }

  function build(select) {
    var combo = document.createElement("div");
    combo.className = "combo";

    var button = document.createElement("button");
    button.type = "button";
    button.className = "combo-btn";
    button.setAttribute("aria-expanded", "false");

    var menu = document.createElement("div");
    menu.className = "combo-menu";
    menu.hidden = true;

    var boxes = [];
    var settling = null;

    function relabel() {
      var chosen = picked(select);
      var every = chosen.length === select.options.length;
      // Everything ticked excludes nothing, so it reads as "all" — same as
      // nothing ticked, which is the request it makes.
      var summary = "all";
      if (!every) {
        if (chosen.length === 1) summary = chosen[0].value;
        else if (chosen.length > 1) summary = chosen.length + " picked";
      }
      button.textContent = select.dataset.combo + ": " + summary;
      combo.classList.toggle("is-filtered", chosen.length > 0 && !every);
      all.disabled = every;
      none.disabled = chosen.length === 0;
    }

    function setAll(checked) {
      boxes.forEach(function (box, i) {
        box.checked = checked;
        select.options[i].selected = checked;
      });
      apply();
    }

    // The label answers immediately; the page catches up once the ticking
    // stops. One load for "groceries, rent, travel", not three.
    function apply() {
      relabel();
      clearTimeout(settling);
      settling = setTimeout(function () {
        if (select.form) select.form.submit();
      }, SETTLE);
    }

    Array.prototype.forEach.call(select.options, function (option) {
      var row = document.createElement("label");
      var box = document.createElement("input");
      box.type = "checkbox";
      box.checked = option.selected;
      box.addEventListener("change", function () {
        option.selected = box.checked;
        apply();
      });
      row.appendChild(box);
      row.appendChild(document.createTextNode(option.text.trim()));
      menu.appendChild(row);
      boxes.push(box);
    });

    // "all" ticks every box, which excludes nothing — the same view as none
    // ticked. It's there as a *starting point*: tick everything, then untick
    // the one you don't want, rather than ticking fourteen of fifteen by hand
    // (user, 2026-08-28). The settle window means those two clicks are still
    // one page load if they come quickly.
    var tools = document.createElement("div");
    tools.className = "combo-tools";

    var all = document.createElement("button");
    all.type = "button";
    all.textContent = "all";
    all.addEventListener("click", function () {
      setAll(true);
    });

    var none = document.createElement("button");
    none.type = "button";
    none.textContent = "none";
    none.addEventListener("click", function () {
      setAll(false);
    });

    tools.appendChild(all);
    tools.appendChild(none);
    menu.appendChild(tools);

    function show() {
      if (open && open !== hide) open();
      menu.hidden = false;
      button.setAttribute("aria-expanded", "true");
      open = hide;
      remember(select, true);
    }

    function hide() {
      if (menu.hidden) return;
      menu.hidden = true;
      button.setAttribute("aria-expanded", "false");
      open = null;
      remember(select, false);
    }

    button.addEventListener("click", function () {
      if (menu.hidden) show();
      else hide();
    });

    combo.addEventListener("keydown", function (e) {
      if (e.key === "Escape") {
        button.focus();
        hide();
      }
    });

    document.addEventListener("click", function (e) {
      if (!menu.hidden && !combo.contains(e.target)) hide();
    });

    relabel();
    combo.appendChild(button);
    combo.appendChild(menu);
    select.parentNode.insertBefore(combo, select);
    select.hidden = true; // still submitted — it's what holds the values

    if (wasOpen(select)) show();
  }

  document.querySelectorAll("select[multiple][data-combo]").forEach(build);
})();
