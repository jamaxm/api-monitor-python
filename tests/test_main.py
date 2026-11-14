import unittest
from unittest.mock import Mock, patch

import requests

import main


class RedactionTests(unittest.TestCase):
    def test_redacts_passwords_recursively(self):
        source = {
            "status": "OK",
            "data": [{"username": "demo", "password": "fake-secret", "nested": {"api_key": "token"}}],
        }
        result = main.redact_sensitive_fields(source)
        self.assertEqual(result["data"][0]["username"], "demo")
        self.assertNotIn("password", result["data"][0])
        self.assertNotIn("api_key", result["data"][0]["nested"])


class FetchResourceTests(unittest.TestCase):
    @patch("main.persist_resource_result")
    @patch("main.requests.get")
    def test_successful_json_response_is_persisted(self, mock_get, mock_persist):
        response = Mock()
        response.status_code = 200
        response.ok = True
        response.json.return_value = {
            "status": "OK",
            "code": 200,
            "total": 1,
            "data": [{"id": 1, "title": "Example", "author": "Test"}],
        }
        mock_get.return_value = response

        succeeded = main.fetch_resource(42, "books")

        self.assertTrue(succeeded)
        mock_get.assert_called_once()
        args = mock_persist.call_args.kwargs
        self.assertEqual(args["sync_run_id"], 42)
        self.assertEqual(args["resource"], "books")
        self.assertEqual(args["http_status"], 200)
        self.assertEqual(len(args["items"]), 1)
        self.assertIsNone(args["error_message"])

    @patch("main.persist_resource_result")
    @patch("main.requests.get")
    def test_http_error_is_logged_as_failure(self, mock_get, mock_persist):
        response = Mock()
        response.status_code = 503
        response.ok = False
        response.json.return_value = {"message": "unavailable"}
        mock_get.return_value = response

        succeeded = main.fetch_resource(7, "addresses")

        self.assertFalse(succeeded)
        args = mock_persist.call_args.kwargs
        self.assertEqual(args["http_status"], 503)
        self.assertEqual(args["items"], [])
        self.assertEqual(args["error_message"], "HTTP 503")

    @patch("main.persist_resource_result")
    @patch("main.requests.get", side_effect=requests.Timeout("timeout"))
    def test_network_error_is_persisted(self, mock_get, mock_persist):
        succeeded = main.fetch_resource(9, "products")

        self.assertFalse(succeeded)
        args = mock_persist.call_args.kwargs
        self.assertIsNone(args["http_status"])
        self.assertEqual(args["items"], [])
        self.assertTrue(args["error_message"].startswith("Timeout:"))

    @patch("main.persist_resource_result")
    @patch("main.requests.get")
    def test_http_200_with_invalid_envelope_is_failure(self, mock_get, mock_persist):
        response = Mock()
        response.status_code = 200
        response.ok = True
        response.json.return_value = {"status": "OK", "data": "not-a-list"}
        mock_get.return_value = response

        succeeded = main.fetch_resource(10, "users")

        self.assertFalse(succeeded)
        args = mock_persist.call_args.kwargs
        self.assertIn("Unexpected API response format", args["error_message"])


class ResourceMappingTests(unittest.TestCase):
    def test_user_mapping_does_not_persist_password(self):
        conn = Mock()
        main.persist_resource_record(
            conn,
            sync_run_id=1,
            resource="users",
            index=1,
            item={
                "id": 1,
                "firstname": "Taylor",
                "lastname": "Smith",
                "username": "taylor",
                "email": "taylor@example.test",
                "password": "not-to-store",
                "macAddress": "00:00:00:00:00:00",
            },
        )
        sql = conn.execute.call_args.args[0]
        self.assertIn("faker_users", sql)
        self.assertNotIn("password", sql.lower())


if __name__ == "__main__":
    unittest.main()
