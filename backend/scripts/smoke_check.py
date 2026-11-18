"""Read-only post-deploy smoke check for a running API (#187).

Calls three unauthenticated GET endpoints and nothing else. It never logs in, never
starts a cycle and never touches orders, so it is safe against production at any time.

    GET /health                -> {"status": "ok", "env": ...}   (env must match --expect-env)
    GET /health/db             -> {"status": "ok", ...}           (the database answers)
    GET /llm/health?probe=true -> 200, "reachable" and "model_available" true
                                  (the configured LLM answers and has the configured model;
                                   for OpenAI this lists models: no tokens billed)

Usage (from backend/, with the venv active; needs only httpx):
    python scripts/smoke_check.py --base-url https://<api-host> --expect-env production
    API_BASE_URL=http://localhost:8000 python scripts/smoke_check.py --expect-env development

The first request retries (on a timeout, a refused connection or a 502/503/504 from
Render's proxy) for up to about 5 minutes, because Render's free tier sleeps and a
cold start can take about a minute. Exits 0 if every check passes, 1 otherwise. The
responses contain no secrets; the script prints only statuses and these fields.

The manual UI steps that follow it are in docs/deployment.md ("Smoke check").
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import httpx

COLD_START_ATTEMPTS = 6
COLD_START_DELAY_SECONDS = 15
WAKING_STATUSES = {502, 503, 504}  # Render's proxy while the instance starts


def _get(client: httpx.Client, path: str, *, retry: bool = False) -> httpx.Response | None:
    attempts = COLD_START_ATTEMPTS if retry else 1
    response: httpx.Response | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = client.get(path)
        except httpx.HTTPError as exc:  # timeout or connection refused while waking up
            print(f"  {path}: {type(exc).__name__} (attempt {attempt}/{attempts})")
        else:
            if not retry or response.status_code not in WAKING_STATUSES:
                return response
            print(f"  {path}: HTTP {response.status_code} (attempt {attempt}/{attempts})")
        if attempt < attempts:
            time.sleep(COLD_START_DELAY_SECONDS)
    return response


def _json(response: httpx.Response) -> dict:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def run(
    base_url: str,
    expect_env: str | None,
    timeout: float,
    *,
    transport: httpx.BaseTransport | None = None,
) -> bool:
    ok = True
    with httpx.Client(
        base_url=base_url.rstrip("/"), timeout=timeout, transport=transport
    ) as client:
        health = _get(client, "/health", retry=True)
        body = _json(health) if health is not None else {}
        env = body.get("env")
        passed = (
            health is not None
            and health.status_code == 200
            and body.get("status") == "ok"
            and bool(env)
            and (expect_env is None or env == expect_env)
        )
        status = health.status_code if health is not None else "no response"
        print(f"[{'PASS' if passed else 'FAIL'}] GET /health -> {status} env={env!r}"
              + (f" (expected {expect_env!r})" if expect_env else ""))
        ok &= passed
        if health is None:
            return False

        db = _get(client, "/health/db")
        db_body = _json(db) if db is not None else {}
        passed = db is not None and db.status_code == 200 and db_body.get("status") == "ok"
        status = db.status_code if db is not None else "no response"
        print(f"[{'PASS' if passed else 'FAIL'}] GET /health/db -> {status}")
        ok &= passed

        llm = _get(client, "/llm/health?probe=true")
        llm_body = _json(llm) if llm is not None else {}
        passed = (
            llm is not None
            and llm.status_code == 200
            and llm_body.get("reachable") is True
            and llm_body.get("model_available") is True
        )
        status = llm.status_code if llm is not None else "no response"
        print(
            f"[{'PASS' if passed else 'FAIL'}] GET /llm/health?probe=true -> {status} "
            f"provider={llm_body.get('provider')!r} model={llm_body.get('model')!r} "
            f"reachable={llm_body.get('reachable')!r} "
            f"model_available={llm_body.get('model_available')!r}"
        )
        if not passed and llm_body.get("detail"):
            print(f"       detail: {llm_body['detail']}")
        ok &= passed
    return ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--base-url",
        default=os.getenv("API_BASE_URL"),
        help="API base URL (or set API_BASE_URL)",
    )
    parser.add_argument(
        "--expect-env",
        default=None,
        help="required value of /health's env field, e.g. production",
    )
    parser.add_argument("--timeout", type=float, default=30.0, help="per-request seconds")
    args = parser.parse_args(argv)
    if not args.base_url:
        parser.error("pass --base-url or set API_BASE_URL")
    passed = run(args.base_url, args.expect_env, args.timeout)
    print("SMOKE CHECK PASSED" if passed else "SMOKE CHECK FAILED")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
