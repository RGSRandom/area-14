import unittest

import main


class TestTestingMode(unittest.TestCase):
    def test_test_user_filtering(self):
        self.assertEqual(main.get_test_user_id({"TEST_USER_ID": "123"}), 123)
        self.assertTrue(main.should_sync_user(123, {"TEST_USER_ID": "123"}))
        self.assertFalse(main.should_sync_user(999, {"TEST_USER_ID": "123"}))

    def test_missing_test_user_raises(self):
        with self.assertRaises(ValueError):
            main.should_sync_user(123, {})


if __name__ == "__main__":
    unittest.main()
