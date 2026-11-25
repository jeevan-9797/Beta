"""AWS Bedrock Plain-Language Receipt Generator for BETA.

Region: ap-south-1 (Mumbai).
Transforms transaction facts into:
1. Spoken Summary: Warm, respectful, elder-friendly Hinglish audio readout including
   a 1-sentence plain-language reason ("Why did BETA do this?").
2. WhatsApp Summary: Structured, transparent text receipt for the household's family guardian
   explicitly detailing "Why BETA did this".
"""

import json
import logging
import os
from typing import Any, Dict, Optional
from src.shared.contracts import ReceiptInput, ReceiptOutput, RiskTier

logger = logging.getLogger(__name__)

DEFAULT_REGION = os.getenv("AWS_DEFAULT_REGION", "ap-south-1")
DEFAULT_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "apac.amazon.nova-micro-v1:0")


class BedrockReceiptGenerator:
    """Generates plain-language voice and WhatsApp receipts via AWS Bedrock AgentCore."""

    def __init__(
        self,
        region_name: str = DEFAULT_REGION,
        model_id: str = DEFAULT_MODEL_ID,
        boto3_client: Optional[Any] = None,
    ) -> None:
        self.region_name = region_name
        self.model_id = model_id
        self._client = boto3_client

    @property
    def client(self) -> Any:
        if self._client is None:
            import boto3
            self._client = boto3.client("bedrock-runtime", region_name=self.region_name)
        return self._client

    def generate(self, receipt_input: ReceiptInput) -> ReceiptOutput:
        """Generates spoken and WhatsApp summaries from ReceiptInput."""
        try:
            return self._invoke_bedrock(receipt_input)
        except Exception as e:
            logger.info(
                "Bedrock receipt invocation offline/unavailable (%s); generating structured elder-fallback", e
            )
            return self._generate_fallback(receipt_input)

    def _invoke_bedrock(self, inp: ReceiptInput) -> ReceiptOutput:
        """Invokes AWS Bedrock Converse API with strict structured JSON output."""
        system_prompt = (
            "You are BETA's Plain-Language Receipt Generator for Indian senior citizens and their families. "
            "Given the transaction details, return a JSON object with exactly two fields:\n"
            "1. 'spoken_summary': A warm, comforting, respectful Hinglish audio script (using Uncleji/Dadaji/Auntyji) "
            "that the AI will speak over the voice call. MUST include a 1-sentence plain-language explanation "
            "answering 'Why did BETA do this?'. Keep it concise, natural, and jargon-free.\n"
            "2. 'whatsapp_summary': A neatly formatted, clear WhatsApp message for the guardian with bullet points, "
            "status emojis, payee, amount, decision, and an explicit bullet point '• Why BETA did this: <1-sentence reason>'."
        )

        user_content = json.dumps(
            {
                "household_id": inp.household_id,
                "action_type": inp.action_type,
                "payee": inp.payee,
                "amount": inp.amount,
                "tier": inp.tier.value if hasattr(inp.tier, "value") else str(inp.tier),
                "decision": inp.decision,
                "reason_facts": inp.reason_facts,
            },
            indent=2,
        )

        messages = [{"role": "user", "content": [{"text": user_content}]}]

        response = self.client.converse(
            modelId=self.model_id,
            messages=messages,
            system=[{"text": system_prompt}],
            inferenceConfig={"temperature": 0.2, "maxTokens": 500},
        )

        response_text = response["output"]["message"]["content"][0]["text"].strip()

        # Handle json markdown fences if present
        if response_text.startswith("```"):
            response_text = response_text.split("```")[1]
            if response_text.startswith("json"):
                response_text = response_text[4:].strip()

        parsed = json.loads(response_text)
        return ReceiptOutput(
            spoken_summary=parsed["spoken_summary"],
            whatsapp_summary=parsed["whatsapp_summary"],
        )

    def _generate_fallback(self, inp: ReceiptInput) -> ReceiptOutput:
        """Deterministic elder-friendly template fallback with explicit 1-sentence explanation."""
        amount_str = f"₹{inp.amount:,.2f}" if inp.amount is not None else "N/A"
        tier_val = inp.tier.value if hasattr(inp.tier, "value") else str(inp.tier)

        if inp.decision == "BLOCKED" or inp.tier == RiskTier.R4:
            why_reason = "Police, CBI ya legitimate agencies kabhi video call ya phone pe urgent paise transfer karne ko nahi bolti."
            spoken = (
                f"Uncleji, humne suspicious request ko suraksha ke liye turant rok diya hai, "
                f"kyunki {why_reason.lower()} "
                f"Kisi bhi anjaan payee ko paise nahi gaye hain aur guardian ko inform kar diya hai."
            )
            whatsapp = (
                f"🛑 *BETA Security Alert: Transaction Blocked*\n"
                f"• Household: `{inp.household_id}`\n"
                f"• Suspected Payee: {inp.payee}\n"
                f"• Requested Amount: {amount_str}\n"
                f"• Risk Tier: {tier_val} (High Risk / Scam Heuristic)\n"
                f"• Status: {inp.decision} (Blocked & Senior Protected) ✅\n"
                f"• Why BETA did this: Blocked coercive extortion/fraud pattern to prevent financial loss.\n"
                f"• Cyber Helpline: 1930\n"
                f"• Notes: Senior was reassured. Please verify with household."
            )
        elif inp.tier == RiskTier.R3:
            why_reason = f"Transaction amount {amount_str} exceeds safe threshold of ₹2,000, requiring Guardian WhatsApp approval."
            spoken = (
                f"Uncleji, yeh payment hold pe rakha hai kyunki amount ₹2,000 se zyada hai. "
                f"Humne aapke guardian ko WhatsApp pe approval request bhej di hai."
            )
            whatsapp = (
                f"⏳ *BETA Action: Guardian Approval Required*\n"
                f"• Household: `{inp.household_id}`\n"
                f"• Payee: {inp.payee}\n"
                f"• Amount: {amount_str}\n"
                f"• Risk Tier: R3 (High Amount / Guardian Gate)\n"
                f"• Status: {inp.decision} ⏸️\n"
                f"• Why BETA did this: {why_reason}\n"
                f"• Action: Please tap Approve or Reject in WhatsApp."
            )
        else:
            why_reason = f"Verified domestic payee ({inp.payee}) within routine elder allowance."
            spoken = (
                f"Uncleji, aapka {inp.payee} ka {amount_str} successfully clear ho gaya hai, "
                f"kyunki yeh aapka regular verified domestic bill hai. "
                f"Receipt guardian ke WhatsApp pe bhej di gayi hai."
            )
            ref_id = inp.reason_facts.get("reference_id", "REF-BBPS-OK")
            whatsapp = (
                f"🧾 *BETA Household Receipt*\n"
                f"• Household: `{inp.household_id}`\n"
                f"• Payee/Biller: {inp.payee}\n"
                f"• Amount Paid: {amount_str}\n"
                f"• Status: {inp.decision} ✅\n"
                f"• Verified Risk Tier: {tier_val}\n"
                f"• Why BETA did this: {why_reason}\n"
                f"• Reference: {ref_id}\n"
                f"• Dispatched to family guardian record."
            )

        return ReceiptOutput(
            spoken_summary=spoken,
            whatsapp_summary=whatsapp,
        )


def generate_receipt(
    household_id: str,
    action_type: str,
    payee: str,
    amount: Optional[float],
    tier: RiskTier,
    decision: str,
    reason_facts: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Convenience functional wrapper for generate_receipt."""
    inp = ReceiptInput(
        household_id=household_id,
        action_type=action_type,
        payee=payee,
        amount=amount,
        tier=tier,
        decision=decision,
        reason_facts=reason_facts or {},
    )
    generator = BedrockReceiptGenerator()
    return generator.generate(inp).model_dump()
