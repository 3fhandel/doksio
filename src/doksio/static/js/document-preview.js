function initPreview(root) {
    initReviewAssist(root);
    const advancedReviewAssist = initAdvancedReviewAssist(root);

    const stage = root.querySelector(".document-preview-stage");
    const pages = root.querySelector("[data-pdf-pages]");
    const status = root.querySelector("[data-pdf-status]");
    const pageCurrent = root.querySelector("[data-pdf-page-current]");
    const pageTotal = root.querySelector("[data-pdf-page-total]");
    const zoomOutButton = root.querySelector("[data-pdf-zoom-out]");
    const zoomInButton = root.querySelector("[data-pdf-zoom-in]");
    const rotateLeftButton = root.querySelector("[data-viewer-rotate-left]");
    const rotateRightButton = root.querySelector("[data-viewer-rotate-right]");
    const pdfUrl = root.dataset.pdfUrl;

    if (!stage || !pages || !pdfUrl || !window.pdfjsLib) {
      if (status) {
        status.textContent = "PDF.js konnte nicht geladen werden.";
      }
      return;
    }

    let pdfDocument = null;
    let pageNumber = 1;
    let scale = 1.2;
    const defaultRotation = viewerRotation(root);
    const pageRotations = viewerPageRotations(root);
    let generation = 0;
    let pageObserver = null;
    const renderTasks = new Set();

    function setStatus(message) {
      if (status) {
        status.textContent = message;
      }
    }

    function updatePageIndicator() {
      if (!pdfDocument) {
        return;
      }
      pageCurrent.textContent = pageNumber;
      pageTotal.textContent = pdfDocument.numPages;
    }

    async function renderPage(pageElement, expectedGeneration) {
      if (
        !pdfDocument
        || expectedGeneration !== generation
        || pageElement.dataset.rendered === "true"
        || pageElement.dataset.rendering === "true"
      ) {
        return;
      }
      pageElement.dataset.rendering = "true";
      const number = Number(pageElement.dataset.pageNumber);
      const canvas = pageElement.querySelector("canvas");
      const context = canvas.getContext("2d");
      const page = await pdfDocument.getPage(number);
      const rotation = rotationForPage(number);
      const viewport = page.getViewport({ scale, rotation });
      const outputScale = window.devicePixelRatio || 1;

      canvas.width = Math.floor(viewport.width * outputScale);
      canvas.height = Math.floor(viewport.height * outputScale);
      canvas.style.width = `${Math.floor(viewport.width)}px`;
      canvas.style.height = `${Math.floor(viewport.height)}px`;

      const renderTask = page.render({
        canvasContext: context,
        transform: outputScale !== 1 ? [outputScale, 0, 0, outputScale, 0, 0] : null,
        viewport,
      });
      renderTasks.add(renderTask);

      try {
        await renderTask.promise;
        if (expectedGeneration === generation) {
          pageElement.dataset.rendered = "true";
        }
      } catch (error) {
        if (error && error.name !== "RenderingCancelledException") {
          setStatus("Vorschau konnte nicht gerendert werden.");
        }
      } finally {
        renderTasks.delete(renderTask);
        delete pageElement.dataset.rendering;
      }
    }

    function updateCurrentPage() {
      const pageElements = [...pages.querySelectorAll("[data-page-number]")];
      if (!pageElements.length) {
        return;
      }
      const stageRect = stage.getBoundingClientRect();
      const viewportCenter = stageRect.top + stage.clientHeight / 2;
      let nearestPage = pageElements[0];
      let nearestDistance = Number.POSITIVE_INFINITY;
      pageElements.forEach(function (pageElement) {
        const rect = pageElement.getBoundingClientRect();
        const distance = Math.abs((rect.top + rect.bottom) / 2 - viewportCenter);
        if (distance < nearestDistance) {
          nearestDistance = distance;
          nearestPage = pageElement;
        }
      });
      pageNumber = Number(nearestPage.dataset.pageNumber);
      updatePageIndicator();
    }

    async function rebuildPages(restoreCurrentPage) {
      if (!pdfDocument) {
        return;
      }
      const targetPage = pageNumber;
      generation += 1;
      const expectedGeneration = generation;
      renderTasks.forEach(function (task) {
        task.cancel();
      });
      renderTasks.clear();
      if (pageObserver) {
        pageObserver.disconnect();
      }
      pages.replaceChildren();
      setStatus("Seiten werden vorbereitet ...");

      pageObserver = new IntersectionObserver(
        function (entries) {
          entries.forEach(function (entry) {
            if (entry.isIntersecting) {
              renderPage(entry.target, expectedGeneration);
            }
          });
        },
        { root: stage, rootMargin: "800px 0px" },
      );

      for (let number = 1; number <= pdfDocument.numPages; number += 1) {
        const page = await pdfDocument.getPage(number);
        if (expectedGeneration !== generation) {
          return;
        }
        const viewport = page.getViewport({
          scale,
          rotation: rotationForPage(number),
        });
        const pageElement = document.createElement("div");
        pageElement.className = "document-preview-page";
        pageElement.dataset.pageNumber = String(number);
        pageElement.dataset.pageRotation = String(rotationForPage(number));
        pageElement.style.width = `${Math.floor(viewport.width)}px`;
        pageElement.style.height = `${Math.floor(viewport.height)}px`;
        const canvas = document.createElement("canvas");
        canvas.className = "document-preview-canvas";
        canvas.setAttribute("aria-label", `PDF-Seite ${number}`);
        pageElement.appendChild(canvas);
        pages.appendChild(pageElement);
        advancedReviewAssist?.decorateTarget(pageElement, number);
        pageObserver.observe(pageElement);
      }

      if (restoreCurrentPage) {
        const target = pages.querySelector(`[data-page-number="${targetPage}"]`);
        if (target) {
          stage.scrollTop = Math.max(0, target.offsetTop - 16);
        }
      }
      setStatus("");
      updateCurrentPage();
    }

    function rotationForPage(number) {
      const configuredRotation = pageRotations[String(number)];
      return configuredRotation === undefined
        ? defaultRotation
        : configuredRotation;
    }

    zoomOutButton.addEventListener("click", function () {
      scale = Math.max(0.6, scale - 0.2);
      rebuildPages(true);
    });

    zoomInButton.addEventListener("click", function () {
      scale = Math.min(2.4, scale + 0.2);
      rebuildPages(true);
    });

    function rotateBy(delta) {
      const rotation = normalizeRotation(rotationForPage(pageNumber) + delta);
      pageRotations[String(pageNumber)] = rotation;
      persistViewerRotation(root, rotation, pageNumber);
      rebuildPages(true);
    }

    if (rotateLeftButton) {
      rotateLeftButton.addEventListener("click", function () {
        rotateBy(-90);
      });
    }
    if (rotateRightButton) {
      rotateRightButton.addEventListener("click", function () {
        rotateBy(90);
      });
    }

    setStatus("Vorschau wird geladen ...");
    window.pdfjsLib.GlobalWorkerOptions.workerSrc = root.dataset.pdfWorkerUrl;
    window.pdfjsLib.getDocument({ url: pdfUrl, withCredentials: true }).promise
      .then(function (loadedDocument) {
        pdfDocument = loadedDocument;
        updatePageIndicator();
        rebuildPages(false);
      })
      .catch(function () {
        setStatus("PDF-Vorschau konnte nicht geladen werden.");
      });

    let scrollFrame = null;
    stage.addEventListener("scroll", function () {
      if (scrollFrame !== null) {
        return;
      }
      scrollFrame = window.requestAnimationFrame(function () {
        scrollFrame = null;
        updateCurrentPage();
      });
    });
}

function initImagePreview(root) {
  initReviewAssist(root);
  const advancedReviewAssist = initAdvancedReviewAssist(root);

  const stage = root.querySelector("[data-image-stage]");
  const frame = root.querySelector("[data-image-frame]");
  const image = root.querySelector("[data-image-preview-img]");
  const fitButton = root.querySelector("[data-image-fit]");
  const zoomOutButton = root.querySelector("[data-image-zoom-out]");
  const zoomInButton = root.querySelector("[data-image-zoom-in]");
  const rotateLeftButton = root.querySelector("[data-viewer-rotate-left]");
  const rotateRightButton = root.querySelector("[data-viewer-rotate-right]");
  const zoomLabel = root.querySelector("[data-image-zoom-label]");

  if (!stage || !frame || !image) {
    return;
  }

  let scale = 1;
  let isFitMode = true;
  let rotation = viewerRotation(root);

  function naturalWidth() {
    return image.naturalWidth || 1;
  }

  function naturalHeight() {
    return image.naturalHeight || 1;
  }

  function isSideways() {
    return rotation === 90 || rotation === 270;
  }

  function visualWidth() {
    return isSideways() ? naturalHeight() : naturalWidth();
  }

  function visualHeight() {
    return isSideways() ? naturalWidth() : naturalHeight();
  }

  function calculateFitScale() {
    const stageRect = stage.getBoundingClientRect();
    const rootRect = root.getBoundingClientRect();
    const availableWidth = Math.max(
      Math.round((stageRect.width || rootRect.width) - 32),
      1
    );
    const availableHeight = Math.max(
      Math.round((stageRect.height || window.innerHeight * 0.7) - 32),
      1
    );
    return Math.min(
      1,
      availableWidth / visualWidth(),
      availableHeight / visualHeight()
    );
  }

  function updateLabel() {
    if (!zoomLabel) {
      return;
    }
    zoomLabel.textContent = isFitMode ? "Fit" : `${Math.round(scale * 100)}%`;
  }

  function applyScale() {
    const imageWidth = Math.max(Math.round(naturalWidth() * scale), 1);
    const imageHeight = Math.max(Math.round(naturalHeight() * scale), 1);
    image.style.width = `${imageWidth}px`;
    image.style.height = `${imageHeight}px`;
    image.style.transform = `rotate(${rotation}deg)`;
    frame.style.width = `${Math.max(Math.round(visualWidth() * scale), 1)}px`;
    frame.style.height = `${Math.max(Math.round(visualHeight() * scale), 1)}px`;
    frame.dataset.pageRotation = String(rotation);
    advancedReviewAssist?.decorateTarget(frame, 1);
    advancedReviewAssist?.refreshTarget(frame);
    updateLabel();
  }

  function fitToView() {
    scale = calculateFitScale();
    isFitMode = true;
    applyScale();
  }

  function zoomBy(delta) {
    isFitMode = false;
    scale = Math.min(4, Math.max(0.1, scale + delta));
    applyScale();
  }

  if (fitButton) {
    fitButton.addEventListener("click", fitToView);
  }
  if (zoomOutButton) {
    zoomOutButton.addEventListener("click", function () {
      zoomBy(-0.1);
    });
  }
  if (zoomInButton) {
    zoomInButton.addEventListener("click", function () {
      zoomBy(0.1);
    });
  }

  function rotateBy(delta) {
    rotation = normalizeRotation(rotation + delta);
    persistViewerRotation(root, rotation);
    if (isFitMode) {
      fitToView();
    } else {
      applyScale();
    }
  }

  if (rotateLeftButton) {
    rotateLeftButton.addEventListener("click", function () {
      rotateBy(-90);
    });
  }
  if (rotateRightButton) {
    rotateRightButton.addEventListener("click", function () {
      rotateBy(90);
    });
  }

  image.addEventListener("load", fitToView);
  window.addEventListener("resize", function () {
    if (isFitMode) {
      fitToView();
    }
  });

  if (image.complete) {
    fitToView();
  }
}

function normalizeRotation(rotation) {
  return ((rotation % 360) + 360) % 360;
}

function viewerRotation(root) {
  const parsedRotation = Number.parseInt(root.dataset.viewerRotation || "0", 10);
  if (![0, 90, 180, 270].includes(parsedRotation)) {
    return 0;
  }
  return parsedRotation;
}

function viewerPageRotations(root) {
  const dataElement = root.querySelector("#pdf-page-rotations");
  if (!dataElement) {
    return {};
  }
  try {
    const rawRotations = JSON.parse(dataElement.textContent);
    return Object.fromEntries(
      Object.entries(rawRotations).filter(function ([pageNumber, rotation]) {
        return Number.parseInt(pageNumber, 10) > 0
          && [0, 90, 180, 270].includes(rotation);
      })
    );
  } catch (_error) {
    return {};
  }
}

function csrfToken() {
  const tokenInput = document.querySelector("[name=csrfmiddlewaretoken]");
  if (tokenInput) {
    return tokenInput.value;
  }
  const cookieMatch = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
  return cookieMatch ? decodeURIComponent(cookieMatch[1]) : "";
}

function persistViewerRotation(root, rotation, pageNumber = null) {
  if (pageNumber === null) {
    root.dataset.viewerRotation = String(rotation);
  }
  if (!root.dataset.viewerSettingsUrl) {
    return;
  }
  fetch(root.dataset.viewerSettingsUrl, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": csrfToken(),
    },
    body: JSON.stringify({
      rotation,
      ...(pageNumber === null ? {} : { page_number: pageNumber }),
    }),
  }).catch(function () {
    root.dataset.viewerRotationSaveFailed = "true";
  });
}

function initAdvancedReviewAssist(root) {
  if (!root.hasAttribute("data-advanced-review-assist")) {
    return null;
  }

  const palette = root.querySelector("[data-review-marker-palette]");
  const sizeLabel = root.querySelector("[data-review-marker-size]");
  const sizeDecreaseButton = root.querySelector("[data-review-size-decrease]");
  const sizeIncreaseButton = root.querySelector("[data-review-size-increase]");
  const markerData = root.querySelector("#document-review-markers");
  const createUrl = root.dataset.reviewMarkerCreateUrl;
  const deleteUrl = root.dataset.reviewMarkerDeleteUrl;
  if (!palette || !markerData || !createUrl || !deleteUrl) {
    return null;
  }

  let markers = [];
  try {
    markers = JSON.parse(markerData.textContent);
  } catch (_error) {
    markers = [];
  }

  let selectedSymbol = null;
  const baseSize = 0.045;
  const sizeStep = baseSize * 0.25;
  const minimumSize = baseSize * 0.5;
  const maximumSize = baseSize * 3;
  let selectedSize = Number.parseFloat(
    window.localStorage.getItem("doksio-review-marker-size") || "0.045",
  );
  if (!Number.isFinite(selectedSize)) {
    selectedSize = baseSize;
  }
  selectedSize = Math.min(
    maximumSize,
    Math.max(
      minimumSize,
      Math.round(selectedSize / sizeStep) * sizeStep,
    ),
  );

  function rotationForTarget(target) {
    return normalizeRotation(
      Number.parseInt(target.dataset.pageRotation || "0", 10),
    );
  }

  function canonicalToDisplay(x, y, rotation) {
    if (rotation === 90) {
      return { x: 1 - y, y: x };
    }
    if (rotation === 180) {
      return { x: 1 - x, y: 1 - y };
    }
    if (rotation === 270) {
      return { x: y, y: 1 - x };
    }
    return { x, y };
  }

  function displayToCanonical(x, y, rotation) {
    if (rotation === 90) {
      return { x: y, y: 1 - x };
    }
    if (rotation === 180) {
      return { x: 1 - x, y: 1 - y };
    }
    if (rotation === 270) {
      return { x: 1 - y, y: x };
    }
    return { x, y };
  }

  function symbolText(symbol) {
    return {
      check: "✓",
      exclamation: "!",
      question: "?",
    }[symbol] || "?";
  }

  function updateSizeLabel() {
    if (sizeLabel) {
      sizeLabel.textContent = `${Math.round(selectedSize / baseSize * 100)} %`;
    }
    if (sizeDecreaseButton) {
      sizeDecreaseButton.disabled = (
        !selectedSymbol || selectedSize <= minimumSize
      );
    }
    if (sizeIncreaseButton) {
      sizeIncreaseButton.disabled = (
        !selectedSymbol || selectedSize >= maximumSize
      );
    }
  }

  function changeSelectedSize(direction) {
    selectedSize = Math.min(
      maximumSize,
      Math.max(minimumSize, selectedSize + direction * sizeStep),
    );
    window.localStorage.setItem(
      "doksio-review-marker-size",
      String(selectedSize),
    );
    updateSizeLabel();
  }

  function markerElement(marker, target) {
    const element = document.createElement("button");
    const displayPosition = canonicalToDisplay(
      Number(marker.x),
      Number(marker.y),
      rotationForTarget(target),
    );
    element.type = "button";
    element.className = `document-review-marker document-review-marker-${marker.symbol}`;
    element.dataset.reviewMarkerId = String(marker.id);
    element.textContent = symbolText(marker.symbol);
    element.style.left = `${displayPosition.x * 100}%`;
    element.style.top = `${displayPosition.y * 100}%`;
    element.style.setProperty(
      "--review-marker-pixels",
      `${Math.max(14, Math.min(target.clientWidth, target.clientHeight) * marker.size)}px`,
    );
    element.dataset.reviewMarkerTooltip = "Rechtsklick zum Entfernen";
    element.classList.toggle(
      "document-review-marker-tooltip-above",
      displayPosition.y > 0.82,
    );
    element.setAttribute("aria-label", `${element.textContent} Prüfmarkierung`);
    return element;
  }

  function renderTargetMarkers(target, pageNumber) {
    const layer = target.querySelector(":scope > [data-review-marker-layer]");
    if (!layer) {
      return;
    }
    layer.querySelectorAll("[data-review-marker-id]").forEach(function (marker) {
      marker.remove();
    });
    markers
      .filter((marker) => Number(marker.page_number) === pageNumber)
      .forEach(function (marker) {
        layer.appendChild(markerElement(marker, target));
      });
  }

  function decorateTarget(target, pageNumber) {
    target.dataset.reviewMarkerPage = String(pageNumber);
    let layer = target.querySelector(":scope > [data-review-marker-layer]");
    if (!layer) {
      layer = document.createElement("div");
      layer.className = "document-review-marker-layer";
      layer.dataset.reviewMarkerLayer = "";
      layer.innerHTML = [
        '<span class="document-advanced-crosshair-x"></span>',
        '<span class="document-advanced-crosshair-y"></span>',
      ].join("");
      target.appendChild(layer);
    }
    target.classList.toggle(
      "document-advanced-review-active",
      Boolean(selectedSymbol),
    );
    renderTargetMarkers(target, pageNumber);
  }

  function refreshTarget(target) {
    const pageNumber = Number(target.dataset.reviewMarkerPage || "1");
    renderTargetMarkers(target, pageNumber);
  }

  function updateToolState() {
    root.querySelectorAll("[data-review-marker-page]").forEach(function (target) {
      target.classList.toggle(
        "document-advanced-review-active",
        Boolean(selectedSymbol),
      );
    });
    updateSizeLabel();
  }

  function updateCrosshair(target, event) {
    const layer = target.querySelector(":scope > [data-review-marker-layer]");
    if (!layer) {
      return;
    }
    const rect = target.getBoundingClientRect();
    layer.style.setProperty(
      "--advanced-review-x",
      `${event.clientX - rect.left}px`,
    );
    layer.style.setProperty(
      "--advanced-review-y",
      `${event.clientY - rect.top}px`,
    );
  }

  async function createMarker(target, event) {
    const rect = target.getBoundingClientRect();
    const displayX = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
    const displayY = Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height));
    const position = displayToCanonical(
      displayX,
      displayY,
      rotationForTarget(target),
    );
    const response = await fetch(createUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrfToken(),
      },
      body: JSON.stringify({
        symbol: selectedSymbol,
        page_number: Number(target.dataset.reviewMarkerPage || "1"),
        x: position.x,
        y: position.y,
        size: selectedSize,
      }),
    });
    if (!response.ok) {
      root.dataset.reviewMarkerSaveFailed = "true";
      return;
    }
    markers.push(await response.json());
    refreshTarget(target);
  }

  async function deleteMarker(markerElementToDelete) {
    const markerId = markerElementToDelete.dataset.reviewMarkerId;
    const response = await fetch(deleteUrl.replace("/0/delete/", `/${markerId}/delete/`), {
      method: "POST",
      headers: { "X-CSRFToken": csrfToken() },
    });
    if (!response.ok) {
      root.dataset.reviewMarkerDeleteFailed = "true";
      return;
    }
    markers = markers.filter((marker) => String(marker.id) !== markerId);
    markerElementToDelete.remove();
  }

  palette.addEventListener("click", function (event) {
    const swatch = event.target.closest("[data-review-symbol]");
    if (!swatch) {
      return;
    }
    selectedSymbol = selectedSymbol === swatch.dataset.reviewSymbol
      ? null
      : swatch.dataset.reviewSymbol;
    palette.querySelectorAll("[data-review-symbol]").forEach(function (button) {
      const selected = button.dataset.reviewSymbol === selectedSymbol;
      button.classList.toggle("active", selected);
      button.setAttribute("aria-pressed", selected ? "true" : "false");
    });
    updateToolState();
  });
  sizeDecreaseButton?.addEventListener("click", function () {
    changeSelectedSize(-1);
  });
  sizeIncreaseButton?.addEventListener("click", function () {
    changeSelectedSize(1);
  });
  root.addEventListener("mousemove", function (event) {
    if (!selectedSymbol) {
      return;
    }
    const target = event.target.closest("[data-review-marker-page]");
    if (target) {
      updateCrosshair(target, event);
    }
  });
  root.addEventListener("click", function (event) {
    if (
      !selectedSymbol
      || event.button !== 0
      || event.target.closest("[data-review-marker-id]")
    ) {
      return;
    }
    const target = event.target.closest("[data-review-marker-page]");
    if (target) {
      createMarker(target, event);
    }
  });
  root.addEventListener("contextmenu", function (event) {
    const marker = event.target.closest("[data-review-marker-id]");
    if (!marker) {
      return;
    }
    event.preventDefault();
    deleteMarker(marker);
  });
  updateSizeLabel();
  return { decorateTarget, refreshTarget };
}

function initReviewAssist(root) {
  const toggleButton = root.querySelector("[data-review-assist-toggle]");
  const stage = root.querySelector(".document-preview-stage");

  if (!toggleButton || !stage) {
    return;
  }

  const overlay = document.createElement("div");
  overlay.className = "document-review-assist";
  overlay.setAttribute("aria-hidden", "true");
  overlay.innerHTML = [
    '<span class="document-review-assist-x"></span>',
    '<span class="document-review-assist-y"></span>',
    '<span class="document-review-assist-preview-x"></span>',
    '<span class="document-review-assist-preview-y"></span>',
  ].join("");
  stage.appendChild(overlay);

  let enabled = false;
  let isPinned = false;

  function setEnabled(nextEnabled) {
    enabled = nextEnabled;
    if (!enabled) {
      isPinned = false;
    }
    stage.classList.toggle("document-review-assist-active", enabled);
    stage.classList.toggle("document-review-assist-pinned", isPinned);
    toggleButton.classList.toggle("active", enabled);
    toggleButton.setAttribute("aria-pressed", enabled ? "true" : "false");
  }

  function syncOverlaySize() {
    overlay.style.width = `${Math.max(stage.scrollWidth, stage.clientWidth)}px`;
    overlay.style.height = `${Math.max(stage.scrollHeight, stage.clientHeight)}px`;
  }

  function eventPosition(event) {
    const rect = stage.getBoundingClientRect();
    return {
      x: event.clientX - rect.left + stage.scrollLeft,
      y: event.clientY - rect.top + stage.scrollTop,
    };
  }

  function placeAssist(event) {
    syncOverlaySize();
    const { x, y } = eventPosition(event);
    overlay.style.setProperty("--review-assist-x", `${Math.round(x)}px`);
    overlay.style.setProperty("--review-assist-y", `${Math.round(y)}px`);
  }

  function placePointerAssist(event) {
    syncOverlaySize();
    const { x, y } = eventPosition(event);
    overlay.style.setProperty("--review-assist-pointer-x", `${Math.round(x)}px`);
    overlay.style.setProperty("--review-assist-pointer-y", `${Math.round(y)}px`);
  }

  function updatePosition(event) {
    if (!enabled) {
      return;
    }
    if (isPinned) {
      placePointerAssist(event);
    } else {
      placeAssist(event);
      placePointerAssist(event);
    }
  }

  toggleButton.setAttribute("aria-pressed", "false");
  toggleButton.addEventListener("click", function () {
    setEnabled(!enabled);
  });
  stage.addEventListener("click", function (event) {
    if (!enabled || event.button !== 0) {
      return;
    }
    placeAssist(event);
    placePointerAssist(event);
    isPinned = true;
    stage.classList.add("document-review-assist-pinned");
  });
  stage.addEventListener("contextmenu", function (event) {
    if (!enabled) {
      return;
    }
    event.preventDefault();
    isPinned = false;
    stage.classList.remove("document-review-assist-pinned");
    placeAssist(event);
    placePointerAssist(event);
  });
  stage.addEventListener("mousemove", updatePosition);
  stage.addEventListener("mouseenter", updatePosition);
}

function initAllPreviews() {
  document.querySelectorAll("[data-pdf-preview]").forEach(initPreview);
  document.querySelectorAll("[data-image-preview]").forEach(initImagePreview);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initAllPreviews);
} else {
  initAllPreviews();
}
