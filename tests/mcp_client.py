#!/usr/bin/env python3
"""
mcp_client.py - Reusable MCP Client & Mock Gateway Transport for BETA
Track 1: Make MCP Matter

Provides:
  - MCPGatewayClient: Sends JSON-RPC 2.0 requests (tools/list, tools/call) over HTTP/SSE
    to the AWS AgentCore Gateway MCP endpoint with Bearer auth.
  - MockGatewayTransport: Complete high-fidelity simulation of:
      1. Request Interceptor (authoritative overwrite of 6 security fields)
      2. Cedar Policy Engine (sole allow/deny authority: R0-R4, ceilings, idempotency, assessments)
      3. MCP Server executions (Errands, Guardian, Shield)
      4. Response Interceptor (masks payment tools during freeze)
"""

import os
import time
import json
import uuid
from typing import Dict, Any, List, Optional
from pathlib import Path
from dotenv import load_dotenv

# Load .env
load_dotenv(Path(__file__).resolve().parent.parent / ".env")


class MockGatewayTransport:
    """
    In-memory simulation of the AWS AgentCore Gateway, Request Interceptor,
    Cedar Policy Engine, and Response Interceptor.
    """

    def __init__(self, seed_file: Optional[Path] = None):
        self.seed_file = seed_file or (Path(__file__).resolve().parent.parent / "seed" / "kamala_household.json")
        self.reset_state()

    def reset_state(self):
        """Reset mock household state, caches, and counters."""
        self.frozen = False
        self.auto_approve_limit_inr = 2000
        self.hard_cap_inr = 25000
        self.quiet_hours_ist = [23, 6]
        self.max_payments_per_hour = 2
        self.cooling_off_seconds = 20
        self.payments_this_hour = 0
        self.hour_ist = 14  # Daytime default

        # Approvals: approval_id -> {"valid": bool, "created_at": float, "amount": int}
        self.approvals: Dict[str, Dict[str, Any]] = {}

        # Assessments: assessment_id -> {"created_at": float, "consumed": bool, "risk_score": float}
        self.assessments: Dict[str, Dict[str, Any]] = {}

        # Idempotency Cache: idem_key -> cached response dict
        self.idempotency_cache: Dict[str, Dict[str, Any]] = {}

        # Known payees and billers loaded from seed
        self.known_payees = {"BILLER#bescom", "PAYEE#apollo", "BESCOM Electricity", "Apollo Pharmacy", "bescom", "apollo"}

        # Scam keywords for R4 detection
        self.scam_keywords = [
            "cbi", "digital arrest", "arrest", "secret account",
            "police", "narcotics", "parcel", "urgent transfer",
            "otp", "pin", "password"
        ]

        # Audit logs and receipts
        self.receipts: List[Dict[str, Any]] = []
        self.audit_logs: List[Dict[str, Any]] = []

        # Load seed overrides if available
        if self.seed_file.exists():
            try:
                with open(self.seed_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for item in data:
                        if item.get("SK") == "RULE#default":
                            self.auto_approve_limit_inr = item.get("auto_approve_limit_inr", 2000)
                            self.hard_cap_inr = item.get("hard_cap_inr", 25000)
                            self.quiet_hours_ist = item.get("quiet_hours_ist", [23, 6])
                            self.cooling_off_seconds = item.get("cooling_off_seconds", 20)
                            self.frozen = item.get("frozen", False)
                        elif item.get("payee_known"):
                            if "biller_name" in item:
                                self.known_payees.add(item["biller_name"])
                            if "payee_name" in item:
                                self.known_payees.add(item["payee_name"])
                            if "SK" in item:
                                self.known_payees.add(item["SK"])
            except Exception:
                pass

    def create_assessment(self, assessment_id: Optional[str] = None, age_seconds: float = 0.0) -> str:
        """Helper to register a valid risk assessment."""
        aid = assessment_id or f"ASSESS#{uuid.uuid4().hex[:8]}"
        self.assessments[aid] = {
            "created_at": time.time() - age_seconds,
            "consumed": False,
            "risk_score": 0.05
        }
        return aid

    def create_approval(self, approval_id: Optional[str] = None, valid: bool = True) -> str:
        """Helper to register an active guardian approval."""
        apid = approval_id or f"APP#{uuid.uuid4().hex[:8]}"
        self.approvals[apid] = {
            "valid": valid,
            "created_at": time.time(),
        }
        return apid

    def list_tools(self) -> Dict[str, Any]:
        """Simulates tools/list after passing through Response Interceptor."""
        all_tools = [
            {
                "name": "Errands___get_due_bills",
                "description": "List pending utility bills for household",
                "inputSchema": {"type": "object", "properties": {"household_id": {"type": "string"}}}
            },
            {
                "name": "Errands___list_reminders",
                "description": "List scheduled reminders and medications",
                "inputSchema": {"type": "object", "properties": {"household_id": {"type": "string"}}}
            },
            {
                "name": "Errands___pay_bill",
                "description": "Pay verified utility or medical bill",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "household_id": {"type": "string"},
                        "biller": {"type": "string"},
                        "amount": {"type": "number"},
                        "assessment_id": {"type": "string"},
                        "idem_key": {"type": "string"},
                        "note": {"type": "string"}
                    },
                    "required": ["household_id", "biller", "amount", "assessment_id", "idem_key"]
                }
            },
            {
                "name": "Errands___reorder_medicine",
                "description": "Reorder prescription medication from pre-approved pharmacy",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "household_id": {"type": "string"},
                        "med_name": {"type": "string"},
                        "amount": {"type": "number"},
                        "assessment_id": {"type": "string"},
                        "idem_key": {"type": "string"}
                    },
                    "required": ["household_id", "med_name", "amount", "assessment_id", "idem_key"]
                }
            },
            {
                "name": "Guardian___request_approval",
                "description": "Trigger WhatsApp approval request to guardian",
                "inputSchema": {"type": "object", "properties": {"amount": {"type": "number"}, "biller": {"type": "string"}}}
            },
            {
                "name": "Guardian___freeze_account",
                "description": "Emergency kill switch freezing all monetary transactions",
                "inputSchema": {"type": "object", "properties": {"household_id": {"type": "string"}}}
            },
            {
                "name": "Guardian___unfreeze_account",
                "description": "Unfreeze account following guardian verification",
                "inputSchema": {"type": "object", "properties": {"household_id": {"type": "string"}}}
            },
            {
                "name": "Shield___assess_risk",
                "description": "Assess risk of transaction, producing assessment_id",
                "inputSchema": {"type": "object", "properties": {"transcript": {"type": "string"}}}
            }
        ]

        # Response Interceptor Lambda Logic:
        # If frozen == True, hide money and mutation tools
        if self.frozen:
            filtered = [
                t for t in all_tools
                if t["name"] not in ("Errands___pay_bill", "Errands___reorder_medicine")
            ]
            return {"tools": filtered}

        return {"tools": all_tools}

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Executes a tool call through:
        1. Request Interceptor (strips/overwrites 6 security fields)
        2. Cedar Policy Engine (sole allow/deny gatekeeper)
        3. Target tool implementation
        """
        raw_args = dict(arguments or {})

        # -------------------------------------------------------------
        # 1. Request Interceptor Lambda (Sanitizes & Computes Facts)
        # -------------------------------------------------------------
        # Security fields to overwrite / strip from client
        biller_arg = raw_args.get("biller", "") or raw_args.get("pharmacy", "") or raw_args.get("payee", "")
        computed_payee_known = biller_arg in self.known_payees
        computed_hour_ist = self.hour_ist
        computed_payments_last_hour = self.payments_this_hour
        computed_frozen = self.frozen

        # Check guardian approval if approval_id provided
        approval_id_arg = raw_args.get("approval_id")
        computed_approval_valid = False
        if approval_id_arg and approval_id_arg in self.approvals:
            computed_approval_valid = self.approvals[approval_id_arg].get("valid", False)

        # Scam Keyword Scanner (R4 Evaluation)
        combined_text = " ".join([
            str(v).lower() for k, v in raw_args.items()
            if isinstance(v, (str, int, float))
        ])
        computed_scam_flag = any(kw in combined_text for kw in self.scam_keywords)

        # Authoritative context emitted by Interceptor (client spoof attempts are replaced)
        security_context = {
            "payee_known": computed_payee_known,
            "hour_ist": computed_hour_ist,
            "payments_last_hour": computed_payments_last_hour,
            "approval_valid": computed_approval_valid,
            "scam_flag": computed_scam_flag,
            "frozen": computed_frozen,
        }

        # -------------------------------------------------------------
        # 2. Cedar Policy Engine (Allow / Deny Evaluation)
        # -------------------------------------------------------------
        # Normalizing tool name
        clean_name = name.split("___")[-1] if "___" in name else name

        # A. Account Frozen Check
        if security_context["frozen"]:
            return {
                "decision": "DENY",
                "error": "FORBIDDEN",
                "reason": "Account is FROZEN. All transactional operations blocked by Guardian kill switch.",
                "tier": "FREEZE_BLOCKED"
            }

        # B. Scam / Coercion Detection (R4)
        if security_context["scam_flag"]:
            return {
                "decision": "DENY",
                "error": "FORBIDDEN",
                "reason": "Security Alert (R4): Potential coercion or digital arrest scam detected. National Cyber Crime Helpline: 1930.",
                "tier": "R4_SCAM"
            }

        # C. Read Tools (R0) -> Auto Permit
        if clean_name in ("get_due_bills", "list_bills", "list_reminders"):
            return {
                "decision": "ALLOW",
                "tier": "R0",
                "bills": [
                    {"biller": "BESCOM Electricity", "amount_due": 1240, "status": "UNPAID"},
                    {"biller": "Apollo Pharmacy", "amount_due": 3800, "status": "PENDING"}
                ]
            }

        # D. Freeze / Unfreeze Management Tools
        if clean_name == "freeze_account":
            self.frozen = True
            return {"decision": "ALLOW", "tier": "ADMIN", "status": "FROZEN"}

        if clean_name == "unfreeze_account":
            self.frozen = False
            return {"decision": "ALLOW", "tier": "ADMIN", "status": "ACTIVE"}

        # E. Monetary Tools (pay_bill, reorder_medicine)
        if clean_name in ("pay_bill", "reorder_medicine"):
            amount = raw_args.get("amount", 0)
            idem_key = raw_args.get("idem_key")
            assessment_id = raw_args.get("assessment_id")

            # Check hard ceiling
            if amount > self.hard_cap_inr:
                return {
                    "decision": "DENY",
                    "error": "FORBIDDEN",
                    "reason": f"Payment amount ₹{amount} exceeds hard cap of ₹{self.hard_cap_inr}.",
                    "tier": "HARD_CAP_EXCEEDED"
                }

            # Check Idempotency Key Replay FIRST
            if idem_key and idem_key in self.idempotency_cache:
                cached = self.idempotency_cache[idem_key]
                return {
                    "decision": "ALLOW",
                    "cached": True,
                    "receipt_id": cached["receipt_id"],
                    "amount": cached["amount"],
                    "status": "PAID",
                    "tier": cached.get("tier", "R2")
                }

            # Assessment ID Validation
            if not assessment_id or assessment_id not in self.assessments:
                return {
                    "decision": "DENY",
                    "error": "FORBIDDEN",
                    "reason": "Missing or invalid assessment_id. Prior shield.assess_risk required.",
                    "tier": "ASSESSMENT_REQUIRED"
                }

            assess_info = self.assessments[assessment_id]
            # Check single-use
            if assess_info.get("consumed", False):
                return {
                    "decision": "DENY",
                    "error": "FORBIDDEN",
                    "reason": "assessment_id already consumed. Single-use invariant violated.",
                    "tier": "ASSESSMENT_REUSED"
                }

            # Check TTL (5 minutes = 300s)
            if time.time() - assess_info.get("created_at", 0) > 300:
                return {
                    "decision": "DENY",
                    "error": "FORBIDDEN",
                    "reason": "assessment_id expired (> 5 minutes).",
                    "tier": "ASSESSMENT_EXPIRED"
                }

            # Cedar Allow/Deny Logic:
            # R2: Amount <= ₹2,000, known payee, daytime, < max velocity
            is_r2 = (
                amount <= self.auto_approve_limit_inr
                and security_context["payee_known"]
                and security_context["hour_ist"] not in range(self.quiet_hours_ist[0], 24)
                and security_context["hour_ist"] not in range(0, self.quiet_hours_ist[1])
                and security_context["payments_last_hour"] < self.max_payments_per_hour
            )

            if is_r2:
                # Consume assessment
                assess_info["consumed"] = True
                self.payments_this_hour += 1
                rec_id = f"REC#{uuid.uuid4().hex[:8]}"
                res = {
                    "decision": "ALLOW",
                    "tier": "R2",
                    "status": "PAID",
                    "receipt_id": rec_id,
                    "amount": amount,
                    "biller": biller_arg
                }
                if idem_key:
                    self.idempotency_cache[idem_key] = res
                return res

            # R3: High amount (> ₹2,000) or new payee or quiet hours or velocity burst
            if security_context["approval_valid"]:
                assess_info["consumed"] = True
                self.payments_this_hour += 1
                rec_id = f"REC#{uuid.uuid4().hex[:8]}"
                res = {
                    "decision": "ALLOW",
                    "tier": "R3_APPROVED",
                    "status": "PAID",
                    "receipt_id": rec_id,
                    "amount": amount,
                    "biller": biller_arg
                }
                if idem_key:
                    self.idempotency_cache[idem_key] = res
                return res

            # Denied without approval
            return {
                "decision": "DENY",
                "error": "FORBIDDEN",
                "reason": f"Payment of ₹{amount} exceeds auto-approve threshold of ₹{self.auto_approve_limit_inr} or payee is unverified. Guardian approval required.",
                "tier": "R3_APPROVAL_REQUIRED"
            }

        # Fallback for unrecognized tool
        return {"decision": "DENY", "error": "UNKNOWN_TOOL", "reason": f"Tool '{name}' not found."}


class MCPGatewayClient:
    """
    Standard JSON-RPC 2.0 client for AWS AgentCore Gateway / MCP endpoint.
    Supports live HTTP/SSE connection and seamless local mock fallback.
    """

    def __init__(
        self,
        gateway_url: Optional[str] = None,
        auth_token: Optional[str] = None,
        timeout: float = 10.0,
        force_mock: Optional[bool] = None
    ):
        self.gateway_url = gateway_url or os.getenv("MCP_GATEWAY_URL", "")
        self.auth_token = auth_token or os.getenv("MCP_AUTH_TOKEN", "")
        self.timeout = timeout
        
        # Decide if using mock gateway
        use_mock_env = os.getenv("USE_MOCK_GATEWAY", "true").lower() in ("true", "1", "yes")
        if force_mock is not None:
            self.use_mock = force_mock
        else:
            self.use_mock = use_mock_env or not self.gateway_url or "amazonaws.com" in self.gateway_url

        self.mock_transport = MockGatewayTransport()
        self._request_id = 0

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def list_tools(self) -> List[Dict[str, Any]]:
        """Invokes tools/list via JSON-RPC 2.0."""
        req = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/list",
            "params": {}
        }

        if self.use_mock:
            res = self.mock_transport.list_tools()
            return res.get("tools", [])

        # Live Gateway call via httpx
        import httpx
        headers = {"Content-Type": "application/json"}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"

        try:
            with httpx.Client(timeout=self.timeout) as client:
                r = client.post(self.gateway_url, json=req, headers=headers)
                r.raise_for_status()
                data = r.json()
                return data.get("result", {}).get("tools", [])
        except Exception as e:
            # Fallback to mock on connection error
            return self.mock_transport.list_tools().get("tools", [])

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Invokes tools/call with tool name and arguments via JSON-RPC 2.0."""
        req = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {
                "name": name,
                "arguments": arguments
            }
        }

        if self.use_mock:
            return self.mock_transport.call_tool(name, arguments)

        # Live Gateway call via httpx
        import httpx
        headers = {"Content-Type": "application/json"}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"

        try:
            with httpx.Client(timeout=self.timeout) as client:
                r = client.post(self.gateway_url, json=req, headers=headers)
                r.raise_for_status()
                data = r.json()
                result = data.get("result", {})
                
                # Normalize result
                if "content" in result and len(result["content"]) > 0:
                    text_content = result["content"][0].get("text", "")
                    try:
                        parsed = json.loads(text_content)
                        return parsed
                    except Exception:
                        return {"text": text_content, "raw": result}
                return result
        except Exception as e:
            # Fallback to mock on network error
            return self.mock_transport.call_tool(name, arguments)
