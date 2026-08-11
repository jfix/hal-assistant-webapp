// Progressive enhancement: click a metadata value to edit it in place.
// Saves on blur (click outside / tab away) or Enter; Escape cancels.
// Unchanged values are not submitted, so no no-op decisions are recorded.
// Without JS, the inline form stays visible with a submit button (see <noscript>).
(function () {
  "use strict";
  document.documentElement.classList.add("js");

  var greeting = document.querySelector("[data-local-greeting]");
  if (greeting) {
    var localHour = new Date().getHours();
    if (localHour >= 5 && localHour < 12) {
      greeting.textContent = "Bonjour";
    } else if (localHour >= 12 && localHour < 18) {
      greeting.textContent = "Bon après-midi";
    } else {
      greeting.textContent = "Bonsoir";
    }
  }

  function wire(cell) {
    var form = cell.querySelector(".inline-edit");
    var trigger = cell.querySelector(".editable-value");
    if (!form || !trigger) {
      return;
    }
    var input = form.querySelector('[name="edited_value"]');
    if (!input) {
      return;
    }
    var done = false;

    if (form.querySelector("[data-keyword-editor]")) {
      wireKeywords(cell, form, trigger, input);
      return;
    }

    function enter() {
      done = false;
      cell.classList.add("editing");
      input.focus();
      input.select();
    }
    function close() {
      cell.classList.remove("editing");
    }
    function save() {
      if (done) {
        return;
      }
      done = true;
      if (input.value === input.defaultValue) {
        close();
      } else {
        form.submit();
      }
    }
    function cancel() {
      done = true;
      input.value = input.defaultValue;
      close();
      trigger.focus();
    }

    trigger.setAttribute("role", "button");
    trigger.setAttribute("tabindex", "0");
    trigger.title = "Cliquer pour modifier";
    trigger.addEventListener("click", enter);
    trigger.addEventListener("keydown", function (event) {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        enter();
      }
    });
    input.addEventListener("blur", save);
    input.addEventListener("keydown", function (event) {
      if (event.key === "Enter" && input.tagName !== "TEXTAREA") {
        event.preventDefault();
        save();
      } else if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        save();
      } else if (event.key === "Escape") {
        event.preventDefault();
        cancel();
      }
    });
  }

  function wireKeywords(cell, form, trigger, valueInput) {
    var list = form.querySelector("[data-keyword-list]");
    var addInput = form.querySelector("[data-keyword-input]");
    var addButton = form.querySelector("[data-keyword-add]");
    var saveButton = form.querySelector("[data-keyword-save]");
    var cancelButton = form.querySelector("[data-keyword-cancel]");
    var terms = parseTerms(valueInput.defaultValue);

    function parseTerms(value) {
      var seen = {};
      return value.split(";").map(function (term) { return term.trim(); }).filter(function (term) {
        var key = term.toLocaleLowerCase();
        if (!term || seen[key]) return false;
        seen[key] = true;
        return true;
      });
    }
    function sync() {
      valueInput.value = terms.join("; ");
      list.replaceChildren();
      terms.forEach(function (term, index) {
        var pill = document.createElement("span");
        var remove = document.createElement("button");
        pill.className = "keyword-pill";
        pill.append(document.createTextNode(term));
        remove.type = "button";
        remove.textContent = "×";
        remove.setAttribute("aria-label", "Supprimer " + term);
        remove.addEventListener("click", function () {
          terms.splice(index, 1);
          sync();
        });
        pill.append(remove);
        list.append(pill);
      });
    }
    function enter() {
      terms = parseTerms(valueInput.defaultValue);
      sync();
      cell.classList.add("editing");
      addInput.focus();
    }
    function add(refocus) {
      var term = addInput.value.trim();
      if (term && !terms.some(function (item) { return item.toLocaleLowerCase() === term.toLocaleLowerCase(); })) {
        terms.push(term);
        sync();
      }
      addInput.value = "";
      if (refocus !== false) addInput.focus();
    }
    function cancel() {
      terms = parseTerms(valueInput.defaultValue);
      sync();
      cell.classList.remove("editing");
      trigger.focus();
    }

    trigger.setAttribute("role", "button");
    trigger.setAttribute("tabindex", "0");
    trigger.title = "Cliquer pour modifier";
    trigger.addEventListener("click", enter);
    trigger.addEventListener("keydown", function (event) {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        enter();
      }
    });
    addButton.addEventListener("click", function () { add(true); });
    addInput.addEventListener("keydown", function (event) {
      if (event.key === "Enter") {
        event.preventDefault();
        add(true);
      } else if (event.key === "Escape") {
        event.preventDefault();
        cancel();
      }
    });
    saveButton.addEventListener("click", function () {
      add(false);
      if (valueInput.value === valueInput.defaultValue) {
        cell.classList.remove("editing");
      } else {
        form.submit();
      }
    });
    cancelButton.addEventListener("click", cancel);
    sync();
  }

  document.querySelectorAll(".editable-field").forEach(wire);

  var fileInput = document.querySelector("[data-file-input]");
  var dropZone = document.querySelector("[data-drop-zone]");
  var fileLabel = document.querySelector("[data-file-label]");
  if (fileInput && dropZone && fileLabel) {
    function showFile() {
      if (fileInput.files.length) fileLabel.textContent = fileInput.files[0].name;
    }
    fileInput.addEventListener("change", showFile);
    ["dragenter", "dragover"].forEach(function (name) {
      dropZone.addEventListener(name, function () { dropZone.classList.add("dragging"); });
    });
    ["dragleave", "drop"].forEach(function (name) {
      dropZone.addEventListener(name, function () { dropZone.classList.remove("dragging"); });
    });
  }

  document.querySelectorAll("[data-copy-target]").forEach(function (button) {
    button.addEventListener("click", async function () {
      var target = document.getElementById(button.dataset.copyTarget);
      if (!target) return;
      await navigator.clipboard.writeText(target.value);
      var original = button.textContent;
      button.textContent = "Copié ✓";
      setTimeout(function () { button.textContent = original; }, 1400);
    });
  });

  document.querySelectorAll("[data-publication-search]").forEach(function (input) {
    var results = document.querySelector("#" + input.getAttribute("aria-controls"));
    var card = input.closest(".publication-lookup");
    var form = card && card.querySelector("[data-publication-link-form]");
    var idInput = form && form.querySelector("[data-publication-id]");
    var submit = form && form.querySelector("[data-publication-link]");
    var selected = form && form.querySelector("[data-selected-publication]");
    if (!results || !form || !idInput || !submit || !selected) return;

    var timer;
    var controller;

    function closeResults() {
      results.replaceChildren();
      input.setAttribute("aria-expanded", "false");
    }

    function clearSelection() {
      idInput.value = "";
      submit.disabled = true;
      selected.textContent = "Aucune notice sélectionnée.";
    }

    function choose(item) {
      idInput.value = item.id;
      submit.disabled = false;
      input.value = item.title;
      selected.textContent = "Notice sélectionnée : " + item.title;
      closeResults();
      submit.focus();
    }

    function resultButton(item) {
      var button = document.createElement("button");
      var title = document.createElement("strong");
      var meta = document.createElement("span");
      var details = [];
      button.type = "button";
      button.className = "publication-search-result";
      button.setAttribute("role", "option");
      title.textContent = item.title;
      if (item.authors.length) details.push(item.authors.join(", "));
      if (item.year) details.push(String(item.year));
      if (item.hal_type) details.push(item.hal_type);
      if (item.hal_id) details.push(item.hal_id);
      meta.textContent = details.join(" · ");
      button.append(title, meta);
      button.addEventListener("click", function () { choose(item); });
      return button;
    }

    async function search(query) {
      if (controller) controller.abort();
      controller = new AbortController();
      try {
        var response = await fetch(
          input.dataset.searchUrl + "?q=" + encodeURIComponent(query),
          { credentials: "same-origin", signal: controller.signal }
        );
        if (!response.ok) throw new Error("publication search failed");
        var payload = await response.json();
        results.replaceChildren();
        payload.results.forEach(function (item) { results.append(resultButton(item)); });
        if (!payload.results.length) {
          var empty = document.createElement("p");
          empty.className = "subtle";
          empty.textContent = "Aucune notice trouvée.";
          results.append(empty);
        }
        input.setAttribute("aria-expanded", "true");
      } catch (error) {
        if (error.name !== "AbortError") closeResults();
      }
    }

    input.addEventListener("input", function () {
      clearTimeout(timer);
      clearSelection();
      var query = input.value.trim();
      if (query.length < 2) {
        if (controller) controller.abort();
        closeResults();
        return;
      }
      timer = setTimeout(function () { search(query); }, 180);
    });
    input.addEventListener("keydown", function (event) {
      if (event.key === "ArrowDown") {
        var first = results.querySelector("button");
        if (first) {
          event.preventDefault();
          first.focus();
        }
      } else if (event.key === "Escape") {
        closeResults();
      }
    });
    document.addEventListener("click", function (event) {
      if (!card.contains(event.target)) closeResults();
    });
  });

  document.querySelectorAll("[data-manual-publication-form]").forEach(function (form) {
    var typeSelect = form.querySelector("#id_hal_document_type");
    if (!typeSelect) return;

    function showRelevantFields() {
      form.querySelectorAll("[data-hal-fields]").forEach(function (fieldset) {
        var active = fieldset.dataset.halFields === typeSelect.value;
        fieldset.hidden = !active;
        fieldset.querySelectorAll("input, select, textarea").forEach(function (control) {
          control.disabled = !active;
          control.required = active;
        });
      });
    }

    typeSelect.addEventListener("change", showRelevantFields);
    showRelevantFields();
  });

  var uploadForm = document.querySelector("[data-upload-form]");
  if (uploadForm) {
    uploadForm.addEventListener("submit", function () {
      var submit = uploadForm.querySelector("[data-submit]");
      submit.disabled = true;
      submit.textContent = "Analyse en cours…";
    });
  }
})();
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/service-worker.js"));
}

let deferredInstallPrompt = null;
window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault();
  deferredInstallPrompt = event;
  document.querySelectorAll("[data-install-app]").forEach((button) => {
    button.hidden = false;
  });
});

document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-install-app]");
  if (!button || !deferredInstallPrompt) return;
  deferredInstallPrompt.prompt();
  await deferredInstallPrompt.userChoice;
  deferredInstallPrompt = null;
  button.hidden = true;
});
