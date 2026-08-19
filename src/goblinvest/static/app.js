/* All of Goblinvest's JavaScript. Two small things, no dependencies:
 *
 *   1. sortable, optionally paginated tables
 *   2. selects that submit their form on change
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
 */
(function () {
  "use strict";

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

  function setup(table) {
    var body = table.tBodies[0];
    if (!body) return;
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
        Array.prototype.forEach.call(headers, function (other) {
          other.setAttribute("aria-sort", "none");
        });
        th.setAttribute("aria-sort", ascending ? "ascending" : "descending");

        var direction = ascending ? 1 : -1;
        rows.sort(function (a, b) {
          var x = cellValue(a, index, type);
          var y = cellValue(b, index, type);
          if (x < y) return -direction;
          if (x > y) return direction;
          return a._i - b._i;
        });
        page = 0;
        draw();
      });
    });

    if (pager) draw();
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
