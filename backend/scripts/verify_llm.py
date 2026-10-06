"""Verify the configured local LLM is installed and reachable (issue #56).

Runs the real code path — the LLM factory builds the configured client, probes
connectivity, confirms the model is available, and performs one live generation.

Usage (from backend/, with deps installed and the venv active):
    python scripts/verify_llm.py

Reads configuration from the environment / .env (LLM_PROVIDER, OLLAMA_BASE_URL,
OLLAMA_MODEL, etc.). Exits 0 on success, 1 on any failure — suitable for a
manual check or a pre-demo smoke test.
"""

import asyncio
import sys

from app.core.config import get_settings
from app.llm.factory import create_llm_client


async def _run() -> int:
    settings = get_settings()
    client = create_llm_client(settings)

    health = await client.health_check()
    print(f"provider        : {health.provider}")
    print(f"model           : {health.model}")
    print(f"reachable       : {health.reachable}")
    print(f"model_available : {health.model_available}")
    print(f"detail          : {health.detail}")

    if not health.reachable:
        print("FAIL: LLM backend is not reachable.")
        return 1
    if not health.model_available:
        print("FAIL: configured model is not available on the backend.")
        return 1

    reply = await client.generate("Reply with exactly: VERIFY_OK")
    print(f"generate()      : {reply!r}")
    if not reply.strip():
        print("FAIL: backend returned an empty completion.")
        return 1

    print("PASS: local LLM verified (reachable, model available, generation works).")
    return 0


def main() -> int:
    try:
        return asyncio.run(_run())
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
