(() => {
  const workspace = document.querySelector("[data-import-batch-workspace]");
  if (!workspace) {
    return;
  }

  const preview = workspace.querySelector("[data-import-batch-preview]");
  const previewTitle = preview?.querySelector("[data-preview-title]");
  const previewBody = preview?.querySelector("[data-preview-body]");
  const previewOpen = preview?.querySelector("[data-preview-open]");
  const csrfToken = workspace.querySelector(
    "input[name='csrfmiddlewaretoken']",
  )?.value;
  const pendingSaves = new WeakMap();

  const showPreview = (trigger) => {
    if (!preview || !previewTitle || !previewBody || !previewOpen) {
      return;
    }
    const source = trigger.dataset.previewUrl;
    const kind = trigger.dataset.previewKind;
    const title = trigger.dataset.previewTitle;

    previewTitle.textContent = title;
    previewOpen.href = source;
    previewOpen.hidden = false;
    previewBody.replaceChildren();

    if (kind === "pdf") {
      const frame = document.createElement("iframe");
      frame.className = "import-batch-preview-frame";
      frame.src = source;
      frame.title = `Vorschau ${title}`;
      previewBody.append(frame);
    } else if (kind === "image") {
      const image = document.createElement("img");
      image.className = "import-batch-preview-image";
      image.src = source;
      image.alt = title;
      previewBody.append(image);
    } else {
      const empty = document.createElement("div");
      empty.className = "document-preview-empty";
      empty.textContent = "Keine Inline-Vorschau für diesen Dateityp verfügbar.";
      previewBody.append(empty);
    }

    workspace.querySelectorAll("[data-import-batch-preview-trigger]")
      .forEach((button) => {
        const selected = button === trigger;
        button.setAttribute("aria-pressed", selected ? "true" : "false");
        button.closest("[data-import-batch-item]")?.classList.toggle(
          "import-batch-item-selected",
          selected,
        );
      });
  };

  const setSaveState = (row, state, message) => {
    const indicator = row.querySelector("[data-assignment-state]");
    if (!indicator) {
      return;
    }
    indicator.className = `import-batch-save-state import-batch-save-state-${state}`;
    indicator.textContent = message;
  };

  const saveAssignment = async (row) => {
    const select = row.querySelector("select");
    const checkbox = row.querySelector("input[type='checkbox']");
    if (!select || !checkbox || !csrfToken) {
      return;
    }

    pendingSaves.get(row)?.abort();
    const controller = new AbortController();
    pendingSaves.set(row, controller);
    setSaveState(row, "saving", "Wird gespeichert...");
    const payload = new FormData();
    payload.append(select.name, select.value);
    if (checkbox.checked) {
      payload.append(checkbox.name, checkbox.value || "on");
    }

    try {
      const response = await fetch(row.dataset.assignmentUrl, {
        method: "POST",
        body: payload,
        headers: {
          "X-CSRFToken": csrfToken,
          "X-Requested-With": "XMLHttpRequest",
        },
        signal: controller.signal,
      });
      const result = await response.json();
      if (!response.ok) {
        throw new Error(result.message || "Zuordnung konnte nicht gespeichert werden.");
      }

      row.classList.toggle("import-batch-item-assigned", result.assigned);
      row.classList.toggle(
        "import-batch-item-pending",
        !result.assigned && !result.skipped,
      );
      row.classList.toggle("import-batch-item-skipped", result.skipped);
      if (result.assigned || result.skipped) {
        setSaveState(row, "saved", "Gespeichert");
      } else {
        setSaveState(row, "pending", "Offen");
      }

      const resultCell = row.querySelector("[data-assignment-result]");
      if (resultCell) {
        if (result.skipped) {
          resultCell.textContent = "Manuell übersprungen";
        } else if (result.target_label) {
          resultCell.textContent = `Ziel: ${result.target_label}`;
        } else {
          resultCell.textContent = "Bitte Zielbox wählen";
        }
      }
    } catch (error) {
      if (error.name === "AbortError") {
        return;
      }
      setSaveState(row, "error", error.message);
    } finally {
      if (pendingSaves.get(row) === controller) {
        pendingSaves.delete(row);
      }
    }
  };

  workspace.addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-import-batch-preview-trigger]");
    if (trigger) {
      showPreview(trigger);
    }
  });

  workspace.addEventListener("change", (event) => {
    const row = event.target.closest("[data-import-batch-item]");
    if (row && event.target.matches("select, input[type='checkbox']")) {
      saveAssignment(row);
    }
  });
})();
