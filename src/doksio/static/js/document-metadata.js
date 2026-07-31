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

document.querySelectorAll("[data-metadata-choice-picker]").forEach((picker) => {
  const display = picker.querySelector("[data-metadata-choice-display]");
  const hidden = picker.querySelector("[data-metadata-choice-select]");
  const listId = display?.getAttribute("list");
  const datalist = listId ? document.getElementById(listId) : null;
  if (!display || !hidden || !datalist) {
    return;
  }

  const syncValue = () => {
    const entered = display.value.trim().toLocaleLowerCase("de");
    const match = Array.from(datalist.options).find(
      (option) => option.value.trim().toLocaleLowerCase("de") === entered,
    );
    hidden.value = match ? match.getAttribute("data-choice-value") || "" : "";
    display.setCustomValidity(
      entered && !match ? "Bitte einen Eintrag aus der Liste wählen." : "",
    );
  };

  display.addEventListener("input", syncValue);
  display.addEventListener("change", syncValue);
  display.addEventListener("blur", syncValue);
});
