// Formular- und Galerie-Logik (externe Datei wegen CSP: script-src 'self').
"use strict";

document.addEventListener("click", function (event) {
  var button = event.target.closest("[data-copy-target]");
  if (!button) return;
  var source = document.getElementById(button.getAttribute("data-copy-target"));
  if (!source) return;
  var text = "value" in source ? source.value : source.textContent;
  var done = function () {
    var original = button.textContent;
    button.textContent = "Kopiert ✓";
    setTimeout(function () { button.textContent = original; }, 1500);
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done);
  } else {
    source.focus();
    source.select && source.select();
    document.execCommand("copy");
    done();
  }
});

// ---------- Uhr-Formular: Titel aus Bausteinen ----------
(function () {
  var title = document.querySelector("[data-auto-title]");
  if (!title) return;
  var parts = Array.prototype.slice.call(document.querySelectorAll("[data-title-part]"));
  var byId = {};
  parts.forEach(function (el) { byId[el.id] = el; });

  function compose() {
    var head = [byId.brand && byId.brand.value.trim(), byId.factory && byId.factory.value.trim()]
      .filter(Boolean).join(" ");
    var headline = [head, byId.version && byId.version.value.trim(),
      byId.model && byId.model.value.trim()].filter(Boolean);
    var suffix = [byId.band && byId.band.value.trim(),
      byId.highlight && byId.highlight.value.trim()].filter(Boolean);
    var text = headline.join(" – ");
    if (suffix.length) {
      var tail = suffix.join(", ");
      text = text ? text + ", " + tail : tail;
    }
    return text;
  }

  function refresh() {
    if (title.dataset.auto === "1") title.value = compose();
  }
  title.dataset.auto = (title.value.trim() === "" || title.value.trim() === compose()) ? "1" : "0";
  title.addEventListener("input", function () {
    title.dataset.auto = title.value.trim() === compose() ? "1" : "0";
  });
  parts.forEach(function (el) { el.addEventListener("input", refresh); });
  refresh();
})();

// ---------- Uhr-Formular: USD-Kurs (Vorschlag + EUR-Vorschau) ----------
(function () {
  var usd = document.querySelector("[data-rate-usd]");
  var rate = document.querySelector("[data-rate-field]");
  var fetchBtn = document.querySelector("[data-rate-fetch]");
  var preview = document.querySelector("[data-rate-preview]");
  if (!usd || !rate) return;

  function parseDe(text) {
    var t = String(text || "").trim().replace(/[€$\s]/g, "");
    if (!t) return NaN;
    if (t.indexOf(",") >= 0) t = t.replace(/\./g, "").replace(",", ".");
    return parseFloat(t);
  }
  function updatePreview() {
    if (!preview) return;
    var u = parseDe(usd.value), r = parseDe(rate.value);
    if (!isFinite(u) || !isFinite(r) || u <= 0 || r <= 0) {
      preview.textContent = "—";
      return;
    }
    var eur = u * r;
    preview.textContent = eur.toFixed(2).replace(".", ",") + " € (ca., serverseitig exakt gerundet)";
  }
  [usd, rate].forEach(function (el) { el.addEventListener("input", updatePreview); });

  if (fetchBtn) {
    fetchBtn.addEventListener("click", function () {
      fetchBtn.disabled = true;
      fetch("/api/usd-rate", { headers: { "Accept": "application/json" } })
        .then(function (res) {
          return res.json().then(function (data) {
            if (!res.ok) throw new Error(data.detail || "Kursdienst nicht erreichbar.");
            rate.value = data.rate;
            updatePreview();
          });
        })
        .catch(function (err) {
          window.alert("Kurs konnte nicht geholt werden: " + err.message +
            "\nKurs kann auch manuell eingetragen werden.");
        })
        .then(function () { fetchBtn.disabled = false; });
    });
  }
  updatePreview();
})();

// ---------- Uhr-Formular: Zusatzkosten-Zeilen ----------
(function () {
  var wrap = document.querySelector("[data-cost-rows]");
  var count = document.querySelector("[data-cost-count]");
  var addBtn = document.querySelector("[data-cost-add]");
  if (!wrap || !count) return;

  var CATEGORIES = [
    ["", "– Art wählen –"],
    ["china_logistics", "Versand/Versicherung China"],
    ["de_dhl", "Versand aus Deutschland (DHL)"],
    ["wise", "Wise-Gebühren"],
    ["box", "Box"],
    ["sonstiges", "Sonstiges"],
  ];

  function renumber() {
    var rows = wrap.querySelectorAll(".cost-row");
    rows.forEach(function (row, i) {
      var input = row.querySelector("input");
      var select = row.querySelector("select");
      input.name = "extra_cost_amount_" + i;
      select.name = "extra_cost_category_" + i;
    });
    count.value = String(rows.length);
  }

  function makeRow() {
    var row = document.createElement("div");
    row.className = "cost-row";
    var input = document.createElement("input");
    input.setAttribute("inputmode", "decimal");
    input.placeholder = "z. B. 30,00";
    input.setAttribute("aria-label", "Betrag in Euro");
    var select = document.createElement("select");
    select.setAttribute("aria-label", "Art der Kosten");
    CATEGORIES.forEach(function (pair) {
      var opt = document.createElement("option");
      opt.value = pair[0];
      opt.textContent = pair[1];
      select.appendChild(opt);
    });
    var remove = document.createElement("button");
    remove.type = "button";
    remove.className = "btn btn-xs";
    remove.setAttribute("data-cost-remove", "");
    remove.setAttribute("aria-label", "Position entfernen");
    remove.textContent = "✕";
    row.appendChild(input);
    row.appendChild(select);
    row.appendChild(remove);
    return row;
  }

  if (addBtn) {
    addBtn.addEventListener("click", function () {
      wrap.appendChild(makeRow());
      renumber();
      var last = wrap.querySelector(".cost-row:last-child input");
      if (last) last.focus();
    });
  }
  wrap.addEventListener("click", function (event) {
    var btn = event.target.closest("[data-cost-remove]");
    if (!btn) return;
    btn.closest(".cost-row").remove();
    renumber();
  });
  renumber();
})();

// ---------- Uhr-Formular: Specs-Checkliste ----------
document.addEventListener("change", function (event) {
  var toggle = event.target.closest("[data-spec-toggle]");
  if (!toggle) return;
  var row = toggle.closest(".spec-row");
  var input = row && row.querySelector("[data-spec-input]");
  if (!input) return;
  if (toggle.checked) {
    input.removeAttribute("data-disabled");
    input.focus();
  } else {
    input.setAttribute("data-disabled", "1");
  }
});

// ---------- Status: Verkaufs-Dialog ----------
(function () {
  var dialog = document.querySelector("dialog.sale-dialog");
  var select = document.getElementById("status");
  if (!dialog || !select) return;
  var saleInput = dialog.querySelector("[name='sale_price']");
  var cancelBtn = dialog.querySelector("[data-sale-cancel]");
  var previous = select.value;

  select.addEventListener("change", function () {
    if (select.value === "sold") {
      if (!dialog.open) dialog.showModal();
      if (saleInput) setTimeout(function () { saleInput.focus(); }, 50);
    } else {
      previous = select.value;
    }
  });
  if (cancelBtn) {
    cancelBtn.addEventListener("click", function () {
      dialog.close();
      select.value = previous;
    });
  }
  dialog.addEventListener("close", function () {
    if (select.value === "sold" && !dialog.returnValue) select.value = previous;
  });
})();

// ---------- Fotos: Pfeile & Drag-and-Drop ohne Seitenreload ----------
(function () {
  var grid = document.querySelector(".img-grid");
  if (!grid) return;

  function csrfOf(form) {
    var input = form.querySelector("[name='csrf_token']");
    return input ? input.value : "";
  }
  function refreshMoveButtons() {
    var cards = grid.querySelectorAll(".img-card");
    cards.forEach(function (card, i) {
      var up = card.querySelector("[data-move='up']");
      var down = card.querySelector("[data-move='down']");
      if (up) up.disabled = i === 0;
      if (down) down.disabled = i === cards.length - 1;
      card.draggable = true;
    });
  }
  function fail() {
    window.location.reload();
  }

  // Pfeile: Serverbestätigung, danach nur der Tausch im DOM.
  grid.addEventListener("click", function (event) {
    var btn = event.target.closest("[data-move]");
    if (!btn) return;
    var form = btn.closest("form");
    var card = btn.closest(".img-card");
    if (!form || !card) return;
    event.preventDefault();
    var direction = btn.getAttribute("data-move");
    var sibling = direction === "up" ? card.previousElementSibling
                                     : card.nextElementSibling;
    if (!sibling || !sibling.classList.contains("img-card")) return;
    fetch(form.action, { method: "POST", body: new FormData(form), credentials: "same-origin" })
      .then(function (res) {
        if (!res.ok) throw new Error("HTTP " + res.status);
        if (direction === "up") {
          card.parentNode.insertBefore(card, sibling);
        } else {
          card.parentNode.insertBefore(sibling, card);
        }
        refreshMoveButtons();
      })
      .catch(fail);
  });

  // HTML5-Drag & Drop (Desktop; mobil dienen die Pfeile).
  var dragged = null;
  grid.addEventListener("dragstart", function (event) {
    var card = event.target.closest(".img-card");
    if (!card) return;
    dragged = card;
    event.dataTransfer.effectAllowed = "move";
  });
  grid.addEventListener("dragover", function (event) {
    if (dragged) event.preventDefault();
  });
  grid.addEventListener("drop", function (event) {
    if (!dragged) return;
    event.preventDefault();
    var target = event.target.closest(".img-card");
    if (!target || target === dragged) return;
    var cards = Array.prototype.slice.call(grid.querySelectorAll(".img-card"));
    var from = cards.indexOf(dragged);
    var to = cards.indexOf(target);
    if (from < 0 || to < 0) return;
    var rect = target.getBoundingClientRect();
    var after = event.clientY > rect.top + rect.height / 2;
    grid.insertBefore(dragged, after ? target.nextSibling : target);
    refreshMoveButtons();
    var newIndex = Array.prototype.slice.call(grid.querySelectorAll(".img-card"))
      .indexOf(dragged);
    var id = dragged.getAttribute("data-image-id");
    var csrf = csrfOf(dragged.querySelector("form"));
    fetch("/images/" + id + "/reorder", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: "csrf_token=" + encodeURIComponent(csrf) + "&index=" + newIndex,
    }).then(function (res) {
      if (!res.ok) fail();
    }).catch(fail);
  });
  grid.addEventListener("dragend", function () { dragged = null; });
  refreshMoveButtons();
})();
