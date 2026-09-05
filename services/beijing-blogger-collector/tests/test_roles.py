from __future__ import annotations

import unittest
from unittest.mock import patch

from mx_agent.roles import ApplicationRole, application_role, require_role


class ApplicationRoleTests(unittest.TestCase):
    def test_default_is_explicit_development_role(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(application_role(), ApplicationRole.ALL_DEV)

    def test_collector_role_can_be_required_without_loading_ai_modules(self) -> None:
        self.assertEqual(
            require_role(ApplicationRole.COLLECTOR, value="collector"),
            ApplicationRole.COLLECTOR,
        )

    def test_wrong_or_unknown_role_is_rejected(self) -> None:
        with self.assertRaises(RuntimeError):
            require_role(ApplicationRole.INTELLIGENCE, value="collector")
        with self.assertRaises(RuntimeError):
            application_role("combined-production")

    def test_production_roles_are_distinct_from_combined_development(self) -> None:
        self.assertNotEqual(ApplicationRole.COLLECTOR, ApplicationRole.ALL_DEV)
        self.assertNotEqual(ApplicationRole.INTELLIGENCE, ApplicationRole.ALL_DEV)


if __name__ == "__main__":
    unittest.main()
