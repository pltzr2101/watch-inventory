// Clipboard- und UI-Hilfen (externe Datei wegen CSP: script-src 'self').
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
