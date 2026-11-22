"""Data models and schemas for BETA backend."""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class SKPrefix:
    """DynamoDB Sort Key prefixes for BETA single-table design."""

    MEMBER = "MEMBER#"
    PAYEE = "PAYEE#"
    BILLER = "BILLER#"
    MED = "MED#"
    RULE = "RULE#"
    APPROVAL = "APPROVAL#"
    ASSESSMENT = "ASSESSMENT#"
    AUDIT = "AUDIT#"
    RECEIPT = "RECEIPT#"
    PAYMENT = "PAYMENT#"


class MemberRole(str, Enum):
    PARENT = "PARENT"
    GUARDIAN = "GUARDIAN"
    ASSISTANT = "ASSISTANT"


class ApprovalStatus(str, Enum):
    PENDING = "PENDING"
    COOLING_OFF = "COOLING_OFF"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


@dataclass
class HouseholdMember:
    """Represents a member of the household (parent or guardian)."""

    household_id: str
    member_id: str
    name: str
    role: str  # PARENT or GUARDIAN
    age: Optional[int] = None
    location: str = ""
    phone: Optional[str] = None
    relation: Optional[str] = None

    def to_item(self) -> Dict[str, Any]:
        return {
            "PK": self.household_id
            if self.household_id.startswith("HH#")
            else f"HH#{self.household_id}",
            "SK": f"{SKPrefix.MEMBER}{self.member_id}",
            "member_id": self.member_id,
            "name": self.name,
            "role": self.role,
            "age": self.age,
            "location": self.location,
            "phone": self.phone,
            "relation": self.relation,
            "entity_type": "MEMBER",
        }


@dataclass
class HouseholdRules:
    """Guardrail policies and threshold rules configured for a household."""

    auto_limit: float = 2000.0  # In INR: under this runs automatically
    max_with_approval: float = (
        25000.0  # In INR: upper ceiling even with guardian approval
    )
    frozen: bool = False  # Emergency kill switch
    cooling_off_seconds: int = 20  # Delay period before guardian approval activates
    night_start: int = 23  # Night curfew start hour in IST (23 = 11 PM)
    night_end: int = 6  # Night curfew end hour in IST (6 = 6 AM)
    max_payments_per_hour: int = 2  # Rate limit for financial transactions

    def to_item(self, household_id: str) -> Dict[str, Any]:
        return {
            "PK": household_id
            if household_id.startswith("HH#")
            else f"HH#{household_id}",
            "SK": f"{SKPrefix.RULE}FINANCIAL",
            "auto_limit": self.auto_limit,
            "max_with_approval": self.max_with_approval,
            "frozen": self.frozen,
            "cooling_off_seconds": self.cooling_off_seconds,
            "night_start": self.night_start,
            "night_end": self.night_end,
            "max_payments_per_hour": self.max_payments_per_hour,
            "entity_type": "RULE",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }


@dataclass
class Biller:
    """Pre-registered utility biller (e.g., BESCOM, BWSSB)."""

    biller_id: str
    name: str
    utility_type: str  # electricity, water, gas, internet
    bill_id: Optional[str] = None
    amount: float = 0.0
    due_date: str = ""
    account_no: Optional[str] = None
    status: str = "PENDING"  # PENDING or PAID

    def to_item(self, household_id: str) -> Dict[str, Any]:
        return {
            "PK": household_id
            if household_id.startswith("HH#")
            else f"HH#{household_id}",
            "SK": f"{SKPrefix.BILLER}{self.biller_id}",
            "biller_id": self.biller_id,
            "name": self.name,
            "utility_type": self.utility_type,
            "bill_id": self.bill_id,
            "amount": self.amount,
            "due_date": self.due_date,
            "account_no": self.account_no,
            "status": self.status,
            "entity_type": "BILLER",
        }


@dataclass
class Payee:
    """Known and verified payee or merchant (e.g. Apollo Pharmacy)."""

    payee_id: str
    name: str
    category: str  # Pharmacy, Grocery, Clinic, Utility
    upi_id: Optional[str] = None
    verified: bool = True
    trusted: bool = True

    def to_item(self, household_id: str) -> Dict[str, Any]:
        return {
            "PK": household_id
            if household_id.startswith("HH#")
            else f"HH#{household_id}",
            "SK": f"{SKPrefix.PAYEE}{self.payee_id}",
            "payee_id": self.payee_id,
            "name": self.name,
            "category": self.category,
            "upi_id": self.upi_id,
            "verified": self.verified,
            "trusted": self.trusted,
            "entity_type": "PAYEE",
        }


@dataclass
class Medicine:
    """Prescribed chronic medication for elderly parent."""

    med_id: str
    name: str
    dosage: str
    schedule: str
    refill_qty: int
    unit_price: float
    reorder_total: float
    prescribed_by: Optional[str] = None

    def to_item(self, household_id: str) -> Dict[str, Any]:
        return {
            "PK": household_id
            if household_id.startswith("HH#")
            else f"HH#{household_id}",
            "SK": f"{SKPrefix.MED}{self.med_id}",
            "med_id": self.med_id,
            "name": self.name,
            "dosage": self.dosage,
            "schedule": self.schedule,
            "refill_qty": self.refill_qty,
            "unit_price": self.unit_price,
            "reorder_total": self.reorder_total,
            "prescribed_by": self.prescribed_by,
            "entity_type": "MED",
        }


@dataclass
class SecurityFacts:
    """The 7 injected security facts evaluated by Bedrock AgentCore Interceptors."""

    payee_known: bool
    hour_ist: int  # 0 to 23
    payments_last_hour: int
    approval_valid: bool
    scam_flag: bool
    frozen: bool
    assessment_valid: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ApprovalRequest:
    """Guardian authorization request for an errand exceeding auto_limit."""

    approval_id: str
    household_id: str
    errand_type: str
    payee_name: str
    amount: float
    status: str = ApprovalStatus.PENDING.value
    requested_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    cooling_off_until: Optional[str] = None
    expires_at: Optional[str] = None
    guardian_response: Optional[str] = None
    ttl: Optional[int] = None

    def to_item(self) -> Dict[str, Any]:
        return {
            "PK": self.household_id
            if self.household_id.startswith("HH#")
            else f"HH#{self.household_id}",
            "SK": f"{SKPrefix.APPROVAL}{self.approval_id}",
            "approval_id": self.approval_id,
            "errand_type": self.errand_type,
            "payee_name": self.payee_name,
            "amount": self.amount,
            "status": self.status,
            "requested_at": self.requested_at,
            "cooling_off_until": self.cooling_off_until,
            "expires_at": self.expires_at,
            "guardian_response": self.guardian_response,
            "ttl": self.ttl,
            "entity_type": "APPROVAL",
        }


@dataclass
class AuditEvent:
    """Structured audit trail record logged for every security-sensitive action."""

    household_id: str
    action: str
    actor: str
    details: Dict[str, Any]
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    risk_score: float = 0.0
    status: str = "SUCCESS"
    ttl: Optional[int] = None

    def to_item(self) -> Dict[str, Any]:
        return {
            "PK": self.household_id
            if self.household_id.startswith("HH#")
            else f"HH#{self.household_id}",
            "SK": f"{SKPrefix.AUDIT}{self.timestamp}",
            "action": self.action,
            "actor": self.actor,
            "details": self.details,
            "timestamp": self.timestamp,
            "risk_score": self.risk_score,
            "status": self.status,
            "ttl": self.ttl,
            "entity_type": "AUDIT",
        }
