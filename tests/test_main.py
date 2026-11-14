import unittest
from unittest.mock import Mock, patch

import requests

import main


class CheckApiTests(unittest.TestCase):
    @patch("main.save_result")
    @patch("main.requests.get")
    def test_successful_json_response_is_saved(self, mock_get, mock_save):
        response = Mock()
        response.status_code = 200
        response.ok = True
        response.json.return_value = {"id": 1, "title": "demo"}
        mock_get.return_value = response

        main.check_api()

        mock_get.assert_called_once()
        saved_status, saved_duration, saved_body, saved_error = mock_save.call_args.args
        self.assertEqual(saved_status, 200)
        self.assertIsInstance(saved_duration, int)
        self.assertEqual(saved_body, {"id": 1, "title": "demo"})
        self.assertIsNone(saved_error)

    @patch("main.save_result")
    @patch("main.requests.get")
    def test_http_error_is_saved(self, mock_get, mock_save):
        response = Mock()
        response.status_code = 503
        response.ok = False
        response.json.return_value = {"message": "unavailable"}
        mock_get.return_value = response

        main.check_api()

        saved_status, _, saved_body, saved_error = mock_save.call_args.args
        self.assertEqual(saved_status, 503)
        self.assertEqual(saved_body, {"message": "unavailable"})
        self.assertEqual(saved_error, "HTTP 503")

    @patch("main.save_result")
    @patch("main.requests.get", side_effect=requests.Timeout("timeout"))
    def test_network_error_is_saved(self, mock_get, mock_save):
        main.check_api()

        saved_status, _, saved_body, saved_error = mock_save.call_args.args
        self.assertIsNone(saved_status)
        self.assertIsNone(saved_body)
        self.assertTrue(saved_error.startswith("Timeout:"))


if __name__ == "__main__":
    unittest.main()
