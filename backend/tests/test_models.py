"""Tests for domain data models and schema serialization."""

import unittest
from backend.common.models import (
    HouseholdMember,
    HouseholdRules,
    Biller,
    Payee,
    Medicine,
    ApprovalRequest,
    AuditEvent,
    SecurityFacts,
    SKPrefix,
)


class TestModels(unittest.TestCase):
    def test_household_member_to_item(self):
        m = HouseholdMember(
            household_id="HH#KAMALA_01",
            member_id="KAMALA",
            name="Kamala",
            role="PARENT",
            age=74,
            location="Dharwad",
        )
        item = m.to_item()
        self.assertEqual(item["PK"], "HH#KAMALA_01")
        self.assertEqual(item["SK"], f"{SKPrefix.MEMBER}KAMALA")
        self.assertEqual(item["role"], "PARENT")
        self.assertEqual(item["age"], 74)

    def test_household_rules_to_item(self):
        rules = HouseholdRules(
            auto_limit=2000.0,
            max_with_approval=25000.0,
            frozen=False,
            cooling_off_seconds=20,
        )
        item = rules.to_item("HH#KAMALA_01")
        self.assertEqual(item["SK"], f"{SKPrefix.RULE}FINANCIAL")
        self.assertEqual(item["auto_limit"], 2000.0)
        self.assertFalse(item["frozen"])

    def test_biller_and_payee_to_item(self):
        biller = Biller(
            biller_id="BESCOM",
            name="BESCOM Electricity",
            utility_type="electricity",
            bill_id="BILL_BESCOM_NOV",
            amount=1240.0,
            due_date="28 Nov",
        )
        item = biller.to_item("HH#KAMALA_01")
        self.assertEqual(item["SK"], f"{SKPrefix.BILLER}BESCOM")
        self.assertEqual(item["amount"], 1240.0)

        payee = Payee(
            payee_id="APOLLO_PHARMACY",
            name="Apollo Pharmacy",
            category="Pharmacy",
            upi_id="apollopharmacy@icici",
        )
        p_item = payee.to_item("HH#KAMALA_01")
        self.assertEqual(p_item["SK"], f"{SKPrefix.PAYEE}APOLLO_PHARMACY")

    def test_security_facts_to_dict(self):
        facts = SecurityFacts(
            payee_known=True,
            hour_ist=14,
            payments_last_hour=0,
            approval_valid=False,
            scam_flag=False,
            frozen=False,
        )
        d = facts.to_dict()
        self.assertTrue(d["payee_known"])
        self.assertEqual(d["hour_ist"], 14)
        self.assertEqual(d["payments_last_hour"], 0)
        self.assertFalse(d["frozen"])


if __name__ == "__main__":
    unittest.main()
