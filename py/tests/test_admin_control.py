import unittest

import main


class TestAdminControl(unittest.TestCase):
    def test_allowed_control_users(self):
        self.assertTrue(main.is_controlled_user(1020581214077333525))
        self.assertTrue(main.is_controlled_user(1241045030274203659))
        self.assertFalse(main.is_controlled_user(999999999999999999))

    def test_pause_and_resume(self):
        main.set_sync_enabled(True)
        self.assertTrue(main.is_sync_enabled())
        main.set_sync_enabled(False)
        self.assertFalse(main.is_sync_enabled())
        main.set_sync_enabled(True)
        self.assertTrue(main.is_sync_enabled())

    def test_non_test_mode_syncs_all_users(self):
        main.set_sync_enabled(True)
        self.assertTrue(main.should_sync_user(123, {"TEST_MODE": False}))
        self.assertTrue(main.should_sync_user(456, {"TEST_MODE": False}))

    def test_test_mode_only_syncs_configured_user(self):
        self.assertTrue(main.should_sync_user(123, {"TEST_MODE": True, "TEST_USER_ID": "123"}))
        self.assertFalse(main.should_sync_user(456, {"TEST_MODE": True, "TEST_USER_ID": "123"}))


if __name__ == "__main__":
    unittest.main()
