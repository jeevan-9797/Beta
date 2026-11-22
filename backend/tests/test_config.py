"""Tests for configuration settings and defaults."""

import os
import unittest

from backend.common.config import (
    AWS_REGION,
    DYNAMODB_TABLE,
    BEDROCK_MODEL_ID,
    APPROVAL_TTL_SECONDS,
    COOLING_OFF_SECONDS,
    DEFAULT_HOUSEHOLD_ID,
    LOCAL_MOCK_DB,
    settings,
)


class TestConfig(unittest.TestCase):
    def test_default_values(self):
        self.assertEqual(settings.AWS_REGION, "ap-south-1")
        self.assertEqual(settings.DYNAMODB_TABLE, "BETA_Household")
        self.assertEqual(settings.BEDROCK_MODEL_ID, "apac.amazon.nova-micro-v1:0")
        self.assertEqual(settings.APPROVAL_TTL_SECONDS, 7200)
        self.assertEqual(settings.COOLING_OFF_SECONDS, 20)
        self.assertEqual(settings.DEFAULT_HOUSEHOLD_ID, "kamala")
        self.assertTrue(isinstance(settings.LOCAL_MOCK_DB, bool))

    def test_top_level_exports(self):
        self.assertEqual(AWS_REGION, "ap-south-1")
        self.assertEqual(DYNAMODB_TABLE, "BETA_Household")
        self.assertEqual(BEDROCK_MODEL_ID, "apac.amazon.nova-micro-v1:0")
        self.assertEqual(APPROVAL_TTL_SECONDS, 7200)
        self.assertEqual(COOLING_OFF_SECONDS, 20)
        self.assertEqual(DEFAULT_HOUSEHOLD_ID, "kamala")


if __name__ == "__main__":
    unittest.main()
