"""Offline contract checks for the opt-in DeepSeek rerank transport."""

import json
from uuid import UUID

import pytest
import requests

from server.services.agenthub.discovery import DiscoveryCandidate, PublicAgentMetadata
from server.services.agenthub.openai_rerank import OpenAICompatibleRerankTransport
from server.services.agenthub.reranker import RerankAdapter, RerankError

IDS = (UUID(int=1), UUID(int=2))
PRIVATE_MARKER = "private_tool_not_for_rerank"


def _candidate(index):
    return DiscoveryCandidate(
        agent_id=IDS[index], version=1,
        public_metadata=PublicAgentMetadata(
            name=f"Agent {index}", description="Public capability",
            capabilities=("analyze supplied text",), tags=("internal",),
            tools=(PRIVATE_MARKER,),
        ), raw_similarity=0.8,
    )


def _reply(ids=IDS, *, finish_reason="stop", content=None, status=200):
    if content is None:
        content = json.dumps({
            "candidate_ids": [str(agent_id) for agent_id in ids],
            "reason_code": "CAPABILITY_MATCH",
        })
    return FakeResponse(status, {
        "id": "fixture-completion", "object": "chat.completion", "created": 1,
        "model": "deepseek-flash",
        "choices": [{"index": 0, "finish_reason": finish_reason,
                     "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
    })


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self.payload = payload

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def post(self, url, *, headers, json, timeout):
        self.calls.append((url, headers, json, timeout))
        if self.error is not None:
            raise self.error
        return self.response


def _transport(session):
    return OpenAICompatibleRerankTransport(
        base_url="https://api.deepseek.com", model="deepseek-flash",
        api_key_env="DEEPSEEK_API_KEY", session=session,
    )


def test_json_request_uses_only_recalled_public_candidates(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture-secret")
    session = FakeSession(_reply(ids=(IDS[1], IDS[0])))

    result = RerankAdapter(_transport(session)).rerank(
        "Analyze supplied text", (_candidate(0), _candidate(1)),
    )

    assert result.ordered_candidate_ids == (IDS[1], IDS[0])
    url, headers, body, timeout = session.calls[0]
    assert url == "https://api.deepseek.com/chat/completions"
    assert headers == {"Authorization": "Bearer fixture-secret"}
    assert body["model"] == "deepseek-flash"
    assert body["response_format"] == {"type": "json_object"}
    assert body["max_tokens"] == 2048
    assert body["stream"] is False
    assert "JSON" in body["messages"][0]["content"]
    assert [item["id"] for item in json.loads(body["messages"][1]["content"])["candidates"]] == [
        str(IDS[0]), str(IDS[1]),
    ]
    assert PRIVATE_MARKER not in str(body)
    assert timeout > 0


def test_environment_defaults_to_supported_chat_model(monkeypatch):
    for key in ("AGENTHUB_RERANK_BASE_URL", "AGENTHUB_RERANK_API_KEY_ENV",
                "AGENTHUB_RERANK_MODEL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AGENTHUB_RERANK_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("AGENTHUB_RERANK_API_KEY_ENV", "DEEPSEEK_API_KEY")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture-secret")
    session = FakeSession(_reply(ids=(IDS[0],)))

    result = RerankAdapter(OpenAICompatibleRerankTransport.from_environment(
        session=session,
    )).rerank("Analyze", (_candidate(0),))

    assert result.ordered_candidate_ids == (IDS[0],)
    assert session.calls[0][2]["model"] == "deepseek-flash"


@pytest.mark.parametrize("status,code", [
    (401, "RERANK_AUTH_FAILED"), (403, "RERANK_AUTH_FAILED"),
    (429, "RERANK_RATE_LIMITED"), (500, "RERANK_SERVICE_UNAVAILABLE"),
    (400, "RERANK_REQUEST_FAILED"),
])
def test_http_failures_are_safe(monkeypatch, status, code):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture-secret")
    session = FakeSession(FakeResponse(status, {"error": "private provider response"}))
    with pytest.raises(RerankError) as raised:
        RerankAdapter(_transport(session)).rerank("Analyze", (_candidate(0),))
    assert raised.value.code == code
    assert "private" not in str(raised.value)
    assert "fixture-secret" not in repr(raised.value)


@pytest.mark.parametrize("error,code", [
    (requests.exceptions.Timeout("private provider response"), "RERANK_TIMEOUT"),
    (requests.exceptions.ConnectionError("private provider response"), "RERANK_SERVICE_ERROR"),
])
def test_network_failures_are_safe(monkeypatch, error, code):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture-secret")
    with pytest.raises(RerankError) as raised:
        RerankAdapter(_transport(FakeSession(error=error))).rerank(
            "Analyze", (_candidate(0),),
        )
    assert raised.value.code == code
    assert "private" not in str(raised.value)


def test_unknown_transport_error_detail_is_not_exposed():
    class UnsafeTransport:
        def complete(self, request):
            raise RerankError("private provider response")

    with pytest.raises(RerankError) as raised:
        RerankAdapter(UnsafeTransport()).rerank("Analyze", (_candidate(0),))
    assert raised.value.code == "RERANK_SERVICE_ERROR"
    assert "private" not in repr(raised.value)


@pytest.mark.parametrize("response,code", [
    (_reply(finish_reason="length"), "RERANK_TRUNCATED_RESPONSE"),
    (_reply(content="{"), "RERANK_INVALID_RESPONSE"),
    (_reply(ids=(UUID(int=99),)), "RERANK_UNKNOWN_CANDIDATE"),
    (_reply(content=""), "RERANK_INVALID_RESPONSE"),
])
def test_truncated_malformed_and_unknown_candidate_fail_safely(monkeypatch, response, code):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture-secret")
    with pytest.raises(RerankError) as raised:
        RerankAdapter(_transport(FakeSession(response))).rerank(
            "Analyze", (_candidate(0), _candidate(1)),
        )
    assert raised.value.code == code
    assert "fixture-secret" not in repr(raised.value)
