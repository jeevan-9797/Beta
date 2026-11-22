"""Tests for Bedrock AgentCore Request and Response interceptors."""

import unittest
from backend.common.db import reset_mock_db, put_item
from backend.common.models import SKPrefix
from backend.interceptors.request_interceptor import (
    RequestInterceptor,
    detect_scam_keywords,
    get_current_hour_ist,
)
from backend.interceptors.response_interceptor import ResponseInterceptor


class TestInterceptors(unittest.TestCase):
    def setUp(self):
        self.mock_db = reset_mock_db()
        self.hh_id = "HH#KAMALA_01"

        # Seed standard test rules
        put_item({
            "PK": self.hh_id,
            "SK": f"{SKPrefix.RULE}FINANCIAL",
            "auto_limit": 2000.0,
            "max_with_approval": 25000.0,
            "frozen": False,
            "cooling_off_seconds": 20,
            "night_start": 23,
            "night_end": 6,
            "max_payments_per_hour": 2,
        })
        # Seed known biller
        put_item({
            "PK": self.hh_id,
            "SK": f"{SKPrefix.BILLER}BESCOM",
            "name": "BESCOM Electricity",
            "biller_id": "BESCOM",
            "amount": 1240.0,
        })

    def test_detect_scam_keywords(self):
        self.assertTrue(detect_scam_keywords("A CBI officer called saying I am in digital arrest."))
        self.assertTrue(detect_scam_keywords("Urgent KYC suspended! Download APK immediately."))
        self.assertFalse(detect_scam_keywords("Please pay my BESCOM electricity bill for November."))

    def test_extract_security_facts(self):
        interceptor = RequestInterceptor(self.hh_id)
        facts = interceptor.extract_security_facts(
            user_prompt="Pay BESCOM electricity bill",
            target_payee="BESCOM",
        )
        self.assertTrue(facts.payee_known)
        self.assertFalse(facts.frozen)
        self.assertFalse(facts.scam_flag)
        self.assertIsInstance(facts.hour_ist, int)
        self.assertGreaterEqual(facts.hour_ist, 0)
        self.assertLessEqual(facts.hour_ist, 23)

    def test_intercept_allowed_auto_limit(self):
        interceptor = RequestInterceptor(self.hh_id)
        allowed, context, reason = interceptor.intercept({
            "prompt": "Pay BESCOM bill",
            "tool_name": "pay_utility_bill",
            "tool_args": {"biller_id": "BESCOM", "amount": 1240.0},
        })
        # If running outside night hours, this should be allowed
        facts = context["security_facts"]
        if 6 <= facts["hour_ist"] < 23:
            self.assertTrue(allowed)
            self.assertIsNone(reason)

    def test_intercept_blocks_when_frozen(self):
        # Freeze household
        put_item({
            "PK": self.hh_id,
            "SK": f"{SKPrefix.RULE}FINANCIAL",
            "frozen": True,
            "auto_limit": 2000.0,
        })
        interceptor = RequestInterceptor(self.hh_id)
        allowed, context, reason = interceptor.intercept({
            "prompt": "Pay electricity bill",
            "tool_name": "pay_utility_bill",
            "tool_args": {"biller_id": "BESCOM", "amount": 500.0},
        })
        self.assertFalse(allowed)
        self.assertIn("frozen", reason.lower())

    def test_intercept_blocks_scam(self):
        interceptor = RequestInterceptor(self.hh_id)
        allowed, context, reason = interceptor.intercept({
            "prompt": "Police officer demanding urgent transfer under digital arrest",
            "tool_name": "pay_utility_bill",
            "tool_args": {"biller_id": "BESCOM", "amount": 1000.0},
        })
        self.assertFalse(allowed)
        self.assertIn("scam", reason.lower())

    def test_response_interceptor_sanitizes(self):
        resp_interceptor = ResponseInterceptor(self.hh_id)
        dirty_response = "Success! Transaction approved. Internal key: AKIAIOSFODNN7EXAMPLE used."
        cleaned = resp_interceptor.sanitize_text(dirty_response)
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", cleaned)
        self.assertIn("[REDACTED_AWS_KEY]", cleaned)


if __name__ == "__main__":
    unittest.main()
