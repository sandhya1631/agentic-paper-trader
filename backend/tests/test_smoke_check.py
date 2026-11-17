"""The post-deploy smoke check script (#187): read-only, pass/fail per endpoint."""

import importlib.util
from pathlib import Path

import httpx

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "smoke_check.py"
_spec = importlib.util.spec_from_file_location("smoke_check", _PATH)
smoke_check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(smoke_check)


def _transport(responses: dict[str, tuple[int, dict]], seen: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        status, body = responses[request.url.path]
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler)


HEALTHY = {
    "/health": (200, {"status": "ok", "env": "production"}),
    "/health/db": (200, {"status": "ok", "result": 1}),
    "/llm/health": (
        200,
        {"provider": "openai", "model": "gpt-4o-mini", "reachable": True,
         "model_available": True, "detail": "ok"},
    ),
}


def test_passes_when_every_endpoint_is_healthy_and_only_reads(capsys):
    seen: list[httpx.Request] = []

    assert smoke_check.run(
        "https://api.example", "production", 5, transport=_transport(HEALTHY, seen)
    )

    assert [r.method for r in seen] == ["GET", "GET", "GET"]
    assert [r.url.path for r in seen] == ["/health", "/health/db", "/llm/health"]
    assert seen[2].url.params["probe"] == "true"
    assert all("authorization" not in r.headers for r in seen)
    assert "FAIL" not in capsys.readouterr().out


def test_fails_on_the_wrong_env():
    seen: list[httpx.Request] = []
    assert not smoke_check.run(
        "https://api.example", "production", 5,
        transport=_transport({**HEALTHY, "/health": (200, {"status": "ok", "env": "dev"})}, seen),
    )


def test_fails_when_the_llm_is_unreachable(capsys):
    seen: list[httpx.Request] = []
    unreachable = {
        **HEALTHY,
        "/llm/health": (503, {"provider": "anthropic", "reachable": False,
                              "detail": "LLM client unavailable: key missing"}),
    }

    assert not smoke_check.run(
        "https://api.example", None, 5, transport=_transport(unreachable, seen)
    )
    out = capsys.readouterr().out
    assert "[FAIL] GET /llm/health?probe=true -> 503" in out
    assert "key missing" in out


def test_fails_when_the_database_is_down():
    seen: list[httpx.Request] = []
    assert not smoke_check.run(
        "https://api.example", None, 5,
        transport=_transport({**HEALTHY, "/health/db": (500, {"detail": "error"})}, seen),
    )
