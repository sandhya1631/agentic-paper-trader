from enum import StrEnum


class UserRole(StrEnum):
    VIEWER = "VIEWER"
    OPERATOR = "OPERATOR"
    ADMIN = "ADMIN"
    AUDITOR = "AUDITOR"


class Symbol(StrEnum):
    AAPL = "AAPL"
    NVDA = "NVDA"
    SPY = "SPY"


class DecisionAction(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class TimeInForce(StrEnum):
    DAY = "DAY"


class ApprovalMode(StrEnum):
    OBSERVE_ONLY = "OBSERVE_ONLY"
    REQUIRED = "REQUIRED"
    AUTO = "AUTO"


class ApprovalStatus(StrEnum):
    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class CycleTrigger(StrEnum):
    SCHEDULED = "SCHEDULED"
    MANUAL = "MANUAL"


class CycleStatus(StrEnum):
    PENDING = "PENDING"
    COLLECTING = "COLLECTING"
    REASONING = "REASONING"
    VALIDATING = "VALIDATING"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    HOLD = "HOLD"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    RECONCILING = "RECONCILING"


class PolicyOutcome(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class PolicyDecisionType(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    RESIZE = "RESIZE"


class BrokerOrderStatus(StrEnum):
    PENDING_SUBMISSION = "PENDING_SUBMISSION"
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    RECONCILING = "RECONCILING"


class DataQualityStatus(StrEnum):
    VALID = "VALID"
    STALE = "STALE"
    INCOMPLETE = "INCOMPLETE"
    INVALID = "INVALID"


class MarketSessionStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class AuditActorType(StrEnum):
    USER = "USER"
    SYSTEM = "SYSTEM"
    SCHEDULER = "SCHEDULER"
