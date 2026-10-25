import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("seats", Path(__file__).parents[1] / "scripts/seats.py")
seats = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seats)


def run(*argv):
    with contextlib.redirect_stdout(io.StringIO()) as out:
        code = seats.main(list(argv))
    return code, out.getvalue()


class TrackingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"XDG_CONFIG_HOME": self.tmp.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def state(self, sample=False):
        return json.loads(seats.state_file(sample).read_text())

    def blocked(self, *args, **kwargs):
        raise seats.Stop(seats.EXIT_BLOCKED, "reason=forbidden HTTP 403")

    def test_track_by_number_while_blocked(self):
        with patch.object(seats, "fetch", side_effect=self.blocked):
            code, out = run("watch", "add", "--term", "2271", "--class", "21671")
        self.assertEqual(code, 0)
        self.assertIn("fill in on the first check that gets through", out)
        watch = self.state()["watches"][0]
        self.assertEqual(watch["nbr"], "21671")
        self.assertIsNone(watch["last_status"])

    def test_first_check_that_gets_through_fills_in_details(self):
        with patch.object(seats, "fetch", side_effect=self.blocked):
            run("watch", "add", "--term", "2271", "--class", "21671")
        seats.state_file(True).write_text(seats.state_file(False).read_text())  # same watch, sample data
        code, out = run("check", "--sample")
        self.assertEqual(code, 0)
        self.assertIn("FIRST-LOOK #21671 CSE 340-1003", out)
        watch = self.state(sample=True)["watches"][0]
        self.assertEqual((watch["subject"], watch["catalog"], watch["last_status"]), ("CSE", "340", "FULL"))

    def test_wrong_number_is_not_tracked_when_asu_answers(self):
        code, out = run("watch", "add", "--term", "2271", "--class", "99999", "--sample")
        self.assertEqual(code, seats.EXIT_NOT_FOUND)
        self.assertFalse(seats.state_file(True).exists())

    def test_history_counts_blocked_and_successful_checks(self):
        run("watch", "add", "--term", "2271", "--course", "CSE 340", "--class", "21671", "--sample")
        with patch.object(seats, "fetch", side_effect=self.blocked):
            run("check", "--sample")
        run("check", "--sample")
        code, out = run("history", "--sample")
        self.assertIn("CHECKS 1 of the last 2 got through", out)
        self.assertIn("got through just now | blocked just now", out)

    def test_link_points_at_class_search(self):
        code, out = run("link", "--term", "spring 2027", "--course", "cse412")
        self.assertIn("catalogNbr=412", out)
        self.assertIn("subject=CSE&term=2271", out)


if __name__ == "__main__":
    unittest.main()
