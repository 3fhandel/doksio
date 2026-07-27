(() => {
  const modalElement = document.getElementById("documentQuickPreviewModal");
  const previewImage = modalElement?.querySelector(
    "[data-document-quick-preview-image]",
  );
  const modalTitle = modalElement?.querySelector(".modal-title");
  if (!modalElement || !previewImage || !modalTitle || !window.bootstrap) {
    return;
  }

  const modal = window.bootstrap.Modal.getOrCreateInstance(modalElement);
  document.addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-document-quick-preview]");
    if (!trigger) {
      return;
    }
    const source = trigger.dataset.previewSrc;
    if (!source) {
      return;
    }
    const title = trigger.dataset.previewTitle || "Schnellvorschau";
    modalTitle.textContent = title;
    previewImage.src = source;
    previewImage.alt = `Vorschau ${title}`;
    modal.show();
  });

  modalElement.addEventListener("hidden.bs.modal", () => {
    previewImage.removeAttribute("src");
    previewImage.alt = "";
    modalTitle.textContent = "Schnellvorschau";
  });
})();
