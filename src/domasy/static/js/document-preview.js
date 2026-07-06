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
      const viewport = page.getViewport({ scale });
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
  const image = root.querySelector("[data-image-preview-img]");
  const fitButton = root.querySelector("[data-image-fit]");
  const zoomOutButton = root.querySelector("[data-image-zoom-out]");
  const zoomInButton = root.querySelector("[data-image-zoom-in]");
  const zoomLabel = root.querySelector("[data-image-zoom-label]");

  if (!stage || !image) {
    return;
  }

  let scale = 1;
  let isFitMode = true;

  function naturalWidth() {
    return image.naturalWidth || 1;
  }

  function naturalHeight() {
    return image.naturalHeight || 1;
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
      availableWidth / naturalWidth(),
      availableHeight / naturalHeight()
    );
  }

  function updateLabel() {
    if (!zoomLabel) {
      return;
    }
    zoomLabel.textContent = isFitMode ? "Fit" : `${Math.round(scale * 100)}%`;
  }

  function applyScale() {
    image.style.width = `${Math.max(Math.round(naturalWidth() * scale), 1)}px`;
    image.style.height = "auto";
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

function initReviewAssist(root) {
  const toggleButton = root.querySelector("[data-review-assist-toggle]");
  const stage = root.querySelector(".document-preview-stage");

  if (!toggleButton || !stage) {
    return;
  }

  const overlay = document.createElement("div");
  overlay.className = "document-review-assist";
  overlay.setAttribute("aria-hidden", "true");
  overlay.innerHTML = '<span class="document-review-assist-x"></span><span class="document-review-assist-y"></span>';
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

  function placeAssist(event) {
    overlay.style.width = `${Math.max(stage.scrollWidth, stage.clientWidth)}px`;
    overlay.style.height = `${Math.max(stage.scrollHeight, stage.clientHeight)}px`;
    const rect = stage.getBoundingClientRect();
    const x = event.clientX - rect.left + stage.scrollLeft;
    const y = event.clientY - rect.top + stage.scrollTop;
    overlay.style.setProperty("--review-assist-x", `${Math.round(x)}px`);
    overlay.style.setProperty("--review-assist-y", `${Math.round(y)}px`);
  }

  function updatePosition(event) {
    if (!enabled || isPinned) {
      return;
    }
    placeAssist(event);
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
