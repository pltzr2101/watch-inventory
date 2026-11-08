// Live-Vorschau der Bildkorrekturen: aktualisiert die Vorschau-URL mit Query-Parametern.
"use strict";
(function () {
  var form = document.getElementById("adjust-form");
  var img = document.getElementById("preview-img");
  if (!form || !img) return;

  var brightness = document.getElementById("brightness");
  var contrast = document.getElementById("contrast");
  var rotation = document.getElementById("rotation");
  var outB = document.getElementById("out-brightness");
  var outC = document.getElementById("out-contrast");
  var baseSrc = img.getAttribute("src").split("?")[0];

  function val(input, fallback) {
    var v = parseFloat(input && input.value);
    return isNaN(v) ? fallback : v;
  }

  function update() {
    if (outB) outB.textContent = val(brightness, 1).toFixed(2);
    if (outC) outC.textContent = val(contrast, 1).toFixed(2);
    var params = new URLSearchParams();
    params.set("brightness", val(brightness, 1));
    params.set("contrast", val(contrast, 1));
    params.set("rotation", (rotation && parseInt(rotation.value, 10)) || 0);
    var crop = ["crop_left", "crop_top", "crop_right", "crop_bottom"]
      .map(function (name) { return form.elements[name]; })
      .map(function (el) { return el ? el.value.trim() : ""; });
    if (crop.some(function (v) { return v !== ""; })) {
      params.set("crop", crop.map(function (v) { return v || "0"; }).join(","));
    }
    img.src = baseSrc + "?adjusted=preview&" + params.toString();
  }

  form.addEventListener("input", update);
  form.addEventListener("change", update);
})();
