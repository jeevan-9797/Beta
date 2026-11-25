#!/usr/bin/env python3
"""Sync Gateway Tools for BETA Voice Agent.

Queries AWS Bedrock AgentCore Gateway via MCP (Streamable HTTP / SSE)
using protocol versions 2026-07-28 and 2025-11-25 with NONE auth.
Discovers live tool schemas (e.g. Errands___pay_bill, Shield___assess_risk)
and automatically generates:
1. agent_config/tool_descriptions_live.json
2. agent_config/system_prompt_live.md
"""

import argparse
import json
import logging
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("sync_gateway_tools")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BASE_PROMPT_PATH = PROJECT_ROOT / "agent_config" / "system_prompt.md"
BASE_TOOLS_PATH = PROJECT_ROOT / "agent_config" / "tool_descriptions.json"
LIVE_PROMPT_PATH = PROJECT_ROOT / "agent_config" / "system_prompt_live.md"
LIVE_TOOLS_PATH = PROJECT_ROOT / "agent_config" / "tool_descriptions_live.json"

MCP_SUPPORTED_VERSIONS = ["2026-07-28", "2025-11-25"]

# Canonical default tools exposed by AWS Bedrock AgentCore Gateway
DEFAULT_MOCK_GATEWAY_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "Shield___assess_risk",
        "description": "MANDATORY PRE-REQUISITE FOR ANY PAYMENT OR ACTION. Evaluates user intent, conversation snippet, and amount against Scam Shield fraud models and DynamoDB single-table schema. Returns an assessment_id, risk_level (LOW, MEDIUM, HIGH), and tier (R0, R1, R2, R3, R4). You MUST call this before Errands___pay_bill.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "household_id": {
                    "type": "string",
                    "description": "Unique identifier for the senior citizen's household (e.g. 'hh_delhi_9921', 'hh_blr_4012')."
                },
                "snippet": {
                    "type": "string",
                    "description": "The exact transcript of the senior's voice request or suspicious call dialogue."
                },
                "intent": {
                    "type": "string",
                    "description": "Identified conversation intent (e.g. 'pay_utility_bill', 'digital_arrest', 'electricity_disconnection', 'reorder_medicine')."
                },
                "amount": {
                    "type": "number",
                    "description": "The monetary amount in INR (₹) requested to be paid or transferred, or null if non-monetary."
                }
            },
            "required": ["household_id", "snippet", "intent"]
        }
    },
    {
        "name": "Shield___explain_scam",
        "description": "Generates a comforting, elder-friendly spoken Hinglish de-escalation explanation and official Cyber Crime Helpline (1930) details whenever a scam pattern is detected (Risk Tier R4). Never lecture the senior; calm and reassure them.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "scam_type": {
                    "type": "string",
                    "description": "Identified scam category: 'digital_arrest', 'electricity_disconnection', 'customs_narcotics', 'emergency_relative', 'kyc_suspension', 'fake_stock_investment'."
                },
                "language": {
                    "type": "string",
                    "description": "Target response language. Default is 'hi-en' (Hinglish).",
                    "default": "hi-en"
                },
                "household_id": {
                    "type": "string",
                    "description": "Household identifier."
                }
            },
            "required": ["scam_type"]
        }
    },
    {
        "name": "Errands___pay_bill",
        "description": "Executes a verified bill payment. STRICT REQUIREMENTS: 1. You CANNOT invoke this tool without passing a valid assessment_id from Shield___assess_risk. 2. You MUST read back payee and amount to elder first. 3. If tier is R3 (> ₹2,000) or R4 (Scam), payment is gated/rejected.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "assessment_id": {
                    "type": "string",
                    "description": "The security token returned by Shield___assess_risk confirming this transaction is cleared for execution."
                },
                "household_id": {
                    "type": "string",
                    "description": "Household identifier."
                },
                "biller_id": {
                    "type": "string",
                    "description": "Registered biller name or identifier (e.g. 'BESCOM', 'BSES_RAJDHANI', 'TATA_POWER', 'APOLLO_PHARMACY', 'BHARAT_GAS')."
                },
                "consumer_number": {
                    "type": "string",
                    "description": "Consumer account number, meter ID, or LPG consumer ID."
                },
                "amount": {
                    "type": "number",
                    "description": "Exact transaction amount in INR (₹)."
                }
            },
            "required": ["assessment_id", "household_id", "biller_id", "consumer_number", "amount"]
        }
    },
    {
        "name": "Errands___book_appointment",
        "description": "Books a medical or outpatient appointment with a verified hospital or doctor.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "household_id": {
                    "type": "string",
                    "description": "Household identifier."
                },
                "doctor_name": {
                    "type": "string",
                    "description": "Doctor's name."
                },
                "hospital_clinic": {
                    "type": "string",
                    "description": "Hospital or clinic name."
                },
                "date_time": {
                    "type": "string",
                    "description": "Preferred date and time."
                },
                "reason": {
                    "type": "string",
                    "description": "Reason for appointment."
                }
            },
            "required": ["household_id", "doctor_name"]
        }
    },
    {
        "name": "Errands___reorder_medicine",
        "description": "Reorders routine elder medications from Apollo Pharmacy, Tata 1mg, or Jan Aushadhi.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "household_id": {
                    "type": "string",
                    "description": "Household identifier."
                },
                "pharmacy": {
                    "type": "string",
                    "description": "Pharmacy name."
                },
                "medicine_names": {
                    "type": "string",
                    "description": "Comma-separated list of medications."
                },
                "quantity": {
                    "type": "string",
                    "description": "Quantity or refill duration."
                },
                "amount": {
                    "type": "number",
                    "description": "Expected price."
                }
            },
            "required": ["household_id", "medicine_names"]
        }
    },
    {
        "name": "Errands___set_reminder",
        "description": "Sets a daily walking, medication, or household staff reminder for the elder.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "household_id": {
                    "type": "string",
                    "description": "Household identifier."
                },
                "reminder_text": {
                    "type": "string",
                    "description": "Description of the reminder."
                },
                "time_str": {
                    "type": "string",
                    "description": "Scheduled time (e.g., '06:30 AM')."
                },
                "category": {
                    "type": "string",
                    "description": "Reminder category ('medication', 'walk', 'staff', 'temple')."
                }
            },
            "required": ["household_id", "reminder_text", "time_str"]
        }
    },
    {
        "name": "Guardian___request_approval",
        "description": "Dispatches an interactive WhatsApp approval request to the family guardian when a transaction is Tier R3 (> ₹2,000 or new unverified payee).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "household_id": {
                    "type": "string",
                    "description": "Household identifier."
                },
                "action_type": {
                    "type": "string",
                    "description": "Action requiring approval ('utility_payment_above_limit', 'new_payee_transfer')."
                },
                "payee": {
                    "type": "string",
                    "description": "Requested payee name."
                },
                "amount": {
                    "type": "number",
                    "description": "Requested amount in INR (₹)."
                },
                "tier": {
                    "type": "string",
                    "enum": ["R3"],
                    "description": "Risk Tier R3."
                },
                "reason": {
                    "type": "string",
                    "description": "Reason for guardian escalation."
                }
            },
            "required": ["household_id", "action_type", "payee", "amount", "tier"]
        }
    },
    {
        "name": "Guardian___notify_alert",
        "description": "Dispatches an urgent emergency alert to the family guardian's WhatsApp/SMS when an R4 scam is detected and blocked by BETA.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "household_id": {
                    "type": "string",
                    "description": "Household identifier."
                },
                "alert_type": {
                    "type": "string",
                    "description": "Alert category (e.g. 'SCAM_ATTEMPT_DETECTED', 'DIGITAL_ARREST_BLOCKED')."
                },
                "guardian_message": {
                    "type": "string",
                    "description": "Detailed alert message for the guardian."
                },
                "urgency_level": {
                    "type": "string",
                    "enum": ["HIGH", "CRITICAL"],
                    "description": "Priority of the alert."
                }
            },
            "required": ["household_id", "alert_type", "guardian_message", "urgency_level"]
        }
    },
    {
        "name": "Errands___generate_receipt",
        "description": "Calls AWS Bedrock Plain-Language Receipt Generator in ap-south-1 to produce a 1-sentence plain-language reason ('why BETA did this') for both spoken audio readout and the guardian WhatsApp summary.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "household_id": {
                    "type": "string",
                    "description": "Household identifier."
                },
                "action_type": {
                    "type": "string",
                    "description": "Action executed: 'utility_payment', 'medicine_reorder', 'appointment_booked', 'scam_blocked'."
                },
                "payee": {
                    "type": "string",
                    "description": "Recipient or merchant name (e.g. 'BESCOM', 'Apollo Pharmacy', 'Fraudulent Caller Blocked')."
                },
                "amount": {
                    "type": "number",
                    "description": "Amount in INR (₹), or null."
                },
                "tier": {
                    "type": "string",
                    "enum": ["R0", "R1", "R2", "R3", "R4"],
                    "description": "Risk tier assessed during transaction."
                },
                "decision": {
                    "type": "string",
                    "description": "Outcome status: 'EXECUTED', 'BLOCKED', 'SCHEDULED'."
                },
                "reason_facts": {
                    "type": "object",
                    "description": "Key metadata dictionary."
                }
            },
            "required": ["household_id", "action_type", "payee", "tier", "decision"]
        }
    }
]


def fetch_gateway_tools(
    gateway_url: str,
    protocol_version: str = "2026-07-28",
    timeout: float = 8.0,
) -> List[Dict[str, Any]]:
    """Sends JSON-RPC 2.0 tools/list request to AgentCore Gateway.

    Supports streamable HTTP and SSE responses with NONE auth.
    """
    req_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": {}
    }
    data = json.dumps(req_payload).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": protocol_version,
        "User-Agent": "BETA-ElevenLabs-Sync/1.0",
    }

    req = urllib.request.Request(gateway_url, data=data, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            content_type = response.headers.get("Content-Type", "")
            raw_body = response.read().decode("utf-8", errors="replace")

            # Handle SSE stream
            if "text/event-stream" in content_type:
                tools = []
                for line in raw_body.splitlines():
                    line = line.strip()
                    if line.startswith("data:"):
                        payload_str = line[5:].strip()
                        if payload_str:
                            try:
                                parsed = json.loads(payload_str)
                                if "result" in parsed and "tools" in parsed["result"]:
                                    return parsed["result"]["tools"]
                            except json.JSONDecodeError:
                                continue
                raise ValueError("No valid JSON-RPC tools response found in SSE stream")

            # Standard JSON-RPC response
            parsed = json.loads(raw_body)
            if "result" in parsed and "tools" in parsed["result"]:
                return parsed["result"]["tools"]
            elif "tools" in parsed:
                return parsed["tools"]
            else:
                raise ValueError(f"Unexpected JSON-RPC response format: {parsed}")

    except Exception as e:
        # Fallback to secondary MCP protocol version if first failed
        if protocol_version == MCP_SUPPORTED_VERSIONS[0] and len(MCP_SUPPORTED_VERSIONS) > 1:
            fallback_ver = MCP_SUPPORTED_VERSIONS[1]
            logger.warning(
                "Request failed with MCP-Protocol-Version %s (%s). Retrying with %s...",
                protocol_version, e, fallback_ver
            )
            return fetch_gateway_tools(gateway_url, protocol_version=fallback_ver, timeout=timeout)
        raise e


def build_tool_name_mappings(gateway_tools: List[Dict[str, Any]]) -> Dict[str, str]:
    """Builds a mapping from dot-notation tool names to live gateway names.

    e.g. 'shield.assess_risk' -> 'Shield___assess_risk'
         'errands.pay_bill' -> 'Errands___pay_bill'
         'guardian.request_approval' -> 'Guardian___request_approval'
         'generate_receipt' -> 'Errands___generate_receipt'
    """
    mappings: Dict[str, str] = {}

    for tool in gateway_tools:
        live_name = tool["name"]

        # Parse patterns like Namespace___tool_name or namespace_tool_name
        if "___" in live_name:
            ns, base = live_name.split("___", 1)
            ns_lower = ns.lower()
            mappings[f"{ns_lower}.{base}"] = live_name
            # Direct base mapping if unique
            if base not in mappings:
                mappings[base] = live_name
        elif "__" in live_name:
            ns, base = live_name.split("__", 1)
            mappings[f"{ns.lower()}.{base}"] = live_name
            if base not in mappings:
                mappings[base] = live_name
        elif "_" in live_name:
            parts = live_name.split("_", 1)
            if parts[0].lower() in ("shield", "errands", "guardian"):
                mappings[f"{parts[0].lower()}.{parts[1]}"] = live_name
            mappings[live_name] = live_name
        else:
            mappings[live_name] = live_name

    # Ensure canonical aliases are covered
    alias_lookups = [
        ("shield.assess_risk", ["Shield___assess_risk", "shield_assess_risk", "assess_risk"]),
        ("shield.explain_scam", ["Shield___explain_scam", "shield_explain_scam", "explain_scam"]),
        ("errands.pay_bill", ["Errands___pay_bill", "errands_pay_bill", "pay_bill"]),
        ("errands.book_appointment", ["Errands___book_appointment", "errands_book_appointment", "book_appointment"]),
        ("errands.reorder_medicine", ["Errands___reorder_medicine", "errands_reorder_medicine", "reorder_medicine"]),
        ("errands.set_reminder", ["Errands___set_reminder", "errands_set_reminder", "set_reminder"]),
        ("guardian.request_approval", ["Guardian___request_approval", "guardian_request_approval", "request_approval"]),
        ("guardian.notify_alert", ["Guardian___notify_alert", "guardian_notify_alert", "notify_alert"]),
        ("generate_receipt", ["Errands___generate_receipt", "generate_receipt", "errands_generate_receipt"]),
        ("errands.generate_receipt", ["Errands___generate_receipt", "generate_receipt", "errands_generate_receipt"]),
    ]

    gateway_tool_names = {t["name"] for t in gateway_tools}

    for canonical, candidates in alias_lookups:
        if canonical not in mappings:
            for cand in candidates:
                if cand in gateway_tool_names:
                    mappings[canonical] = cand
                    break

    return mappings


def generate_live_system_prompt(base_prompt_text: str, mappings: Dict[str, str]) -> str:
    """Substitutes dot-notation tool names with exact Gateway-prefixed tool names."""
    live_prompt = base_prompt_text

    # Sort keys by length descending to prevent partial prefix replacements
    sorted_mappings = sorted(mappings.items(), key=lambda kv: len(kv[0]), reverse=True)

    for dot_name, live_name in sorted_mappings:
        # Replace occurrences in backticks, code blocks, or function calls
        # e.g. `shield.assess_risk(...)` or `errands.pay_bill`
        live_prompt = re.sub(
            rf"(?<![a-zA-Z0-9_]){re.escape(dot_name)}(?![a-zA-Z0-9_])",
            live_name,
            live_prompt,
        )

    # Add a live synchronization header
    header = (
        "<!-- AUTO-GENERATED LIVE PROMPT VIA scripts/sync_gateway_tools.py -->\n"
        "<!-- Mapped to AWS Bedrock AgentCore Gateway MCP tool names. DO NOT EDIT DIRECTLY. -->\n\n"
    )
    return header + live_prompt


def generate_live_tool_descriptions(
    base_tools_config: Dict[str, Any],
    gateway_tools: List[Dict[str, Any]],
    mappings: Dict[str, str],
) -> Dict[str, Any]:
    """Generates ElevenLabs-ready live tool configurations."""
    live_tools_list = []

    # Map existing tools from base config to live names for descriptions/execution modes
    base_tools_by_dot = {t.get("name"): t for t in base_tools_config.get("tools", [])}

    for gw_tool in gateway_tools:
        live_name = gw_tool["name"]
        input_schema = gw_tool.get("inputSchema", gw_tool.get("parameters", {}))
        desc = gw_tool.get("description", "")

        # Find matching base tool
        base_match = None
        for dot_name, mapped in mappings.items():
            if mapped == live_name and dot_name in base_tools_by_dot:
                base_match = base_tools_by_dot[dot_name]
                break

        # Determine execution_mode (MANDATORY: post_tool_speech for pay_bill)
        if "pay_bill" in live_name.lower():
            execution_mode = "post_tool_speech"
        elif base_match and "execution_mode" in base_match:
            execution_mode = base_match["execution_mode"]
        else:
            execution_mode = "immediate"

        if not desc and base_match:
            desc = base_match.get("description", "")

        live_tools_list.append({
            "name": live_name,
            "description": desc,
            "execution_mode": execution_mode,
            "parameters": input_schema,
        })

    live_config = dict(base_tools_config)
    live_config["version"] = f"{base_tools_config.get('version', '1.0.0')}-live-synced"
    live_config["tools"] = live_tools_list

    return live_config


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sync ElevenLabs agent configs with AWS Bedrock AgentCore Gateway MCP tools."
    )
    parser.add_argument(
        "--gateway-url",
        default=os.getenv("AGENTCORE_GATEWAY_URL", ""),
        help="AWS Bedrock AgentCore Gateway endpoint URL (e.g. https://gateway.ap-south-1.amazonaws.com/mcp).",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use canonical AgentCore Gateway mock toolset (default if no gateway URL provided or unreachable).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print schemas and mappings without writing output files.",
    )
    parser.add_argument(
        "--protocol-version",
        choices=MCP_SUPPORTED_VERSIONS,
        default="2026-07-28",
        help="Preferred MCP protocol version (default: 2026-07-28; falls back to 2025-11-25).",
    )

    args = parser.parse_args()
    gateway_url = args.gateway_url.strip()

    gateway_tools: List[Dict[str, Any]] = []

    if gateway_url and not args.mock:
        logger.info("Connecting to AWS Bedrock AgentCore Gateway at: %s", gateway_url)
        logger.info("Using MCP protocol version: %s with NONE auth", args.protocol_version)
        try:
            gateway_tools = fetch_gateway_tools(gateway_url, protocol_version=args.protocol_version)
            logger.info("Successfully fetched %d live tools from Gateway.", len(gateway_tools))
        except Exception as e:
            logger.warning("Failed to connect to live Gateway (%s). Falling back to mock Gateway tools.", e)
            gateway_tools = DEFAULT_MOCK_GATEWAY_TOOLS
    else:
        logger.info("Using canonical AWS Bedrock AgentCore Gateway mock tools (NONE auth, protocol: %s).", args.protocol_version)
        gateway_tools = DEFAULT_MOCK_GATEWAY_TOOLS

    print("\n" + "=" * 70)
    print("      AWS BEDROCK AGENTCORE GATEWAY EXPOSED TOOLS (MCP tools/list)")
    print("=" * 70)
    for i, t in enumerate(gateway_tools, 1):
        name = t["name"]
        props = list(t.get("inputSchema", {}).get("properties", {}).keys())
        reqs = t.get("inputSchema", {}).get("required", [])
        print(f"\n{i}. Tool: {name}")
        print(f"   Description: {t.get('description', '').strip()[:85]}...")
        print(f"   Properties : {props}")
        print(f"   Required   : {reqs}")
    print("\n" + "=" * 70)

    # Build mappings
    mappings = build_tool_name_mappings(gateway_tools)
    print("\n[Tool Name Mappings: Dot-Notation -> Live Gateway Tools]")
    for dot_name, live_name in sorted(mappings.items()):
        print(f"  {dot_name:<28} -> {live_name}")
    print()

    # Read base prompt & tool descriptions
    if not BASE_PROMPT_PATH.exists():
        logger.error("Base prompt file not found at %s", BASE_PROMPT_PATH)
        return 1
    if not BASE_TOOLS_PATH.exists():
        logger.error("Base tools file not found at %s", BASE_TOOLS_PATH)
        return 1

    with open(BASE_PROMPT_PATH, "r", encoding="utf-8") as f:
        base_prompt_text = f.read()

    with open(BASE_TOOLS_PATH, "r", encoding="utf-8") as f:
        base_tools_config = json.load(f)

    # Generate live files
    live_prompt = generate_live_system_prompt(base_prompt_text, mappings)
    live_tools = generate_live_tool_descriptions(base_tools_config, gateway_tools, mappings)

    if args.dry_run:
        logger.info("Dry-run mode enabled: files will not be written.")
        print(f"\n[Preview: {LIVE_PROMPT_PATH.name} (first 25 lines)]")
        for line in live_prompt.splitlines()[:25]:
            print("  " + line)
        print(f"\n[Preview: {LIVE_TOOLS_PATH.name} Tools Array Count: {len(live_tools['tools'])}]")
    else:
        LIVE_PROMPT_PATH.parent.mkdir(parents=True, exist_ok=True)

        with open(LIVE_PROMPT_PATH, "w", encoding="utf-8") as f:
            f.write(live_prompt)
        logger.info("Successfully generated live system prompt at: %s", LIVE_PROMPT_PATH)

        with open(LIVE_TOOLS_PATH, "w", encoding="utf-8") as f:
            json.dump(live_tools, f, indent=2, ensure_ascii=False)
        logger.info("Successfully generated live tool descriptions at: %s", LIVE_TOOLS_PATH)

    print("\n✅ Gateway tool synchronization completed successfully!\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
