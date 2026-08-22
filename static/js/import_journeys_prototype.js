// PROTOTYPE ONLY: browser state for comparing import journey interaction models.
(() => {
  const variants = [
    { key: "A", name: "Guided handoff" },
    { key: "B", name: "Review workspace" },
    { key: "C", name: "Checkpoint conversation" },
  ];
  const journeys = {
    onboarding: {
      kicker: "Build your starting profile",
      acquireTitle: "Bring a resume, or start by hand.",
      acquireCopy: "We extract source facts only. Nothing is added to your profile until you review and save it.",
      processingTitle: "Reading source facts...",
      impactTitle: "No existing profile is changed.",
      impactCopy: "You will see a complete draft before the first save.",
      savedTitle: "Your profile is ready.",
      savedCopy: "The reviewed draft is now your Candidate Profile.",
      workspaceTitle: "Candidate profile",
      sourceName: "alex-morgan-resume.pdf",
      sourceMeta: "8 pages · text PDF · ready",
    },
    reimport: {
      kicker: "Replace your Candidate Profile",
      acquireTitle: "Build a replacement draft.",
      acquireCopy: "Your current profile remains live while you review a complete replacement. Saving resets existing Resumes.",
      processingTitle: "Building replacement draft...",
      impactTitle: "Save replaces the whole profile.",
      impactCopy: "Unchanged fields are recreated too, and all tailored Resumes reset. Cancel keeps everything as-is.",
      savedTitle: "Replacement committed.",
      savedCopy: "The Candidate Profile was recreated and its Resumes reset in one operation.",
      workspaceTitle: "Profile replacement",
      sourceName: "alex-morgan-2026.docx",
      sourceMeta: "DOCX · replacement candidate · ready",
    },
    application: {
      kicker: "Create a draft application",
      acquireTitle: "Start from a job posting.",
      acquireCopy: "Use a supported posting URL, or paste the description manually. You will confirm the Company and every extracted fact.",
      processingTitle: "Acquiring job posting...",
      impactTitle: "No application exists yet.",
      impactCopy: "Saving creates one Draft application. Duplicate roles are warned about, not blocked.",
      savedTitle: "Draft application created.",
      savedCopy: "The confirmed Company, posting facts, and resolved requirements were saved atomically.",
      workspaceTitle: "Draft application",
      sourceName: "jobs.example.com/platform-engineer",
      sourceMeta: "Supported ATS adapter · private provenance",
    },
  };
  const params = new URLSearchParams(window.location.search);
  const state = {
    variant: variants.some(({ key }) => key === params.get("variant")) ? params.get("variant") : "A",
    journey: journeys[params.get("journey")] ? params.get("journey") : "onboarding",
    stage: ["acquire", "processing", "review", "failure", "saved"].includes(params.get("stage")) ? params.get("stage") : "acquire",
    consent: false,
    corrections: 0,
  };

  const all = (selector) => [...document.querySelectorAll(selector)];
  const setText = (selector, value) => all(selector).forEach((node) => { node.textContent = value; });
  const writeUrl = () => {
    const next = new URL(window.location.href);
    next.searchParams.set("variant", state.variant);
    next.searchParams.set("journey", state.journey);
    next.searchParams.set("stage", state.stage);
    window.history.replaceState({}, "", next);
  };

  const render = () => {
    const content = journeys[state.journey];
    all("[data-variant]").forEach((node) => { node.hidden = node.dataset.variant !== state.variant; });
    all("[data-stage-panel]").forEach((node) => { node.hidden = node.dataset.stagePanel !== state.stage; });
    all("[data-stage-marker]").forEach((node) => {
      node.classList.toggle("bg-coral", node.dataset.stageMarker === state.stage);
      node.classList.toggle("text-white", node.dataset.stageMarker === state.stage);
    });
    all("[data-checkpoint]").forEach((node) => {
      const order = ["acquire", "processing", "review", "saved"];
      const activeIndex = state.stage === "failure" ? 1 : order.indexOf(state.stage);
      node.classList.toggle("opacity-50", order.indexOf(node.dataset.checkpoint) > activeIndex);
    });
    all("[data-failure-card]").forEach((node) => { node.hidden = state.stage !== "failure"; });
    all("[data-upload-panel]").forEach((node) => node.classList.toggle("hidden", state.journey === "application"));
    all("[data-url-panel]").forEach((node) => node.classList.toggle("hidden", state.journey !== "application"));
    all("[data-consent-checkbox]").forEach((node) => { node.checked = state.consent; });
    all("[data-save-button]").forEach((node) => { node.disabled = !state.consent; });
    all("[data-set-journey]").forEach((node) => {
      const active = node.dataset.setJourney === state.journey;
      node.classList.toggle("bg-ink", active);
      node.classList.toggle("text-white", active);
    });
    setText("[data-journey-kicker]", content.kicker);
    setText("[data-acquire-title]", content.acquireTitle);
    setText("[data-acquire-copy]", content.acquireCopy);
    setText("[data-processing-title]", content.processingTitle);
    setText("[data-impact-title]", content.impactTitle);
    setText("[data-impact-copy]", content.impactCopy);
    setText("[data-saved-title]", content.savedTitle);
    setText("[data-saved-copy]", content.savedCopy);
    setText("[data-workspace-title]", content.workspaceTitle);
    setText("[data-source-name]", content.sourceName);
    setText("[data-source-meta]", content.sourceMeta);
    setText("[data-correction-count]", String(state.corrections));
    setText("[data-state-output]", `journey=${state.journey} · stage=${state.stage} · consent=${state.consent} · corrections=${state.corrections}`);
    const current = variants.find(({ key }) => key === state.variant);
    setText("[data-variant-label]", `${current.key} — ${current.name}`);
    writeUrl();
  };

  const cycleVariant = (direction) => {
    const current = variants.findIndex(({ key }) => key === state.variant);
    state.variant = variants[(current + direction + variants.length) % variants.length].key;
    render();
  };
  all("[data-variant-previous]").forEach((node) => node.addEventListener("click", () => cycleVariant(-1)));
  all("[data-variant-next]").forEach((node) => node.addEventListener("click", () => cycleVariant(1)));
  all("[data-set-journey]").forEach((node) => node.addEventListener("click", () => {
    state.journey = node.dataset.setJourney;
    state.stage = "acquire";
    state.consent = false;
    state.corrections = 0;
    render();
  }));
  all("[data-set-state]").forEach((node) => node.addEventListener("click", () => {
    state.stage = node.dataset.setState;
    render();
  }));
  all("[data-consent-checkbox]").forEach((node) => node.addEventListener("change", () => {
    state.consent = node.checked;
    render();
  }));
  all("[data-add-correction]").forEach((node) => node.addEventListener("click", () => {
    state.corrections += 1;
    node.classList.add("ring-2", "ring-coral");
    render();
  }));
  all("[data-manual-fallback]").forEach((node) => node.addEventListener("click", () => {
    state.stage = "review";
    state.consent = true;
    state.corrections = 0;
    render();
  }));
  document.addEventListener("keydown", (event) => {
    if (["INPUT", "TEXTAREA"].includes(document.activeElement.tagName) || document.activeElement.isContentEditable) return;
    if (event.key === "ArrowLeft") cycleVariant(-1);
    if (event.key === "ArrowRight") cycleVariant(1);
  });
  render();
})();
