# OpenRouter capabilities for structured imports

Research for [Research OpenRouter capabilities for structured imports](https://github.com/soloidx/Applykit_web/issues/58), conducted 2026-08-21 against current first-party OpenRouter documentation and API references.

## Executive answer

OpenRouter can support the initial structured-import boundary through its OpenAI-compatible Chat Completions API, but the specification must not treat either model portability or strict structured output as unconditional. Structured output support varies by model *and provider endpoint*, unsupported parameters may otherwise be ignored, and even `strict: true` is not an identical guarantee across providers. The application therefore needs a configured model allowlist, `provider.require_parameters: true`, a strict JSON Schema, application-side schema validation, and an explicit retry/failure policy. [Structured Outputs](https://openrouter.ai/docs/guides/features/structured-outputs) [Provider Routing](https://openrouter.ai/docs/guides/routing/provider-selection#requiring-providers-to-support-all-parameters)

For the initial DOCX and text-extractable PDF scope, extract and normalize text inside Applykit rather than sending source files to OpenRouter. OpenRouter documents first-class direct PDF handling, but it is an OpenRouter-specific extension that may invoke native model handling or a third-party parsing plugin, has separate parser behavior and costs, and is explicitly outside ZDR enforcement. The official material does not document an equivalent DOCX contract. Local extraction gives both formats one provider-neutral text boundary, keeps parsing deterministic and testable, and avoids paying for or disclosing documents to a second parsing service. Direct PDF input should remain a separately evaluated optimization, not the initial contract. [PDF Inputs](https://openrouter.ai/docs/guides/overview/multimodal/pdfs) [Zero Data Retention](https://openrouter.ai/docs/guides/features/zdr)

## Capability and constraint findings

### 1. API shape and portability

- `POST /api/v1/chat/completions` uses a schema described as very similar to, and normalized toward, the OpenAI Chat API. It supports standard `messages`, `model`, `response_format`, and token/sampling fields, plus OpenRouter-only fields including `models`, `provider`, `plugins`, and `user`. OpenRouter-specific controls require an adapter or an SDK escape hatch such as the OpenAI Python SDK's `extra_body`; they are not portable OpenAI fields. [API Reference](https://openrouter.ai/docs/api_reference/overview) [Model Fallbacks](https://openrouter.ai/docs/guides/routing/model-fallbacks#using-with-the-openai-sdk)
- OpenRouter's normalization permits switching models behind one request shape, but provider behavior is not fully normalized. OpenRouter transforms requests for provider-specific interfaces, and unsupported parameters can be ignored. `provider.require_parameters: true` changes this from best-effort routing to excluding endpoints that do not support all supplied parameters. [API Reference](https://openrouter.ai/docs/api_reference/overview) [Provider Routing](https://openrouter.ai/docs/guides/routing/provider-selection#requiring-providers-to-support-all-parameters)
- The model catalog is dynamic. `GET /api/v1/models` exposes model IDs, context length, input modalities, pricing, per-request limits, and supported parameters; model availability changes independently of API versioning. The specification should name a configurable model ID and required capabilities, not bake in a permanent model assumption. [Models API](https://openrouter.ai/docs/api/api-reference/models/list-all-models-and-their-properties) [API Versioning](https://openrouter.ai/docs/api_reference/versioning)
- OpenRouter's `v1` evolves continuously. Additive response fields, status codes, schemas, and enum variants may ship without notice, so the client must ignore unknown fields and enum values and handle documented nullable fields. [API Versioning](https://openrouter.ai/docs/api_reference/versioning)

**Specification consequence:** put OpenRouter behind the shared AI boundary. Keep the importer's domain request/result independent of OpenRouter objects, but let the adapter supply vendor extensions (`provider`, privacy controls, and potentially `models`). Verify the configured model at deployment/startup or with a health check against current model/endpoint metadata.

### 2. Structured outputs

- OpenRouter accepts `response_format.type: "json_schema"` with a named JSON Schema and optional `strict: true`; basic `json_object` mode only guarantees valid JSON, not a domain schema. Structured output is available only on selected models and is determined per provider endpoint, so two endpoints serving the same model may differ. [Structured Outputs](https://openrouter.ai/docs/guides/features/structured-outputs)
- `strict: true` requests native strict enforcement where available, but OpenRouter states that providers may translate the schema or treat it as a strong hint; exact compliance is not guaranteed on every endpoint, and supported JSON Schema features vary. [Structured Outputs](https://openrouter.ai/docs/guides/features/structured-outputs#best-practices)
- Without `require_parameters: true`, `response_format` is a routing preference: supporting endpoints are preferred, but if none support it the request can still be sent and the parameter ignored. With `require_parameters: true`, unsupported endpoints are excluded and the request can fail if none remain. [Provider Routing](https://openrouter.ai/docs/guides/routing/provider-selection#requiring-providers-to-support-all-parameters)
- Invalid schemas or lack of structured-output support can fail the request. The response-healing plugin can repair malformed JSON only for non-streaming JSON-schema requests, but enabling another plugin introduces another processing behavior and should not replace application validation. [Structured Outputs](https://openrouter.ai/docs/guides/features/structured-outputs#error-handling) [Response Healing](https://openrouter.ai/docs/guides/features/plugins/response-healing)

**Specification consequence:** use non-streaming JSON-schema responses with `strict: true`, `additionalProperties: false`, all required fields declared, and clear property descriptions. Also validate the decoded payload against the same schema in Applykit. A schema-invalid response is a failed import attempt, never partially trusted domain data. Keep prompts model-agnostic and test every allowed model against a fixed import corpus before configuration changes are promoted.

### 3. Model and provider routing

- OpenRouter normally routes among providers for the requested model using price and recent uptime, with provider fallback enabled. A `models` array adds cross-model fallback and charges according to the model ultimately used, returned in the response. [Provider Routing](https://openrouter.ai/docs/guides/routing/provider-selection) [Model Fallbacks](https://openrouter.ai/docs/guides/routing/model-fallbacks)
- Cross-model fallbacks can trigger on broad categories including context-length errors and moderation flags, not just transient availability. A fallback model can also differ in cost, context, schema support, and extraction quality. [Model Fallbacks](https://openrouter.ai/docs/guides/routing/model-fallbacks#fallback-behavior)
- Routing constraints can enforce parameter support, data policy, ZDR, provider allow/deny lists, and maximum price. If constraints eliminate all endpoints, OpenRouter returns a typed `constraint_filtered` or `privacy_restricted` availability error rather than silently relaxing them. [Provider Routing](https://openrouter.ai/docs/guides/routing/provider-selection) [Errors and Debugging](https://openrouter.ai/docs/api_reference/errors-and-debugging#model-availability-errors)

**Specification consequence:** allow provider fallback within one configured model, but do not enable arbitrary cross-model fallback initially. Model changes should be an explicit configuration rollout with corpus evaluation and a cost ceiling. Always require request parameters and privacy constraints rather than accepting silent degradation.

### 4. Request and output limits

- The usable request budget is model/endpoint-dependent: the model catalog reports context length, top-provider completion limits, and optional per-request prompt/completion limits. `max_tokens` cannot exceed context length minus prompt length. There is no single fixed token ceiling suitable for the product specification. [Models API](https://openrouter.ai/docs/api/api-reference/models/list-all-models-and-their-properties) [Parameters](https://openrouter.ai/docs/api_reference/parameters#max-tokens)
- OpenRouter documents a typed `payload_too_large` error (HTTP 413), but does not publish one universal byte limit in the limits documentation. The application must impose its own upload, extracted-character/token, and output-token ceilings below the configured model's limits. [Errors and Debugging](https://openrouter.ai/docs/api_reference/errors-and-debugging#request-validation) [Limits](https://openrouter.ai/docs/api_reference/limits)
- Paid model variants have no documented OpenRouter platform request-per-minute cap, but requests remain subject to DDoS protection and upstream provider rate/capacity limits. Free variants are currently limited to 20 requests/minute and either 50 or 1,000 requests/day depending on credits purchased. Limits are account-global rather than increased by creating keys. [Limits](https://openrouter.ai/docs/api_reference/limits#rate-limits)
- `GET /api/v1/key` returns key credit caps, remaining credits, and daily/weekly/monthly usage. Per-key credit limits can enforce spend, while exhausted account/key credit produces HTTP 402. [Limits](https://openrouter.ai/docs/api_reference/limits#checking-your-limits)

**Specification consequence:** define product-level per-user attempt throttles and file/text size ceilings independently of provider limits; reserve output tokens based on the schema; preflight oversized extracted text; use a dedicated API key with a resettable credit cap; and do not rely on free variants for production.

### 5. Usage and cost reporting

- Every non-streaming response includes usage; streaming puts usage in the final chunk. The usage object includes native-tokenizer prompt, completion, and total token counts, plus `cost` in credits and optional reasoning/cache and upstream cost details. Deprecated usage-inclusion flags are unnecessary. [Usage Accounting](https://openrouter.ai/docs/cookbook/administration/usage-accounting) [API Reference](https://openrouter.ai/docs/api_reference/overview#responses)
- Generation usage and cost can also be queried later by response `id`. Model catalog pricing is available before a request, but the response's actual cost is the authoritative charge for the provider/model route used. [API Reference](https://openrouter.ai/docs/api_reference/overview#querying-cost-and-stats)
- PDF parsing may add separate costs: Mistral OCR is documented at $2 per 1,000 pages and is charged through OpenRouter even with BYOK; Cloudflare parsing is listed as free; native PDF handling is charged as model input tokens. [PDF Inputs](https://openrouter.ai/docs/guides/overview/multimodal/pdfs#pricing)

**Specification consequence:** persist the generation ID, actual model, token counts, and charged cost with an internal import attempt/operational record, not with imported domain facts. Enforce both a per-request model price ceiling and account/key spend cap, and expose aggregate monitoring. Local extraction avoids parser charges.

### 6. Errors, retries, and partial results

- Pre-generation request failures use an HTTP status and `{error: {code, message, metadata}}`. If an error occurs after generation starts, non-streaming Chat Completions can return it inside a choice with `finish_reason: "error"`; streaming errors arrive in-band while HTTP remains 200. [Errors and Debugging](https://openrouter.ai/docs/api_reference/errors-and-debugging)
- Stable typed error categories include validation, context/length, authentication, payment, rate limit, provider overload/unavailability, timeout, policy/refusal, and server errors. Availability errors additionally carry a stable code and `retryable` boolean; clients should switch on these machine-readable fields rather than message prose or HTTP status alone. [Errors and Debugging](https://openrouter.ai/docs/api_reference/errors-and-debugging#typed-error-codes) [Errors and Debugging](https://openrouter.ai/docs/api_reference/errors-and-debugging#model-availability-errors)
- HTTP 429 and 503 may include `Retry-After`; official SDKs honor it. Provider fallback can happen before output starts, but cannot safely happen after partial streamed output. Empty responses can occur and may still incur prompt cost. [Errors and Debugging](https://openrouter.ai/docs/api_reference/errors-and-debugging#retry-after-header) [Errors and Debugging](https://openrouter.ai/docs/api_reference/errors-and-debugging#when-no-content-is-generated)
- Normalized completion `finish_reason` values include `stop`, `length`, `content_filter`, and `error`. A syntactically successful HTTP response is therefore not sufficient evidence of a complete import. [API Reference](https://openrouter.ai/docs/api_reference/overview#finish-reason)

**Specification consequence:** make the initial call non-streaming. Accept an import candidate only when there is no embedded error, finish reason is successful, content is present, JSON parses, and schema validation passes. Automatically retry only errors explicitly marked retryable (or known transient 429/502/503/504 categories), with bounded attempts, jittered exponential backoff, and `Retry-After`; never retry validation, authentication, payment, privacy/constraint, moderation/refusal, or oversize errors unchanged. Import writes must be idempotent and occur only after successful validation and user confirmation.

### 7. Data retention and privacy controls

- OpenRouter says it does not store prompt/response content unless the account opts into private input/output logging or OpenRouter use of inputs/outputs. It does retain request metadata such as token counts and latency. Anonymous prompt categorization may occur, using a ZDR model, without account association when content-use opt-in is disabled. [Data Collection](https://openrouter.ai/docs/guides/privacy/data-collection)
- Provider policies vary by endpoint. `provider.data_collection: "deny"` excludes providers that collect user data, while `provider.zdr: true` restricts inference routing to endpoints that retain prompts for no period. OpenRouter treats unknown endpoint policies conservatively. [Provider Routing](https://openrouter.ai/docs/guides/routing/provider-selection#requiring-providers-to-comply-with-data-policies) [Zero Data Retention](https://openrouter.ai/docs/guides/features/zdr)
- ZDR controls inference-provider routing only. They do not cover optional plugins/tools, which can use third parties under separate retention policies. [Zero Data Retention](https://openrouter.ai/docs/guides/features/zdr)
- Private input/output logging is off by default. If enabled, content is retained at least three months and potentially longer until deletion is requested; it is independent of the content-use-for-discount setting. [Input & Output Logging](https://openrouter.ai/docs/guides/features/input-output-logging)

**Specification consequence:** require account content logging and content-use opt-ins to remain off; send `provider: {data_collection: "deny", zdr: true, require_parameters: true}` on every import regardless of account defaults; use a dedicated key/guardrail; avoid plugins; do not send the user's name or email as OpenRouter's optional `user` value (use an opaque stable identifier only if abuse attribution is required); and document that request metadata remains with OpenRouter. Treat a privacy-filtered no-endpoint result as a hard, user-safe service-unavailable failure, never as permission to relax privacy.

### 8. Direct files versus extracted text

- OpenRouter documents PDFs as a file content part supplied by public URL or base64 data URL. A native-file model receives the PDF directly; otherwise OpenRouter parses it and passes parsed results to the model. This makes direct PDF support broad but introduces routing-dependent behavior. [PDF Inputs](https://openrouter.ai/docs/guides/overview/multimodal/pdfs)
- Parser selection is an OpenRouter plugin configuration. If unspecified, OpenRouter prefers native handling and otherwise defaults to Mistral OCR. Parsed PDFs can produce reusable file annotations; failed inference can still return parsed annotations for a later retry. [PDF Inputs](https://openrouter.ai/docs/guides/overview/multimodal/pdfs#plugin-configuration) [PDF Inputs](https://openrouter.ai/docs/guides/overview/multimodal/pdfs#error-responses-with-parsed-annotations)
- The PDF docs mention other file types can accompany PDFs but provide no first-party DOCX processing contract, engine behavior, portability guarantee, or pricing. The model API does expose a generic `file` input modality, but that is capability metadata, not a documented DOCX extraction guarantee. [PDF Inputs](https://openrouter.ai/docs/guides/overview/multimodal/pdfs) [Models API](https://openrouter.ai/docs/api/api-reference/models/list-all-models-and-their-properties)

**Specification consequence:** Applykit should own text extraction for both accepted formats, reject scanned/image-only PDFs as already out of scope, normalize extracted text, and send only bounded text to the AI boundary. Store extraction diagnostics separately from model errors. Do not upload a private document by public URL. Reconsider direct PDF only after a separate privacy, quality, cost, and failure-mode comparison.

## Recommended initial provider contract

The implementation specification should require the OpenRouter adapter to make a non-streaming Chat Completions request equivalent to:

```json
{
  "model": "<configured-and-qualified-model>",
  "messages": [
    {"role": "system", "content": "<versioned import instructions>"},
    {"role": "user", "content": "<bounded locally extracted text>"}
  ],
  "response_format": {
    "type": "json_schema",
    "json_schema": {
      "name": "<versioned-import-schema>",
      "strict": true,
      "schema": "<closed JSON Schema>"
    }
  },
  "provider": {
    "require_parameters": true,
    "data_collection": "deny",
    "zdr": true,
    "max_price": "<configured ceiling>"
  },
  "max_tokens": "<schema-sized output budget>"
}
```

The exact schema, import facts, limits, retry count, model qualification corpus, and cost ceiling remain product/architecture decisions for the parent map. OpenRouter facts constrain those decisions but do not select their values.

## Acceptance checks for the eventual specification

- A configured model is deployable only if current endpoint metadata supports structured outputs under the required privacy constraints.
- Every request sets `require_parameters`, `data_collection: "deny"`, ZDR, an output-token bound, and a price bound.
- The account/workspace has prompt logging and content-use opt-ins disabled, and the dedicated key has a credit cap.
- DOCX and text-extractable PDF are locally extracted into the same bounded text request; scanned PDFs fail before provider invocation.
- The output is accepted only after finish-reason checks, JSON parsing, and local schema validation.
- Retry logic uses typed retryability and `Retry-After`, is bounded, and cannot duplicate domain writes.
- Operational records capture generation ID, actual model, usage, cost, latency, and safe error category without storing prompt/document content.
- Changing the model or enabling model fallbacks requires passing the representative import corpus and cost/privacy checks.

## Sources

All sources are first-party OpenRouter documentation or OpenAPI references, accessed 2026-08-21:

- [API Reference overview](https://openrouter.ai/docs/api_reference/overview)
- [API Versioning](https://openrouter.ai/docs/api_reference/versioning)
- [Models API](https://openrouter.ai/docs/api/api-reference/models/list-all-models-and-their-properties)
- [Parameters](https://openrouter.ai/docs/api_reference/parameters)
- [Structured Outputs](https://openrouter.ai/docs/guides/features/structured-outputs)
- [Provider Routing](https://openrouter.ai/docs/guides/routing/provider-selection)
- [Model Fallbacks](https://openrouter.ai/docs/guides/routing/model-fallbacks)
- [Limits](https://openrouter.ai/docs/api_reference/limits)
- [Usage Accounting](https://openrouter.ai/docs/cookbook/administration/usage-accounting)
- [Errors and Debugging](https://openrouter.ai/docs/api_reference/errors-and-debugging)
- [Data Collection](https://openrouter.ai/docs/guides/privacy/data-collection)
- [Provider Logging](https://openrouter.ai/docs/guides/privacy/provider-logging)
- [Zero Data Retention](https://openrouter.ai/docs/guides/features/zdr)
- [Input & Output Logging](https://openrouter.ai/docs/guides/features/input-output-logging)
- [PDF Inputs](https://openrouter.ai/docs/guides/overview/multimodal/pdfs)
- [Response Healing](https://openrouter.ai/docs/guides/features/plugins/response-healing)
