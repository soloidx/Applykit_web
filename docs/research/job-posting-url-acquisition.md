# Job-posting URL acquisition research

- Research question: GitHub issue [#53](https://github.com/soloidx/Applykit_web/issues/53), supporting parent map [#49](https://github.com/soloidx/Applykit_web/issues/49)
- Access date for every external source in this document: **2026-08-21**
- Scope: acquiring public job-posting content from a URL supplied by an authenticated ApplyKit candidate. This document does not specify downstream AI extraction or implement the feature.

## Executive conclusion

**Recommendation:** The initial product should accept only URLs recognized by explicitly supported, deterministic ATS adapters whose public posting APIs and terms have been reviewed for ApplyKit's use. Lever and Ashby are the strongest technical candidates found, but neither should be enabled until that review is recorded. Convert the submitted posting URL into a fixed, provider-owned API request, fetch only from an adapter-specific hostname allowlist, and return a candidate-reviewable draft. Do not ship arbitrary-domain server-side fetching, browser automation, agent/web-search retrieval, authenticated pages, CAPTCHA or challenge bypass, or a silent cascade between acquisition methods in the initial release.

This boundary is the smallest one consistent with the repository architecture and issue #49. ApplyKit is a server-rendered Django modular monolith ([ADR-0001](../adr/0001-build-a-server-rendered-django-modular-monolith.md)); it currently has no workers or durable asynchronous infrastructure, and the first feature requiring that infrastructure must make explicit retry, idempotency, scheduling, and observability decisions ([ADR-0005](../adr/0005-defer-asynchronous-and-generative-infrastructure.md)). A Job Application requires a Company, role title, and job description while the posting URL remains optional ([domain context](../../CONTEXT.md#job-application)). Acquisition should therefore improve form completion, not become a prerequisite for creating an application.

The conclusion above is a **recommendation/inference**, not a fact asserted by any source. The supporting sourced facts and tradeoffs follow.

## Repository constraints

The following are repository facts rather than external research findings:

- ApplyKit is a personal job-search assistant with private candidate workspaces. Job descriptions and Application Skill Requirements are private account data; Company and Skill Concept identities are shared public data ([domain context](../../CONTEXT.md#ownership-and-privacy-invariants)).
- A draft Job Application requires Company, role title, and job description; posting URL, location, compensation, source, and notes are optional ([domain context](../../CONTEXT.md#job-application)).
- Re-extracting skill requirements is additive and must not erase candidate edits ([ADR-0006](../adr/0006-separate-skill-concepts-from-candidate-and-application-associations.md)). URL acquisition must consequently produce source facts for review and must not directly replace existing application requirements.
- The architecture is synchronous Django with server-rendered HTML and no client-side application framework ([ADR-0001](../adr/0001-build-a-server-rendered-django-modular-monolith.md)). Production is a stateless web container with platform-supplied infrastructure ([ADR-0002](../adr/0002-use-environment-specific-django-settings-and-container-boundaries.md)).
- Workers, Redis, Celery, provider retention, deletion, audit, consent, and AI failure behavior are deliberately unresolved until a real workload requires them ([ADR-0005](../adr/0005-defer-asynchronous-and-generative-infrastructure.md)).

## Retrieval strategy comparison

| Strategy | What it does | Coverage | Determinism and fidelity | Security and policy surface | Operational fit | Recommendation |
| --- | --- | --- | --- | --- | --- | --- |
| Known-domain adapter | Recognizes an ATS URL, extracts provider identifiers, and calls a documented public posting endpoint on a fixed provider host | Narrow but measurable; one adapter per provider/URL family | Highest. Provider JSON has named fields and often both HTML and plain-text descriptions | Lowest of the three. Fixed hosts make allowlisting possible, but redirects, DNS, response limits, terms, and API changes still require controls | Good for a bounded synchronous request with a short timeout | **Initial release** |
| Generic server fetch plus extraction | Fetches an arbitrary public HTTP(S) page, then parses `JobPosting` JSON-LD and/or HTML | Broad static-page coverage | Medium to low. Markup can be absent, stale, contradictory, malformed, or unrelated; static fetches miss client-rendered content | High. User controls the initial destination; every redirect creates another destination; DNS rebinding, metadata endpoints, unusual IP forms, large bodies, and terms vary by site | Possible only with a hardened fetch boundary and strong time/size/rate limits; still produces frequent unsupported outcomes | **Defer; separately approve** |
| Browser automation | Runs a browser such as Playwright, waits for JavaScript, then reads the rendered DOM | Adds some JavaScript-rendered pages | Medium. Rendering state, localization, consent dialogs, timing, experiments, and bot defenses change output | Very high. Scripts can initiate many subrequests, redirects, downloads, WebSockets, and navigations; every request needs egress enforcement. Challenges and login remain intentional barriers | Poor synchronous fit; browser binaries, memory, latency, isolation, and cleanup create a worker-like workload | **Not an initial fallback** |
| Hosted agent, web fetch, or web search | Sends the URL/query to a model/vendor that retrieves or searches the web | Vendor-dependent and opaque | Low for exact acquisition. Search can choose a different or cached source; hosted fetch may reject the URL or omit dynamic content | Transfers URL/content to another processor; controls, retention, robots behavior, citations, cache freshness, and supported content types are provider-specific | Adds provider cost, latency, availability, and privacy dependencies; OpenRouter compatibility does not imply common retrieval tools | **Do not use for acquisition initially** |

### Why adapters are a different security case

OWASP separates SSRF defenses into calls to identified/trusted applications, where an allowlist is available, and calls to arbitrary external addresses, where it is not. OWASP recommends defense in depth at both application and network layers, warns that complete URLs are difficult to validate, and recommends disabling automatic redirects so validation cannot be bypassed ([OWASP SSRF Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html), accessed 2026-08-21).

**Inference:** An adapter should not merely be a scraper selected by hostname. Its security value comes from reducing an untrusted posting URL to validated provider identifiers and constructing a request from constants: fixed HTTPS API origin, fixed path template, and encoded identifiers. If an adapter passes an attacker-controlled URL through to a generic client, it loses this advantage.

## Mandatory SSRF and fetch controls

These controls are mandatory for any ApplyKit component that dereferences a user-supplied URL, including adapter URL probes, generic fetching, `robots.txt`, redirects, browser navigation, browser subresources, and callbacks made by an extraction library. They are **recommendations** derived from the cited standards and official security guidance.

1. **Use an allowlist whenever possible.** Adapter requests must use hard-coded schemes, hosts, allowed ports, and path templates. The adapter parses an input URL only to obtain bounded identifiers; it never uses the input authority as the API authority. OWASP states that allowlists are preferred and deny-lists are bypass-prone ([OWASP SSRF Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html), accessed 2026-08-21).
2. **Parse once with one standards-conforming URL implementation and compare canonical components, not substrings or regex alone.** Accept absolute `https` URLs initially; reject userinfo, fragments for retrieval, empty hosts, malformed percent encoding, IPv6 zone identifiers, non-default ports, and every non-HTTP(S) scheme. RFC 3986 defines authority as optional `userinfo`, host, and port and warns that userinfo can be crafted to look like a trusted domain ([RFC 3986 sections 3.2 and 7.6](https://www.rfc-editor.org/rfc/rfc3986.html#section-3.2), accessed 2026-08-21). HTTP(S) userinfo is deprecated ([RFC 9110 section 4.2.4](https://www.rfc-editor.org/rfc/rfc9110.html#section-4.2.4), accessed 2026-08-21).
3. **Canonicalize the hostname before policy checks.** Lowercase it, remove only a syntactically valid trailing root dot, convert internationalized names to ASCII IDNA form, and reject ambiguous or invalid authorities. Match complete host labels (`host == allowed` or `host.endswith("." + allowed)` only where subdomains are deliberately allowed), never `contains`/suffix text without a label boundary.
4. **Resolve and inspect every address.** Resolve all A and AAAA results before connection. Reject the request if any result is not globally reachable, including loopback, private/unique-local, link-local, shared address space, unspecified, multicast, documentation, benchmarking, reserved, and IPv4-mapped IPv6 forms. IANA maintains the authoritative IPv4 and IPv6 special-purpose registries ([IPv4 registry](https://www.iana.org/assignments/iana-ipv4-special-registry/iana-ipv4-special-registry.xhtml), [IPv6 registry](https://www.iana.org/assignments/iana-ipv6-special-registry/iana-ipv6-special-registry.xhtml), both accessed 2026-08-21). Python's `ipaddress.ip_address()` parses IPv4/IPv6, exposes `is_global`, and reports the underlying IPv4 address for IPv4-mapped IPv6 ([Python `ipaddress` documentation](https://docs.python.org/3/library/ipaddress.html), accessed 2026-08-21). Do not equate “not private” with safe: Python documents shared address space where both `is_private` and `is_global` are false.
5. **Prevent DNS rebinding and validation/connect time-of-check/time-of-use gaps.** After validating all DNS answers, connect to one of those exact validated addresses without asking the HTTP library to resolve the hostname again. Preserve the original validated hostname for TLS SNI, certificate verification, and the HTTP `Host`/`:authority`. Reject if the connection is not to the pinned address. OWASP explicitly identifies DNS pinning/rebinding as a bypass and says all A and AAAA results must be checked ([OWASP SSRF Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html), accessed 2026-08-21). Network egress policy must independently block non-public and internal destinations even if application checks fail.
6. **Disable automatic redirects and validate every hop.** Read each 3xx response manually, resolve a relative `Location` against the current URL according to RFC 3986, then repeat scheme, authority, port, hostname, DNS, IP, and terms/robots policy checks before the next request. Apply a small total hop limit (recommended: three), detect loops, and reject HTTPS-to-HTTP downgrade. HTTP redirects can point to a different URI through `Location` ([RFC 9110 sections 10.2.2 and 15.4](https://www.rfc-editor.org/rfc/rfc9110.html#section-15.4), accessed 2026-08-21); OWASP recommends disabling automatic redirect following to prevent validation bypass ([OWASP SSRF Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html), accessed 2026-08-21).
7. **Constrain the request.** Use only `GET` (or an adapter-documented safe method), verified TLS, a truthful stable ApplyKit user agent/contact URL, no ambient cookies, no user credentials, no `Authorization`, no client certificates, no browser session import, and no environment-derived proxy settings. Do not forward candidate headers or internal request headers. Never submit forms or call application endpoints.
8. **Constrain the response before parsing.** Set connect, TLS, first-byte, read, and total deadlines; cap compressed and decompressed bytes; cap header count/size; stream and abort above the limit; allow only expected JSON or HTML media types; reject archives and downloads; and limit parser depth, JSON nesting, and extracted text. RFC 9110 advises defensive parsing within reasonable buffers and defines `413 Content Too Large` ([RFC 9110 sections 2.3 and 15.5.14](https://www.rfc-editor.org/rfc/rfc9110.html#section-2.3), accessed 2026-08-21). Recommended initial limits are a 10-second total acquisition deadline, 2 MiB decoded body, 64 KiB headers, three redirects, and one fetch attempt; these numbers are product recommendations, not sourced protocol limits.
9. **Isolate and restrict egress.** Run retrieval with no access to loopback, RFC/IANA special-use networks, service discovery, databases, container/control-plane APIs, or cloud metadata. Permit DNS only through a controlled resolver and outbound TCP only to approved public destinations/ports. AWS documents its metadata endpoints at `169.254.169.254` and `[fd00:ec2::254]` and recommends requiring token-based IMDSv2; this is defense in depth, not permission to allow metadata traffic ([AWS EC2 IMDS documentation](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/configuring-instance-metadata-service.html), accessed 2026-08-21).
10. **Bound abuse and disclosure.** Require authentication and account scope, rate-limit per account and destination, limit concurrent fetches, avoid reflecting raw upstream bodies/headers/errors, log normalized destination/provider, redirect hosts, result class, byte count, and timing without query secrets, and retain only what the product lifecycle requires. The submitted URL itself can contain sensitive query tokens, so redact query and userinfo from logs and analytics.

### Browser-specific SSRF expansion

Playwright pages can navigate to URLs and pages in a browser context can open popups; context-level network routing applies to pages ([Playwright Python page documentation](https://playwright.dev/python/docs/pages), accessed 2026-08-21). **Inference:** validating only `page.goto()` is insufficient. A safe browser service must intercept and enforce the same destination policy for every document, script, stylesheet, image, font, XHR/fetch, WebSocket, worker, iframe, popup, download, service worker, DNS resolution, and navigation. It should block all nonessential resource classes by default, use a new isolated browser context per job, persist no cookies/storage/cache, expose no credentials or host mounts, and terminate the complete process tree on deadline. This is substantially more machinery than an HTTP adapter and is not compatible with treating browser fallback as a minor synchronous enhancement.

## Extraction after a safe fetch

Acquisition and extraction should be separate stages. The acquisition result should include the original normalized URL, final URL, adapter/provider, retrieval time, status, media type, and bounded raw source or source hash needed for debugging under the eventual retention policy. Extraction should return a typed draft with field-level provenance and warnings; it should not mutate a Job Application until the candidate confirms it.

### `JobPosting` JSON-LD

**Sourced facts:** JSON-LD is a W3C Recommendation and can be embedded in HTML `script` elements ([JSON-LD 1.1 sections 7 and 11](https://www.w3.org/TR/json-ld11/#embedding-json-ld-in-html-documents), accessed 2026-08-21). Schema.org defines `JobPosting` and properties including `title`, `description`, `hiringOrganization`, `jobLocation`, `baseSalary`, `skills`, `qualifications`, `responsibilities`, `employmentType`, `datePosted`, and `validThrough` ([Schema.org `JobPosting`](https://schema.org/JobPosting), accessed 2026-08-21). Google's official job-posting guidance says the structured data should be on a single-job leaf page, appear on the same page as the job description visible to users, and include title, description, hiring organization, and location or remote-location data; it also shows JSON-LD embedded as `application/ld+json` ([Google JobPosting structured-data documentation](https://developers.google.com/search/docs/appearance/structured-data/job-posting), accessed 2026-08-21).

**Recommended extraction algorithm:**

1. Parse HTML without executing scripts. Locate every `script[type="application/ld+json"]` within the body-size limit.
2. Parse each script as untrusted JSON with depth and object-count limits. Do not dereference remote `@context`, `@id`, or other URLs; remote context loading would create an additional SSRF channel. Recognize `https://schema.org`, `http://schema.org`, and equivalent local context forms without network access.
3. Walk top-level objects, arrays, and `@graph`; accept `@type` as a string or array and select nodes containing `JobPosting`. If exactly one credible node exists, map supported fields. If several exist, match `url`/`mainEntityOfPage` or provider identity to the final page URL; otherwise report ambiguity rather than guessing.
4. Treat JSON-LD as untrusted claims, not authoritative truth. Require a nonempty plausible title and substantial description. Decode the `description` HTML to text with a strict sanitizer/parser; never render source HTML directly in ApplyKit. Preserve paragraph/list boundaries.
5. Cross-check title, company, and a sample of description text against visible/static HTML when available. Warn on contradictions, expired `validThrough`, a list page, or a node whose canonical URL points elsewhere. Google requires markup and user-visible description to describe the same job, but that publisher rule does not guarantee compliance.
6. Record provenance per field (`provider_api`, `json_ld`, or `html`) and retain the original submitted URL separately from the final/canonical URL. Do not infer a Company solely from the URL host when `hiringOrganization` names another organization; staffing and hosted ATS pages are common domain-model cases.

### HTML extraction

**Recommended fallback within an already-approved fetch:**

1. Parse the static DOM with scripts, styles, templates, navigation, cookie dialogs, forms, related-job cards, and hidden elements excluded.
2. Prefer provider-specific selectors in a versioned adapter. For a future generic extractor, score semantic `main`/`article` regions using heading/title correspondence, text density, and job-language landmarks, but require a minimum description size and return ambiguity when multiple regions score similarly.
3. Preserve headings, paragraphs, and list-item boundaries as plain text. Normalize whitespace conservatively; do not summarize, rewrite, classify required/preferred skills, or silently remove equal-opportunity, compensation, location, or eligibility text at acquisition time.
4. Compare Open Graph/document title only as supporting evidence. Metadata snippets are often abbreviated and should not replace a full description.
5. Sanitize even if only plain text is expected, place length limits on every field, and escape all values on rendering. Source HTML is attacker-controlled content.

**Inference:** JSON-LD should precede generic visual-text heuristics because it has a job-specific vocabulary and explicit fields. Provider API fields should precede both because they avoid page-layout inference. HTML remains necessary because JSON-LD can omit useful prose or be stale, but disagreements must be visible to the candidate rather than silently merged.

## JavaScript rendering and anti-bot limitations

### Static fetching

A normal server-side HTTP client receives the server representation and does not execute JavaScript. It therefore misses descriptions populated by client-side API calls and JavaScript redirects. This is a capability limitation, not an error that should automatically trigger a more invasive method.

Anthropic's official web-fetch documentation gives a concrete hosted-tool example: its fetch tool retrieves text/HTML/PDF, does not support dynamically rendered JavaScript sites, can reject private addresses and `robots.txt`, can restrict allowed domains and request count, may return cached content, and warns that mixing untrusted content with sensitive data poses exfiltration risk ([Anthropic web-fetch tool documentation](https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-fetch-tool), accessed 2026-08-21). These are Anthropic-specific facts, not guarantees for OpenRouter or other vendors.

### Browser rendering

A browser can execute page JavaScript and observe the rendered DOM, but it does not guarantee success. Pages can require login, consent state, geolocation, interaction, delayed requests, or a human challenge. Rendering also changes the acquisition from one bounded response to an execution environment with many network requests.

Cloudflare documents that a Challenge Page can replace the requested response, sets `cf-mitigated: challenge`, and uses `text/html` regardless of the requested resource type ([Cloudflare Challenge response documentation](https://developers.cloudflare.com/cloudflare-challenges/challenge-types/challenge-pages/detect-response/), accessed 2026-08-21). **Recommendation:** Treat a challenge, CAPTCHA, login wall, access-denied response, or repeated `403`/`429` as a terminal “provider blocked automated access” outcome. Do not rotate proxies, spoof browser fingerprints, solve CAPTCHAs, import a user's session, retry through an agent, or otherwise circumvent the control.

### Agent or search retrieval

OpenAI's official web-search tool searches, opens, and finds within pages; results include citations and may use live or cached/indexed access depending on configuration. It is model-directed unless tool choice forces search, and it has model/tool-specific controls, rate limits, pricing, and context limits ([OpenAI web-search documentation](https://platform.openai.com/docs/guides/tools-web-search), accessed 2026-08-21). **Inference:** web search is not an exact-URL acquisition contract. It can discover or cite a different representation and is inappropriate as a silent fallback for importing the facts from the candidate's submitted posting.

Issue #49 names OpenRouter through an OpenAI-compatible API as the initial AI-provider direction. **Inference:** OpenAI-compatible chat/completions transport does not establish that every routed model/provider offers the same hosted browsing tool, SSRF controls, source fidelity, retention, or citations. Acquisition should remain an ApplyKit-controlled boundary and send only the already-acquired bounded text to an AI extraction boundary if that later decision is approved.

## Robots, terms, technical controls, and law are distinct

These categories must not be collapsed into a single “scraping allowed” flag:

| Category | What the primary source establishes | Product treatment |
| --- | --- | --- |
| `robots.txt` | RFC 9309 standardizes crawler-requested allow/disallow rules and explicitly says they are **not** access authorization or a substitute for security controls. A successful robots fetch must be followed; an unreachable robots file due to server/network errors means complete disallow for a crawler ([RFC 9309 sections 1, 2.3, and 3](https://www.rfc-editor.org/rfc/rfc9309.html), accessed 2026-08-21). | **Recommendation:** Use an identifiable ApplyKit crawler token and honor applicable disallow rules for generic automated retrieval. Fetch and cache robots safely, validating every redirect hop under the same SSRF policy. Treat unreachable/ambiguous robots as no generic fetch. An adapter's documented API is governed primarily by its API contract rather than page-crawl rules, but its terms still apply. |
| Contractual terms | Site/user/API terms can grant, restrict, or condition automated use independently of technical accessibility. LinkedIn's current User Agreement applies to visitors and members and prohibits software, scripts, robots, crawlers, browser plugins, or other processes used to scrape/copy the Services, as well as bypassing access controls ([LinkedIn User Agreement sections 1.2 and 8.2](https://www.linkedin.com/legal/user-agreement#dos), accessed 2026-08-21). Indeed's Terms state that access/use binds the user, limit job-seeker use to personal non-commercial job seeking, and subject API use to API-specific terms ([Indeed Terms of Service introduction, section A, and API terms](https://www.indeed.com/legal), accessed 2026-08-21). | **Recommendation:** Maintain a reviewed provider registry recording permitted URL families, approved endpoint/API, terms version/date, authentication, rate limits, attribution, storage, and removal duties. Do not support LinkedIn page acquisition. Do not support Indeed page acquisition without counsel/provider approval of the exact product flow. User submission of a URL does not grant ApplyKit rights. |
| Technical access controls | Authentication, CAPTCHAs, challenge pages, rate limits, bot blocks, and `403` responses are technical enforcement. Their presence or absence does not itself answer contractual or legal permission. Cloudflare documents challenge replacement behavior as one example ([Cloudflare Challenge response documentation](https://developers.cloudflare.com/cloudflare-challenges/challenge-types/challenge-pages/detect-response/), accessed 2026-08-21). | **Recommendation:** Never bypass. Stop and offer paste/manual entry. Respect `Retry-After` but do not schedule repeated retries in the synchronous initial boundary. |
| Legal interpretation | The standards and vendor documents above do not decide whether a proposed retrieval is lawful in every jurisdiction or factual setting. Contract formation, copyright, database rights, privacy, anti-circumvention, computer-access law, and exceptions are legal questions. | **Recommendation:** Obtain qualified legal review before enabling a provider or generic retrieval. This document is technical/product research, not legal advice, and deliberately makes no conclusion that robots permission, public visibility, or user direction makes retrieval lawful. |

**Important distinction:** A `robots.txt` allow rule is not a license; a disallow rule is not authentication; public accessibility is not contractual permission; contractual permission does not make bypassing a CAPTCHA technically safe; and a technical block is not a legal opinion.

## Concrete ATS/provider examples

### Lever: strong initial adapter candidate

**Sourced facts:** Lever's official Postings API documentation says published postings are publicly viewable, provides a JSON endpoint for a specific posting at `GET https://api.lever.co/v0/postings/{site}/{posting-id}` (and an EU host), and returns fields including title (`text`), location/categories, HTML and plain-text description variants, `hostedUrl`, `applyUrl`, workplace type, and optional salary fields. It also states that internal postings are not exposed and that the API is HTTPS-only ([Lever Postings API](https://github.com/lever/postings-api), accessed 2026-08-21).

**Recommended adapter:** Recognize only `https://jobs.lever.co/{site}/{uuid}` and `https://jobs.eu.lever.co/{site}/{uuid}` job-detail URLs, reject `/apply`, derive the corresponding fixed API origin (`api.lever.co` or `api.eu.lever.co`), percent-encode the bounded site and UUID, request JSON, and require returned `hostedUrl` to canonicalize to the submitted site/posting identity. Never call `applyUrl`. Keep global and EU origins distinct.

### Ashby: feasible initial adapter with a board-list constraint

**Sourced facts:** Ashby's official Job Postings API returns currently published postings from `GET https://api.ashbyhq.com/posting-api/job-board/{JOB_BOARD_NAME}`. Results include title, location, team/department, remote/workplace type, `descriptionHtml`, `descriptionPlain`, publication time, employment type, `jobUrl`, `applyUrl`, and optional compensation. The board name is the final path segment of the hosted board URL ([Ashby Job Postings API](https://developers.ashbyhq.com/docs/public-job-posting-api), accessed 2026-08-21).

**Recommended adapter:** Recognize only Ashby-hosted job URLs, derive a tightly validated board name, query the fixed `api.ashbyhq.com` endpoint, cap the response/list size, and select exactly one returned job whose normalized `jobUrl` equals the submitted URL. Reject no match or multiple matches. Never call `applyUrl`. Because the documented endpoint lists a board rather than fetching one posting, this adapter has a higher response-size/cost ceiling than Lever and needs stricter limits.

### SmartRecruiters: documented but credentialed; defer

**Sourced facts:** SmartRecruiters says its Posting API exposes postings made public through SmartRecruiters and is intended for customer-built career sites. Its current documentation says the Public Posting API supports API-key and OAuth client-credentials authentication and warns that valid credentials can access all internal postings because system-role/access-group configuration is not enforced by that API ([SmartRecruiters Posting API](https://developers.smartrecruiters.com/docs/posting-api), accessed 2026-08-21).

**Recommendation:** Do not place ApplyKit-owned customer credentials behind arbitrary user URL imports. Defer this adapter until the exact endpoint, licensing, credential ownership, scope, secret storage, internal-posting exposure, rate limits, and tenant authorization are reviewed. “Public Posting API” in the product name is not evidence of anonymous public access.

### LinkedIn and Indeed: not initial providers

LinkedIn's explicit anti-scraping and anti-access-control-bypass clauses make page acquisition outside an authorized LinkedIn API or written agreement an unsuitable initial boundary ([LinkedIn User Agreement](https://www.linkedin.com/legal/user-agreement#dos), accessed 2026-08-21).

Indeed's official terms impose user-purpose and API-specific conditions; this research did not establish an official anonymous API intended for a third-party personal assistant to retrieve an arbitrary Indeed job URL ([Indeed Terms of Service](https://www.indeed.com/legal), accessed 2026-08-21). **Recommendation:** Detect these domains and explain that automated import is unavailable; offer paste/manual entry. Do not route around the restriction by following an outbound apply URL automatically, because that would turn the unsupported URL into an arbitrary-domain fetch without informed user control.

## Recommended initial product boundary

This entire section is a **product/architecture recommendation**, not a sourced fact.

### Supported behavior

- Add “Import from posting URL” as an optional convenience on Job Application creation, while retaining the existing manual Company, role title, and job-description path.
- Subject to recorded terms/legal approval, initially support exact Lever and Ashby job-detail URL families through deterministic adapters. Keep the adapter registry closed by default and versioned in code/configuration.
- Perform one synchronous, bounded acquisition request. If the deadline is exceeded, return a recoverable failure; do not continue work after the HTTP response and do not add background infrastructure implicitly.
- Extract only source facts: role title, hiring-organization name, full job description, location/remote wording, compensation wording/structured range when supplied, source/provider, canonical posting URL, and provider posting identifier. Preserve source wording. Do not infer requirements, company identity merges, or application state during acquisition.
- Present a review form showing every imported field before creation. The candidate can edit or omit each field. Never auto-submit an application to the employer.
- Store the candidate-submitted posting URL and accepted text according to the Job Application lifecycle. Do not retain full raw provider responses beyond a short, explicitly decided diagnostic window; this retention period remains unresolved below.
- Add providers only after a review fixture demonstrates URL recognition, API construction, redirect behavior, response limits, field mapping, terms/API basis, and failure behavior.

### Explicitly unsupported initially

- Arbitrary employer career pages, URL shorteners, search-result/list pages, LinkedIn, Indeed, authenticated/intranet postings, emailed tracking links, local files, PDFs reached through posting URLs, and non-HTTPS URLs.
- Browser rendering, CAPTCHA solving, login/session/cookie import, proxy rotation, residential proxies, stealth plugins, or retrying through another acquisition method.
- Hosted model/agent web search or fetch as a source of record.
- Recursive crawling, related-job discovery, automatic following of “Apply” links, and periodic refresh of imported postings.
- Silent fallback from an adapter API to scraping the provider's HTML. A provider API regression must be visible and fixed deliberately.

### Why not generic fetching in v1

The generic fetcher can be engineered safely only with a dedicated outbound-request boundary, DNS pinning, manual redirect handling, network egress enforcement, robots/terms policy, abuse limits, parser hardening, and operational monitoring. That is feasible, but it is not a small extension to a form. Its broad domain set also prevents a one-time terms review. Implementing it later should be its own decision and threat model, with an allowlisted pilot before any “all public sites” claim.

## Failure and fallback UX

This section is a **recommendation**.

Return stable user-facing outcome classes rather than leaking upstream details:

| Outcome | Candidate message | Offered action |
| --- | --- | --- |
| Invalid or unsafe URL | “This does not look like a supported public job-posting URL.” | Correct URL or continue with manual fields |
| Unsupported provider | “Automatic import is not available for this site.” | Paste the job description, enter details manually, and keep the URL as reference |
| Listing/search/apply URL instead of detail URL | “Use the page for one specific job, not a search or application form.” | Replace URL or enter manually |
| Posting not found/closed | “The provider no longer returns this posting.” | Open the submitted URL in the candidate's own browser, paste content, or enter manually |
| Blocked by login, robots, terms policy, CAPTCHA, or anti-bot control | “This site does not permit this automated import path.” | Paste content or enter manually; never imply that repeated retries will bypass it |
| Timeout, `429`, or temporary provider failure | “The provider did not respond in time. Your entries have not been lost.” | Retry once manually after a displayed delay or continue manually |
| Content too large/invalid/ambiguous | “We found the page but could not identify one complete job posting safely.” | Paste the relevant description or enter manually |
| Partial extraction | “Some details were imported. Review highlighted missing or conflicting fields.” | Edit every field before confirmation |

UX invariants:

- Keep all candidate-entered form values after failure.
- Do not say “AI failed” when acquisition failed before AI.
- Do not expose DNS/IP policy, redirect targets with secret queries, raw HTML, provider headers, or stack traces.
- Show provider and retrieval time and link to the original URL in the review screen.
- Require candidate confirmation; never treat imported content as verified, current, or complete.
- If the user pastes text, treat it as user-supplied source material and apply the same length, sanitization, provenance, and review rules without making a network request.

## Unresolved decisions and fog

These decisions remain open and should be resolved by parent map #49 or follow-up tickets before implementation beyond the narrow adapter boundary:

1. **Terms/legal approval owner:** Who reviews each provider's page/API terms, at what cadence, in which jurisdictions, and what evidence is recorded? No source can replace project-specific legal advice.
2. **Commercial-use characterization:** ApplyKit serves an individual's job search, but ApplyKit itself is a product. Counsel must decide how provider “personal,” “non-commercial,” and API-purpose clauses apply.
3. **Provider rollout:** Are Lever and Ashby sufficient for the first slice, and what observed user demand justifies each additional adapter?
4. **URL retention:** May posting URLs contain candidate-specific or expiring query tokens? Decide query stripping, encryption, display, logs, analytics, retention, and deletion behavior.
5. **Raw-source retention:** Decide whether to retain no raw response, a hash, or a short-lived encrypted snapshot for audit/debugging, and define deletion with the owning Job Application/Account.
6. **Synchronous budget:** Confirm request/body/redirect limits with the deployment platform's web timeout and concurrency. A longer or retried workflow may trigger ADR-0005's requirement for a durable asynchronous-infrastructure decision.
7. **Network enforcement:** The generic container platform in ADR-0002 is unspecified. Determine whether it supports an egress firewall/proxy and DNS policy. Application-only SSRF checks are not sufficient defense in depth.
8. **DNS pinning implementation:** Select an HTTP stack or egress proxy that can connect to a validated IP while preserving TLS SNI/certificate hostname and HTTP authority without a second DNS lookup. This must be proven with rebinding tests.
9. **Robots policy for one-shot user-directed fetches:** RFC 9309 defines crawler behavior but does not provide project-specific legal interpretation. The recommendation here is conservative; legal/product owners must ratify the user-agent token, cache behavior, and no-fetch outcomes.
10. **Attribution and source display:** Provider API/terms may require branding, links, or refresh/removal behavior. Review each adapter before release.
11. **Posting freshness:** Decide whether an import is a one-time candidate-reviewed snapshot or may later refresh. Automatic refresh introduces changed/removed content, re-extraction semantics, scheduling, and terms questions and is not recommended initially.
12. **Company resolution:** A provider host is not the hiring company. Specify how imported `hiringOrganization` proposes a shared Company without incorrectly merging staffing clients, confidential employers, or subsidiaries.
13. **Downstream AI boundary:** URL acquisition should finish before AI processing. Parent #49 still needs consent, fields sent, provider retention/training, redaction, deletion, audit, cost, rate, and prompt-injection controls required by ADR-0005.
14. **Untrusted-content prompt injection:** Job descriptions can contain instructions aimed at a later model/agent. Decide how downstream extraction treats source text strictly as data and prevents it from selecting tools or destinations. This risk is another reason not to combine acquisition with an autonomous browser agent.
15. **Accessibility/manual fallback:** Specify the paste/manual review form and non-JavaScript behavior consistent with ADR-0001 before calling adapter coverage sufficient.

## Source register

Only standards, official security guidance, official project documentation, and official vendor documentation/terms were used. All were accessed **2026-08-21**.

- [RFC 3986: Uniform Resource Identifier (URI): Generic Syntax](https://www.rfc-editor.org/rfc/rfc3986.html)
- [RFC 9110: HTTP Semantics](https://www.rfc-editor.org/rfc/rfc9110.html)
- [RFC 9309: Robots Exclusion Protocol](https://www.rfc-editor.org/rfc/rfc9309.html)
- [OWASP Server-Side Request Forgery Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html)
- [IANA IPv4 Special-Purpose Address Space](https://www.iana.org/assignments/iana-ipv4-special-registry/iana-ipv4-special-registry.xhtml)
- [IANA IPv6 Special-Purpose Address Space](https://www.iana.org/assignments/iana-ipv6-special-registry/iana-ipv6-special-registry.xhtml)
- [Python `ipaddress` library documentation](https://docs.python.org/3/library/ipaddress.html)
- [AWS EC2 Instance Metadata Service documentation](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/configuring-instance-metadata-service.html)
- [W3C JSON-LD 1.1 Recommendation](https://www.w3.org/TR/json-ld11/)
- [Schema.org `JobPosting`](https://schema.org/JobPosting)
- [Google JobPosting structured-data documentation](https://developers.google.com/search/docs/appearance/structured-data/job-posting)
- [Lever Postings API](https://github.com/lever/postings-api)
- [Ashby Job Postings API](https://developers.ashbyhq.com/docs/public-job-posting-api)
- [SmartRecruiters Posting API](https://developers.smartrecruiters.com/docs/posting-api)
- [Playwright Python page documentation](https://playwright.dev/python/docs/pages)
- [Cloudflare Challenge response documentation](https://developers.cloudflare.com/cloudflare-challenges/challenge-types/challenge-pages/detect-response/)
- [Anthropic web-fetch tool documentation](https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-fetch-tool)
- [OpenAI web-search documentation](https://platform.openai.com/docs/guides/tools-web-search)
- [LinkedIn User Agreement](https://www.linkedin.com/legal/user-agreement)
- [Indeed Terms of Service](https://www.indeed.com/legal)

## Research limitations

- Vendor documentation and terms can change after the access date. The adapter registry needs an owner and periodic review; this artifact neither establishes initial permission nor supplies continuing permission.
- This was documentation research, not live interoperability testing. No requests were made against sample posting APIs, redirect chains, robots files, challenge pages, or deployment-network controls.
- The official Greenhouse developer page redirected to a sign-in page during this research, so no Greenhouse capability claim or initial-adapter recommendation is made here. Workday and other ATSs were not added without a verified official public posting API and applicable terms.
- Indeed's terms are long and jurisdiction/entity dependent. This document recommends deferral rather than interpreting them as categorical legal prohibition.
- Hosted retrieval vendors expose different controls and may change behavior by model, region, or API version. Anthropic and OpenAI examples are concrete provider constraints, not guarantees about OpenRouter.
- No legal conclusion is offered. Qualified counsel must assess contracts and applicable law for the actual product, operator, users, providers, locations, storage, and use.
