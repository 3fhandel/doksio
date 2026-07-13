(() => {
  const shortcutsElement = document.getElementById("doksio-keyboard-shortcuts");
  if (!shortcutsElement) {
    return;
  }

  let shortcuts = {};
  try {
    shortcuts = JSON.parse(shortcutsElement.textContent || "{}");
  } catch {
    shortcuts = {};
  }

  const shortcutToAction = new Map();
  Object.entries(shortcuts).forEach(([action, shortcut]) => {
    if (shortcut) {
      shortcutToAction.set(shortcut, action);
    }
  });

  function isTypingTarget(target) {
    if (!(target instanceof Element)) {
      return false;
    }
    return Boolean(
      target.closest("input, textarea, select, [contenteditable='true']")
    );
  }

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

  function normalizeEvent(event) {
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

  function isActionElementAvailable(element) {
    if (!(element instanceof HTMLElement)) {
      return false;
    }
    if (element.hidden || element.disabled || element.getAttribute("aria-disabled") === "true") {
      return false;
    }
    return Boolean(element.offsetParent || element.getClientRects().length);
  }

  document.addEventListener("keydown", (event) => {
    if (event.defaultPrevented || isTypingTarget(event.target)) {
      return;
    }
    const shortcut = normalizeEvent(event);
    const action = shortcutToAction.get(shortcut);
    if (!action) {
      return;
    }

    const element = Array.from(
      document.querySelectorAll(`[data-shortcut-action="${CSS.escape(action)}"]`)
    ).find(isActionElementAvailable);
    if (!element) {
      return;
    }
    event.preventDefault();
    element.click();
  });
})();
