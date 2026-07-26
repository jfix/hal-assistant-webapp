// Progressive enhancement: click a metadata value to edit it in place.
// Saves on blur (click outside / tab away) or Enter; Escape cancels.
// Unchanged values are not submitted, so no no-op decisions are recorded.
// Without JS, the inline form stays visible with a submit button (see <noscript>).
(function () {
  "use strict";

  function wire(cell) {
    var form = cell.querySelector(".inline-edit");
    var trigger = cell.querySelector(".editable-value");
    if (!form || !trigger) {
      return;
    }
    var input = form.querySelector('input[name="edited_value"]');
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
      if (event.key === "Enter") {
        event.preventDefault();
        save();
      } else if (event.key === "Escape") {
        event.preventDefault();
        cancel();
      }
    });
  }

  document.querySelectorAll(".editable-field").forEach(wire);
})();
