(() => {
  function keyFromEvent(event) {
    if (event.code?.startsWith("Key")) {
      return event.code.slice(3).toUpperCase();
    }
    if (event.code?.startsWith("Digit")) {
      return event.code.slice(5);
    }
    if (event.code === "Space") {
      return "Space";
    }
    if (event.code?.startsWith("Arrow")) {
      return event.code;
    }

    let key = event.key;
    if (key === " ") {
      key = "Space";
    } else if (key.length === 1) {
      key = key.toUpperCase();
    }
    return key;
  }

  function normalizeShortcutEvent(event) {
    const parts = [];
    if (event.ctrlKey) {
      parts.push("Ctrl");
    }
    if (event.altKey) {
      parts.push("Alt");
    }
    if (event.shiftKey) {
      parts.push("Shift");
    }
    if (event.metaKey) {
      parts.push("Meta");
    }
    if (!parts.length) {
      return "";
    }

    const key = keyFromEvent(event);
    if (["Control", "Alt", "Shift", "Meta"].includes(key)) {
      return "";
    }
    parts.push(key);
    return parts.join("+");
  }

  document.querySelectorAll("[data-shortcut-capture]").forEach((input) => {
    input.setAttribute("readonly", "readonly");
    input.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        input.blur();
        return;
      }
      if (!event.ctrlKey && !event.altKey && !event.shiftKey && !event.metaKey) {
        if (event.key === "Backspace" || event.key === "Delete") {
          event.preventDefault();
          input.value = "";
        }
        return;
      }

      const shortcut = normalizeShortcutEvent(event);
      if (!shortcut) {
        return;
      }
      event.preventDefault();
      input.value = shortcut;
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });
  });

  document.querySelectorAll("[data-shortcut-clear]").forEach((button) => {
    button.addEventListener("click", () => {
      const input = document.getElementById(button.dataset.shortcutClear);
      if (!input) {
        return;
      }
      input.value = "";
      input.focus();
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });
  });
})();
