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

// ---------- Uhr-Formular: Kostenpositionen (Kategorie, Währung, Betrag/%) ----------
(function () {
  var wrap = document.querySelector("[data-cost-rows]");
  var count = document.querySelector("[data-cost-count]");
  var addBtn = document.querySelector("[data-cost-add]");
  var insBtn = document.querySelector("[data-cost-insurance]");
  var tpl = document.querySelector("[data-cost-template]");
  var rateInput = document.querySelector('input[name="usd_eur_rate"]');
  if (!wrap || !count) return;

  function parseDe(s) {
    if (!s) return null;
    var t = String(s).trim().replace(/ /g, "");
    if (!t) return null;
    if (t.indexOf(",") >= 0 && t.indexOf(".") >= 0) {
      if (t.lastIndexOf(",") > t.lastIndexOf(".")) t = t.replace(/\./g, "").replace(",", ".");
      else t = t.replace(/,/g, "");
    } else if (t.indexOf(",") >= 0) t = t.replace(",", ".");
    if (!/^\d+(\.\d+)?$/.test(t)) return null;
    return Math.round(parseFloat(t) * 100);
  }
  function parseRate(s) {
    if (!s) return null;
    var t = String(s).trim().replace(",", ".");
    if (!/^\d+(\.\d+)?$/.test(t)) return null;
    return parseFloat(t);
  }
  function fmtCents(cents) {
    var neg = cents < 0;
    var abs = Math.abs(cents);
    return (neg ? "-" : "") + Math.floor(abs / 100) + "," + String(abs % 100).padStart(2, "0");
  }

  function syncMode(row) {
    var pct = row.querySelector(".cost-pct");
    var amount = row.querySelector(".cost-amount");
    var basis = row.querySelector(".cost-basis");
    if (!pct || !amount || !basis) return;
    var hasPct = pct.value.trim() !== "";
    amount.disabled = hasPct;
    basis.disabled = !hasPct;
  }
  function updatePreview(row) {
    var out = row.querySelector(".cost-preview");
    if (!out) return;
    var cur = (row.querySelector(".cost-cur") || {}).value || "EUR";
    var pctEl = row.querySelector(".cost-pct");
    var basisEl = row.querySelector(".cost-basis");
    var amtEl = row.querySelector(".cost-amount");
    var cents = null;
    var parts = [];
    if (pctEl && pctEl.value.trim() && basisEl && basisEl.value.trim()) {
      var b = parseDe(basisEl.value);
      var pp = parseDe(pctEl.value);
      if (b != null && pp != null && pp > 0) {
        cents = Math.round((b * pp) / 10000);
        parts.push(fmtCents(cents) + " " + cur);
      }
    } else if (amtEl && amtEl.value.trim()) {
      cents = parseDe(amtEl.value);
      if (cents != null) parts.push(fmtCents(cents) + " " + cur);
    }
    if (cents != null && cur === "USD" && rateInput) {
      var rate = parseRate(rateInput.value);
      if (rate) parts.push("≈ " + fmtCents(Math.round(cents * rate)) + " €");
    }
    out.textContent = parts.join("  →  ");
  }
  function updateAll() {
    wrap.querySelectorAll(".cost-row").forEach(function (row) {
      syncMode(row);
      updatePreview(row);
    });
  }

  function renumber() {
    var rows = wrap.querySelectorAll(".cost-row");
    rows.forEach(function (row, i) {
      var map = {
        ".cost-cat": "extra_cost_category_",
        ".cost-cur": "extra_cost_currency_",
        ".cost-amount": "extra_cost_amount_",
        ".cost-basis": "extra_cost_basis_",
        ".cost-pct": "extra_cost_pct_",
      };
      Object.keys(map).forEach(function (sel) {
        var el = row.querySelector(sel);
        if (el) el.name = map[sel] + i;
      });
    });
    count.value = String(rows.length);
    updateAll();
  }

  function makeRow() {
    if (!tpl) return null;
    var frag = tpl.content.cloneNode(true);
    return frag.querySelector(".cost-row");
  }

  if (addBtn) {
    addBtn.addEventListener("click", function () {
      var row = makeRow();
      if (!row) return;
      wrap.appendChild(row);
      renumber();
      var first = row.querySelector(".cost-cat");
      if (first) first.focus();
    });
  }
  if (insBtn) {
    insBtn.addEventListener("click", function () {
      var row = makeRow();
      if (!row) return;
      var sel = row.querySelector(".cost-cat");
      if (sel) {
        for (var i = 0; i < sel.options.length; i++) {
          if (sel.options[i].textContent.indexOf("Versicherung") === 0) {
            sel.selectedIndex = i;
            break;
          }
        }
      }
      var pct = row.querySelector(".cost-pct");
      if (pct) pct.value = "6";
      var cur = row.querySelector(".cost-cur");
      if (cur) cur.value = "USD";
      wrap.appendChild(row);
      renumber();
      var basis = row.querySelector(".cost-basis");
      if (basis) basis.focus();
    });
  }
  wrap.addEventListener("click", function (event) {
    var btn = event.target.closest("[data-cost-remove]");
    if (!btn) return;
    btn.closest(".cost-row").remove();
    renumber();
  });
  wrap.addEventListener("input", function (event) {
    var row = event.target.closest(".cost-row");
    if (!row) return;
    syncMode(row);
    updatePreview(row);
  });
  wrap.addEventListener("change", function (event) {
    var row = event.target.closest(".cost-row");
    if (!row) return;
    updatePreview(row);
  });
  if (rateInput) rateInput.addEventListener("input", updateAll);
  var form = wrap.closest("form");
  if (form) form.addEventListener("submit", updateAll);
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
  var scopeSel = dialog.querySelector("[name='sale_scope']");
  var boxFields = dialog.querySelector("[data-box-fields]");
  if (scopeSel && boxFields) {
    var syncBoxFields = function () {
      boxFields.style.display = scopeSel.value === "with_box" ? "" : "none";
    };
    scopeSel.addEventListener("change", syncBoxFields);
    syncBoxFields();
  }
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

/* ===== Box-Einkauf (V5): Währungsumschalter + CNY-Kurs holen ===== */
(function () {
  var cur = document.getElementById("box-cur");
  var group = document.getElementById("box-rate-group");
  var btn = document.getElementById("box-rate-btn");
  var rate = document.getElementById("box-rate");
  if (!cur || !group) return;
  function sync() { group.classList.toggle("wd-hidden", cur.value !== "CNY"); }
  cur.addEventListener("change", sync);
  sync();
  if (btn && rate) {
    btn.addEventListener("click", function () {
      btn.disabled = true;
      btn.textContent = "…";
      fetch("/api/fx-rate?cur=cny")
        .then(function (r) {
          return r.json().then(function (d) {
            if (!r.ok) throw new Error(d.detail || String(r.status));
            return d;
          });
        })
        .then(function (d) { rate.value = d.rate; })
        .catch(function (e) { alert("Kurs nicht verfügbar: " + e.message); })
        .then(function () { btn.disabled = false; btn.textContent = "Kurs holen"; });
    });
  }
})();

/* ===== Auswertung (V5): Live-VK/Margen-Kalkulator ohne Seitenwechsel ===== */
(function () {
  if (!document.getElementById("wd-main")) return;
  var seed = { ek: 0, extra: 0, box: 0 };
  var seedEl = document.getElementById("wd-seed");
  if (seedEl) {
    try { seed = JSON.parse(seedEl.getAttribute("data-seed") || "{}"); }
    catch (e) { /* Default */ }
  }
  function $(id) { return document.getElementById(id); }
  function parseDe(s) {
    if (s == null) return NaN;
    var t = String(s).trim().replace(/\s/g, "");
    if (!t) return NaN;
    if (t.indexOf(",") >= 0 && t.indexOf(".") >= 0) {
      if (t.lastIndexOf(",") > t.lastIndexOf(".")) t = t.replace(/\./g, "").replace(",", ".");
      else t = t.replace(/,/g, "");
    } else if (t.indexOf(",") >= 0) t = t.replace(",", ".");
    return parseFloat(t);
  }
  function eur(n) {
    var neg = n < 0; n = Math.abs(n);
    var w = Math.floor(n), f = Math.round((n - w) * 100);
    if (f === 100) { w += 1; f = 0; }
    var ws = String(w).replace(/\B(?=(\d{3})+(?!\d))/g, ".");
    return (neg ? "−" : "") + ws + "," + String(f).padStart(2, "0") + " €";
  }
  var mode = "marge";
  function setMode(m) {
    mode = m;
    $("wd-modeM").classList.toggle("on", m === "marge");
    $("wd-modeV").classList.toggle("on", m === "vk");
    $("wd-wrapMarge").style.display = m === "marge" ? "" : "none";
    $("wd-wrapVk").style.display = m === "vk" ? "" : "none";
    calc();
  }
  function preset() {
    $("wd-ek").value = eur((seed.ek || 0) / 100);
    $("wd-zus").value = eur((seed.extra || 0) / 100);
    $("wd-boxk").value = eur((seed.box || 0) / 100);
    calc();
  }
  function calc() {
    var ek = parseDe($("wd-ek").value) || 0;
    var ekCur = $("wd-cur") ? $("wd-cur").value : "EUR";
    if (ekCur === "USD") {
      var k = parseDe($("wd-rate") ? $("wd-rate").value : "") || 0;
      if (k > 0) { ek = Math.round(ek * k * 100) / 100; }
      else {
        $("wd-cost").textContent = "–";
        $("wd-cap").textContent = "Netto-EK in USD – Kurs fehlt";
        $("wd-main").textContent = "–";
        $("wd-hint").textContent = "Bitte den USD→EUR-Kurs setzen („Kurs holen“), " +
          "sonst wird der EK nicht umgerechnet.";
        return;
      }
    }
    var zus = parseDe($("wd-zus").value) || 0;
    var boxOn = $("wd-box").checked;
    var box = boxOn ? (parseDe($("wd-boxk").value) || 0) : 0;
    var cost = ek + zus + box;
    var step = parseFloat($("wd-round").value) || 1;
    $("wd-cost").textContent = eur(cost);
    if (mode === "marge") {
      var w = parseDe($("wd-marge").value) || 0;
      var raw = cost + w;
      var vk = step === 1 ? Math.round(raw * 100) / 100 : Math.ceil(raw / step) * step;
      var marge = vk - cost;
      var pct = vk > 0 ? (marge / vk * 100) : 0;
      $("wd-cap").textContent = "Verkaufspreis-Vorschlag (" +
        (step === 1 ? "genau" : step + "er-Rundung") + ")";
      $("wd-main").textContent = eur(vk);
      $("wd-marge-out").textContent = eur(marge);
      $("wd-marge-out").className = marge >= 0 ? "wd-ok" : "wd-warn";
      $("wd-pct").textContent = pct.toFixed(1).replace(".", ",") + " %";
      $("wd-hint").textContent = step === 1
        ? "Exakter VK für Wunschmarge " + eur(w) + "."
        : "Gerundet aufwärts auf die " + step + " → echte Marge " + eur(marge) +
          " statt " + eur(w) + ". Ohne Rundung: " + eur(raw) + ".";
    } else {
      var vk2 = parseDe($("wd-vk").value) || 0;
      var m = vk2 - cost;
      var p = vk2 > 0 ? (m / vk2 * 100) : 0;
      $("wd-cap").textContent = "Marge bei VK " + eur(vk2);
      $("wd-main").textContent = eur(m);
      $("wd-main").style.color = m >= 0 ? "#5fc98a" : "#e0716c";
      $("wd-marge-out").textContent = eur(m);
      $("wd-marge-out").className = m >= 0 ? "wd-ok" : "wd-warn";
      $("wd-pct").textContent = p.toFixed(1).replace(".", ",") + " %";
      $("wd-hint").textContent = "Zum Vergleich: bei 150 € Marge müsste der VK " +
        eur(cost + 150) + " lauten" +
        (step > 1 ? " (gerundet " + eur(Math.ceil((cost + 150) / step) * step) + ")" : "") + ".";
    }
  }
  $("wd-modeM").addEventListener("click", function () { setMode("marge"); });
  $("wd-modeV").addEventListener("click", function () { setMode("vk"); });
  $("wd-preset").addEventListener("click", preset);
  ["wd-ek", "wd-zus", "wd-boxk", "wd-marge", "wd-vk"].forEach(function (id) {
    $(id).addEventListener("input", calc);
  });
  $("wd-box").addEventListener("change", calc);
  $("wd-round").addEventListener("change", calc);
  if ($("wd-cur")) {
    $("wd-cur").addEventListener("change", function () {
      var g = $("wd-rate-group");
      if (g) g.classList.toggle("wd-hidden", $("wd-cur").value !== "USD");
      calc();
    });
  }
  if ($("wd-rate")) $("wd-rate").addEventListener("input", calc);
  if ($("wd-rate-btn")) {
    $("wd-rate-btn").addEventListener("click", function () {
      var b = $("wd-rate-btn");
      b.disabled = true; b.textContent = "…";
      fetch("/api/fx-rate?cur=usd")
        .then(function (r) {
          return r.json().then(function (d) {
            if (!r.ok) throw new Error(d.detail || String(r.status));
            return d;
          });
        })
        .then(function (d) { $("wd-rate").value = d.rate; calc(); })
        .catch(function (e) { alert("Kurs nicht verfügbar: " + e.message); })
        .then(function () { b.disabled = false; b.textContent = "Kurs holen"; });
    });
  }
  preset();
  setMode("marge");
})();

/* ===== Bestätigungsdialoge per data-confirm (CSP-konform, delegiert) ===== */
document.addEventListener("submit", function (e) {
  var f = e.target;
  if (f && f.dataset && f.dataset.confirm && !window.confirm(f.dataset.confirm)) {
    e.preventDefault();
  }
});
