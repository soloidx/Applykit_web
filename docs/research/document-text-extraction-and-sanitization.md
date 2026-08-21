# Document text extraction and sanitization research

- Research ticket: [#51](https://github.com/soloidx/Applykit_web/issues/51)
- Parent map: [#49](https://github.com/soloidx/Applykit_web/issues/49)
- Researched: 2026-08-21
- Scope: DOCX and text-based PDF uploads before AI-assisted import

## Decision summary

Use a deliberately small, format-specific stack:

- **DOCX:** use `mammoth` for body text and `python-docx` for active header/footer
  stories that Mammoth omits. Do not generate or render HTML. Mammoth explicitly supports tables,
  footnotes/endnotes, text boxes, links, and raw-text extraction; `python-docx` exposes document-order
  traversal for paragraphs/tables and headers/footers. [1][2][3]
- **PDF:** use `pypdf` in strict mode, page by page, with fixed text-extraction options. It is pure
  Python, BSD-licensed, explicitly supports Python 3.14, and is under active security and robustness
  maintenance. [4][5][6]
- **Detection:** treat the filename extension and browser MIME type as hints only. Identify PDF by
  its header plus successful strict parsing. Identify DOCX as a bounded ZIP package with the expected
  Office Open XML content types and relationships plus successful parsing. Do not add `libmagic` for
  two known formats: its Python wrapper adds a native deployment dependency, was last released in
  2022, and still cannot replace parser validation. [7][8][9]
- **Safety:** reject before parsing when fixed input/package limits fail, then parse synchronously in
  a disposable child process with wall-clock, CPU, address-space, file-size, and output limits. A
  timeout in the web process alone cannot contain an out-of-memory parser. Python exposes Unix
  resource limits and kills timed-out subprocesses; this fits ApplyKit's Linux container without
  requiring Celery or a durable worker. [10][11]
- **Sanitization:** return canonical plain text, not HTML. Normalize line endings and Unicode to NFC,
  remove NUL and non-layout control characters, bound whitespace and output length, and preserve all
  other source wording. Document text remains untrusted data at the AI boundary; extraction must not
  attempt to guess and delete prompt-injection-like prose. [12]
- **Fidelity:** fail with an actionable "could not reliably extract text; re-export as DOCX or
  text-based PDF" result for encrypted, malformed, image-only, empty, truncated, or over-budget
  documents. Do not silently import partial output and do not add OCR in this scope.

These are implementation defaults, not universal file-format maxima. Keep them named and
configurable, collect rejection metrics, and raise an individual limit only with corpus evidence.

## Why this stack

### DOCX options

| Option | Current evidence | Fit |
| --- | --- | --- |
| Mammoth 1.12.1 | BSD-2-Clause, production/stable, released 2026-08-09. Raw-text extraction is available; tables, footnotes/endnotes, text boxes, and links are supported. External file access is disabled by default. Its own security guidance warns that conversion is not sanitization and may have pathological CPU/memory behavior. [1] | Best body-text extractor for resume-shaped DOCX files, especially text boxes. Use raw text only, disable embedded style maps, keep external access disabled, and ignore images. |
| python-docx 1.2.0 | MIT, production/stable, released 2025-06-16, Python `>=3.9`. It provides document-order paragraph/table traversal, recursive cell traversal, and first/even/default headers and footers. Its documented `paragraphs` view omits paragraphs inside revision marks. [2][3][13] | Best narrow supplement for active headers/footers and useful package validation. Alone, its public API has more extraction gaps for resume layouts. |
| Direct OOXML parsing | Maximum control over parts and byte budgets, but requires implementing relationship resolution, revisions, fields, tables, drawings, and ordering. | Do not build a second WordprocessingML parser initially. Use ZIP metadata for preflight only. |

Mammoth does **not** extract Word headers in `extract_raw_text`; this was also confirmed against
1.12.1 on the project's Python 3.14 runtime with a generated DOCX containing header and body text.
Therefore:

1. Extract the main story with `mammoth.extract_raw_text()`.
2. Traverse only the header/footer definitions that are active for each section with `python-docx`,
   including nested tables through `iter_inner_content()`.
3. De-duplicate inherited and repeated story text by part identity, not by deleting repeated lines
   globally; repeated lines in employment history may be meaningful.
4. Join unique headers, body, and unique footers with explicit section separators.
5. Treat any parser warning, unsupported relationship needed for text, or disagreement in package
   validity as a failed extraction until covered by an acceptance fixture.

Do not convert Mammoth HTML and sanitize it afterward. Raw text removes the XSS surface that Mammoth
warns about, and `external_file_access` must remain false. Do not extract images or follow hyperlinks,
external relationships, attached objects, or embedded files.

### PDF options

| Option | Current evidence | Fit |
| --- | --- | --- |
| pypdf 6.16.1 | BSD-3-Clause, pure Python, production/stable, released 2026-08-14, and classified for Python 3.14. It has plain and layout extraction modes. Releases in 2026 repeatedly added cycle, iteration, token, stream, decompression, image-size, and XML-entity limits. [4][5][6] | Recommended. Small deployment surface and active hardening, but still requires process isolation and application budgets. |
| pdfminer.six 20260107 | MIT, pure Python, production/stable, Python 3.14 classifier. Its configurable layout analysis groups glyphs into lines and boxes. The project says maintainer availability is limited. [14][15] | Viable fallback if the acceptance corpus proves materially better than pypdf for required resumes; otherwise its larger layout-analysis surface is unnecessary. |
| PyMuPDF 1.28.x | Fast native MuPDF binding with positional extraction and reading-order sorting. Distributed under AGPL or a commercial license. [16][17] | Do not select without an explicit licensing decision and corpus evidence that the permissive pure-Python options fail. |

PDF has no dependable paragraph/table semantic layer; text is positioned glyph content, and reading
order, whitespace, headers, tables, ligatures, and scanned pages are inherently ambiguous. pypdf's
documentation also records an observed case where extracting a roughly 300 MB uncompressed content
stream required 10 GB RAM. [5] Library-level hardening reduces known attacks but does not establish a
safe application memory bound.

Start with `extract_text(extraction_mode="layout", layout_mode_space_vertically=False)` because
resume columns are common, then apply bounded whitespace normalization. Snapshot its output against
the acceptance corpus. If plain mode wins the corpus, change the fixed option globally; never select
mode heuristically per upload because that makes behavior hard to reproduce. Extract each page once,
in order, and include a stable page separator. Do not access images, attachments, JavaScript, actions,
annotations, forms, or metadata for import.

Use `PdfReader(..., strict=True)`. pypdf documents that strict mode raises when a PDF violates the
specification, while non-strict mode makes best-effort repairs and warnings. [18] A user can re-export
a malformed file; silently repairing it weakens deterministic imports. Reject encrypted PDFs rather
than accepting passwords or attempting decryption in the initial flow.

## Required pipeline

### 1. Intake and classification

Use one upload field and stream it to a private temporary file. Django warns that `content_type` is
user-supplied and must be verified, and recommends chunked reads to avoid loading large uploads into
memory. Its default memory threshold is not a maximum upload size. [7][19]

Apply these initial limits while streaming:

| Limit | Initial value | Failure |
| --- | ---: | --- |
| Files per request | 1 | Reject before parsing |
| Compressed/upload bytes | 10 MiB | Stop upload and delete temporary data |
| Extracted canonical text | 100,000 Unicode code points | Reject; do not truncate into an import |

Classify without trusting the name:

- A PDF must begin with the PDF header accepted by the selected pypdf version and then pass strict
  parser construction and bounded page enumeration.
- A DOCX must pass `zipfile.is_zipfile()`, have no encrypted entries, contain `[Content_Types].xml`
  and the package relationship to a WordprocessingML main document, and declare the non-macro DOCX
  main content type. Reject `.docm`, generic ZIP, legacy `.doc`, RTF, and encrypted Office containers.
- If bytes and extension disagree, use the validated bytes to report the mismatch and reject. Do not
  silently rename or reinterpret the upload.

`python-magic`/libmagic can be defense-in-depth telemetry, but is not an acceptance authority. The
wrapper requires system `libmagic`, recommends reading at least 2,048 bytes, and its `Magic` object is
not thread-safe. [8] Its addition would enlarge the production image without removing either strict
parser.

### 2. Container preflight

DOCX is a ZIP package. Before any XML parser or DOCX library runs, inspect the central directory
without extracting to the filesystem and reject when any condition is met:

| Limit | Initial value |
| --- | ---: |
| ZIP members | 2,000 |
| Sum of declared uncompressed member sizes | 100 MiB |
| One declared uncompressed member | 20 MiB |
| Compression ratio for any non-empty compressed member | 100:1 |
| Duplicate member names | 0 allowed |
| Encrypted members | 0 allowed |
| Unsupported compression methods | 0 allowed |

Also reject absolute names, `..` path components, NULs, and malformed central-directory metadata,
even though no member should be written to disk. Python's ZIP documentation warns about ZIP bombs,
resource exhaustion, path traversal, and duplicate member names, and exposes compressed and
uncompressed sizes for preflight. [9] Declared sizes can be dishonest, so preflight is not a sandbox;
the child-process limits remain mandatory.

For PDF, reject more than 50 pages during bounded enumeration. A 50-page ceiling is intentionally
well above a normal resume but prevents an upload endpoint from becoming a general book parser.

### 3. Isolated extraction

Run extraction synchronously in a fresh child process. This preserves the existing request/response
architecture; it is isolation, not asynchronous job infrastructure.

The child must:

- apply its own limits immediately on startup rather than relying on `preexec_fn` (Python warns that
  `preexec_fn` can deadlock in threaded applications); [11]
- have a 10-second CPU limit, 15-second parent-enforced wall timeout, 512 MiB address-space limit,
  zero core-file limit, 1 MiB created-file limit, and a small open-file limit;
- receive a generated temporary path and a fixed format enum, never a shell command or original
  filename; invoke Python with `shell=False`, a fixed executable, a minimal environment, and no
  inherited application secrets; [11]
- keep Mammoth external file access disabled and perform no network or subprocess calls;
- emit a bounded UTF-8 result and machine-readable outcome only; never return parser tracebacks or
  document bytes to the browser;
- be killed on timeout, limit breach, malformed input, parser warning classified as fatal, or excess
  output, after which all temporary files are deleted.

`RLIMIT_AS` and related limits are Unix-specific, which is compatible with the production Linux
container but must be exercised in container CI; macOS development behavior is not proof of the
production limit. [10] The worker must not run as root. Container memory/PID limits remain a second
boundary.

### 4. Canonical plain text

Canonicalization is intentionally loss-minimizing and deterministic:

1. Decode only library-returned Unicode; do not guess a separate character encoding.
2. Normalize CRLF and CR to LF.
3. Normalize Unicode to NFC, which preserves canonical character equivalence without the
   compatibility folding performed by NFKC. [12]
4. Convert form feed/page boundaries to the fixed page separator.
5. Preserve tabs only while converting table cells; otherwise convert tabs and horizontal whitespace
   runs to one ASCII space.
6. Remove NUL and Unicode control characters except LF. Preserve ordinary non-ASCII letters,
   punctuation, symbols, combining behavior after NFC, and right-to-left text.
7. Strip trailing spaces, collapse more than two consecutive blank lines to two, and trim the whole
   document.
8. Reject output that is empty, over 100,000 code points, or implausibly sparse. For PDF, initially
   classify fewer than 20 non-whitespace characters per non-empty page or zero text on every page as
   non-extractable; record the reason and ask for a text-based re-export. Do not invoke OCR.

The canonical text is the only document-derived payload passed to AI. Never interpolate it into
system/developer instructions. Pass it as a clearly delimited untrusted data field and require the AI
boundary to produce schema-validated source facts. Prompt-injection resistance belongs to that
boundary; deleting phrases such as "ignore previous instructions" would corrupt legitimate source
text and is not sanitization.

### 5. Failure and observability contract

Expose stable user-facing categories, not parser exceptions:

- unsupported or mismatched format;
- encrypted/password-protected document;
- file/package/page/output limit exceeded;
- malformed document;
- no reliable text found (including image-only PDF);
- extraction timed out or exhausted resources;
- temporary internal failure, safe to retry.

Log format, byte/page/member counts, duration, peak child RSS where available, extractor/version,
outcome category, and a request correlation ID. Do not log filenames, extracted text, hashes usable as
cross-user identifiers, document metadata, or parser dumps containing candidate data.

## Fidelity acceptance gate

Before implementation is considered complete, build a small non-sensitive fixture corpus and assert
canonical output, failure category, and runtime ceiling. It must include:

- DOCX body paragraphs, lists, nested tables, header contact details, first/even headers, inherited
  headers, footers, hyperlinks, footnotes, text boxes, tracked changes, Unicode/RTL, duplicate visible
  lines, external relationships, embedded images, macro-enabled content type, encrypted entry,
  duplicate ZIP names, malformed XML, and high-compression members;
- PDF one/two-column resumes, ligatures, rotated text, unusual fonts/encodings, headers/footers,
  tables, empty pages, mixed image/text pages, image-only scans, encrypted files, malformed xref/page
  trees, cyclic objects, oversized streams, and page-limit cases;
- adversarial text containing HTML, control characters, bidi text, and prompt-like instructions, all
  proving that output is plain text and source wording is retained.

Use real exporter diversity: current Microsoft Word, Google Docs, LibreOffice, macOS Quartz, and at
least one browser "Print to PDF". Pin versions in `uv.lock`; dependency upgrades must run this corpus
and security-limit tests before merge. The chosen libraries' active hardening means upgrades should
be prompt, but deterministic output changes must be reviewed rather than accepted silently.

## Decisions and remaining fog

### Resolved here

- Select Mammoth plus narrow python-docx header/footer traversal for DOCX and pypdf for PDF.
- Reject malformed input strictly; do not repair, decrypt, OCR, render, execute, or follow external
  content.
- Use structural format validation rather than filename/MIME or libmagic as authority.
- Parse in a resource-limited child process even when the product flow remains synchronous.
- Produce bounded NFC plain text and preserve source wording; treat it as untrusted at the AI
  boundary.
- Reject low-confidence or partial extraction instead of silently continuing.

### Follow-up evidence, not a blocking architecture decision

- The fixture corpus may show pypdf plain mode is more faithful than layout mode for the actual resume
  population. Resolve that by one global, versioned extraction option before release.
- Initial numeric limits are conservative operational defaults. Tune from rejection and latency
  metrics without weakening process isolation.
- Malware scanning may be required by a later retention/storage policy. It is not a substitute for
  parser isolation and is not necessary merely to turn an ephemeral upload into plain text without
  rendering or execution.

## Primary sources

1. [Mammoth 1.12.1 project metadata, supported features, API, and security guidance](https://pypi.org/project/mammoth/)
2. [python-docx 1.2.0 document API](https://python-docx.readthedocs.io/en/latest/api/document.html)
3. [python-docx 1.2.0 section/header/footer API](https://python-docx.readthedocs.io/en/latest/api/section.html)
4. [pypdf 6.16.1 project metadata and Python 3.14 classifier](https://pypi.org/project/pypdf/)
5. [pypdf text extraction guide and memory warning](https://pypdf.readthedocs.io/en/stable/user/extract-text.html)
6. [pypdf changelog](https://pypdf.readthedocs.io/en/stable/meta/CHANGELOG.html)
7. [Django 5.2 UploadedFile API](https://docs.djangoproject.com/en/5.2/ref/files/uploads/)
8. [python-magic 0.4.27 project metadata and deployment requirements](https://pypi.org/project/python-magic/)
9. [Python 3.14 `zipfile` documentation and decompression pitfalls](https://docs.python.org/3.14/library/zipfile.html)
10. [Python 3.14 `resource` limits](https://docs.python.org/3.14/library/resource.html)
11. [Python 3.14 `subprocess` timeouts and security considerations](https://docs.python.org/3.14/library/subprocess.html)
12. [Python 3.14 Unicode normalization](https://docs.python.org/3.14/library/unicodedata.html)
13. [python-docx 1.2.0 package metadata](https://pypi.org/project/python-docx/)
14. [pdfminer.six 20260107 project metadata](https://pypi.org/project/pdfminer.six/)
15. [pdfminer.six layout analysis](https://pdfminersix.readthedocs.io/en/latest/topic/converting_pdf_to_text.html)
16. [PyMuPDF text extraction guide](https://pymupdf.readthedocs.io/en/latest/recipes-text.html)
17. [PyMuPDF licensing](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright)
18. [pypdf strict-mode behavior](https://pypdf.readthedocs.io/en/stable/user/robustness.html)
19. [Django 5.2 file-upload handling](https://docs.djangoproject.com/en/5.2/topics/http/file-uploads/)
