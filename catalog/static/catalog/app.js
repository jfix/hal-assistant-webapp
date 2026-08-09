// Progressive enhancement: click a metadata value to edit it in place.
// Saves on blur (click outside / tab away) or Enter; Escape cancels.
// Unchanged values are not submitted, so no no-op decisions are recorded.
// Without JS, the inline form stays visible with a submit button (see <noscript>).
(function () {
  "use strict";
  document.documentElement.classList.add("js");

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

  var uploadForm = document.querySelector("[data-upload-form]");
  if (uploadForm) {
    uploadForm.addEventListener("submit", function () {
      var submit = uploadForm.querySelector("[data-submit]");
      submit.disabled = true;
      submit.textContent = "Analyse en cours…";
    });
  }
})();
