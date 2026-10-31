// Kleine Helfer zu HTMX. Alles Wesentliche funktioniert über Formulare und HTMX-Attribute.
(function () {
  "use strict";

  // Bereich ein/aus: zugehörige Felder sperren (gesperrte Felder werden nicht mitgeschickt)
  document.addEventListener("change", function (e) {
    var el = e.target;
    if (el.matches("[data-schaltet]")) {
      var fs = document.getElementById(el.dataset.schaltet);
      if (fs) fs.disabled = !el.checked;
    }
    // Konfidenz für alle Bereiche: setzt die Status-Filter jedes Bereichs auf denselben Wert
    if (el.matches("[data-konfidenz-global]")) {
      document.querySelectorAll('[data-konfidenz="' + el.dataset.konfidenzGlobal + '"]').forEach(function (c) {
        c.checked = el.checked;
      });
    }
  });

  // Phasenwechsel eines Laufs für Screenreader ansagen (nicht jede Sekunde, nur bei Änderung)
  var zuletzt = {};
  document.addEventListener("htmx:afterSettle", function () {
    document.querySelectorAll(".status[data-phase]").forEach(function (s) {
      var phase = s.dataset.phase;
      if (zuletzt[s.id] !== undefined && zuletzt[s.id] !== phase && phase) {
        document.getElementById("ansage").textContent = s.dataset.ansage;
      }
      zuletzt[s.id] = phase;
    });
  });

  // Fehlerantworten des Servers sichtbar machen statt still zu scheitern
  function fehler(text) {
    var leiste = document.getElementById("fehlerleiste");
    leiste.querySelector("span").textContent = text;
    leiste.hidden = false;
  }
  document.addEventListener("htmx:responseError", function (e) {
    fehler((e.detail.xhr.responseText || "").replace(/<[^>]*>/g, " ").trim().slice(0, 300) || ("HTTP " + e.detail.xhr.status));
  });
  document.addEventListener("htmx:sendError", function () {
    fehler(document.getElementById("fehlerleiste").dataset.offline);
  });
  document.addEventListener("click", function (e) {
    if (e.target.matches("#fehlerleiste button")) document.getElementById("fehlerleiste").hidden = true;
  });
})();
