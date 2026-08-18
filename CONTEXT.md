# ApplyKit Domain Context

ApplyKit is a personal job-search assistant. It helps a candidate organize a career profile, run a focused application campaign, track each job application through recruitment, and see upcoming recruitment events.

## Current product boundary

The first release is the tracking core:

- verified-email account registration and sign-in
- one private candidate workspace per account
- a structured career profile
- application campaigns with weekly and monthly submission goals
- job applications with stage history
- upcoming recruitment events on the dashboard

Tailored Resume workbenches are available for Job Applications. Cover Letter authoring, company research, AI suggestions, and email reminders remain future capabilities. Do not create speculative abstractions for them.

## Core workflow

1. A candidate registers with an email address and verifies it.
2. The candidate supplies at least a full name and timezone; other career-profile sections can be completed progressively.
3. The candidate activates a campaign with weekly and monthly submission targets.
4. The candidate creates a draft application for a company and supplies a role title and job description.
5. Moving the application to `Submitted` records its first submission time and contributes to campaign progress.
6. The candidate records later stage changes and recruitment events.
7. The dashboard shows campaign progress and upcoming scheduled events.

## Glossary

### Account

The authentication identity for one candidate. Email is unique, sign-in uses email and password, and email verification is required. An account owns one private workspace and at most one Candidate Profile.

Do not call an Account a customer, member, tenant, or workspace user.

### Candidate Profile

The candidate's structured source data. It contains identity and contact details, professional summary, Experience entries and highlights, Education entries, Projects, Skills, and Languages.

The profile is relational. A previously used JSON resume shape is neither the persistence model nor a supported import/export contract.

Only full name and timezone are required before using tracking features. All other sections are progressively completed.

The timezone is stored as an IANA timezone identifier. Candidates choose it from every identifier available in the installed IANA timezone database; the selector displays each identifier with its current UTC offset, but the offset is not stored.

Do not shorten this term to CV when referring to the source data. A future Resume is a document derived from this profile.

### Skill Concept

A globally shared canonical identity for one public hard skill, such as `Node.js`. A Skill Concept has a preferred canonical name and deterministic aliases such as `nodejs` and `NodeJS`. Its stable identifier, not its canonical name, is the matching identity because names and aliases may be corrected or merged later.

The catalog includes technologies, tools, methods, certifications, and domain skills, but not soft traits. Version labels collapse into the broader capability rather than creating version-specific concepts. Any unknown candidate-entered hard-skill label creates a globally shared Skill Concept. This deliberately treats submitted skill names as public catalog data.

Exact normalized aliases resolve automatically. The MVP does not infer aliases from string similarity or an AI provider. Future automated semantic merges must retain aliases and stable references, and uncertain merges require review.

### Candidate Skill Association

A private assertion that a candidate used or possesses a Skill Concept. Profile Skills, Experience Skills, and Project Skills are independent lists: adding a skill in one location does not add it to another. Each association preserves the candidate's trimmed entered label for display, including its spelling, case, and punctuation, and may reference a Skill Concept only once within its location.

Candidate Skill Associations have display order but no proficiency, duration, or recency metadata in the MVP. Matching treats a concept as one binary capability even when it appears in several locations; repeated associations provide explanatory evidence but do not increase match strength.

### Campaign

A candidate-owned container for a focused job-search effort. Each Campaign defines positive integer weekly and monthly submission targets and is either `Active` or `Archived`. When activated, it snapshots the Candidate Profile's timezone.

An account may have at most one Active Campaign. Every Job Application belongs to exactly one Campaign.

### Campaign Progress

The number of Job Applications in a Campaign whose first submission time falls within a calendar week or month. Calendar boundaries use the Campaign's snapshotted timezone, and weeks begin on Monday. Later Candidate Profile timezone changes do not rebucket submissions.

Progress is derived data, not a manually completed flag.

### Company

A globally shared public identity referenced by Job Applications. A normalized website domain is its canonical identity when known. Normalize an internationalized hostname to IDNA and use its public-suffix-aware registrable domain, so a host such as `jobs.example.co.uk` identifies `example.co.uk`. A Company without a website domain is provisional and may later be merged by an administrator.

Authenticated users may create Companies, but canonical identity fields are immutable to ordinary users after creation. Only administrators may correct or merge them. A merge reassigns all Job Applications to the surviving Company and retains the duplicate identity as an alias. Private notes, contacts, research, and recruiting observations never belong on the shared Company.

### Job Application

A candidate's attempt to obtain one role at one Company within one Campaign. A draft requires a Company, role title, and job description. Posting URL, location, compensation, source, and private notes are optional.

Duplicate applications are allowed because a candidate may apply to the same Company or role more than once.

### Application Skill Requirement

A private, candidate-editable hard-skill requirement attached to one Job Application and mapped to one shared Skill Concept. It preserves the trimmed extracted or entered label for display, including its spelling, case, and punctuation, and classifies the requirement as `Required` or `Preferred`.

The candidate may add, edit, or remove requirements. Re-extracting requirements after a job-description change only adds newly found concepts; it does not remove requirements that are no longer present in the text.

### Resume

An application-owned, private document that opens lazily for one Job Application. A Resume is a relational live overlay over its Account's Candidate Profile: source content inherits current profile values until a Resume-specific override is saved. Blank overrides reset to inheritance. Membership, inclusion, ordering, and overrides are application-specific, and Reset Resume rebuilds them from current profile and application requirements. A Resume cannot be independently deleted; deleting its Job Application deletes it.

### Cover Letter

An optional application-owned, private document. Reading an application does not create one. The first successful save creates sanitized content; deleting it returns the application to Not created. Deleting its Job Application or Account deletes it.

### Skill Coverage

The live comparison between a Job Application's Skill Concepts and the unique Skill Concepts referenced by all of the candidate's Profile, Experience, and Project skill associations. Coverage is shown as matched and missing lists separated into Required and Preferred requirements. It is derived on every read and is not persisted as a score or snapshot.

### Application Stage

The current recruitment state of a Job Application:

- `Draft`
- `Submitted`
- `Interviewing`
- `Offer`
- `Accepted`
- `Rejected`
- `Withdrawn`

Stage transitions are flexible rather than strictly ordered. `Offer` means an offer exists; `Accepted` is the successful outcome. An explicit correction may move an Accepted application to another stage and must append another Stage Transition. `Rejected` records an employer decision, while `Withdrawn` records the candidate's decision.

### Stage Transition

An append-only, timestamped record of a Job Application changing from one Application Stage to another. A Job Application also stores its current stage for efficient reads. Both values must be updated by the same domain operation.

The first transition into `Submitted` sets the immutable first submission time. Moving to another stage does not clear or replace that time.

### Applications Board

The read-only view of the Active Campaign's Job Applications, grouped into one column for each Application Stage. It shows all applications in each column with the most recently updated first. Each card links the role title to the Job Application detail page and shows the Company name as supporting context.

The board does not change Application Stages or expose other application mutations. Those actions remain on the Job Application detail page so stage changes continue through the operation that records a Stage Transition. If there is no Active Campaign, the board shows all seven empty stage columns and does not offer application creation.

### Recruitment Event

A candidate-owned upcoming or historical item attached to a Job Application. Event types are `Follow-up`, `Interview`, `Assessment`, `Deadline`, `Offer response`, and `Custom`. An event is `Scheduled`, `Completed`, or `Cancelled`.

The first release displays upcoming events on the dashboard and does not send reminders.

## Ownership and privacy invariants

- Candidate Profile, Campaign, Job Application, Stage Transition, and Recruitment Event data is private to one Account.
- Every query and mutation of private data must be scoped to the authenticated Account.
- A shared Company must contain only public canonical identity data.
- Skill Concepts and their aliases are globally shared public catalog data. Candidate Skill Associations and Application Skill Requirements remain private to one Account.
- Deleting an Account deletes its private domain data but does not delete shared Companies.
- Users may hard-delete Job Applications. Because Campaign Progress is derived from existing applications, deleting a submitted application changes any current or historical goal calculation that included it. The UI must warn about that consequence.

## Capability boundaries

The initial Django apps are:

- `accounts`: custom account model and django-allauth integration
- `profiles`: Candidate Profile and its ordered career-history records
- `campaigns`: Campaign lifecycle, targets, and progress calculations
- `applications`: Company, Job Application, Stage Transition, and Recruitment Event
- `resumes`: application-owned Resume live overlays and deterministic initialization
- `cover_letters`: optional application-owned Cover Letter content

AI-assisted research and suggestions need a separate decision about provider boundaries, consent, retention, and handling of candidate data before implementation.

## Unresolved future questions

- Which AI providers are permitted to receive candidate and job-description data
- What consent, redaction, retention, and deletion rules apply to AI requests and outputs
- When asynchronous tasks, Redis, Celery, and email reminders become necessary
