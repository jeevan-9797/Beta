"""Shield MCP Server implementation for BETA.

Exposes security and risk evaluation tools using:
1. FastMCP HTTP/SSE transport
2. AWS Lambda handler (lambda_handler) for AWS Bedrock AgentCore Gateway in ap-south-1
3. DynamoDB single-table persistence (BETA_Household) with MOCK_DB support
4. 2-Step Evaluation: Deterministic Keyword Fast Path (Step 1) + Amazon Bedrock LLM (Step 2)
"""

import hashlib
import json
import logging
import os
import re
import time
import uuid
from typing import Any, Dict, List, Optional

try:
    from fastmcp import FastMCP
except ImportError:
    from mcp.server.mcpserver import MCPServer as FastMCP

from src.shared.contracts import (
    AssessRiskInput,
    AssessRiskOutput,
    ExplainScamInput,
    ExplainScamOutput,
    RiskLevel,
    RiskTier,
)
from src.shield_mcp.keywords import get_shield_keywords, get_whitelisted_billers

logger = logging.getLogger(__name__)

# Environment Configurations
AWS_REGION = os.getenv("AWS_DEFAULT_REGION", "ap-south-1")
BEDROCK_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "apac.amazon.nova-micro-v1:0")
DYNAMODB_TABLE = os.getenv("DYNAMODB_TABLE", "BETA_Household")
MOCK_DB_ENV = os.getenv("MOCK_DB", "true").lower() in ("true", "1", "yes")

# In-memory single-table store for MOCK_DB / testing
# Key: f"{PK}#{SK}" -> Item Dict
_MOCK_DYNAMO_STORE: Dict[str, Dict[str, Any]] = {}

mcp = FastMCP("beta-shield-server")

# Load keyword sets and whitelists
SCAM_KEYWORDS = get_shield_keywords()
WHITELISTED_BILLERS = get_whitelisted_billers()


# ============================================================================
# DynamoDB Single-Table Persistence Layer
# ============================================================================

def save_assessment_record(record: Dict[str, Any]) -> None:
    """Persists an assessment record to DynamoDB table BETA_Household.

    Schema:
      PK: HH#{household_id}
      SK: ASSESS#{assessment_id}
      Attributes: risk_level, tier, signals, used, created_at, expires_at, etc.
    """
    pk = record["PK"]
    sk = record["SK"]

    if MOCK_DB_ENV:
        _MOCK_DYNAMO_STORE[f"{pk}#{sk}"] = dict(record)
        return

    try:
        import boto3
        dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
        table = dynamodb.Table(DYNAMODB_TABLE)
        table.put_item(Item=record)
    except Exception as e:
        logger.warning("Failed to persist assessment to DynamoDB (%s); falling back to mock store: %s", DYNAMODB_TABLE, e)
        _MOCK_DYNAMO_STORE[f"{pk}#{sk}"] = dict(record)


def get_assessment_record(household_id: str, assessment_id: str) -> Optional[Dict[str, Any]]:
    """Retrieves an assessment record from DynamoDB or Mock store."""
    pk = f"HH#{household_id}"
    sk = f"ASSESS#{assessment_id}"

    if MOCK_DB_ENV or f"{pk}#{sk}" in _MOCK_DYNAMO_STORE:
        record = _MOCK_DYNAMO_STORE.get(f"{pk}#{sk}")
        return dict(record) if record else None

    try:
        import boto3
        dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
        table = dynamodb.Table(DYNAMODB_TABLE)
        response = table.get_item(Key={"PK": pk, "SK": sk})
        return response.get("Item")
    except Exception as e:
        logger.warning("Failed to retrieve assessment from DynamoDB (%s): %s", DYNAMODB_TABLE, e)
        return _MOCK_DYNAMO_STORE.get(f"{pk}#{sk}")


def mark_assessment_used(household_id: str, assessment_id: str) -> bool:
    """Marks an assessment token as used (single-use token verification for Request Interceptor)."""
    pk = f"HH#{household_id}"
    sk = f"ASSESS#{assessment_id}"
    key = f"{pk}#{sk}"

    if key in _MOCK_DYNAMO_STORE:
        if _MOCK_DYNAMO_STORE[key].get("used", False):
            return False  # Already used
        _MOCK_DYNAMO_STORE[key]["used"] = True
        return True

    try:
        import boto3
        dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
        table = dynamodb.Table(DYNAMODB_TABLE)
        response = table.update_item(
            Key={"PK": pk, "SK": sk},
            UpdateExpression="SET used = :u",
            ConditionExpression="used = :f",
            ExpressionAttributeValues={":u": True, ":f": False},
            ReturnValues="UPDATED_NEW",
        )
        return True
    except Exception as e:
        logger.warning("Failed to mark assessment used in DynamoDB: %s", e)
        return False


def clear_mock_db() -> None:
    """Clears in-memory mock store (used in tests)."""
    _MOCK_DYNAMO_STORE.clear()


# ============================================================================
# Step 1: Fast Deterministic Keyword / Heuristic Evaluator
# ============================================================================

def _evaluate_deterministic_rules(snippet: str, intent: str, amount: Optional[float]) -> Optional[Dict[str, Any]]:
    """Step 1: Rapid deterministic keyword matching.

    Bypasses LLM latency for blatant R4 scams or routine whitelisted actions.
    """
    text_lower = snippet.lower()
    intent_lower = intent.lower()

    # 1. Digital Arrest / Law Enforcement Extortion
    digital_arrest_triggers = [
        kw for kw in SCAM_KEYWORDS["digital_arrest"] if kw in text_lower
    ]
    if digital_arrest_triggers and ("arrest" in intent_lower or "digital" in text_lower or any(
        auth in text_lower for auth in ["cbi", "police", "dcp", "supreme court", "ed", "crime branch", "trai", "customs"]
    )):
        return {
            "tier": RiskTier.R4,
            "risk_level": RiskLevel.HIGH,
            "action": "BLOCK_AND_ALERT",
            "signals": ["law_enforcement_impersonation", "digital_arrest_coercion", f"triggers:{','.join(digital_arrest_triggers[:3])}"],
        }

    # 2. Customs / Narcotics Parcel Extortion
    customs_triggers = [kw for kw in SCAM_KEYWORDS["customs_narcotics"] if kw in text_lower]
    if customs_triggers and any(c in text_lower for c in ["customs", "fedex", "dhl", "speedpost", "courier", "airport", "port"]):
        return {
            "tier": RiskTier.R4,
            "risk_level": RiskLevel.HIGH,
            "action": "BLOCK_AND_ALERT",
            "signals": ["customs_contraband_fabrication", "drug_extortion_threat", f"triggers:{','.join(customs_triggers[:3])}"],
        }

    # 3. Electricity Disconnection Scam (Tonight 9:30 PM / APK / QuickSupport)
    electricity_triggers = [kw for kw in SCAM_KEYWORDS["electricity_scam"] if kw in text_lower]
    if electricity_triggers and any(w in text_lower for w in ["power", "bill", "meter", "bijli", "line", "transformer", "dhbvn", "msedcl", "tata power", "wbsedcl", "uppcl", "bescom"]):
        return {
            "tier": RiskTier.R4,
            "risk_level": RiskLevel.HIGH,
            "action": "BLOCK_AND_ALERT",
            "signals": ["electricity_disconnection_panic", "unofficial_contact_channel", f"triggers:{','.join(electricity_triggers[:3])}"],
        }

    # 4. Emergency Relative / Grandchild in Hospital or Lockup
    emergency_triggers = [kw for kw in SCAM_KEYWORDS["emergency_relative"] if kw in text_lower]
    if emergency_triggers and (
        any(r in text_lower for r in ["rohan", "pota", "beta", "beti", "neha", "amit", "bhanja", "rajesh", "damad", "rahul", "suresh", "vicky", "nephew", "friend"])
        or "emergency" in intent_lower
    ):
        return {
            "tier": RiskTier.R4,
            "risk_level": RiskLevel.HIGH,
            "action": "BLOCK_AND_ALERT",
            "signals": ["family_member_impersonation", "medical_legal_emergency_fabrication", f"triggers:{','.join(emergency_triggers[:3])}"],
        }

    # 5. KYC Expiry / APK Sideload / PIN Harvesting
    kyc_triggers = [kw for kw in SCAM_KEYWORDS["kyc_apk_fraud"] if kw in text_lower]
    if kyc_triggers:
        return {
            "tier": RiskTier.R4,
            "risk_level": RiskLevel.HIGH,
            "action": "BLOCK_AND_ALERT",
            "signals": ["malicious_apk_distribution", "screen_sharing_hijack", f"triggers:{','.join(kyc_triggers[:3])}"],
        }

    # 6. Fake Stock Trading / High Return Investment
    stock_triggers = [kw for kw in SCAM_KEYWORDS["fake_stock_investment"] if kw in text_lower]
    if stock_triggers:
        return {
            "tier": RiskTier.R4,
            "risk_level": RiskLevel.HIGH,
            "action": "BLOCK_AND_ALERT",
            "signals": ["unrealistic_guaranteed_returns", "unregistered_whatsapp_telegram_solicitation", f"triggers:{','.join(stock_triggers[:3])}"],
        }

    # If no blatant R4 scam triggers, check for whitelisted routine operations
    is_whitelisted = any(biller in text_lower for biller in WHITELISTED_BILLERS)
    is_routine_intent = any(kw in intent_lower for kw in ["utility", "medicine", "lpg", "wallet", "grocery"])

    if is_whitelisted or is_routine_intent:
        if amount is not None and amount > 2000.0:
            # Guardian Gate: payments > ₹2,000 require Guardian WhatsApp approval (R3)
            return {
                "tier": RiskTier.R3,
                "risk_level": RiskLevel.MEDIUM,
                "action": "REQUIRE_GUARDIAN_APPROVAL",
                "signals": ["whitelisted_payee", "amount_above_guardian_threshold_2000"],
            }
        elif amount is not None and amount > 0:
            # Routine verified payee <= ₹2,000 (R2)
            return {
                "tier": RiskTier.R2,
                "risk_level": RiskLevel.LOW,
                "action": "ALLOW",
                "signals": ["whitelisted_payee", "amount_within_limits"],
            }

    # Non-monetary routine errands (R0 / R1)
    if amount is None or amount == 0:
        if any(kw in intent_lower for kw in ["read", "check", "view", "status"]):
            return {
                "tier": RiskTier.R0,
                "risk_level": RiskLevel.LOW,
                "action": "ALLOW",
                "signals": ["read_only_inquiry"],
            }
        return {
            "tier": RiskTier.R1,
            "risk_level": RiskLevel.LOW,
            "action": "ALLOW",
            "signals": ["low_reversible_errand_or_calendar"],
        }

    return None  # Pass to Step 2 (Fuzzy / Bedrock)


# ============================================================================
# Step 2: Bedrock LLM Classifier (Fuzzy / Ambiguous Snippets)
# ============================================================================

def _evaluate_bedrock_llm(snippet: str, intent: str, amount: Optional[float]) -> Dict[str, Any]:
    """Step 2: Invokes Amazon Bedrock in ap-south-1 for fuzzy/ambiguous requests."""
    try:
        import boto3
        bedrock = boto3.client("bedrock-runtime", region_name=AWS_REGION)

        prompt = (
            "You are BETA Scam Shield classifier for Indian seniors. Analyze the text for fraud.\n"
            "Respond ONLY with a JSON object containing:\n"
            "- risk_level: 'LOW', 'MEDIUM', or 'HIGH'\n"
            "- tier: 'R0', 'R1', 'R2', 'R3', or 'R4'\n"
            "- signals: list of detected heuristic signals\n"
            "- recommended_action: 'ALLOW', 'CONFIRM_WITH_USER', 'REQUIRE_GUARDIAN_APPROVAL', or 'BLOCK_AND_ALERT'\n\n"
            f"Snippet: {snippet}\n"
            f"Intent: {intent}\n"
            f"Amount: {amount}\n"
        )

        response = bedrock.converse(
            modelId=BEDROCK_MODEL_ID,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"temperature": 0.0, "maxTokens": 300},
        )

        response_text = response["output"]["message"]["content"][0]["text"].strip()
        if "```" in response_text:
            response_text = response_text.split("```")[1]
            if response_text.startswith("json"):
                response_text = response_text[4:].strip()

        parsed = json.loads(response_text)
        return {
            "tier": RiskTier(parsed["tier"]),
            "risk_level": RiskLevel(parsed["risk_level"]),
            "action": parsed["recommended_action"],
            "signals": parsed.get("signals", ["bedrock_classified"]),
        }
    except Exception as e:
        logger.info("Bedrock LLM evaluation not available or offline (%s); using conservative fallback", e)
        # Conservative default for ambiguous payments
        if amount and amount > 2000.0:
            return {
                "tier": RiskTier.R3,
                "risk_level": RiskLevel.MEDIUM,
                "action": "REQUIRE_GUARDIAN_APPROVAL",
                "signals": ["unverified_payee_amount_above_2000"],
            }
        elif amount and amount > 0:
            return {
                "tier": RiskTier.R2,
                "risk_level": RiskLevel.LOW,
                "action": "ALLOW",
                "signals": ["unverified_payee_small_amount"],
            }
        return {
            "tier": RiskTier.R1,
            "risk_level": RiskLevel.LOW,
            "action": "ALLOW",
            "signals": ["routine_non_monetary_task"],
        }


# ============================================================================
# Main Tool Implementations
# ============================================================================

@mcp.tool()
def shield_assess_risk(
    household_id: str,
    snippet: str,
    intent: str,
    amount: Optional[float] = None,
) -> Dict[str, Any]:
    """Evaluates conversation snippet, intent, and amount against Scam Shield risk models.
    Persists token into DynamoDB table BETA_Household.
    """
    raw_input = AssessRiskInput(
        household_id=household_id,
        snippet=snippet,
        intent=intent,
        amount=amount,
    )

    # Step 1: Fast deterministic keyword check
    eval_result = _evaluate_deterministic_rules(raw_input.snippet, raw_input.intent, raw_input.amount)

    # Step 2: Bedrock LLM for fuzzy/ambiguous cases
    if eval_result is None:
        eval_result = _evaluate_bedrock_llm(raw_input.snippet, raw_input.intent, raw_input.amount)

    tier = eval_result["tier"]
    risk_level = eval_result["risk_level"]
    action = eval_result["action"]
    signals = eval_result["signals"]

    # Generate assessment token
    now_ts = int(time.time())
    expires_at = now_ts + 300  # 5 minutes TTL
    seed = f"{household_id}:{intent}:{amount}:{now_ts}:{uuid.uuid4().hex[:8]}"
    assessment_id = "eval_" + hashlib.sha256(seed.encode()).hexdigest()[:16]

    # DynamoDB Single-Table Schema: PK = HH#{household_id}, SK = ASSESS#{assessment_id}
    record = {
        "PK": f"HH#{household_id}",
        "SK": f"ASSESS#{assessment_id}",
        "assessment_id": assessment_id,
        "household_id": household_id,
        "risk_level": risk_level.value if hasattr(risk_level, "value") else str(risk_level),
        "tier": tier.value if hasattr(tier, "value") else str(tier),
        "signals": signals,
        "used": False,
        "created_at": now_ts,
        "expires_at": expires_at,
        "snippet": snippet,
        "intent": intent,
        "amount": amount,
        "recommended_action": action,
    }

    # Persist record in DynamoDB / Mock store
    save_assessment_record(record)

    output = AssessRiskOutput(
        assessment_id=assessment_id,
        risk_level=risk_level,
        tier=tier,
        signals=signals,
        recommended_action=action,
        expires_at=expires_at,
    )
    return output.model_dump()


@mcp.tool()
def shield_explain_scam(
    scam_type: str,
    language: str = "hi-en",
    household_id: str = "hh_default",
) -> Dict[str, Any]:
    """Generates soothing Hinglish de-escalation explanation and official Cyber Crime Helpline (1930) details."""
    raw_input = ExplainScamInput(scam_type=scam_type, language=language)

    templates = {
        "digital_arrest": {
            "script_spoken": "Uncleji, bilkul ghabrayiye mat. Police, CBI ya Supreme Court kabhi kisi ko phone ya video call pe digitally arrest nahi karti. Yeh 100% fraud call hai. Aap turant call disconnect kar dijiye, main hoon na.",
            "guardian_alert_text": "🚨 CRITICAL ALERT for Household {household_id}: Senior received a fake CBI / Police Digital Arrest call. BETA successfully blocked payment and de-escalated panic. Official Cyber Helpline is 1930.",
        },
        "electricity_disconnection": {
            "script_spoken": "Dadaji, bilkul chinta mat kijiye. Bijli vibhag raat ko 9:30 baje kisi personal number se call karke line nahi kaat-ta. Yeh scammer aapko dara kar paise lena chahta tha. Hum kal subah official portal se aapka bill check karenge.",
            "guardian_alert_text": "⚠️ ALERT for Household {household_id}: Fraud electricity disconnection call detected (threat of 9:30 PM cutoff). Transaction blocked by BETA.",
        },
        "customs_narcotics": {
            "script_spoken": "Auntyji, aapne koi aisa parcel nahi bheja hai. Customs ya FedEx ke naam pe fraudsters parcel mein drugs aur police ka dar dikha kar paise maangte hain. Yeh call bilkul nakli hai.",
            "guardian_alert_text": "🚨 CRITICAL ALERT for Household {household_id}: Senior targeted by Customs/Narcotics courier extortion scam. Transaction blocked.",
        },
        "emergency_relative": {
            "script_spoken": "Dadaji, pehle ek lambi saans lijiye. Fraudsters voice clone karke ya rishtedaar ban ke accident aur jail ka natak karte hain. Hum pehle seedhe bache ke mata-pita ko phone lagate hain aur confirm karte hain. Kisi anjaan account pe paise mat bhejiye.",
            "guardian_alert_text": "⚠️ ALERT for Household {household_id}: Emergency grandchild/relative accident scam attempt. BETA prevented unverified UPI transfer.",
        },
        "kyc_suspension": {
            "script_spoken": "Uncleji, bank ya EPFO kabhi phone pe koi application download karne ya ATM PIN / OTP maangne ko nahi bolta. Is link ko bilkul mat kholiye aur na hi koi app download kijiye.",
            "guardian_alert_text": "⚠️ ALERT for Household {household_id}: Phishing / Malicious APK install attempt for bank/pension KYC blocked.",
        },
        "fake_stock_investment": {
            "script_spoken": "Uncleji, stock market mein koi bhi guaranteed double ya triple return nahi deta. Yeh fake WhatsApp aur Telegram groups senior citizens ki pension churaane ke liye banaye jaate hain.",
            "guardian_alert_text": "⚠️ ALERT for Household {household_id}: High-risk unregulated stock trading / fake IPO solicitation blocked.",
        },
    }

    key = raw_input.scam_type.lower()
    matched_key = "digital_arrest"
    for candidate in templates:
        if candidate in key or key in candidate:
            matched_key = candidate
            break

    template = templates[matched_key]
    output = ExplainScamOutput(
        script_spoken=template["script_spoken"],
        helpline="1930",
        guardian_alert_text=template["guardian_alert_text"].format(household_id=household_id),
    )
    return output.model_dump()


# ============================================================================
# AWS Lambda Handler for AWS Bedrock AgentCore Gateway
# ============================================================================

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """AWS Lambda entrypoint for AWS Bedrock AgentCore Gateway (ap-south-1).

    Supports:
    1. AWS Bedrock Agent Action Group events
    2. API Gateway proxy events
    3. Direct JSON invocation events
    """
    logger.info("Received Lambda event: %s", json.dumps(event))

    # Case 1: Bedrock Agent Action Group Invocation
    if "actionGroup" in event or "apiPath" in event:
        action_group = event.get("actionGroup", "shield-mcp")
        api_path = event.get("apiPath", "/assess_risk")
        http_method = event.get("httpMethod", "POST")

        # Extract parameters from requestBody or parameters list
        params: Dict[str, Any] = {}
        if "requestBody" in event and "content" in event["requestBody"]:
            content = event["requestBody"]["content"].get("application/json", {})
            for prop in content.get("properties", []):
                params[prop["name"]] = prop["value"]
        elif "parameters" in event:
            for p in event["parameters"]:
                params[p["name"]] = p["value"]

        # Route tool
        if "explain_scam" in api_path:
            result = shield_explain_scam(
                scam_type=params.get("scam_type", "digital_arrest"),
                language=params.get("language", "hi-en"),
                household_id=params.get("household_id", "hh_default"),
            )
        else:
            amount_val = params.get("amount")
            if amount_val is not None:
                try:
                    amount_val = float(amount_val)
                except (ValueError, TypeError):
                    amount_val = None

            result = shield_assess_risk(
                household_id=params.get("household_id", "hh_default"),
                snippet=params.get("snippet", ""),
                intent=params.get("intent", "unknown"),
                amount=amount_val,
            )

        return {
            "messageVersion": "1.0",
            "response": {
                "actionGroup": action_group,
                "apiPath": api_path,
                "httpMethod": http_method,
                "httpStatusCode": 200,
                "responseBody": {
                    "application/json": {
                        "body": json.dumps(result)
                    }
                },
            },
        }

    # Case 2: API Gateway Proxy Event
    if "httpMethod" in event and "body" in event:
        body = json.loads(event["body"]) if isinstance(event["body"], str) else event.get("body", {})
        action = body.get("action", "assess_risk")

        if action == "explain_scam":
            res = shield_explain_scam(
                scam_type=body.get("scam_type", "digital_arrest"),
                language=body.get("language", "hi-en"),
                household_id=body.get("household_id", "hh_default"),
            )
        else:
            res = shield_assess_risk(
                household_id=body.get("household_id", "hh_default"),
                snippet=body.get("snippet", ""),
                intent=body.get("intent", "unknown"),
                amount=body.get("amount"),
            )

        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(res),
        }

    # Case 3: Direct JSON Invocation
    action = event.get("action", event.get("tool", "assess_risk"))
    if "explain_scam" in action:
        res = shield_explain_scam(
            scam_type=event.get("scam_type", "digital_arrest"),
            language=event.get("language", "hi-en"),
            household_id=event.get("household_id", "hh_default"),
        )
    else:
        res = shield_assess_risk(
            household_id=event.get("household_id", "hh_default"),
            snippet=event.get("snippet", ""),
            intent=event.get("intent", "unknown"),
            amount=event.get("amount"),
        )
    return res


if __name__ == "__main__":
    mcp.run()
