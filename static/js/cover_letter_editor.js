(() => {
  const initialize = root => {
    if (!root || root.dataset.editorInitialized === "true") return;

    const form = root.querySelector("#cover-letter-form");
    const textarea = root.querySelector("[data-cover-letter-input]");
    const editor = root.querySelector("[data-rich-editor]");
    const status = root.querySelector("[data-save-status]");
    const baselineElement = root.querySelector("#cover-letter-baseline");
    if (!form || !textarea || !editor || !baselineElement || !window.Quill) return;

    const baseline = JSON.parse(baselineElement.textContent);
    const normalize = value => value.replace(/\s+/g, " ").trim();
    const snapshot = () => normalize(textarea.value);
    const savedBaseline = normalize(baseline);
    const updateDirty = () => {
      const dirty = snapshot() !== savedBaseline;
      root.dataset.dirty = String(dirty);
      if (status) {
        status.textContent = dirty ? "Unsaved changes" : "Saved";
        status.classList.toggle("text-coral", dirty);
      }
    };
    const syncTextarea = () => {
      textarea.value = editor.querySelector(".ql-editor").innerHTML;
      updateDirty();
    };

    const quill = new Quill(editor, {
      theme: "snow",
      modules: { toolbar: root.querySelector("[data-toolbar]") },
      formats: ["header", "bold", "italic", "list", "link"],
    });
    quill.clipboard.dangerouslyPasteHTML(textarea.value || "");
    editor.classList.remove("hidden");
    textarea.classList.add("sr-only");
    quill.on("text-change", syncTextarea);
    root._coverLetterQuill = quill;
    root.dataset.editorInitialized = "true";

    form.addEventListener("submit", event => {
      if (form.dataset.saving === "true") {
        event.preventDefault();
        return;
      }
      syncTextarea();
      quill.enable(false);
      form.dataset.saving = "true";
      if (status) status.textContent = "Saving...";
      setTimeout(() => form.querySelectorAll("button, textarea, select, [contenteditable]").forEach(control => {
        control.disabled = true;
        control.setAttribute("aria-disabled", "true");
      }), 0);
    });

    const deleteDialog = root.querySelector("[data-delete-dialog]");
    const deleteForm = root.querySelector("[data-delete-form]");
    root.querySelector("[data-action=delete]")?.addEventListener("click", () => {
      if (!deleteDialog || !deleteForm) return;
      deleteForm.querySelector("[data-delete-dirty]").value = root.dataset.dirty === "true" ? "1" : "0";
      deleteDialog.querySelector("[data-delete-copy]").textContent = root.dataset.dirty === "true"
        ? "Saved content and your unsaved edits will be discarded."
        : "The saved Cover Letter will be permanently deleted.";
      if (deleteDialog.showModal) deleteDialog.showModal();
      else if (window.confirm("Delete this Cover Letter?")) deleteForm.submit();
    });
    deleteDialog?.querySelector("[data-action=cancel-delete]")?.addEventListener("click", () => deleteDialog.close());

    const exitDialog = root.querySelector("[data-exit-dialog]");
    root.querySelector("[data-action=back]")?.addEventListener("click", event => {
      if (root.dataset.dirty !== "true") return;
      event.preventDefault();
      if (!exitDialog) {
        if (window.confirm("Discard unsaved Cover Letter changes?")) window.location.assign(event.currentTarget.href);
        return;
      }
      exitDialog.dataset.href = event.currentTarget.href;
      if (exitDialog.showModal) exitDialog.showModal();
      else if (window.confirm("Discard unsaved Cover Letter changes?")) window.location.assign(event.currentTarget.href);
    });
    exitDialog?.addEventListener("close", () => {
      if (exitDialog.returnValue === "discard") window.location.assign(exitDialog.dataset.href);
    });
    window.addEventListener("beforeunload", event => {
      if (root.dataset.dirty === "true") {
        event.preventDefault();
        event.returnValue = "";
      }
    });
  };

  const destroy = root => {
    if (!root) return;
    root._coverLetterQuill?.disable();
    delete root._coverLetterQuill;
    delete root.dataset.editorInitialized;
  };

  if (!window.__applykitCoverLetterLifecycle) {
    window.__applykitCoverLetterLifecycle = { destroy, initialize };
    document.addEventListener("htmx:beforeCleanupElement", event => {
      const target = event.detail.elt;
      destroy(target.matches?.("[data-cover-letter-workbench]") ? target : target.closest?.("[data-cover-letter-workbench]"));
    });
    document.addEventListener("htmx:afterSwap", event => {
      const target = event.detail.target;
      initialize(target.matches?.("[data-cover-letter-workbench]") ? target : target.querySelector?.("[data-cover-letter-workbench]"));
    });
  }
  window.__applykitCoverLetterLifecycle.initialize(document.querySelector("[data-cover-letter-workbench]"));
})();
