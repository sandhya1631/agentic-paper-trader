from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    JSON, Boolean, CheckConstraint, DateTime, Enum, ForeignKey, Integer,
    Numeric, String, Text, Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.db.enums import (
    ApprovalMode, ApprovalStatus, AuditActorType, BrokerOrderStatus, CycleStatus,
    CycleTrigger, DecisionAction, OrderSide, OrderType, PolicyDecisionType,
    PolicyOutcome, Symbol, TimeInForce, UserRole,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


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


class TradingAccount(Base, UUIDPrimaryKey, Timestamped):
    __tablename__ = "trading_accounts"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    provider: Mapped[str] = mapped_column(String(30), default="ALPACA")
    encrypted_api_key: Mapped[str] = mapped_column(Text, nullable=False)
    encrypted_api_secret: Mapped[str] = mapped_column(Text, nullable=False)
    is_paper: Mapped[bool] = mapped_column(Boolean, default=True)
    is_connected: Mapped[bool] = mapped_column(Boolean, default=False)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user: Mapped[User] = relationship(back_populates="accounts")
    agents: Mapped[list[AgentConfig]] = relationship(back_populates="account")
    __table_args__ = (CheckConstraint("is_paper = true", name="ck_account_paper_only"),)


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
    strategy_version: Mapped[str] = mapped_column(String(30), default="S-001")
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
    agent: Mapped[AgentConfig] = relationship(back_populates="cycles")
