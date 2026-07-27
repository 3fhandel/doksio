document.querySelectorAll("[data-upload-dropzone]").forEach((dropzone) => {
  const input = dropzone.querySelector('input[type="file"]');
  const form = dropzone.closest("form");
  const fileList = form?.querySelector("[data-upload-file-list]");
  const titleField = form?.querySelector("[data-upload-title-field]");
  const titleInput = titleField?.querySelector("input");

  if (!input || !fileList || !form) {
    return;
  }

  let activeInput = input;
  let selectedFiles = Array.from(input.files || []);
  const fallbackInputs = [];
  const supportsMutableFileList = (() => {
    try {
      return typeof DataTransfer === "function" && new DataTransfer().items;
    } catch (_error) {
      return false;
    }
  })();

  const fileKey = (file) => [
    file.name,
    file.size,
    file.lastModified,
    file.type,
  ].join(":");

  const synchronizeInput = () => {
    if (!supportsMutableFileList) {
      return;
    }
    const transfer = new DataTransfer();
    selectedFiles.forEach((file) => transfer.items.add(file));
    activeInput.files = transfer.files;
  };

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
    if (!selectedFiles.length) {
      fileList.textContent = "";
      return;
    }

    const list = document.createElement("ul");
    list.className = "upload-file-list-items";
    selectedFiles.forEach((file, index) => {
      const item = document.createElement("li");
      const description = document.createElement("span");
      description.textContent = `${file.name} · ${formatBytes(file.size)}`;
      item.append(description);
      if (supportsMutableFileList) {
        const removeButton = document.createElement("button");
        removeButton.className = "upload-file-remove";
        removeButton.type = "button";
        removeButton.dataset.uploadFileRemove = String(index);
        removeButton.textContent = "×";
        removeButton.title = `${file.name} entfernen`;
        removeButton.setAttribute("aria-label", `${file.name} entfernen`);
        item.append(removeButton);
      }
      list.appendChild(item);
    });

    fileList.replaceChildren(list);
  };

  const updateTitleField = () => {
    if (!titleField || !titleInput) {
      return;
    }

    const hasMultipleFiles = selectedFiles.length > 1;
    titleField.hidden = hasMultipleFiles;
    titleInput.disabled = hasMultipleFiles;
  };

  const refreshUploadState = () => {
    renderFileList();
    updateTitleField();
  };

  const addFiles = (files) => {
    if (!supportsMutableFileList) {
      selectedFiles.push(...Array.from(files || []));
      refreshUploadState();
      return;
    }
    const knownFiles = new Set(selectedFiles.map(fileKey));
    Array.from(files || []).forEach((file) => {
      const key = fileKey(file);
      if (!knownFiles.has(key)) {
        selectedFiles.push(file);
        knownFiles.add(key);
      }
    });
    synchronizeInput();
    refreshUploadState();
  };

  const stageFallbackDrop = (files) => {
    const stagedInput = activeInput.cloneNode();
    stagedInput.removeAttribute("id");
    stagedInput.hidden = true;
    stagedInput.files = files;
    fallbackInputs.push(stagedInput);
    form.append(stagedInput);
    addFiles(files);
  };

  const bindInput = (fileInput) => {
    fileInput.addEventListener("change", () => {
      if (supportsMutableFileList) {
        addFiles(fileInput.files);
        return;
      }

      const stagedInput = fileInput;
      const replacementInput = stagedInput.cloneNode();
      stagedInput.removeAttribute("id");
      stagedInput.hidden = true;
      stagedInput.after(replacementInput);
      fallbackInputs.push(stagedInput);
      activeInput = replacementInput;
      bindInput(replacementInput);
      addFiles(stagedInput.files);
    });
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
    if (supportsMutableFileList) {
      addFiles(event.dataTransfer.files);
    } else {
      stageFallbackDrop(event.dataTransfer.files);
    }
  });

  bindInput(activeInput);

  fileList.addEventListener("click", (event) => {
    const removeButton = event.target.closest("[data-upload-file-remove]");
    if (!removeButton) {
      return;
    }
    selectedFiles.splice(Number(removeButton.dataset.uploadFileRemove), 1);
    synchronizeInput();
    refreshUploadState();
  });

  refreshUploadState();
});
