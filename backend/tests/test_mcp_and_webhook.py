"""Tests for MCP servers and WhatsApp webhook."""

import unittest
from backend.common.db import reset_mock_db, put_item, get_item, get_household_rules
from backend.common.models import SKPrefix, ApprovalStatus
from backend.mcp_errands import tools as errand_tools
from backend.mcp_errands import handler as errand_handler
from backend.mcp_guardian import tools as guardian_tools
from backend.mcp_guardian import handler as guardian_handler
from backend.mcp_shield import tools as shield_tools
from backend.guardian_api.whatsapp_webhook import process_whatsapp_message


class TestMcpAndWebhook(unittest.TestCase):
    def setUp(self):
        self.mock_db = reset_mock_db()
        self.hh_id = "HH#KAMALA_01"

        # Seed initial rules
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
        # Seed biller
        put_item({
            "PK": self.hh_id,
            "SK": f"{SKPrefix.BILLER}BESCOM",
            "biller_id": "BESCOM",
            "name": "BESCOM Electricity",
            "amount": 1240.0,
            "bill_id": "BILL_BESCOM_NOV",
            "status": "PENDING",
        })
        # Seed meds
        put_item({
            "PK": self.hh_id,
            "SK": f"{SKPrefix.MED}METFORMIN_500MG",
            "med_id": "METFORMIN_500MG",
            "name": "Metformin 500mg",
            "dosage": "500mg",
            "refill_qty": 60,
            "unit_price": 20.0,
            "reorder_total": 1200.0,
        })
        put_item({
            "PK": self.hh_id,
            "SK": f"{SKPrefix.MED}AMLODIPINE_5MG",
            "med_id": "AMLODIPINE_5MG",
            "name": "Amlodipine 5mg",
            "dosage": "5mg",
            "refill_qty": 60,
            "unit_price": 43.333,
            "reorder_total": 2600.0,
        })

    def test_errand_pay_bill(self):
        res = errand_tools.pay_utility_bill("BESCOM", 1240.0, household_id=self.hh_id)
        self.assertTrue(res["success"])
        self.assertIn("tx_id", res)

        # Check biller status updated
        biller = get_item(self.hh_id, f"{SKPrefix.BILLER}BESCOM")
        self.assertEqual(biller["status"], "PAID")

    def test_errand_reorder_medicines(self):
        res = errand_tools.reorder_medicines(
            ["METFORMIN_500MG", "AMLODIPINE_5MG"],
            pharmacy_name="Apollo Pharmacy",
            household_id=self.hh_id,
        )
        self.assertTrue(res["success"])
        self.assertEqual(res["total_amount"], 3800.0)

    def test_guardian_approval_request(self):
        res = guardian_tools.request_guardian_approval(
            errand_type="MEDICINE_REORDER",
            payee_name="Apollo Pharmacy",
            amount=3800.0,
            reason="monthly BP and diabetes tablets",
            household_id=self.hh_id,
        )
        self.assertTrue(res["success"])
        approval_id = res["approval_id"]

        # Check in DB
        app_item = get_item(self.hh_id, f"{SKPrefix.APPROVAL}{approval_id}")
        self.assertIsNotNone(app_item)
        self.assertEqual(app_item["amount"], 3800.0)

    def test_whatsapp_webhook_freeze_and_unfreeze(self):
        # Freeze command
        reply = process_whatsapp_message("+919876543210", "FREEZE", household_id=self.hh_id)
        self.assertIn("EMERGENCY FREEZE ACTIVATED", reply)
        rules = get_household_rules(self.hh_id)
        self.assertTrue(rules["frozen"])

        # Unfreeze command
        reply2 = process_whatsapp_message("+919876543210", "UNFREEZE", household_id=self.hh_id)
        self.assertIn("EMERGENCY FREEZE LIFTED", reply2)
        rules2 = get_household_rules(self.hh_id)
        self.assertFalse(rules2["frozen"])

    def test_shield_scam_detection(self):
        res = shield_tools.screen_scam_risk("Arrest warrant issued, transfer money now", household_id=self.hh_id)
        self.assertTrue(res["scam_detected"])
        self.assertGreater(res["risk_score"], 0.8)


if __name__ == "__main__":
    unittest.main()
