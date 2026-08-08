# ADR-0003: Use personal workspaces and shared company identities

- Status: Accepted
- Date: 2026-08-08

## Context

ApplyKit stores sensitive career and recruitment data. The first release serves individual candidates rather than teams. Companies, however, represent public organizations that may be referenced by many candidates and later enriched through public research.

A globally shared Company creates a risk that one candidate can change another candidate's application context or expose private observations.

## Decision

Use a custom Django account model from the first migration. Email is the unique authentication identity. Use django-allauth for open self-service registration, email-and-password sign-in, and mandatory email verification.

Each Account owns one private workspace and at most one Candidate Profile. Scope every private domain query and mutation to the authenticated Account.

Model Company as a globally shared public identity:

- an IDNA-normalized, public-suffix-aware registrable website domain is the canonical key when known
- name-only companies are provisional and may later be merged
- authenticated users may create a Company
- ordinary users cannot edit canonical identity fields after creation
- only administrators may correct or merge Companies
- merging reassigns Job Applications to the surviving Company and retains the duplicate identity as an alias
- private notes, contacts, research, and recruitment observations are account-owned data outside Company

## Consequences

- Authorization is a domain invariant rather than only a view concern.
- The custom account model avoids a disruptive user-model migration later.
- Shared Company creation needs public-suffix data, normalization tests, and duplicate feedback.
- Name-only records can duplicate one another until administrators merge them, and aliases must prevent those identities from being recreated.
- Deleting an Account removes private records but does not remove shared Companies.
