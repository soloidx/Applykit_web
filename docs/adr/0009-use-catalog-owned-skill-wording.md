# ADR-0009: Use catalog-owned skill wording

- Status: Accepted
- Date: 2026-08-25

## Context

ADR-0006 separated globally shared Skill Concepts from private candidate and application relationships, but kept candidate-entered wording on those private relationships. AI-assisted imports need one authoritative identity and wording model that can reconcile semantic proposals, preserve consistent matching, and expose ambiguous mappings for review without allowing private labels to drift from the shared catalog.

## Decision

Skill Concepts and Skill Aliases own skill identity and wording. Candidate Profile, Experience, and Project skill associations reference a Skill Concept and display its canonical name without storing a private label. Application Skill Requirements reference a Skill Alias, display that alias, and derive their effective Skill Concept through it without storing redundant concept or label fields. Transactional skills-domain operations enforce one effective concept per private location and perform all catalog mutations.

Concept merges move aliases and references to an administrator-selected survivor, resolve relationship collisions according to the operation's reviewed plan, append an immutable audit record, and hard-delete the losing concept. Alias reassignment similarly updates every referencing requirement atomically, collapses effective-concept collisions, and is audited. Referenced aliases and canonical aliases cannot be deleted; an unreferenced noncanonical alias may be deleted through an audited domain operation.

Because this decision is made before a production instance exists, implementation may use a destructive schema migration with no legacy-data backfill or compatibility period. Existing development databases are disposable and must be recreated. AI-assisted imports must not ship until catalog health checks, administrative dry-run and confirmation workflows, deletion protections, audit behavior, and PostgreSQL collision tests pass.

## Consequences

Canonical Concept renames and Alias reassignments deliberately change displayed wording or effective matching wherever those shared records are referenced. Application Requirement uniqueness by effective concept cannot use an ordinary joined database constraint, so every mutation locks the owning Job Application and enforces the invariant in a transactional domain service, backed by catalog health checks. Resume Skills inherit canonical Concept names while retaining application-specific label overrides.

This ADR supersedes ADR-0006's private association-owned label model. The complete collision and repair policy is recorded in [Design skill relationship migration and catalog repair](https://github.com/soloidx/Applykit_web/issues/65).
