function initPreview(root) {
    initReviewAssist(root);

    const canvas = root.querySelector("[data-pdf-canvas]");
    const status = root.querySelector("[data-pdf-status]");
    const pageCurrent = root.querySelector("[data-pdf-page-current]");
    const pageTotal = root.querySelector("[data-pdf-page-total]");
    const prevButton = root.querySelector("[data-pdf-prev]");
    const nextButton = root.querySelector("[data-pdf-next]");
    const zoomOutButton = root.querySelector("[data-pdf-zoom-out]");
    const zoomInButton = root.querySelector("[data-pdf-zoom-in]");
    const rotateLeftButton = root.querySelector("[data-viewer-rotate-left]");
    const rotateRightButton = root.querySelector("[data-viewer-rotate-right]");
    const pdfUrl = root.dataset.pdfUrl;

    if (!canvas || !pdfUrl || !window.pdfjsLib) {
      if (status) {
        status.textContent = "PDF.js konnte nicht geladen werden.";
      }
      return;
    }

    const context = canvas.getContext("2d");
    let pdfDocument = null;
    let pageNumber = 1;
    let scale = 1.2;
    let rotation = viewerRotation(root);
    let renderTask = null;

    function setStatus(message) {
      if (status) {
        status.textContent = message;
      }
    }

    function updateControls() {
      if (!pdfDocument) {
        return;
      }
      pageCurrent.textContent = pageNumber;
      pageTotal.textContent = pdfDocument.numPages;
      prevButton.disabled = pageNumber <= 1;
      nextButton.disabled = pageNumber >= pdfDocument.numPages;
    }

    async function renderPage() {
      if (!pdfDocument) {
        return;
      }
      if (renderTask) {
        renderTask.cancel();
      }

      const page = await pdfDocument.getPage(pageNumber);
      const viewport = page.getViewport({ scale, rotation });
      const outputScale = window.devicePixelRatio || 1;

      canvas.width = Math.floor(viewport.width * outputScale);
      canvas.height = Math.floor(viewport.height * outputScale);
      canvas.style.width = `${Math.floor(viewport.width)}px`;
      canvas.style.height = `${Math.floor(viewport.height)}px`;

      renderTask = page.render({
        canvasContext: context,
        transform: outputScale !== 1 ? [outputScale, 0, 0, outputScale, 0, 0] : null,
        viewport,
      });

      try {
        await renderTask.promise;
        setStatus("");
      } catch (error) {
        if (error && error.name !== "RenderingCancelledException") {
          setStatus("Vorschau konnte nicht gerendert werden.");
        }
      } finally {
        renderTask = null;
      }
      updateControls();
    }

    prevButton.addEventListener("click", function () {
      if (pageNumber > 1) {
        pageNumber -= 1;
        renderPage();
      }
    });

    nextButton.addEventListener("click", function () {
      if (pdfDocument && pageNumber < pdfDocument.numPages) {
        pageNumber += 1;
        renderPage();
      }
    });

    zoomOutButton.addEventListener("click", function () {
      scale = Math.max(0.6, scale - 0.2);
      renderPage();
    });

    zoomInButton.addEventListener("click", function () {
      scale = Math.min(2.4, scale + 0.2);
      renderPage();
    });

    function rotateBy(delta) {
      rotation = normalizeRotation(rotation + delta);
      persistViewerRotation(root, rotation);
      renderPage();
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
        updateControls();
        renderPage();
      })
      .catch(function () {
        setStatus("PDF-Vorschau konnte nicht geladen werden.");
      });
}

function initImagePreview(root) {
  initReviewAssist(root);

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

function csrfToken() {
  const tokenInput = document.querySelector("[name=csrfmiddlewaretoken]");
  if (tokenInput) {
    return tokenInput.value;
  }
  const cookieMatch = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
  return cookieMatch ? decodeURIComponent(cookieMatch[1]) : "";
}

function persistViewerRotation(root, rotation) {
  root.dataset.viewerRotation = String(rotation);
  if (!root.dataset.viewerSettingsUrl) {
    return;
  }
  fetch(root.dataset.viewerSettingsUrl, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": csrfToken(),
    },
    body: JSON.stringify({ rotation }),
  }).catch(function () {
    root.dataset.viewerRotationSaveFailed = "true";
  });
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
