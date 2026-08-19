(() => {
  const initializedRoots = new WeakSet();
  const stateLabels = {
    clean: "Saved",
    dirty: "Unsaved changes",
    saving: "Saving...",
    saved: "Saved",
    failed: "Save failed",
  };

  const isDraftControl = control => {
    if (!control.name) return false;
    if (control.name === "csrfmiddlewaretoken") return false;
    if (control.name === "reset_resume_draft" || control.name === "reset_scope") return false;
    return !/-((?:TOTAL|INITIAL|MIN_NUM|MAX_NUM)_FORMS)$/.test(control.name);
  };

  const normalizedValue = control => {
    if (control.type === "checkbox" || control.type === "radio") {
      return control.checked ? "1" : "0";
    }
    return String(control.value ?? "").replace(/\r\n?/g, "\n").trim();
  };

  const semanticSnapshot = form => {
    const values = new Map();
    [...form.elements].filter(isDraftControl).forEach(control => {
      values.set(control.name, normalizedValue(control));
    });

    values.forEach((_value, name) => {
      if (!name.endsWith("_inherit")) return;
      const valueName = name.slice(0, -"_inherit".length);
      if (!values.has(valueName)) return;
      if (values.get(name) === "1" || values.get(valueName) === "") {
        values.set(valueName, "");
        values.set(name, "1");
      }
    });

    return [...values.entries()]
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([name, value]) => `${name}=${value}`)
      .join("&");
  };

  const normalizedHtml = value => String(value ?? "").replace(/\s+/g, " ").trim();

  const focusable = dialog => [...dialog.querySelectorAll(
    "button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), " +
      "textarea:not([disabled]), [contenteditable=true], [tabindex]:not([tabindex='-1'])",
  )];

  const initializeDialog = (dialog, onClose) => {
    if (!dialog) return () => {};
    const safeButton = dialog.querySelector("[data-safe-focus]") || dialog.querySelector("button");
    let returnFocus = null;
    const onKeydown = event => {
      if (event.key !== "Tab") return;
      const controls = focusable(dialog);
      if (!controls.length) return;
      const first = controls[0];
      const last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    const close = event => {
      onClose(event.target.returnValue, event.target.dataset.href);
      if (returnFocus?.isConnected) window.requestAnimationFrame(() => returnFocus.focus());
      returnFocus = null;
    };
    dialog.addEventListener("keydown", onKeydown);
    dialog.addEventListener("close", close);
    dialog._openFrom = trigger => {
      returnFocus = trigger;
      if (!dialog.showModal) return false;
      dialog.showModal();
      window.requestAnimationFrame(() => safeButton?.focus());
      return true;
    };
    return () => {
      dialog.removeEventListener("keydown", onKeydown);
      dialog.removeEventListener("close", close);
      delete dialog._openFrom;
    };
  };

  const initialize = root => {
    if (!root || initializedRoots.has(root)) return;
    const form = root.querySelector("[data-workbench-form]");
    const status = root.querySelector("[data-save-status]");
    if (!form) return;

    initializedRoots.add(root);
    const cleanups = [];
    const initialState = root.dataset.saveState || "clean";
    const baselineElement = root.querySelector("[data-workbench-baseline]")
      || root.querySelector("#resume-baseline")
      || root.querySelector("#cover-letter-baseline");
    const baselineValue = baselineElement ? JSON.parse(baselineElement.textContent) : "";
    const isCoverLetter = root.dataset.workbenchKind === "cover-letter";
    const baseline = isCoverLetter ? normalizedHtml(baselineValue) : baselineValue;
    let allowNavigation = false;

    const snapshot = () => {
      if (!isCoverLetter) return semanticSnapshot(form);
      return normalizedHtml(form.querySelector("[data-cover-letter-input]")?.value);
    };
    const setState = state => {
      root.dataset.saveState = state;
      root.dataset.dirty = String(state === "dirty" || state === "saving" || state === "failed");
      if (status) {
        status.textContent = stateLabels[state] || stateLabels.clean;
        status.classList.toggle("text-coral", root.dataset.dirty === "true");
      }
    };
    const updateDirty = () => {
      setState(snapshot() === baseline ? "clean" : "dirty");
      return root.dataset.dirty === "true";
    };
    root._documentWorkbench = { semanticSnapshot: snapshot, setState, updateDirty };
    if (initialState !== "failed") updateDirty();
    else setState("failed");

    const editor = root.querySelector("[data-rich-editor]");
    const textarea = root.querySelector("[data-cover-letter-input]");
    let quill = null;
    if (isCoverLetter && editor && textarea && window.Quill) {
      quill = new window.Quill(editor, {
        theme: "snow",
        modules: { toolbar: root.querySelector("[data-toolbar]") },
        formats: ["header", "bold", "italic", "list", "link"],
      });
      quill.clipboard.dangerouslyPasteHTML(textarea.value || "");
      editor.classList.remove("hidden");
      textarea.classList.add("sr-only");
      const syncTextarea = () => {
        textarea.value = editor.querySelector(".ql-editor").innerHTML;
        updateDirty();
      };
      quill.on("text-change", syncTextarea);
      cleanups.push(() => quill.off("text-change", syncTextarea));
      root._documentWorkbenchQuill = quill;
    }

    const onInput = event => {
      const target = event.target;
      if (target.name && !target.name.endsWith("_inherit") && target.value.trim()) {
        const inherit = form.elements.namedItem(`${target.name}_inherit`);
        if (inherit) inherit.checked = false;
      }
      if (form.dataset.saving !== "true") updateDirty();
    };
    form.addEventListener("input", onInput);
    form.addEventListener("change", onInput);
    cleanups.push(() => {
      form.removeEventListener("input", onInput);
      form.removeEventListener("change", onInput);
    });

    const onSubmit = event => {
      if (form.dataset.saving === "true") {
        event.preventDefault();
        return;
      }
      if (quill && textarea && editor) textarea.value = editor.querySelector(".ql-editor").innerHTML;
      quill?.enable(false);
      editor?.setAttribute("aria-disabled", "true");
      form.dataset.saving = "true";
      setState("saving");
      window.setTimeout(() => {
        root.querySelectorAll("button, input, textarea, select, [contenteditable=true]").forEach(control => {
          if (control.type === "hidden") return;
          control.disabled = true;
          control.setAttribute("aria-disabled", "true");
        });
      }, 0);
    };
    form.addEventListener("submit", onSubmit);
    cleanups.push(() => form.removeEventListener("submit", onSubmit));

    const navigate = href => {
      allowNavigation = true;
      root.dataset.dirty = "false";
      window.location.assign(href);
    };
    const exitDialog = root.querySelector("[data-exit-dialog]");
    const cleanupExitDialog = initializeDialog(exitDialog, (value, href) => {
      if (value === "discard" && href) navigate(href);
    });
    cleanups.push(cleanupExitDialog);
    const openExitDialog = (trigger, href) => {
      if (!exitDialog || !exitDialog._openFrom?.(trigger)) {
        if (window.confirm("Discard your unsaved changes?")) navigate(href);
        return;
      }
      exitDialog.dataset.href = href;
    };
    const onNavigationClick = event => {
      const link = event.target.closest?.("a[href]");
      if (!link || (!root.contains(link) && !link.closest("header"))) return;
      if (link.dataset.action === "reset-resume") return;
      if (root.dataset.dirty !== "true" || link.target || link.hasAttribute("download")) return;
      const url = new URL(link.href, window.location.href);
      if (url.origin === window.location.origin && url.pathname === window.location.pathname && url.hash) return;
      event.preventDefault();
      openExitDialog(link, link.href);
    };
    document.addEventListener("click", onNavigationClick);
    cleanups.push(() => document.removeEventListener("click", onNavigationClick));

    const resetDialog = root.querySelector("[data-reset-dialog]");
    const cleanupResetDialog = initializeDialog(resetDialog, value => {
      if (value === "confirm") resetResume();
    });
    cleanups.push(cleanupResetDialog);

    const input = (scope, name) => scope.querySelector(`[name="${name}"]`) || scope.querySelector(`[name$="-${name}"]`);
    const defaultsElement = root.querySelector("#resume-default-draft");
    const defaults = defaultsElement ? JSON.parse(defaultsElement.textContent) : null;
    const sets = ["experiences", "projects", "educations", "languages", "skills"];
    const setValue = (scope, name, value, inherit) => {
      const field = input(scope, name);
      if (!field) return;
      if (field.type === "checkbox") field.checked = Boolean(value);
      else field.value = value ?? "";
      const inheritField = input(scope, `${name}_inherit`);
      if (inheritField) inheritField.checked = inherit;
    };
    const defaultRow = (name, sourceId, experienceId) => defaults?.[name]?.find(row => (
      String(row.source_id) === String(sourceId) &&
      (!experienceId || String(row.experience_id) === String(experienceId))
    ));
    const applyRow = (scope, row, preserveStructure) => {
      const included = input(scope, "included");
      const position = input(scope, "position");
      const oldIncluded = included?.checked;
      const oldPosition = position?.value;
      Object.entries(row).forEach(([name, value]) => {
        if (["source_id", "experience_id", "included", "position"].includes(name)) return;
        setValue(scope, name, value, true);
      });
      if (preserveStructure) {
        setValue(scope, "included", oldIncluded, false);
        setValue(scope, "position", oldPosition, false);
      } else {
        setValue(scope, "included", true, false);
        setValue(scope, "position", row.position, false);
      }
    };
    const resetField = button => {
      const scope = button.closest("[data-formset]") || form;
      const row = scope === form
        ? defaults?.header
        : defaultRow(scope.dataset.formset, scope.dataset.sourceId, scope.dataset.experienceId);
      if (row) setValue(scope, button.dataset.field, row[button.dataset.field], true);
      updateDirty();
    };
    const resetItem = button => {
      const scope = button.closest("[data-formset]");
      const row = defaultRow(scope.dataset.formset, scope.dataset.sourceId, scope.dataset.experienceId);
      if (row) applyRow(scope, row, true);
      if (scope.dataset.formset === "experiences") {
        form.querySelectorAll(`[data-highlight-experience="${scope.dataset.sourceId}"]`).forEach(highlight => {
          const highlightRow = defaults.highlights.find(item => (
            String(item.experience_id) === scope.dataset.sourceId &&
            String(item.source_id) === highlight.dataset.sourceId
          ));
          if (highlightRow) applyRow(highlight, highlightRow, false);
        });
      }
      updateDirty();
    };
    const resetSection = section => {
      const kind = section.dataset.sectionKind;
      if (kind === "experience" || kind === "projects") input(form, "reset_scope").value = kind;
      if (kind === "summary") Object.entries(defaults.header).forEach(([name, value]) => setValue(form, name, value, true));
      const names = kind === "experience" ? ["experiences", "highlights"]
        : kind === "projects" ? ["projects"]
          : kind === "education" ? ["educations"]
            : kind === "languages" ? ["languages"]
              : kind === "skills" ? ["skills"] : [];
      names.forEach(name => form.querySelectorAll(`[data-formset="${name}"]`).forEach(scope => {
        const row = name === "highlights"
          ? defaults.highlights.find(item => String(item.source_id) === scope.dataset.sourceId && String(item.experience_id) === scope.dataset.experienceId)
          : defaultRow(name, scope.dataset.sourceId);
        if (row) applyRow(scope, row, false);
      }));
      updateDirty();
    };
    function resetResume() {
      if (!defaults) return;
      input(form, "reset_resume_draft").value = "1";
      input(form, "reset_scope").value = "all";
      Object.entries(defaults.header).forEach(([name, value]) => setValue(form, name, value, true));
      form.querySelectorAll("[data-section-row]").forEach(scope => {
        const row = defaults.sections.find(item => item.kind === scope.dataset.sectionKind);
        if (row) setValue(scope, "position", row.position, false);
      });
      sets.forEach(name => form.querySelectorAll(`[data-formset="${name}"]`).forEach(scope => {
        const row = defaultRow(name, scope.dataset.sourceId);
        if (row) applyRow(scope, row, false);
      }));
      form.querySelectorAll('[data-formset="highlights"]').forEach(scope => {
        const row = defaults.highlights.find(item => String(item.source_id) === scope.dataset.sourceId && String(item.experience_id) === scope.dataset.experienceId);
        if (row) applyRow(scope, row, false);
      });
      updateDirty();
    }

    const onAction = event => {
      const action = event.target.closest?.("[data-action]");
      if (!action) return;
      if (action.dataset.action === "reset-resume") {
        event.preventDefault();
        if (!resetDialog || !resetDialog._openFrom?.(action)) {
          if (window.confirm("Reset the local Resume draft?")) resetResume();
        }
      } else if (action.dataset.action === "reset-field") resetField(action);
      else if (action.dataset.action === "reset-item") resetItem(action);
      else if (action.dataset.action === "reset-section") resetSection(action.closest("[data-section-kind]"));
    };
    root.addEventListener("click", onAction);
    cleanups.push(() => root.removeEventListener("click", onAction));

    const deleteDialog = root.querySelector("[data-delete-dialog]");
    const deleteForm = root.querySelector("[data-delete-form]");
    const deleteCopy = deleteDialog?.querySelector("[data-delete-copy]");
    const cleanupDeleteDialog = initializeDialog(deleteDialog, () => {});
    cleanups.push(cleanupDeleteDialog);
    const deleteAction = root.querySelector("[data-action=delete]");
    const onDelete = () => {
      if (!deleteDialog || !deleteForm) return;
      deleteForm.querySelector("[data-delete-dirty]").value = root.dataset.dirty === "true" ? "1" : "0";
      if (deleteCopy) deleteCopy.textContent = root.dataset.dirty === "true"
        ? "This permanently deletes the saved Cover Letter and discards your current unsaved draft."
        : "This permanently deletes the saved Cover Letter. It cannot be undone.";
      if (!deleteDialog._openFrom?.(deleteAction)) {
        if (window.confirm(deleteCopy?.textContent || "Delete this Cover Letter?")) deleteForm.submit();
      }
    };
    deleteAction?.addEventListener("click", onDelete);
    const cancelDelete = deleteDialog?.querySelector("[data-action=cancel-delete]");
    const onCancelDelete = () => deleteDialog.close();
    cancelDelete?.addEventListener("click", onCancelDelete);
    const onDeleteSubmit = () => {
      allowNavigation = true;
      root.dataset.dirty = "false";
    };
    deleteForm?.addEventListener("submit", onDeleteSubmit);
    cleanups.push(() => {
      deleteAction?.removeEventListener("click", onDelete);
      cancelDelete?.removeEventListener("click", onCancelDelete);
      deleteForm?.removeEventListener("submit", onDeleteSubmit);
    });

    const onBeforeUnload = event => {
      if (!allowNavigation && root.dataset.saveState !== "saving" && root.dataset.dirty === "true") {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    cleanups.push(() => window.removeEventListener("beforeunload", onBeforeUnload));
    root._documentWorkbenchCleanup = () => {
      cleanups.splice(0).forEach(cleanup => cleanup());
      quill?.disable();
      delete root._documentWorkbench;
      delete root._documentWorkbenchQuill;
      delete root._documentWorkbenchCleanup;
      initializedRoots.delete(root);
    };
  };

  const destroy = root => root?._documentWorkbenchCleanup?.();
  const initializeTarget = target => initialize(
    target?.matches?.("[data-document-workbench]")
      ? target
      : target?.querySelector?.("[data-document-workbench]"),
  );

  window.__applykitDocumentWorkbench = { destroy, initialize, initializeTarget };
  document.addEventListener("DOMContentLoaded", () => initializeTarget(document));
  document.addEventListener("htmx:beforeCleanupElement", event => {
    const target = event.detail?.elt;
    destroy(target?.matches?.("[data-document-workbench]") ? target : target?.closest?.("[data-document-workbench]"));
  });
  document.addEventListener("htmx:afterSwap", event => initializeTarget(event.detail?.target));
})();
