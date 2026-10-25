import contextlib
import importlib.util
import io
import json
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("seats", Path(__file__).parents[1] / "scripts/seats.py")
seats = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seats)


class Response(io.BytesIO):
    def __init__(self, body, headers=None):
        super().__init__(body)
        self.headers = headers or Message()


class NetworkTests(unittest.TestCase):
    def error(self, status, body=b"denied", headers=None):
        return urllib.error.HTTPError(seats.API_URL, status, "error", headers or Message(), io.BytesIO(body))

    def test_public_request_and_json(self):
        with patch.object(seats.urllib.request, "urlopen", return_value=Response(b'{"classes":[]}')) as call:
            self.assertEqual(seats.request_json(seats.API_URL), {"classes": []})
        self.assertEqual(call.call_args.args[0].get_header("Authorization"), "Bearer null")

    def test_denials_do_not_claim_cloudflare(self):
        for status, reason in [(401, "unauthorized"), (403, "forbidden")]:
            with self.subTest(status=status), patch.object(seats.urllib.request, "urlopen", side_effect=self.error(status)):
                with self.assertRaises(seats.Stop) as caught:
                    seats.request_json(seats.API_URL)
                self.assertEqual(caught.exception.code, seats.EXIT_BLOCKED)
                self.assertIn("reason=" + reason, str(caught.exception))

    def test_challenge_with_success_status_uses_browser_fallback(self):
        response = Response(b"<!doctype html><html><title>Just a moment...</title></html>")
        with patch.object(seats.urllib.request, "urlopen", return_value=response):
            with self.assertRaises(seats.Stop) as caught:
                seats.request_json(seats.API_URL)
        self.assertEqual(caught.exception.code, seats.EXIT_BLOCKED)
        self.assertIn("human_check", str(caught.exception))

    def test_challenge_header_on_denial(self):
        headers = Message()
        headers["cf-mitigated"] = "challenge"
        with patch.object(seats.urllib.request, "urlopen", side_effect=self.error(403, headers=headers)):
            with self.assertRaises(seats.Stop) as caught:
                seats.request_json(seats.API_URL)
        self.assertIn("human_check", str(caught.exception))

    def test_json_text_is_not_a_challenge(self):
        self.assertFalse(seats.is_challenge({}, json.dumps({"title": "verify you are human"}).encode()))

    def test_invalid_json_stays_unexpected(self):
        with patch.object(seats.urllib.request, "urlopen", return_value=Response(b"<html>maintenance</html>")):
            with self.assertRaises(seats.Stop) as caught:
                seats.request_json(seats.API_URL)
        self.assertEqual(caught.exception.code, seats.EXIT_UNEXPECTED)

    def test_transient_error_retries(self):
        with patch.object(seats.urllib.request, "urlopen", side_effect=[self.error(503), Response(b'{"classes":[]}')]) as call, patch.object(seats.time, "sleep"):
            seats.request_json(seats.API_URL)
        self.assertEqual(call.call_count, 2)

    def test_tunnel_error_not_attributed_to_asu(self):
        error = urllib.error.URLError("Tunnel connection failed: 403 Forbidden")
        with patch.object(seats.urllib.request, "urlopen", side_effect=error):
            with self.assertRaises(seats.Stop) as caught:
                seats.request_json(seats.API_URL)
        self.assertIn("reason=network_tunnel", str(caught.exception))

    def test_diagnose_does_not_write_state(self):
        with patch.object(seats, "save_state") as save, contextlib.redirect_stdout(io.StringIO()) as output:
            code = seats.main(["diagnose", "--sample", "--term", "2271", "--course", "CSE 340"])
        self.assertEqual(code, 0)
        self.assertIn("DIAG OK", output.getvalue())
        self.assertIn("source=sample", output.getvalue())
        save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
