from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.db.enums import (
    ApprovalMode, ApprovalStatus, AuditActorType, BrokerOrderStatus, CycleStatus,
    CycleTrigger, DecisionAction, OrderSide, OrderType, PolicyDecisionType,
    PolicyOutcome, Symbol, TimeInForce,
)

Money = Decimal


class APIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class AgentConfigRead(APIModel):
    id: UUID
    name: str
    strategy_version: str = "S-001"
    cadence_minutes: int = 15
    target_allocation: Decimal = Decimal("0.05")
    max_symbol_allocation: Decimal = Decimal("0.10")
    cash_reserve: Decimal = Decimal("0.05")
    daily_loss_limit: Decimal = Decimal("0.02")
    approval_mode: ApprovalMode = ApprovalMode.REQUIRED
    enabled: bool = False


class DecisionProposal(APIModel):
    symbol: Symbol
    action: DecisionAction
    quantity: int = Field(gt=0)
    reasoning: str


class MarketSnapshot(APIModel):
    symbol: Symbol
    current_price: Decimal = Field(gt=0)
    rsi_14: Decimal | None = None
    sma_20: Decimal | None = None


class PolicyInput(APIModel):
    agent: AgentConfigRead
    proposal: DecisionProposal
    market: MarketSnapshot
    kill_switch_enabled: bool
    orders_today: int = Field(ge=0)


class PolicyEvaluation(APIModel):
    policy_id: str = Field(pattern=r"^P-\d{3}$")
    policy_name: str
    outcome: PolicyOutcome
    reason: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class PolicyDecision(APIModel):
    decision: PolicyDecisionType
    policy_version: str
    evaluated_at: datetime
    evaluations: list[PolicyEvaluation] = Field(min_length=1)
    final_quantity: int | None = Field(default=None, gt=0)
    policy_token: str | None = None

    @model_validator(mode="after")
    def decision_is_consistent(self):
        failed = any(item.outcome == PolicyOutcome.FAIL for item in self.evaluations)
        if self.decision == PolicyDecisionType.ALLOW and failed:
            raise ValueError("ALLOW cannot contain a failed policy")
        if self.decision == PolicyDecisionType.DENY and not failed:
            raise ValueError("DENY requires at least one failed policy")
        if self.decision != PolicyDecisionType.DENY and not self.policy_token:
            raise ValueError("Allowed/resized decision requires policy_token")
        return self


class ApprovalRequest(APIModel):
    decision_id: UUID
    proposal_hash: str = Field(min_length=32, max_length=128)
    expires_at: datetime


class ApprovalAction(APIModel):
    expected_decision_version: int = Field(ge=1)
    comment: str | None = Field(default=None, max_length=500)


class ApprovalRead(APIModel):
    id: UUID
    decision_id: UUID
    status: ApprovalStatus
    requested_at: datetime
    expires_at: datetime
    acted_at: datetime | None = None
    actor_id: UUID | None = None
    comment: str | None = None


class ApprovedOrder(APIModel):
    decision_id: UUID
    policy_token: str
    client_order_id: str = Field(min_length=8, max_length=64)
    symbol: Symbol
    side: OrderSide
    quantity: int = Field(gt=0)
    order_type: OrderType
    limit_price: Money | None = Field(default=None, gt=0)
    time_in_force: TimeInForce = TimeInForce.DAY

    @model_validator(mode="after")
    def price_matches_type(self):
        if self.order_type == OrderType.LIMIT and self.limit_price is None:
            raise ValueError("LIMIT requires limit_price")
        if self.order_type == OrderType.MARKET and self.limit_price is not None:
            raise ValueError("MARKET cannot include limit_price")
        return self


class BrokerOrderRead(APIModel):
    id: UUID
    decision_id: UUID
    broker_order_id: str | None = None
    client_order_id: str
    status: BrokerOrderStatus
    symbol: Symbol
    side: OrderSide
    requested_quantity: int
    filled_quantity: int = Field(ge=0)
    average_fill_price: Money | None = None
    submitted_at: datetime | None = None
    updated_at: datetime


class CycleRead(APIModel):
    id: UUID
    agent_id: UUID
    trigger: CycleTrigger
    status: CycleStatus
    started_at: datetime
    completed_at: datetime | None = None
    failure_code: str | None = None
    failure_message: str | None = None


class AuditEventRead(APIModel):
    id: UUID
    occurred_at: datetime
    actor_type: AuditActorType
    actor_id: UUID | None = None
    event_type: str
    entity_type: str
    entity_id: UUID | None = None
    correlation_id: UUID
    details: dict[str, Any] = Field(default_factory=dict)


class APIErrorDetail(APIModel):
    field: str | None = None
    policy_id: str | None = None
    reason: str


class APIError(APIModel):
    code: str
    message: str
    correlation_id: UUID
    details: list[APIErrorDetail] = Field(default_factory=list)
