from __future__ import annotations

import unittest

from web_research.profiles import get_work_profile, list_work_profiles


class WorkProfilesTests(unittest.TestCase):
    def test_get_work_profile_returns_expected_defaults(self) -> None:
        fast = get_work_profile('fast')
        careful = get_work_profile('careful')
        private = get_work_profile('private-share')
        exhaustive = get_work_profile('exhaustive')

        self.assertEqual(fast.follow_up_rounds, 0)
        self.assertTrue(careful.probe_tools)
        self.assertTrue(private.redact_exports)
        self.assertTrue(exhaustive.render)
        self.assertEqual(exhaustive.follow_up_rounds, 3)

    def test_get_work_profile_rejects_unknown_profile(self) -> None:
        with self.assertRaises(ValueError):
            get_work_profile('unknown')

    def test_list_work_profiles_is_serializable(self) -> None:
        profiles = list_work_profiles()

        self.assertEqual([item['name'] for item in profiles], ['careful', 'exhaustive', 'fast', 'private-share'])


if __name__ == '__main__':
    unittest.main()
