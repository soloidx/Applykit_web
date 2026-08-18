# ADR-0007: Application-Owned Tailored Documents

## Status

Accepted

## Decision

Resume and Cover Letter are separate Django app boundaries and private one-to-one child aggregates of Job Application. Resume creation is lazy and atomic on the account-scoped detail route. Resume content uses typed relational overlay rows over the Candidate Profile rather than a snapshot or generic JSON document. Candidate Profile values remain live until a Resume-specific override is saved; blank override values reset to inheritance.

Resume initialization materializes the fixed section set, current source membership, relevance partitions, and deterministic aggregated Skill Concept order exactly once. Reopening never reinitializes a saved document. Profile Skill Association changes integrate with existing Resumes through explicit transactional service calls, not signals or background jobs.

Resume saves are synchronous complete-draft transactions with server-rendered native controls. Cover Letters remain optional and are created only on a successful save. Cover Letter HTML is server-sanitized to its narrow allowed element and link protocol set. Both document aggregates are deleted through their Job Application and Account ownership cascades; shared Company and Skill Concept rows remain shared.

## Consequences

- Account ownership is resolved through Job Application for every document read and write.
- Resume source changes remain visible without rebuilding application-specific structure.
- Preview, export, AI, versioning, collaboration, and immutable submission snapshots remain outside this boundary.
