import unittest

import main


class TestAdminControl(unittest.TestCase):
    class Role:
        def __init__(self, role_id):
            self.id = role_id

    class Member:
        def __init__(self, *role_ids):
            self.roles = [TestAdminControl.Role(role_id) for role_id in role_ids]

    def test_staff_access_uses_role_ids(self):
        config = {
            "ALLOWED_TICKET_STAFF_ROLE_IDS": ["123"],
            "ALLOWED_SSU_STAFF_ROLE_IDS": [456],
        }
        ticket_staff = self.Member(123)
        ssu_staff = self.Member(456)
        unapproved_member = self.Member(789)

        self.assertTrue(main.is_allowed_ticket_staff(ticket_staff, config))
        self.assertTrue(main.is_allowed_ssu_staff(ssu_staff, config))
        self.assertFalse(main.is_allowed_ticket_staff(unapproved_member, config))
        self.assertFalse(main.is_allowed_ssu_staff(unapproved_member, config))

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
