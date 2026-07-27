(() => {
  const modalElement = document.getElementById("documentQuickPreviewModal");
  const previewBody = modalElement?.querySelector(".document-quick-preview-body");
  const previewImage = modalElement?.querySelector(
    "[data-document-quick-preview-image]",
  );
  const previewCanvas = modalElement?.querySelector(
    "[data-document-quick-preview-canvas]",
  );
  const previewStatus = modalElement?.querySelector(
    "[data-document-quick-preview-status]",
  );
  const modalTitle = modalElement?.querySelector(".modal-title");
  if (
    !modalElement
    || !previewBody
    || !previewImage
    || !previewCanvas
    || !previewStatus
    || !modalTitle
    || !window.bootstrap
  ) {
    return;
  }

  const modal = window.bootstrap.Modal.getOrCreateInstance(modalElement);
  let activePdf = null;
  let renderTask = null;
  let generation = 0;

  const resetPreview = async () => {
    generation += 1;
    if (renderTask) {
      renderTask.cancel();
      renderTask = null;
    }
    if (activePdf) {
      await activePdf.destroy();
      activePdf = null;
    }
    previewImage.hidden = true;
    previewImage.removeAttribute("src");
    previewImage.alt = "";
    previewCanvas.hidden = true;
    previewCanvas.width = 0;
    previewCanvas.height = 0;
    previewStatus.hidden = false;
    previewStatus.textContent = "Vorschau wird geladen...";
  };

  const showError = () => {
    previewStatus.hidden = false;
    previewStatus.textContent = "Die Originaldatei konnte nicht dargestellt werden.";
  };

  const renderImage = (source, title) => {
    previewImage.onload = () => {
      previewStatus.hidden = true;
      previewImage.hidden = false;
    };
    previewImage.onerror = showError;
    previewImage.alt = `Vorschau ${title}`;
    previewImage.src = source;
  };

  const renderPdf = async (source, expectedGeneration) => {
    if (!window.pdfjsLib) {
      showError();
      return;
    }
    window.pdfjsLib.GlobalWorkerOptions.workerSrc = previewBody.dataset.pdfWorkerUrl;
    try {
      activePdf = await window.pdfjsLib.getDocument(source).promise;
      const page = await activePdf.getPage(1);
      if (expectedGeneration !== generation) {
        return;
      }
      const baseViewport = page.getViewport({ scale: 1 });
      const availableWidth = Math.max(previewBody.clientWidth - 32, 280);
      const availableHeight = Math.max(window.innerHeight * 0.68, 320);
      const scale = Math.min(
        availableWidth / baseViewport.width,
        availableHeight / baseViewport.height,
      );
      const viewport = page.getViewport({ scale });
      const outputScale = window.devicePixelRatio || 1;
      const context = previewCanvas.getContext("2d");
      previewCanvas.width = Math.floor(viewport.width * outputScale);
      previewCanvas.height = Math.floor(viewport.height * outputScale);
      previewCanvas.style.width = `${Math.floor(viewport.width)}px`;
      previewCanvas.style.height = `${Math.floor(viewport.height)}px`;
      previewCanvas.hidden = false;
      renderTask = page.render({
        canvasContext: context,
        transform: outputScale !== 1
          ? [outputScale, 0, 0, outputScale, 0, 0]
          : null,
        viewport,
      });
      await renderTask.promise;
      renderTask = null;
      if (expectedGeneration === generation) {
        previewStatus.hidden = true;
      }
    } catch (error) {
      if (!error || error.name !== "RenderingCancelledException") {
        showError();
      }
    }
  };

  document.addEventListener("click", async (event) => {
    const trigger = event.target.closest("[data-document-quick-preview]");
    if (!trigger) {
      return;
    }
    const source = trigger.dataset.previewSrc;
    if (!source) {
      return;
    }
    const title = trigger.dataset.previewTitle || "Schnellvorschau";
    const contentType = (trigger.dataset.previewContentType || "")
      .split(";", 1)[0]
      .toLowerCase();
    await resetPreview();
    const expectedGeneration = generation;
    modalTitle.textContent = title;
    const modalShown = new Promise((resolve) => {
      modalElement.addEventListener("shown.bs.modal", resolve, { once: true });
    });
    modal.show();
    await modalShown;
    if (expectedGeneration !== generation) {
      return;
    }
    if (contentType === "application/pdf") {
      await renderPdf(source, expectedGeneration);
    } else if (contentType.startsWith("image/")) {
      renderImage(source, title);
    } else {
      showError();
    }
  });

  modalElement.addEventListener("hidden.bs.modal", async () => {
    await resetPreview();
    modalTitle.textContent = "Schnellvorschau";
  });
})();
