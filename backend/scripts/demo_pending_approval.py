"""Park a scripted BUY proposal for human approval, WITHOUT calling the real LLM.

For local testing and demos only. The real LLM keeps answering HOLD when the market
does not offer a BUY setup, so the approve/reject endpoints can never be exercised.
This script runs the real agent cycle (real orchestrator, real database, real policy
preview) but swaps the LLM for a fixed BUY answer. Nothing is submitted to the broker:
the cycle waits in PENDING_APPROVAL until someone calls approve or reject.

Usage (from the backend folder):
    python scripts/demo_pending_approval.py --email you@example.com
    python scripts/demo_pending_approval.py --email you@example.com --symbol AAPL --quantity 1

Prerequisite: call POST /agent/cycles/run once in Swagger so the user has an agent.
"""

import argparse
import asyncio
import json

from sqlalchemy import select

from app.agent.orchestrator import CycleOverlapError, run_agent_cycle
from app.core.config import get_settings
from app.db.enums import CycleTrigger
from app.db.models import AgentConfig, TradingAccount, User
from app.db.session import AsyncSessionLocal, engine
from app.llm.base import LLMClient, LLMHealth


class ScriptedLLM(LLMClient):
    """Returns one fixed answer instead of calling a model."""

    provider = "scripted"

    def __init__(self, answer: str) -> None:
        self.answer = answer

    async def generate(self, prompt: str, *, system: str | None = None, temperature: float = 0.2):
        return self.answer

    async def health_check(self) -> LLMHealth:
        return LLMHealth(
            provider=self.provider, model="scripted", reachable=True, model_available=True
        )


async def main(email: str, symbol: str, quantity: int) -> None:
    if get_settings().app_env.lower() == "production":
        raise SystemExit("Refusing to run: this script is for local testing only.")

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(AgentConfig)
            .join(TradingAccount, AgentConfig.account_id == TradingAccount.id)
            .join(User, TradingAccount.user_id == User.id)
            .where(User.email == email)
            .limit(1)
        )
        agent = result.scalar_one_or_none()
        if agent is None:
            raise SystemExit(
                f"No agent found for {email}. Call POST /agent/cycles/run once in Swagger first."
            )

        answer = json.dumps(
            {
                "symbol": symbol,
                "action": "BUY",
                "quantity": quantity,
                "reasoning": "Scripted BUY to test the approval flow (LLM call skipped).",
            }
        )
        try:
            cycle = await run_agent_cycle(
                agent.id, db, trigger=CycleTrigger.MANUAL, llm_client=ScriptedLLM(answer)
            )
        except CycleOverlapError:
            raise SystemExit(
                "This agent already has an open cycle (probably a pending approval). "
                "Approve or reject it first: GET /agent/approvals/pending."
            ) from None

        print(f"Cycle:    {cycle.id}")
        print(f"Status:   {cycle.status.value}")
        if cycle.approval is not None:
            print(f"Approval: {cycle.approval.id}  ({cycle.approval.status.value})")
            print("Next: GET /agent/approvals/pending, then POST .../approve or .../reject")
        else:
            print("No approval was created (see Status above).")
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--email", required=True, help="Email of the user who owns the agent")
    parser.add_argument("--symbol", default="AAPL")
    parser.add_argument("--quantity", type=int, default=1)
    args = parser.parse_args()
    asyncio.run(main(args.email, args.symbol.upper(), args.quantity))