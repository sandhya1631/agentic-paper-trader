from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.agent.prompt import get_system_prompt
from app.core.constants import (
    ALPACA_PROVIDER,
    DEFAULT_PROMPT_REVISION,
    DEFAULT_STRATEGY_VERSION,
)
from app.db.base import Base
from app.db.enums import (
    ApprovalMode,
    ApprovalStatus,
    BrokerOrderStatus,
    CycleStatus,
    CycleTrigger,
    KillSwitchScope,
    OrderSide,
    UserRole,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)



class UUIDPrimaryKey:
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class User(Base, UUIDPrimaryKey, Timestamped):
    __tablename__ = "users"
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.VIEWER)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    scheduler_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False
    )
    accounts: Mapped[list[TradingAccount]] = relationship(back_populates="user")

    def __init__(self, **kwargs):
        if "hashed_password" in kwargs and "password_hash" not in kwargs:
            kwargs["password_hash"] = kwargs.pop("hashed_password")
        super().__init__(**kwargs)

    @property
    def hashed_password(self) -> str:
        return self.password_hash

    @hashed_password.setter
    def hashed_password(self, value: str) -> None:
        self.password_hash = value


class PasswordResetToken(Base, UUIDPrimaryKey):
    """One-time password reset token (#166).

    Only the SHA-256 hash of the token is stored — the raw token is handed to the
    caller once (logged server-side in dev, since real email delivery is out of
    scope) and never persisted, so a database read alone cannot be used to reset a
    password. A token is single-use (used_at) and time-limited (expires_at).
    """

    __tablename__ = "password_reset_tokens"
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TradingAccount(Base, UUIDPrimaryKey, Timestamped):
    __tablename__ = "trading_accounts"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    provider: Mapped[str] = mapped_column(String(30), default=ALPACA_PROVIDER)
    encrypted_api_key: Mapped[str] = mapped_column(Text, nullable=False)
    encrypted_api_secret: Mapped[str] = mapped_column(Text, nullable=False)
    is_paper: Mapped[bool] = mapped_column(Boolean, default=True)
    is_connected: Mapped[bool] = mapped_column(Boolean, default=False)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user: Mapped[User] = relationship(back_populates="accounts")
    agents: Mapped[list[AgentConfig]] = relationship(back_populates="account")
    snapshots: Mapped[list[AccountSnapshot]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )
    __table_args__ = (
        CheckConstraint("is_paper = true", name="ck_account_paper_only"),
        # One connected account per (user, provider) — closes a race where concurrent
        # POST /accounts/alpaca requests could otherwise create duplicate rows.
        UniqueConstraint("user_id", "provider", name="uq_trading_account_user_provider"),
    )


class AccountSnapshot(Base, UUIDPrimaryKey):
    __tablename__ = "account_snapshots"
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trading_accounts.id", ondelete="CASCADE"), index=True
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    currency: Mapped[str] = mapped_column(String(10), default="USD")
    cash: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    portfolio_value: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    buying_power: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    equity: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    last_equity: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    daily_pnl: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    daily_pnl_percent: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 6), nullable=True
    )
    account: Mapped[TradingAccount] = relationship(back_populates="snapshots")


class PolicySet(Base, UUIDPrimaryKey, Timestamped):
    __tablename__ = "policy_sets"
    version: Mapped[str] = mapped_column(String(40), unique=True)
    limits_json: Mapped[dict] = mapped_column(JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    agents: Mapped[list[AgentConfig]] = relationship(back_populates="policy_set")


class AgentConfig(Base, UUIDPrimaryKey, Timestamped):
    __tablename__ = "agent_configs"
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("trading_accounts.id", ondelete="RESTRICT"), index=True)
    policy_set_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("policy_sets.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(String(80))
    watchlist: Mapped[list[str]] = mapped_column(JSON)
    strategy_version: Mapped[str] = mapped_column(String(30), default=DEFAULT_STRATEGY_VERSION)
    cadence_minutes: Mapped[int] = mapped_column(Integer, default=15)
    target_allocation: Mapped[Decimal] = mapped_column(Numeric(6, 5), default=Decimal("0.05"))
    max_symbol_allocation: Mapped[Decimal] = mapped_column(Numeric(6, 5), default=Decimal("0.10"))
    cash_reserve: Mapped[Decimal] = mapped_column(Numeric(6, 5), default=Decimal("0.05"))
    daily_loss_limit: Mapped[Decimal] = mapped_column(Numeric(6, 5), default=Decimal("0.02"))
    approval_mode: Mapped[ApprovalMode] = mapped_column(Enum(ApprovalMode), default=ApprovalMode.REQUIRED)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    model_provider: Mapped[str] = mapped_column(String(40))
    model_name: Mapped[str] = mapped_column(String(100))
    version: Mapped[int] = mapped_column(Integer, default=1)
    account: Mapped[TradingAccount] = relationship(back_populates="agents")
    policy_set: Mapped[PolicySet] = relationship(back_populates="agents")
    cycles: Mapped[list[AgentCycle]] = relationship(back_populates="agent")

    @validates("strategy_version")
    def validate_strategy_version(self, _key: str, value: str) -> str:
        get_system_prompt(value)
        return value

    __table_args__ = (
        CheckConstraint("cadence_minutes = 15", name="ck_agent_mvp_cadence"),
        CheckConstraint("target_allocation > 0 AND target_allocation <= max_symbol_allocation", name="ck_agent_target_allocation"),
        CheckConstraint("max_symbol_allocation <= 0.10", name="ck_agent_max_allocation"),
    )


class AgentCycle(Base, UUIDPrimaryKey):
    __tablename__ = "agent_cycles"
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_configs.id", ondelete="RESTRICT"), index=True)
    trigger: Mapped[CycleTrigger] = mapped_column(Enum(CycleTrigger), default=CycleTrigger.SCHEDULED)
    status: Mapped[CycleStatus] = mapped_column(Enum(CycleStatus), default=CycleStatus.PENDING)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    snapshot_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    prompt_version: Mapped[str] = mapped_column(
        String(30),
        default=DEFAULT_STRATEGY_VERSION,
        server_default=text(f"'{DEFAULT_STRATEGY_VERSION}'"),
    )
    prompt_revision: Mapped[str] = mapped_column(
        String(30),
        default=DEFAULT_PROMPT_REVISION,
        server_default=text(f"'{DEFAULT_PROMPT_REVISION}'"),
    )
    prompt_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    proposal_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Queryable, first-class policy audit record for every cycle (#121). Holds a
    # PolicyDecisionType value (ALLOW/DENY/RESIZE, or NOT_EVALUATED for HOLD/observe);
    # policy_result_json keeps the full per-policy evidence (minus the signed token).
    # Stored as plain text so the no-Alembic ensure_* patch is a simple ADD COLUMN.
    policy_decision: Mapped[str | None] = mapped_column(String(20), nullable=True)
    policy_result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    correlation_id: Mapped[uuid.UUID] = mapped_column(Uuid, default=uuid.uuid4, index=True)
    agent: Mapped[AgentConfig] = relationship(back_populates="cycles")
    approval: Mapped[CycleApproval | None] = relationship(
        back_populates="cycle", uselist=False
    )


class AgentSchedule(Base, UUIDPrimaryKey, Timestamped):
    __tablename__ = "agent_schedules"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_configs.id", ondelete="CASCADE"), index=True)
    interval_minutes: Mapped[int] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (UniqueConstraint("user_id", "agent_id", name="uq_schedule_user_agent"),)


class SystemRuntimeControl(Base, Timestamped):
    __tablename__ = "system_runtime_controls"
    key: Mapped[str] = mapped_column(String(30), primary_key=True)
    kill_switch_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class KillSwitchEvent(Base, UUIDPrimaryKey):
    """Audit trail: one row for every kill-switch change request (Story #72).

    Rows are only ever added. A request the server refused (an environment force-on
    cannot be disabled by API) is recorded too, with outcome REJECTED_ENV_OVERRIDE.

    scope distinguishes the admin-only GLOBAL switch (halts every user) from a USER
    switch (a user pausing only their own scheduler via /agent/scheduler/*). Regardless
    of scope, previous_enabled/new_enabled always mean "trading halted", so a resumed
    scheduler is new_enabled=False same as the global switch being turned off.
    target_user_id is the user affected (None for a GLOBAL event); actor_id is always
    who made the change, which for a USER event is the same user.
    """

    __tablename__ = "kill_switch_events"
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    previous_enabled: Mapped[bool] = mapped_column(Boolean)
    new_enabled: Mapped[bool] = mapped_column(Boolean)
    outcome: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    scope: Mapped[str] = mapped_column(String(10), default=KillSwitchScope.GLOBAL.value)
    target_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class CycleApproval(Base, UUIDPrimaryKey):
    __tablename__ = "cycle_approvals"
    cycle_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_cycles.id", ondelete="RESTRICT"), unique=True, index=True
    )
    status: Mapped[ApprovalStatus] = mapped_column(
        Enum(ApprovalStatus), default=ApprovalStatus.PENDING
    )
    proposal_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    acted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    comment: Mapped[str | None] = mapped_column(String(500))
    policy_version: Mapped[str | None] = mapped_column(String(40))
    policy_result_json: Mapped[dict | None] = mapped_column(JSON)
    cycle: Mapped[AgentCycle] = relationship(back_populates="approval")


class BrokerOrder(Base, UUIDPrimaryKey, Timestamped):
    """A paper order submitted to the broker (Story 1.3.1 / Issue #62).

    `decision_id` references AgentCycle.id — there is no separate Decision table
    yet (AgentCycle.proposal_json is the decision record for the MVP).
    """

    __tablename__ = "broker_orders"
    decision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_cycles.id", ondelete="RESTRICT"), index=True
    )
    client_order_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[BrokerOrderStatus] = mapped_column(
        Enum(BrokerOrderStatus), default=BrokerOrderStatus.PENDING_SUBMISSION
    )
    symbol: Mapped[str] = mapped_column(String(10))
    side: Mapped[OrderSide] = mapped_column(Enum(OrderSide))
    requested_quantity: Mapped[int] = mapped_column(Integer)
    filled_quantity: Mapped[int] = mapped_column(Integer, default=0)
    average_fill_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)