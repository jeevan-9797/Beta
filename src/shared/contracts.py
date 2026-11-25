"""Shared Pydantic models and contract schemas for BETA.

Defines the core data contracts exchanged between:
- ElevenLabs Conversational AI Agent
- shield-mcp server (FastMCP / Model Context Protocol)
- AWS Bedrock AgentCore Gateway (ap-south-1)
- Bedrock Plain-Language Receipt Generator
- Guardian Notification Channels
"""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class RiskTier(str, Enum):
    """Guardian Gate risk tiers for senior protection:
    
    - R0: Read-only / Informational (check balance, read bill, calendar lookup)
    - R1: Low reversible errands / calendar (book appointment, set reminder)
    - R2: Small known payee <= 2000 (routine verified bill/grocery/medicine under limit)
    - R3: High amount (> 2000), new payee, or odd hour requiring Guardian WhatsApp approval
    - R4: Danger / Scam coercion (Digital arrest, extortion, APK install, cyber police)
    """

    R0 = "R0"  # Read-only queries / informational
    R1 = "R1"  # Low reversible errands (calendar, appointments, reminders)
    R2 = "R2"  # Small known payee <= ₹2,000 (routine verified utility/pharmacy)
    R3 = "R3"  # High amount (> ₹2,000) or new payee requiring Guardian WhatsApp approval
    R4 = "R4"  # Critical scam detected (digital arrest, coercion, extortion, malware)


class RiskLevel(str, Enum):
    """Aggregated risk level for high-level decision gating."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class AssessRiskInput(BaseModel):
    """Input payload for shield.assess_risk."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    household_id: str = Field(
        ...,
        description="Unique identifier for the senior citizen's household profile.",
        examples=["hh_delhi_9921", "hh_blr_4012"],
    )
    snippet: str = Field(
        ...,
        description="Spoken conversation transcript, transcription chunk, or instruction text.",
        examples=["Beta, BESCOM bijli ka bill pay kar do ₹1,240 jo 28 Nov ko due hai."],
    )
    intent: str = Field(
        ...,
        description="Identified conversation intent (e.g., pay_utility_bill, digital_arrest, kyc_suspension).",
        examples=["pay_utility_bill", "digital_arrest"],
    )
    amount: Optional[float] = Field(
        default=None,
        description="Transaction or payment amount in INR (₹), if applicable.",
        examples=[1240.0, 3800.0, 200000.0, None],
    )


class AssessRiskOutput(BaseModel):
    """Assessment result returned by shield.assess_risk."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    assessment_id: str = Field(
        ...,
        description="Cryptographically unique assessment token required to proceed with downstream actions.",
        examples=["sec_eval_9a2b8f41"],
    )
    risk_level: RiskLevel = Field(
        ...,
        description="Aggregated risk level (LOW, MEDIUM, HIGH).",
        examples=[RiskLevel.LOW, RiskLevel.HIGH],
    )
    tier: RiskTier = Field(
        ...,
        description="Specific risk tier (R0 to R4).",
        examples=[RiskTier.R0, RiskTier.R1, RiskTier.R4],
    )
    signals: List[str] = Field(
        default_factory=list,
        description="List of detected risk heuristics, anomaly indicators, or threat indicators.",
        examples=[["whitelisted_payee", "routine_reorder"], ["impersonation_cbi", "urgency_coercion"]],
    )
    recommended_action: str = Field(
        ...,
        description="Recommended action for the agent: ALLOW, CONFIRM_WITH_USER, BLOCK_AND_ALERT, etc.",
        examples=["ALLOW", "BLOCK_AND_ALERT"],
    )
    expires_at: int = Field(
        ...,
        description="Unix timestamp (seconds) until which this assessment token is valid (e.g. 300s TTL).",
        examples=[1774390000],
    )


class ExplainScamInput(BaseModel):
    """Input payload for shield.explain_scam."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    scam_type: str = Field(
        ...,
        description="Categorized scam pattern (e.g., digital_arrest, electricity_bill_fraud, kyc_expiry, customs_parcel).",
        examples=["digital_arrest", "electricity_disconnection"],
    )
    language: str = Field(
        default="hi-en",
        description="Target spoken language code (e.g., 'hi-en' for Hinglish, 'hi' for Hindi, 'en' for English).",
        examples=["hi-en", "hi", "en"],
    )


class ExplainScamOutput(BaseModel):
    """Output explanation and alert scripts from shield.explain_scam."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    script_spoken: str = Field(
        ...,
        description="Empathetic, clear, and reassuring Hinglish/English script for the senior to de-escalate fear.",
        examples=[
            "Uncleji, bilkul ghabrayiye mat. Police ya CBI kabhi video call pe arrest nahi karti. Yeh 100% fraud call hai."
        ],
    )
    helpline: str = Field(
        default="1930",
        description="Official Indian National Cyber Crime Reporting Portal helpline number.",
        examples=["1930"],
    )
    guardian_alert_text: str = Field(
        ...,
        description="Instant SMS/WhatsApp notification text dispatched to the senior's designated guardian/family.",
        examples=[
            "ALERT for Household hh_blr_4012: Dadaji just received a fake CBI digital arrest call. BETA blocked transaction and calmed him. Call 1930 if needed."
        ],
    )


class ReceiptInput(BaseModel):
    """Input payload for Bedrock Plain-Language Receipt Generator."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    household_id: str = Field(
        ...,
        description="Household identifier.",
        examples=["hh_delhi_9921"],
    )
    action_type: str = Field(
        ...,
        description="Action performed (e.g., utility_payment, appointment_booking, transaction_blocked).",
        examples=["utility_payment", "scam_blocked"],
    )
    payee: str = Field(
        ...,
        description="Beneficiary or party name (e.g., BESCOM, Dr. Sharma Clinic, Unknown Extortionist).",
        examples=["BESCOM", "Unknown Threat Actor"],
    )
    amount: Optional[float] = Field(
        default=None,
        description="Monetary amount in INR (₹), or None if not a monetary action.",
        examples=[1240.0, 3800.0, None],
    )
    tier: RiskTier = Field(
        ...,
        description="Risk tier validated during execution.",
        examples=[RiskTier.R2, RiskTier.R3, RiskTier.R4],
    )
    decision: str = Field(
        ...,
        description="Final action outcome (e.g., EXECUTED, REJECTED, BLOCKED, SCHEDULED).",
        examples=["EXECUTED", "BLOCKED"],
    )
    reason_facts: Dict[str, Any] = Field(
        default_factory=dict,
        description="Factual metadata regarding payment reference, meter number, or detected threat signals.",
        examples=[{"biller": "BESCOM", "consumer_no": "90812314", "reference_id": "TXN_771892", "due_date": "28 Nov"}],
    )


class ReceiptOutput(BaseModel):
    """Generated plain-language receipts for voice read-out and guardian WhatsApp."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    spoken_summary: str = Field(
        ...,
        description="Short, comforting, plain-language audio script read to the senior over the voice call.",
        examples=[
            "Uncleji, aapka ₹1,240 ka BESCOM bijli bill successfully pay ho gaya hai. Reference number 771892 hai."
        ],
    )
    whatsapp_summary: str = Field(
        ...,
        description="Detailed, structured WhatsApp receipt dispatched to the senior and guardian.",
        examples=[
            "🧾 *BETA Transaction Receipt*\n• Biller: BESCOM\n• Amount: ₹1,240\n• Status: Successful ✅\n• Ref: TXN_771892\n• Sent to guardian."
        ],
    )
