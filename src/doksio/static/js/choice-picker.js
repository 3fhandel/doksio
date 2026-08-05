(() => {
  const normalize = (value) => value.trim().toLocaleLowerCase("de");

  document.querySelectorAll("[data-choice-picker-panel]").forEach((panel) => {
    const search = panel.querySelector("[data-choice-picker-search]");
    const items = Array.from(panel.querySelectorAll("[data-choice-picker-item]"));
    const count = panel.querySelector("[data-choice-picker-count]");
    if (!search || !count) return;

    const sync = () => {
      const query = normalize(search.value);
      let selected = 0;
      items.forEach((item) => {
        const input = item.querySelector("input");
        item.hidden = Boolean(query) && !item.dataset.choicePickerLabel.includes(query);
        item.classList.toggle("selected", input.checked);
        if (input.checked) selected += 1;
      });
      count.textContent = `${selected} gewählt`;
    };

    panel.addEventListener("change", sync);
    search.addEventListener("input", sync);
    panel.querySelector("[data-choice-picker-select]")?.addEventListener("click", () => {
      items.filter((item) => !item.hidden).forEach((item) => {
        item.querySelector("input").checked = true;
      });
      sync();
    });
    panel.querySelector("[data-choice-picker-clear]")?.addEventListener("click", () => {
      items.filter((item) => !item.hidden).forEach((item) => {
        item.querySelector("input").checked = false;
      });
      sync();
    });
    sync();
  });

  const initCompactPicker = (picker) => {
    if (picker.dataset.choicePickerReady === "true") return;
    const select = picker.querySelector("select");
    const input = picker.querySelector("[data-choice-picker-input]");
    const menu = picker.querySelector("[data-choice-picker-menu]");
    if (!select || !input || !menu) return;

    const options = Array.from(select.options).map((option) => ({
      value: option.value,
      label: option.textContent.trim(),
      empty: option.value === "",
    }));
    const selectWasRequired = select.required;
    select.required = false;
    input.required = selectWasRequired;
    if (select.id && input.id) {
      document.querySelectorAll(`label[for="${select.id}"]`).forEach((label) => {
        label.htmlFor = input.id;
      });
    }
    const initialOption = select.selectedOptions[0];
    input.value = initialOption && (!selectWasRequired || initialOption.value)
      ? initialOption.textContent.trim()
      : "";

    let activeIndex = -1;
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-expanded", "false");
    menu.setAttribute("role", "listbox");

    const optionButtons = () => Array.from(
      menu.querySelectorAll(".choice-picker-option"),
    );
    const setActive = (index) => {
      const buttons = optionButtons();
      if (!buttons.length) {
        activeIndex = -1;
        return;
      }
      activeIndex = (index + buttons.length) % buttons.length;
      buttons.forEach((button, buttonIndex) => {
        const isActive = buttonIndex === activeIndex;
        button.classList.toggle("is-active", isActive);
        button.setAttribute("aria-selected", isActive ? "true" : "false");
      });
      buttons[activeIndex].scrollIntoView({ block: "nearest" });
    };
    const close = () => {
      menu.hidden = true;
      activeIndex = -1;
      input.setAttribute("aria-expanded", "false");
    };
    const choose = (option) => {
      const changed = select.value !== option.value;
      select.value = option.value;
      input.value = selectWasRequired && option.empty ? "" : option.label;
      if (changed) {
        select.dispatchEvent(new Event("change", { bubbles: true }));
      }
      close();
    };
    const render = () => {
      const query = normalize(input.value);
      const matches = options.filter((option) => !query || normalize(option.label).includes(query));
      menu.replaceChildren();
      matches.forEach((option, optionIndex) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "choice-picker-option";
        button.textContent = option.label;
        button.setAttribute("role", "option");
        button.dataset.optionIndex = String(optionIndex);
        button._choicePickerOption = option;
        button.classList.toggle("selected", option.value === select.value);
        button.addEventListener("mouseenter", () => setActive(optionIndex));
        button.addEventListener("mousedown", (event) => {
          event.preventDefault();
          choose(option);
        });
        menu.append(button);
      });
      if (!matches.length) {
        const empty = document.createElement("div");
        empty.className = "choice-picker-empty";
        empty.textContent = picker.dataset.emptyText || "Kein passender Eintrag";
        menu.append(empty);
      }
      menu.hidden = false;
      input.setAttribute("aria-expanded", "true");
      const selectedIndex = matches.findIndex((option) => option.value === select.value);
      setActive(selectedIndex >= 0 ? selectedIndex : 0);
    };

    input.addEventListener("focus", () => {
      input.value = "";
      render();
    });
    input.addEventListener("input", render);
    input.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        close();
        return;
      }
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        if (menu.hidden) {
          input.value = "";
          render();
        }
        setActive(activeIndex + (event.key === "ArrowDown" ? 1 : -1));
        return;
      }
      if (event.key === "Enter") {
        const activeButton = optionButtons()[activeIndex];
        if (!menu.hidden && activeButton?._choicePickerOption) {
          event.preventDefault();
          choose(activeButton._choicePickerOption);
          return;
        }
        const exact = options.find((option) => normalize(option.label) === normalize(input.value));
        if (exact) { event.preventDefault(); choose(exact); }
      }
    });
    input.addEventListener("blur", () => {
      window.setTimeout(() => {
        const exact = options.find((option) => normalize(option.label) === normalize(input.value));
        if (exact) {
          choose(exact);
          return;
        }
        const selected = options.find((option) => option.value === select.value);
        input.value = selected && (!selectWasRequired || selected.value) ? selected.label : "";
        close();
      }, 100);
    });
    select.addEventListener("change", () => {
      const selected = options.find((option) => option.value === select.value);
      input.value = selected && (!selectWasRequired || selected.value) ? selected.label : "";
    });
    input.disabled = select.disabled;
    picker.classList.add("is-ready");
    picker.dataset.choicePickerReady = "true";
  };

  window.DoksioChoicePicker = { init: initCompactPicker };
  document.querySelectorAll("[data-choice-picker]").forEach(initCompactPicker);
})();
