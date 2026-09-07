import decimal
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from apps.ai import transport
from apps.ai.conf import FeatureConfig
from apps.ai.errors import (
    CONFIGURATION_ERROR,
    INVALID_RESPONSE,
    PRIVACY_UNAVAILABLE,
    RATE_LIMITED,
    UNAVAILABLE,
)

pytestmark = pytest.mark.unit


def feature_config(**overrides: object) -> FeatureConfig:
    values: dict[str, object] = {
        "feature": "candidate_profile_extraction",
        "model": "acme/profile-model",
        "base_url": "https://openrouter.example/api/v1",
        "api_key": "test-key",
        "deadline_seconds": 60.0,
        "max_attempts": 2,
        "max_input_tokens": 60_000,
        "max_output_tokens": 8_000,
        "price_ceiling": decimal.Decimal("0.50"),
    }
    values.update(overrides)
    return FeatureConfig(**values)  # type: ignore[arg-type]


def result(status: int = 200, body: bytes = b"{}", headers: dict[str, str] | None = None):
    return transport.TransportResult(status=status, headers=headers or {}, body=body)


def completion_body(
    *,
    model: str = "acme/route-model",
    content: str = '"ok"',
    finish_reason: str = "stop",
    prompt_tokens: int = 100,
    completion_tokens: int = 50,
    cost: float | None = 0.02,
    error: object | None = None,
    choice_error: object | None = None,
) -> bytes:
    usage: dict[str, object] = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
    }
    if cost is not None:
        usage["cost"] = cost
    choice: dict[str, object] = {"finish_reason": finish_reason, "message": {"content": content}}
    if choice_error is not None:
        choice["error"] = choice_error
    body: dict[str, object] = {"model": model, "usage": usage, "choices": [choice]}
    if error is not None:
        body["error"] = error
    return json.dumps(body).encode()


class TestPayloadPrivacyParameters:
    def build(self) -> dict[str, object]:
        return transport.build_payload(
            model="acme/model",
            system_prompt="system",
            user_text="user",
            schema_name="schema_v1",
            schema={"type": "object"},
            max_output_tokens=8_000,
            price_ceiling=decimal.Decimal("0.50"),
        )

    def test_request_is_non_streaming_strict_json_schema(self) -> None:
        payload = self.build()

        assert payload["stream"] is False
        assert payload["response_format"]["type"] == "json_schema"
        assert payload["response_format"]["json_schema"]["strict"] is True
        assert payload["response_format"]["json_schema"]["name"] == "schema_v1"
        assert payload["response_format"]["json_schema"]["schema"] == {"type": "object"}

    def test_request_demands_parameter_support_and_privacy_route(self) -> None:
        payload = self.build()
        provider = payload["provider"]

        assert provider["require_parameters"] is True
        assert provider["data_collection"] == "deny"
        assert provider["zdr"] is True
        assert provider["max_price"] == 0.50

    def test_request_is_bounded_and_routed(self) -> None:
        payload = self.build()

        assert payload["model"] == "acme/model"
        assert payload["max_tokens"] == 8_000
        assert payload["messages"] == [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "user"},
        ]


class TestHttpClassification:
    def test_success_is_not_a_failure(self) -> None:
        assert transport.classify_response(result(200)) is None
        assert transport.classify_response(result(299)) is None

    def test_rate_limit_is_retryable(self) -> None:
        failure = transport.classify_response(result(429, headers={"Retry-After": "3"}))

        assert failure is not None
        assert failure.category == RATE_LIMITED
        assert failure.retryable is True
        assert failure.retry_after == 3.0

    @pytest.mark.parametrize("status", [408, 500, 502, 503, 504])
    def test_transient_statuses_are_retryable_unavailable(self, status: int) -> None:
        failure = transport.classify_response(result(status))

        assert failure is not None
        assert failure.category == UNAVAILABLE
        assert failure.retryable is True

    @pytest.mark.parametrize("status", [400, 401, 402, 403, 413, 422])
    def test_operator_failures_are_not_retried(self, status: int) -> None:
        failure = transport.classify_response(result(status))

        assert failure is not None
        assert failure.category == CONFIGURATION_ERROR
        assert failure.retryable is False

    def test_unknown_status_is_unavailable_and_not_retried(self) -> None:
        failure = transport.classify_response(result(418))

        assert failure is not None
        assert failure.category == UNAVAILABLE
        assert failure.retryable is False


class TestTypedErrorBodies:
    def typed(self, code: object, **extra: object) -> bytes:
        error: dict[str, object] = {"code": code, "message": "provider detail"}
        error.update(extra)
        return completion_body(error=error)

    def failure_from_body(self, body: bytes, status: int = 200) -> transport.TransportFailure:
        """A typed error body is read from either path: 2xx body or status."""

        if 200 <= status < 300:
            with pytest.raises(transport.TransportFailure) as raised:
                transport.interpret_response(result(status, body))
            return raised.value
        failure = transport.classify_response(result(status, body))
        assert failure is not None
        return failure

    def test_privacy_route_failure_is_never_retried_or_relaxed(self) -> None:
        for code in ("privacy_restricted", "constraint_filtered"):
            for status in (200, 503):
                failure = self.failure_from_body(self.typed(code), status)
                assert failure.category == PRIVACY_UNAVAILABLE
                assert failure.retryable is False

    def test_authentication_maps_to_configuration_error(self) -> None:
        failure = self.failure_from_body(self.typed("invalid_api_key"), 401)
        assert failure.category == CONFIGURATION_ERROR
        assert failure.retryable is False

    def test_payment_maps_to_configuration_error(self) -> None:
        failure = self.failure_from_body(self.typed("insufficient_credits"), 402)
        assert failure.category == CONFIGURATION_ERROR

    def test_oversize_maps_to_configuration_error(self) -> None:
        failure = self.failure_from_body(self.typed("context_length_exceeded"), 413)
        assert failure.category == CONFIGURATION_ERROR

    def test_policy_refusal_is_not_retried(self) -> None:
        failure = self.failure_from_body(self.typed("content_filter"), 200)
        assert failure.category == UNAVAILABLE
        assert failure.retryable is False

    def test_embedded_rate_limit_is_retryable(self) -> None:
        failure = self.failure_from_body(self.typed("rate_limit_exceeded"), 200)
        assert failure.category == RATE_LIMITED
        assert failure.retryable is True

    def test_server_error_honors_retryable_flag(self) -> None:
        failure = self.failure_from_body(self.typed("server_error", retryable=True), 200)
        assert failure.category == UNAVAILABLE
        assert failure.retryable is True

        failure = self.failure_from_body(self.typed("server_error", retryable=False), 200)
        assert failure.retryable is False

    def test_unknown_code_fails_safe_without_retry(self) -> None:
        failure = self.failure_from_body(self.typed("mystery_code"), 200)
        assert failure.category == UNAVAILABLE
        assert failure.retryable is False

    def test_embedded_error_after_generation_maps_the_same_way(self) -> None:
        body = completion_body(finish_reason="error", choice_error={"code": "rate_limit"})
        failure = self.failure_from_body(body, 200)
        assert failure.category == RATE_LIMITED
        assert failure.retryable is True

    def test_error_body_in_non_success_response_wins_over_status(self) -> None:
        failure = self.failure_from_body(self.typed("privacy_restricted"), 503)
        assert failure.category == PRIVACY_UNAVAILABLE
        assert failure.retryable is False


class TestResponseInterpretation:
    def test_valid_response_normalizes(self) -> None:
        completion = transport.interpret_response(result(200, completion_body()))

        assert completion.model == "acme/route-model"
        assert completion.content == '"ok"'
        assert completion.prompt_tokens == 100
        assert completion.completion_tokens == 50
        assert completion.cost == decimal.Decimal("0.020000")

    def test_response_without_cost_is_accepted(self) -> None:
        body = completion_body(cost=None)
        completion = transport.interpret_response(result(200, body))
        assert completion.cost is None

    def test_oversized_body_is_rejected(self) -> None:
        body = b"x" * (transport.MAX_BODY_BYTES + 1)
        with pytest.raises(transport.TransportFailure) as raised:
            transport.interpret_response(result(200, body))
        assert raised.value.category == INVALID_RESPONSE
        assert raised.value.retryable is False

    def test_malformed_body_is_rejected(self) -> None:
        with pytest.raises(transport.TransportFailure) as raised:
            transport.interpret_response(result(200, b"not json"))
        assert raised.value.category == INVALID_RESPONSE

    def test_non_object_body_is_rejected(self) -> None:
        with pytest.raises(transport.TransportFailure):
            transport.interpret_response(result(200, b'["array"]'))

    def test_missing_model_is_rejected(self) -> None:
        body = json.dumps({"usage": {"prompt_tokens": 1, "completion_tokens": 1}, "choices": []})
        with pytest.raises(transport.TransportFailure):
            transport.interpret_response(result(200, body.encode()))

    def test_missing_usage_is_rejected(self) -> None:
        body = json.dumps(
            {"model": "m", "choices": [{"finish_reason": "stop", "message": {"content": "x"}}]}
        )
        with pytest.raises(transport.TransportFailure):
            transport.interpret_response(result(200, body.encode()))

    def test_negative_usage_is_rejected(self) -> None:
        body = completion_body(prompt_tokens=-1)
        with pytest.raises(transport.TransportFailure):
            transport.interpret_response(result(200, body))

    def test_empty_content_is_rejected(self) -> None:
        body = completion_body(content="   ")
        with pytest.raises(transport.TransportFailure) as raised:
            transport.interpret_response(result(200, body))
        assert raised.value.category == INVALID_RESPONSE

    def test_missing_content_is_rejected(self) -> None:
        body = completion_body()
        decoded = json.loads(body)
        del decoded["choices"][0]["message"]["content"]
        with pytest.raises(transport.TransportFailure):
            transport.interpret_response(result(200, json.dumps(decoded).encode()))

    @pytest.mark.parametrize("finish_reason", [None, "tool_calls", "content_filter", "unknown"])
    def test_unverified_finish_reasons_are_rejected(self, finish_reason: object) -> None:
        body = completion_body(finish_reason=finish_reason)  # type: ignore[arg-type]
        with pytest.raises(transport.TransportFailure) as raised:
            transport.interpret_response(result(200, body))
        assert raised.value.category == INVALID_RESPONSE

    def test_length_finish_reason_still_requires_local_validation(self) -> None:
        body = completion_body(finish_reason="length", content=json.dumps({"a": 1}))
        completion = transport.interpret_response(result(200, body))
        assert completion.content == '{"a": 1}'

    def test_choice_error_without_detail_is_rejected(self) -> None:
        body = completion_body(finish_reason="error")
        with pytest.raises(transport.TransportFailure) as raised:
            transport.interpret_response(result(200, body))
        assert raised.value.category == INVALID_RESPONSE

    def test_multiple_choices_are_rejected(self) -> None:
        body = completion_body()
        decoded = json.loads(body)
        decoded["choices"].append(decoded["choices"][0])
        with pytest.raises(transport.TransportFailure):
            transport.interpret_response(result(200, json.dumps(decoded).encode()))


class TestRetryAfterParsing:
    def test_seconds_form(self) -> None:
        assert transport.parse_retry_after({"Retry-After": "12"}) == 12.0

    def test_negative_seconds_are_clamped(self) -> None:
        assert transport.parse_retry_after({"Retry-After": "-5"}) == 0.0

    def test_http_date_form(self) -> None:
        import datetime
        import email.utils

        soon = datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=30)
        parsed = transport.parse_retry_after({"Retry-After": email.utils.format_datetime(soon)})
        assert 25 <= parsed <= 30

    def test_invalid_and_missing_values(self) -> None:
        assert transport.parse_retry_after({}) is None
        assert transport.parse_retry_after({"Retry-After": ""}) is None
        assert transport.parse_retry_after({"Retry-After": "not-a-date"}) is None


class TestUrllibTransport:
    @pytest.fixture
    def server(self):
        responses: list[tuple[int, dict[str, str], bytes]] = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                status, headers, body = responses.pop(0)
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: object) -> None:
                pass

        httpd = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield responses, f"http://127.0.0.1:{httpd.server_address[1]}"
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_http_error_response_is_captured_with_headers(self, server) -> None:
        responses, base_url = server
        responses.append((429, {"Retry-After": "7"}, b"{}"))
        client = transport.UrllibTransport(base_url, "secret-key")

        outcome = client.post_json({"messages": []}, timeout=5)

        assert outcome.status == 429
        assert outcome.headers["Retry-After"] == "7"
        failure = transport.classify_response(outcome)
        assert failure is not None
        assert failure.category == RATE_LIMITED
        assert failure.retry_after == 7.0

    def test_success_response_is_captured(self, server) -> None:
        responses, base_url = server
        body = completion_body()
        responses.append((200, {}, body))
        client = transport.UrllibTransport(base_url, "secret-key")

        outcome = client.post_json({}, timeout=5)

        assert outcome.status == 200
        assert outcome.body == body

    def test_timeout_inside_the_call_raises_transport_timeout(self, server) -> None:
        responses, base_url = server

        class SlowHandler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                import time

                time.sleep(2)
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args: object) -> None:
                pass

        httpd = HTTPServer(("127.0.0.1", 0), SlowHandler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            client = transport.UrllibTransport(
                f"http://127.0.0.1:{httpd.server_address[1]}", "secret-key"
            )
            with pytest.raises(transport.TransportTimeout):
                client.post_json({}, timeout=0.2)
        finally:
            httpd.shutdown()
            httpd.server_close()
        assert responses == []
