import json
from types import SimpleNamespace

import httpx
import pytest

from app_dashboard.content_ai import ContentAIError, OpenRouterClient


def settings():
    return SimpleNamespace(
        openrouter_api_key="secret", openrouter_timeout_seconds=10,
        public_base_url="https://mantle.test",
        openrouter_generation_model="openai/test", openrouter_review_model="openai/reviewer",
    )


def client(payload):
    def handler(request):
        assert request.url.path == "/api/v1/chat/completions"
        body=json.loads(request.content)
        assert body["response_format"]["json_schema"]["strict"] is True
        return httpx.Response(200,json={"model":"openai/test","choices":[{"message":{"content":json.dumps(payload)}}]},request=request)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_openrouter_accepts_schema_conforming_payload():
    result=OpenRouterClient(settings(),client=client({"title":"T","sections":["One"]})).complete(
        schema_name="outline",messages=[{"role":"user","content":"test"}],
    )
    assert result.payload["sections"] == ["One"]


def test_openrouter_rejects_missing_or_extra_fields():
    for payload in ({"title":"T"},{"title":"T","sections":[],"invented":True}):
        with pytest.raises(ContentAIError):
            OpenRouterClient(settings(),client=client(payload)).complete(
                schema_name="outline",messages=[{"role":"user","content":"test"}],
            )
