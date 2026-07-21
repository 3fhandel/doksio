(() => {
  const lists = document.querySelectorAll("[data-workflow-step-reorder]");

  function updatePositions(list) {
    list.querySelectorAll("[data-step-position]").forEach((position, index) => {
      position.textContent = String(index + 1);
    });
  }

  function updateFlowWraps(visualization) {
    if (!visualization) {
      return;
    }
    visualization
      .querySelectorAll(".workflow-wrap-svg")
      .forEach((svg) => svg.remove());
    const items = [...visualization.querySelectorAll(".workflow-flow-item")];
    items.forEach((item) => {
      item.classList.remove("workflow-flow-item-wraps");
      item.style.removeProperty("--workflow-wrap-return-width");
      item.style.removeProperty("--workflow-wrap-return-y");
    });
    if (window.matchMedia("(max-width: 767.98px)").matches) {
      return;
    }
    const visualizationRect = visualization.getBoundingClientRect();
    const paths = [];
    items.forEach((item, index) => {
      const nextItem = items[index + 1];
      if (!nextItem) {
        return;
      }
      const itemRect = item.getBoundingClientRect();
      const nextRect = nextItem.getBoundingClientRect();
      if (nextRect.top > itemRect.top + 8) {
        const startX = itemRect.right - visualizationRect.left - 4;
        const startY = itemRect.top + itemRect.height / 2 - visualizationRect.top;
        const turnX = visualizationRect.width + 16;
        const returnY = (itemRect.bottom + nextRect.top) / 2 - visualizationRect.top;
        const endX = Math.max(4, nextRect.left - visualizationRect.left - 10);
        item.classList.add("workflow-flow-item-wraps");
        paths.push(`M ${startX} ${startY} H ${turnX} V ${returnY} H ${endX}`);
      }
    });
    if (!paths.length) {
      return;
    }
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.classList.add("workflow-wrap-svg");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("viewBox", `0 0 ${visualizationRect.width} ${visualizationRect.height}`);
    const defs = document.createElementNS("http://www.w3.org/2000/svg", "defs");
    const marker = document.createElementNS("http://www.w3.org/2000/svg", "marker");
    marker.setAttribute("id", "workflow-wrap-arrow");
    marker.setAttribute("viewBox", "0 0 10 10");
    marker.setAttribute("refX", "4");
    marker.setAttribute("refY", "5");
    marker.setAttribute("markerWidth", "5");
    marker.setAttribute("markerHeight", "5");
    marker.setAttribute("orient", "auto-start-reverse");
    const markerPath = document.createElementNS("http://www.w3.org/2000/svg", "path");
    markerPath.setAttribute("d", "M 0 0 L 10 5 L 0 10 z");
    marker.appendChild(markerPath);
    defs.appendChild(marker);
    svg.appendChild(defs);
    paths.forEach((pathData) => {
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", pathData);
      path.setAttribute("marker-end", "url(#workflow-wrap-arrow)");
      svg.appendChild(path);
    });
    visualization.prepend(svg);
  }

  function updateFlowPositions(visualization) {
    if (!visualization) {
      return;
    }
    visualization
      .querySelectorAll("[data-flow-kind='step']")
      .forEach((item, index) => {
        const kicker = item.querySelector("[data-flow-step-kicker]");
        if (!kicker) {
          return;
        }
        kicker.textContent = kicker.textContent.replace(
          /^Schritt\s+\d+/,
          `Schritt ${index + 1}`,
        );
      });
  }

  function syncVisualizationFromList(list) {
    const visualization = document.querySelector("[data-workflow-visualization]");
    if (!visualization) {
      return;
    }
    const finishItem = visualization.querySelector("[data-flow-kind='finish']");
    list.querySelectorAll(".workflow-step-reorder-item").forEach((item) => {
      const flowItem = visualization.querySelector(
        `[data-flow-step-id='${item.dataset.stepId}']`,
      );
      if (flowItem && finishItem) {
        visualization.insertBefore(flowItem, finishItem);
      }
    });
    updateFlowPositions(visualization);
    window.requestAnimationFrame(() => {
      updateFlowWraps(visualization);
      window.requestAnimationFrame(() => updateFlowWraps(visualization));
    });
  }

  function setStatus(list, message, isError = false) {
    const status = list.parentElement.querySelector("[data-workflow-step-reorder-status]");
    if (!status) {
      return;
    }
    status.textContent = message;
    status.classList.toggle("text-danger", isError);
    status.classList.toggle("text-secondary", !isError);
  }

  function itemAfterPointer(list, pointerY) {
    const items = [...list.querySelectorAll(".workflow-step-reorder-item:not(.dragging)")];
    return items.reduce(
      (closest, item) => {
        const box = item.getBoundingClientRect();
        const offset = pointerY - box.top - box.height / 2;
        if (offset < 0 && offset > closest.offset) {
          return { offset, item };
        }
        return closest;
      },
      { offset: Number.NEGATIVE_INFINITY, item: null },
    ).item;
  }

  async function persistOrder(list) {
    const token = list.querySelector("input[name=csrfmiddlewaretoken]")?.value;
    const body = new FormData();
    list.querySelectorAll(".workflow-step-reorder-item").forEach((item) => {
      body.append("step_ids", item.dataset.stepId);
    });

    setStatus(list, "Reihenfolge wird gespeichert ...");
    const response = await fetch(list.dataset.reorderUrl, {
      method: "POST",
      headers: token ? { "X-CSRFToken": token } : {},
      body,
    });
    if (!response.ok) {
      throw new Error("Reorder failed");
    }
    setStatus(list, "Reihenfolge gespeichert.");
  }

  lists.forEach((list) => {
    let initialOrder = "";

    list.addEventListener("dragstart", (event) => {
      const item = event.target.closest(".workflow-step-reorder-item");
      if (!item) {
        return;
      }
      initialOrder = [...list.querySelectorAll(".workflow-step-reorder-item")]
        .map((currentItem) => currentItem.dataset.stepId)
        .join(",");
      item.classList.add("dragging");
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", item.dataset.stepId);
    });

    list.addEventListener("dragover", (event) => {
      event.preventDefault();
      const draggingItem = list.querySelector(".workflow-step-reorder-item.dragging");
      if (!draggingItem) {
        return;
      }
      const nextItem = itemAfterPointer(list, event.clientY);
      if (nextItem) {
        list.insertBefore(draggingItem, nextItem);
      } else {
        list.appendChild(draggingItem);
      }
      updatePositions(list);
      syncVisualizationFromList(list);
    });

    list.addEventListener("dragend", async (event) => {
      const item = event.target.closest(".workflow-step-reorder-item");
      if (!item) {
        return;
      }
      item.classList.remove("dragging");
      const nextOrder = [...list.querySelectorAll(".workflow-step-reorder-item")]
        .map((currentItem) => currentItem.dataset.stepId)
        .join(",");
      if (nextOrder === initialOrder) {
        setStatus(list, "Schritte per Ziehen sortieren.");
        return;
      }
      try {
        await persistOrder(list);
      } catch {
        setStatus(list, "Reihenfolge konnte nicht gespeichert werden.", true);
      }
    });

    syncVisualizationFromList(list);
  });

  window.addEventListener("resize", () => {
    document
      .querySelectorAll("[data-workflow-visualization]")
      .forEach((visualization) => updateFlowWraps(visualization));
  });

  window.addEventListener("load", () => {
    document
      .querySelectorAll("[data-workflow-visualization]")
      .forEach((visualization) => updateFlowWraps(visualization));
  });

  if (document.fonts?.ready) {
    document.fonts.ready.then(() => {
      document
        .querySelectorAll("[data-workflow-visualization]")
        .forEach((visualization) => updateFlowWraps(visualization));
    });
  }
})();
