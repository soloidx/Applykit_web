# ADR-0008: Use Application-Owned Document Workbenches

- Status: Accepted
- Date: 2026-08-21
- Supersedes: ADR-0007

## Context

Each Job Application needs private tailoring without copying the Candidate Profile into a document snapshot or letting application-specific edits change shared source data. Resume and Cover Letter also have different lifecycle and content requirements: a Resume is a structured live overlay, while a Cover Letter is an optional rich-text document. This decision consolidates and extends the narrower architecture record in ADR-0007 now that both workbenches are implemented.

## Decision

Keep Resume and Cover Letter in separate `resumes` and `cover_letters` Django app boundaries. Each document is a private one-to-one child aggregate of Job Application, and every read and mutation resolves ownership through the authenticated Account's Job Application. A Resume is created and initialized lazily when its workbench is first opened. A Cover Letter remains Not created until its first successful save.

Represent a Resume as a typed relational live overlay over Candidate Profile records, not as generic JSON or a content snapshot. Rendered scalar values inherit from their source until a sticky Resume override is saved; blank overrides reset to inheritance. Membership, inclusion, and ordering remain application-specific structural state. Typed source relationships preserve source identity, while aggregated Resume Skills use the stable Skill Concept identity defined by ADR-0006. Candidate Skill Association creation and deletion integrate with existing Resumes through explicit services in the same transaction, not Django signals, background work, or lazy orphan repair.

Submit each complete normalized document draft as one synchronous, atomic page-wide save. Resume reset actions change only the browser-local draft until that draft is saved. Resume cannot be independently deleted; Cover Letter can be explicitly deleted back to Not created. Deleting the owning Job Application or Account cascades both private documents without deleting shared Company or Skill Concept identities.

Progressively enhance the Cover Letter textarea with a pinned, self-hosted Quill editor. Sanitize HTML on every server write and persist only the narrow supported paragraph, emphasis, list, line-break, and safe-link vocabulary. Stored sanitized HTML is the only rich content rendered back to the candidate.

## Consequences

- Candidate Profile edits continue to flow into inherited Resume content without overwriting application-specific structure or overrides.
- Document creation, saves, resets, and deletion have explicit transactional lifecycle boundaries.
- Preview, PDF/DOCX export, rendered files, print layout, AI, semantic matching, asynchronous infrastructure, named variants, version history, collaboration, and immutable submission snapshots remain outside this decision.
