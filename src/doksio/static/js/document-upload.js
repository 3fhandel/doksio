document.querySelectorAll("[data-upload-dropzone]").forEach((dropzone) => {
  const input = dropzone.querySelector('input[type="file"]');
  const fileList = document.querySelector("[data-upload-file-list]");
  const titleField = document.querySelector("[data-upload-title-field]");
  const titleInput = titleField?.querySelector("input");

  if (!input || !fileList) {
    return;
  }

  const formatBytes = (size) => {
    if (!Number.isFinite(size)) {
      return "";
    }
    if (size < 1024) {
      return `${size} B`;
    }
    if (size < 1024 * 1024) {
      return `${(size / 1024).toFixed(1)} KB`;
    }
    return `${(size / 1024 / 1024).toFixed(1)} MB`;
  };

  const renderFileList = () => {
    const files = Array.from(input.files || []);
    if (!files.length) {
      fileList.textContent = "";
      return;
    }

    const list = document.createElement("ul");
    list.className = "upload-file-list-items";
    files.forEach((file) => {
      const item = document.createElement("li");
      item.textContent = `${file.name} · ${formatBytes(file.size)}`;
      list.appendChild(item);
    });

    fileList.replaceChildren(list);
  };

  const updateTitleField = () => {
    if (!titleField || !titleInput) {
      return;
    }

    const hasMultipleFiles = (input.files || []).length > 1;
    titleField.hidden = hasMultipleFiles;
    titleInput.disabled = hasMultipleFiles;
  };

  const refreshUploadState = () => {
    renderFileList();
    updateTitleField();
  };

  const stopDefaults = (event) => {
    event.preventDefault();
    event.stopPropagation();
  };

  ["dragenter", "dragover"].forEach((eventName) => {
    dropzone.addEventListener(eventName, (event) => {
      stopDefaults(event);
      dropzone.classList.add("is-dragging");
    });
  });

  ["dragleave", "drop"].forEach((eventName) => {
    dropzone.addEventListener(eventName, (event) => {
      stopDefaults(event);
      dropzone.classList.remove("is-dragging");
    });
  });

  dropzone.addEventListener("drop", (event) => {
    input.files = event.dataTransfer.files;
    refreshUploadState();
  });

  input.addEventListener("change", refreshUploadState);
  refreshUploadState();
});
