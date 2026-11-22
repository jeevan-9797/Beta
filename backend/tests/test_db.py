"""Tests for single-table DynamoDB helpers and MockDatabase."""

import unittest
from backend.common.db import (
    MockDatabase,
    format_pk,
    get_item,
    put_item,
    delete_item,
    query_by_prefix,
    query_all_for_household,
    update_item,
    reset_mock_db,
)
from backend.common.models import SKPrefix


class TestDatabaseHelpers(unittest.TestCase):
    def setUp(self):
        self.mock_db = reset_mock_db()
        self.hh_id = "HH#TEST_01"

    def test_format_pk(self):
        self.assertEqual(format_pk("KAMALA_01"), "HH#KAMALA_01")
        self.assertEqual(format_pk("HH#KAMALA_01"), "HH#KAMALA_01")
        self.assertEqual(format_pk(""), "HH#KAMALA_01")

    def test_put_and_get_item(self):
        item = {
            "PK": self.hh_id,
            "SK": f"{SKPrefix.BILLER}BESCOM",
            "name": "BESCOM Electricity",
            "amount": 1240.0,
        }
        saved = put_item(item)
        self.assertEqual(saved["PK"], self.hh_id)

        retrieved = get_item(self.hh_id, f"{SKPrefix.BILLER}BESCOM")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved["name"], "BESCOM Electricity")
        self.assertEqual(retrieved["amount"], 1240.0)

    def test_query_by_prefix(self):
        put_item({"PK": self.hh_id, "SK": f"{SKPrefix.MED}MED_1", "name": "Med 1"})
        put_item({"PK": self.hh_id, "SK": f"{SKPrefix.MED}MED_2", "name": "Med 2"})
        put_item({"PK": self.hh_id, "SK": f"{SKPrefix.BILLER}BESCOM", "name": "BESCOM"})

        meds = query_by_prefix(self.hh_id, SKPrefix.MED)
        self.assertEqual(len(meds), 2)
        self.assertTrue(all(m["SK"].startswith(SKPrefix.MED) for m in meds))

        billers = query_by_prefix(self.hh_id, SKPrefix.BILLER)
        self.assertEqual(len(billers), 1)

    def test_update_item(self):
        item = {
            "PK": self.hh_id,
            "SK": f"{SKPrefix.RULE}FINANCIAL",
            "auto_limit": 2000,
            "frozen": False,
        }
        put_item(item)

        updated = update_item(self.hh_id, f"{SKPrefix.RULE}FINANCIAL", {"frozen": True})
        self.assertTrue(updated["frozen"])
        self.assertEqual(updated["auto_limit"], 2000)

    def test_delete_item(self):
        sk = f"{SKPrefix.PAYEE}TEST_PAYEE"
        put_item({"PK": self.hh_id, "SK": sk, "name": "Test Payee"})
        self.assertIsNotNone(get_item(self.hh_id, sk))

        deleted = delete_item(self.hh_id, sk)
        self.assertTrue(deleted)
        self.assertIsNone(get_item(self.hh_id, sk))


if __name__ == "__main__":
    unittest.main()
