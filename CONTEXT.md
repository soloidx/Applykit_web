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

Resume generation, cover-letter generation, company research, AI suggestions, and email reminders are future capabilities. Do not create empty apps or speculative abstractions for them.

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
- Deleting an Account deletes its private domain data but does not delete shared Companies.
- Users may hard-delete Job Applications. Because Campaign Progress is derived from existing applications, deleting a submitted application changes any current or historical goal calculation that included it. The UI must warn about that consequence.

## Capability boundaries

The initial Django apps are:

- `accounts`: custom account model and django-allauth integration
- `profiles`: Candidate Profile and its ordered career-history records
- `campaigns`: Campaign lifecycle, targets, and progress calculations
- `applications`: Company, Job Application, Stage Transition, and Recruitment Event

Future capabilities should use `resumes` and `cover_letters` app names when their behavior is implemented. AI-assisted research and suggestions need a separate decision about provider boundaries, consent, retention, and handling of candidate data before implementation.

## Unresolved future questions

- Whether a Resume snapshots profile data or always reflects the latest Candidate Profile
- How tailored Resume and Cover Letter variants relate to a Job Application
- Which AI providers are permitted to receive candidate and job-description data
- What consent, redaction, retention, and deletion rules apply to AI requests and outputs
- When asynchronous tasks, Redis, Celery, and email reminders become necessary
