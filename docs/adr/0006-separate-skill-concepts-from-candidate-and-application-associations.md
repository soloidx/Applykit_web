# ADR-0006: Separate skill concepts from candidate and application associations

- Status: Accepted
- Date: 2026-08-13

## Context

Candidates may use different labels for the same hard skill, such as `node`, `nodejs`, `NodeJS`, and `Node.js`. ApplyKit needs to associate skills independently with the Candidate Profile, Experiences, Projects, and Job Applications, then compare candidate capabilities with application requirements.

The existing profile-owned Skill stores a display name and a case-folded `normalized_name`. Adding a `canonical_name` to that record would mix three different concerns: shared skill identity, a candidate's private assertion, and the label displayed in one location. String identity would also make canonical renames and duplicate merges disruptive to matching references.

## Decision

Model a globally shared Skill Concept separately from private associations:

- a Skill Concept represents one public hard-skill identity and has a stable database identifier and canonical display name
- deterministic normalized aliases map entered labels to a Skill Concept
- matching compares stable Skill Concept identifiers, never canonical-name strings
- an unknown entered hard-skill label creates a globally shared Skill Concept
- candidate-entered display labels remain on private Profile, Experience, and Project associations
- Profile, Experience, and Project associations are independent and unique by Skill Concept within their respective location
- Job Applications have separate candidate-editable Skill Requirements, unique by Skill Concept per application and classified as Required or Preferred
- deleting one private association does not delete the shared Skill Concept or another association

For the MVP, alias resolution is deterministic. Do not use string similarity or an AI provider to infer semantic equivalence. Version labels map to the broader capability. Future automated semantic merging must preserve losing names as aliases and redirect references to a stable surviving concept; uncertain merges require review.

Derive Skill Coverage live by comparing the unique set of candidate Skill Concept identifiers across Profile, Experience, and Project associations with the Job Application's Required and Preferred concepts. Show matched and missing lists and do not persist a percentage, result, or historical snapshot.

## Consequences

- A `canonical_name` remains useful as a preferred display label but is not sufficient as the domain identity or matching key.
- Aliases can change and concepts can merge without rewriting candidate-entered display labels.
- The same capability appearing in several candidate locations does not inflate coverage, while associations can explain where the candidate used it.
- Application requirements can evolve independently from candidate capability assertions.
- Re-extraction is additive: it adds newly found requirements and leaves existing requirements untouched, even if their source wording disappears.
- Globally creating concepts from unknown labels can publish typos, proprietary terminology, or inappropriate text entered by a candidate. This is an accepted catalog-quality and privacy risk and requires moderation and merge tooling as the catalog grows.
- Any future provider-assisted extraction or semantic merging remains subject to ADR-0005's consent, retention, redaction, deletion, and audit decisions.
