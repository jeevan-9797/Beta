"""Offline Guardian Console and Mumbai MCP integration matrix."""

import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from threading import Barrier
from unittest.mock import Mock

import pytest

from backend.common.config import settings
from backend.common.db import get_item, put_item, query_by_prefix, reset_mock_db, update_item
from backend.common.receipts import deliver_receipt_notification, generate_receipt_summary, queue_receipt_notification
from backend.guardian_api.handler import lambda_handler as console
from backend.guardian_api.whatsapp_webhook import lambda_handler as webhook
from backend.interceptors import request_interceptor, response_interceptor
from backend.mcp_errands.handler import handle_tool_call
from backend.mcp_guardian.tools import request_approval
from backend.policies.evaluator import evaluate_policy
from backend.scripts.seed_data import get_seed_payload


HH = "HH#kamala"
ASSESSMENT = "ASM_KAMALA_INIT"
PAY = "Errands___pay_bill"
REORDER = "Errands___reorder_medicine"


@pytest.fixture(autouse=True)
def seeded(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "LOCAL_MOCK_DB", True)
    monkeypatch.setattr(settings, "MOCK_DB_FILE", str(tmp_path / "household.json"))
    monkeypatch.setattr(request_interceptor, "get_hour_ist", lambda _: 12)
    reset_mock_db()
    for item in get_seed_payload():
        put_item(item)
    yield
    reset_mock_db()


def api(method, path, payload=None, *, v1=False):
    path = "/api/household/HH%23kamala" + path
    event = {"httpMethod": method, "path": path} if v1 else {
        "version": "2.0", "rawPath": path, "requestContext": {"http": {"method": method}},
    }
    if payload is not None:
        event["body"] = json.dumps(payload)
        if v1:
            event["isBase64Encoded"] = True
            event["body"] = base64.b64encode(event["body"].encode()).decode()
    result = console(event)
    assert result["headers"]["Access-Control-Allow-Origin"] == "*"
    return result["statusCode"], json.loads(result["body"]) if result["body"] else None


def arguments(**overrides):
    return {"household_id": HH, "bill_id": "BILL_BESCOM_NOV", "payee_id": "BESCOM",
            "amount": 1240, "assessment_id": ASSESSMENT, "idem_key": "matrix-bill", **overrides}


def gate(tool, args):
    req = {"mcp": {"gatewayRequest": {"body": {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args},
    }}}}
    response = request_interceptor.lambda_handler(req)
    result = response["mcp"]["transformedGatewayRequest"]["body"]["params"]["arguments"]
    return evaluate_policy(result, tool_name=tool, household_id=HH)[1], result


def make_approval():
    return request_approval(HH, "MEDICINE_REORDER", "Apollo Pharmacy", 3800)["approval_id"]


def change_approval(approval_id, action):
    return api("POST", f"/approvals/{approval_id}/action", {"action": action})


def medicine_args(approval_id):
    return {"household_id": HH, "med_ids": ["METFORMIN_500MG", "AMLODIPINE_5MG"],
            "pharmacy_name": "Apollo Pharmacy", "amount": 3800, "assessment_id": ASSESSMENT,
            "approval_id": approval_id, "idem_key": "matrix-medicine"}


@pytest.mark.parametrize("scenario", [1, 5, 13])
def test_payment_idempotency(scenario):
    decision, args = gate(PAY, arguments())
    assert decision == "ALLOW"
    first = handle_tool_call(PAY, args)
    assert first["status"] == "SUCCESS"
    if scenario in {5, 13}:
        decision, retry = gate(PAY, arguments())
        assert decision == "ALLOW" and retry["idempotent_replay"] is True and retry["assessment_valid"] is False
        replay = handle_tool_call(PAY, retry)
        assert replay["idempotent_replay"] and replay["receipt"] == first["receipt"]
        assert gate(PAY, arguments(amount=1000, idempotent_replay=True))[0] == "DENY"
    assert len(query_by_prefix(HH, "PAYMENT#")) == 1
    assert len(query_by_prefix(HH, "RECEIPT#")) == 1
    assert len(query_by_prefix(HH, "RECEIPT_NOTICE#")) == 1


def test_concurrent_duplicate_returns_one_payment_and_receipt(monkeypatch):
    from backend.mcp_errands import tools

    ready = Barrier(2)
    summarize = tools.generate_receipt_summary

    def simultaneous_summaries(*args):
        ready.wait(timeout=5)
        return summarize(*args)

    monkeypatch.setattr(tools, "generate_receipt_summary", simultaneous_summaries)
    decision, data = gate(PAY, arguments())
    assert decision == "ALLOW"
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: handle_tool_call(PAY, data), range(2)))
    assert results[0]["receipt"] == results[1]["receipt"]
    assert sorted(result["idempotent_replay"] for result in results) == [False, True]
    assert len(query_by_prefix(HH, "PAYMENT#")) == 1
    assert len(query_by_prefix(HH, "RECEIPT_NOTICE#")) == 1


def test_scenario_2_approval_cools_then_allows_reorder():
    approval = make_approval()
    code, state = api("GET", "/state", v1=True)
    assert code == 200 and state["household_id"] == HH
    assert state["profile"]["parent"]["name"] == "Kamala"
    assert state["profile"]["guardian"]["name"] == "Arjun"
    assert state["rules"] == {"frozen": False, "auto_limit": 2000, "max_with_approval": 25000, "cooling_off_seconds": 20}
    assert state["approvals"][0]["status"] == "PENDING"
    code, response = change_approval(approval, "APPROVE")
    approved = response["approval"]
    assert code == 200 and approved["status"] == "APPROVED" and approved["seconds_remaining"] == 20
    assert approved["executable_at"] - approved["approved_at"] == 20
    assert gate(REORDER, medicine_args(approval))[0] == "DENY"
    assert change_approval(approval, "APPROVE")[1]["approval"]["executable_at"] == approved["executable_at"]
    update_item(HH, f"APPROVAL#{approval}", {"executable_at": approved["approved_at"] - 1})
    ready = api("GET", "/state")[1]["approvals"][0]
    assert ready["status"] == "READY" and ready["seconds_remaining"] == 0
    decision, args = gate(REORDER, medicine_args(approval))
    assert decision == "ALLOW"
    receipt = handle_tool_call(REORDER, args)["receipt"]
    assert receipt["status"] == "CONFIRMED" and "Apollo Pharmacy" in receipt["summary"]["en"]


@pytest.mark.parametrize("scenario,action,expected", [(3, "DECLINE", "DECLINED"), (16, "CANCEL", "CANCELLED")])
def test_approval_denials(scenario, action, expected):
    approval = make_approval()
    if scenario == 16:
        assert change_approval(approval, "APPROVE")[0] == 200
    code, response = change_approval(approval, action)
    assert code == 200 and response["approval"]["status"] == expected
    assert api("GET", "/state")[1]["approvals"] == []
    assert gate(REORDER, medicine_args(approval))[0] == "DENY"
    feed = api("GET", "/feed")[1]["feed"]
    assert any(event["action"] == f"REST_APPROVAL_{expected}" for event in feed)
    assert change_approval(approval, "APPROVE")[0] == 409


def test_scenario_4_expired_approval():
    approval = make_approval()
    update_item(HH, f"APPROVAL#{approval}", {"ttl": int(datetime.now(timezone.utc).timestamp()) - 1})
    assert change_approval(approval, "APPROVE") == (409, {"error": "Approval is EXPIRED"})
    assert api("GET", "/state")[1]["approvals"] == []
    assert gate(REORDER, medicine_args(approval))[0] == "DENY"


def test_scenario_6_freeze_changes_gate_immediately():
    assert gate(PAY, arguments())[0] == "ALLOW"
    code, change = api("POST", "/freeze", {"frozen": True}, v1=True)
    assert code == 200 and change["frozen"] and change["updated_at"]
    assert get_item(HH, "RULE#CONFIG")["frozen"]
    assert gate(PAY, arguments())[0] == "DENY"
    assert api("GET", "/state")[1]["rules"]["frozen"]
    assert api("POST", "/freeze", {"frozen": False})[1]["frozen"] is False
    assert gate(PAY, arguments())[0] == "ALLOW"
    actions = [event["action"] for event in api("GET", "/feed")[1]["feed"]]
    assert actions.index("EMERGENCY_FREEZE_LIFTED") < actions.index("EMERGENCY_FREEZE_ACTIVATED")


@pytest.mark.parametrize("scenario,overrides,fact,expected", [
    (12, {"amount": 50000}, "within_auto_limit", False),
    (14, {"payee_id": "UNKNOWN", "bill_id": "UNKNOWN", "amount": 500}, "payee_known", False),
    (15, {"amount": 15000, "approval_valid": True, "within_auto_limit": True}, "approval_valid", False),
    (19, {"note": "CBI parcel"}, "scam_flag", True),
])
def test_policy_denials(scenario, overrides, fact, expected):
    decision, args = gate(PAY, arguments(**overrides))
    assert decision == "DENY" and args[fact] is expected
    audit = api("GET", "/feed")[1]["feed"][0]
    assert audit["tool_name"] == PAY and audit["computed_facts"][fact] is expected
    assert query_by_prefix(HH, "PAYMENT#") == []


@pytest.mark.parametrize("kind", ["missing", "stale", "reused"])
def test_scenario_17_assessment_is_required_fresh_and_single_use(kind):
    args = arguments()
    if kind == "missing":
        args.pop("assessment_id")
    elif kind == "stale":
        update_item(HH, f"ASSESS#{ASSESSMENT}", {"expires_at": "2020-01-01T00:00:00+00:00"})
    else:
        decision, transformed = gate(PAY, args)
        assert decision == "ALLOW"
        handle_tool_call(PAY, transformed)
        args["idem_key"] = "new-attempt"
    decision, result = gate(PAY, args)
    assert decision == "DENY" and result["assessment_valid"] is False


@pytest.mark.parametrize("encoded", [False, True])
def test_scenario_18_response_tools_list_follows_freeze(encoded):
    names = ["pay_bill", PAY, REORDER, "Errands___get_profile", "Guardian___get_guard_status", "Shield___assess_risk"]
    body = {"jsonrpc": "2.0", "id": 9, "result": {"tools": [{"name": name} for name in names], "nextCursor": "next"}}
    reply = {"headers": {"x-test": "pass"}, "statusCode": 200, "body": json.dumps(body) if encoded else body}
    event = {"mcp": {"gatewayResponse": reply}}

    def tools_seen():
        result = response_interceptor.lambda_handler(event)["mcp"]["transformedGatewayResponse"]
        assert result["headers"] == reply["headers"] and result["statusCode"] == 200
        transformed = json.loads(result["body"]) if encoded else result["body"]
        assert transformed["id"] == 9 and transformed["result"]["nextCursor"] == "next"
        return [tool["name"] for tool in transformed["result"]["tools"]]

    assert tools_seen() == names
    api("POST", "/freeze", {"frozen": True})
    assert tools_seen() == names[3:]
    api("POST", "/freeze", {"frozen": False})
    assert tools_seen() == names


def test_rules_patch_cors_and_validation():
    assert api("OPTIONS", "/rules", v1=True) == (204, None)
    for payload in ({"auto_limit": -1}, {"auto_limit": True}, {"cooling_off_seconds": 4}, {"auto_limit": float("inf")}):
        assert api("PATCH", "/rules", payload)[0] == 400
    assert api("POST", "/freeze", {"frozen": "true"})[0] == 400
    assert change_approval("unknown", "APPROVE")[0] == 404
    assert api("PATCH", "/rules", {"auto_limit": 1000, "cooling_off_seconds": 10})[0] == 200
    assert gate(PAY, arguments())[0] == "DENY"
    assert gate(PAY, arguments(amount=1000))[0] == "ALLOW"
    approval = change_approval(make_approval(), "APPROVE")[1]["approval"]
    assert approval["executable_at"] - approval["approved_at"] == 10


def test_feed_and_receipts_are_sorted_and_bounded():
    now = datetime.now(timezone.utc) + timedelta(days=1)
    for index in reversed(range(55)):
        timestamp = (now + timedelta(seconds=index)).isoformat()
        put_item({"PK": HH, "SK": f"AUDIT#{timestamp}", "timestamp": timestamp, "action": "TEST",
                  "tool_name": PAY, "computed_facts": {"frozen": False}, "cedar_decision": "ALLOW", "status": "SUCCESS"})
        put_item({"PK": HH, "SK": f"RECEIPT#{100 - index}", "receipt_id": str(index), "timestamp": timestamp})
    feed = api("GET", "/feed")[1]["feed"]
    assert len(feed) == 50 and feed[0]["timestamp"] > feed[-1]["timestamp"]
    assert feed[0]["cedar_decision"] == "ALLOW" and feed[0]["computed_facts"] == {"frozen": False}
    receipts = api("GET", "/receipts")[1]["receipts"]
    assert [row["receipt_id"] for row in receipts] == [str(i) for i in range(54, 34, -1)]


@pytest.mark.parametrize("body,expected", [
    ({"error": {"code": -32002, "message": "Tool Execution Denied: denied by default"}}, "DENY"),
    ({"result": {"isError": True, "content": [{"text": "AuthorizeActionException - Tool Execution Denied: blocked"}]}}, "DENY"),
    ({"result": {"isError": False, "content": [{"text": "success"}]}}, "ALLOW"),
    ({"result": {"isError": True, "content": [{"text": "Lambda failed"}]}}, None),
])
def test_response_records_observable_cedar_decisions_in_feed(body, expected):
    response_interceptor.lambda_handler({"mcp": {
        "gatewayRequest": {"body": {"id": 55, "method": "tools/call", "params": {"name": PAY}}},
        "gatewayResponse": {"body": body},
    }})
    audit = api("GET", "/feed")[1]["feed"][0]
    assert audit["tool_name"] == PAY and audit["mcp_message_id"] == 55
    assert audit["cedar_decision"] == expected


@pytest.mark.parametrize("failure", ["credentials", "timeout", "throttled"])
def test_bedrock_failure_yields_bilingual_receipt_and_saves_it(monkeypatch, failure):
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError, ReadTimeoutError

    failures = {"credentials": NoCredentialsError(), "timeout": ReadTimeoutError(endpoint_url="https://example.test"),
                "throttled": ClientError({"Error": {"Code": "ThrottlingException"}}, "Converse")}
    aws = Mock()
    aws.converse.side_effect = failures[failure]
    client = Mock(return_value=aws)
    monkeypatch.setattr(boto3, "client", client)
    monkeypatch.setattr(settings, "LOCAL_MOCK_DB", False)
    summary = generate_receipt_summary("PAY_BILL", 1240, "BESCOM", "PAID", "utility bill", "MOCK_BBPS")
    assert "BESCOM" in summary["en"] and "भुगतान" in summary["hi"]
    cfg = client.call_args.kwargs["config"]
    assert client.call_args.args == ("bedrock-runtime",)
    assert client.call_args.kwargs["region_name"] == "ap-south-1" and (cfg.connect_timeout, cfg.read_timeout) == (2, 3)
    assert aws.converse.call_args.kwargs["modelId"] == settings.BEDROCK_MODEL_ID
    result = handle_tool_call(PAY, arguments())
    assert get_item(HH, f"RECEIPT#{result['receipt_id']}")["summary"] == summary


def test_appointment_receipt_and_best_effort_whatsapp(monkeypatch):
    from backend.mcp_guardian import tools
    sender = Mock()
    monkeypatch.setattr(tools, "notify_guardian", sender)
    result = handle_tool_call("book_appointment", {"household_id": HH, "doctor_name": "Dr. Rao",
                                                  "slot_iso": "2026-12-20T09:00:00+05:30"})
    receipt_id = result["appointment_id"]
    receipt = api("GET", "/receipts")[1]["receipts"][0]
    assert receipt["receipt_id"] == receipt_id and "Dr. Rao" in receipt["summary"]["en"]
    assert "अपॉइंटमेंट" in receipt["summary"]["hi"]
    deliver_receipt_notification(HH, receipt_id)
    sender.assert_called_once()


def test_aws_receipt_notification_uses_async_invocation(monkeypatch):
    import boto3
    client = Mock()
    monkeypatch.setattr(settings, "LOCAL_MOCK_DB", False)
    monkeypatch.setenv("RECEIPT_NOTIFICATION_FUNCTION", "beta-guardian-api")
    monkeypatch.setattr(boto3, "client", Mock(return_value=client))
    queue_receipt_notification(HH, "TX_MATRIX")
    call = client.invoke.call_args.kwargs
    assert call["FunctionName"] == "beta-guardian-api" and call["InvocationType"] == "Event"
    assert json.loads(call["Payload"])["beta_receipt_notification"]["receipt_id"] == "TX_MATRIX"


def test_http_v2_webhook_rejects_unsigned_requests(monkeypatch):
    monkeypatch.setattr(settings, "TWILIO_VALIDATE_SIGNATURE", True)
    result = webhook({"version": "2.0", "rawPath": "/webhook/whatsapp",
                      "body": "From=whatsapp%3A%2B919876543210&Body=FREEZE"})
    assert result["statusCode"] == 403 and get_item(HH, "RULE#CONFIG")["frozen"] is False
