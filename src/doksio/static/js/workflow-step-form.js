(() => {
  const form = document.querySelector("[data-workflow-step-form]");
  if (!form) {
    return;
  }

  const typeSelect = form.querySelector("select[name='step_type']");
  const sections = [...form.querySelectorAll("[data-step-type-section]")];
  if (!typeSelect || !sections.length) {
    return;
  }

  const updateSections = () => {
    const selectedType = typeSelect.value;
    sections.forEach((section) => {
      const visibleForTypes = section.dataset.stepTypeSection.split(/\s+/);
      const isVisible = visibleForTypes.includes(selectedType);
      section.classList.toggle("d-none", !isVisible);
      section.setAttribute("aria-hidden", isVisible ? "false" : "true");
    });
  };

  typeSelect.addEventListener("change", updateSections);
  updateSections();
})();
