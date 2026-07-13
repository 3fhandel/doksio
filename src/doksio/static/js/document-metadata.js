document.querySelectorAll("[data-metadata-choice-add]").forEach((button) => {
  button.addEventListener("click", () => {
    const targetId = button.getAttribute("data-metadata-choice-add");
    const input = document.getElementById(targetId);
    if (!input) {
      return;
    }

    const field = input.closest("[data-metadata-choice-field]");
    if (field) {
      field.classList.add("is-visible");
    }
    input.hidden = false;
    input.focus();
  });
});
