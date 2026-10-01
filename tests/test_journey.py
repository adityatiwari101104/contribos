"""Tests for the API-backed steps, using a fake GitHub with realistic payloads."""

import io
import json
import unittest
from datetime import datetime, timedelta, timezone

from contribos import find, llm, mcp_server, record, review, setup_doctor
from contribos.claim import draft
from contribos.policy import Evidence, Finding, Policy


def ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat().replace("+00:00", "Z")


def user(login):
    return {"login": login, "type": "User"}


class FakeGitHub:
    available = True
    last_error = None

    def __init__(self, **data):
        self.data = data

    def issue_comments(self, o, n, num):
        return self.data.get("comments", [])

    def timeline(self, o, n, num):
        return self.data.get("timeline", [])

    def repo(self, o, n):
        return self.data.get("repo", {"archived": False, "pushed_at": ago(3)})

    def has_file(self, o, n, path):
        return path in self.data.get("files", ())

    def pulls(self, o, n, state="closed", per_page=50):
        return self.data.get("pulls" if state == "closed" else "open_pulls", [])

    def list_dir(self, o, n, path):
        return self.data.get("workflows", [])

    def me(self):
        return self.data.get("me")

    def search_issues(self, q, per_page=30):
        return self.data.get("search", [])

    def pr_reviews(self, o, n, num):
        return self.data.get("reviews", {}).get(num, [])

    def pr_review_comments(self, o, n, num):
        return self.data.get("review_comments", {}).get(num, [])


def issue(num=1, **kw):
    base = {"number": num, "title": "Crash on empty config", "html_url": f"https://github.com/o/r/issues/{num}",
            "url": f"https://api.github.com/repos/o/r/issues/{num}", "state": "open", "assignees": [],
            "labels": [{"name": "good first issue"}], "created_at": ago(20), "author_association": "MEMBER",
            "body": "Steps to reproduce:\n```py\nload({})\n```\nExpected: no crash. Actual: KeyError. " + "x" * 300}
    base.update(kw)
    return base


class Takeability(unittest.TestCase):
    def test_clear_free_issue_is_good_bet(self):
        gh = FakeGitHub(comments=[{"body": "Confirmed, happy to take a PR.", "author_association": "OWNER",
                                   "created_at": ago(2), "user": user("maint"), "html_url": "u"}])
        v = find.takeability(gh, "o", "r", issue())
        self.assertEqual(v.tier, "Good bet")

    def test_recent_claim_means_skip(self):
        gh = FakeGitHub(comments=[{"body": "Can I work on this?", "author_association": "NONE",
                                   "created_at": ago(3), "user": user("sam"), "html_url": "u"}])
        v = find.takeability(gh, "o", "r", issue())
        self.assertEqual(v.tier, "Skip")
        self.assertTrue(any("@sam" in s.text for s in v.signals))

    def test_open_pr_means_skip(self):
        tl = [{"event": "cross-referenced", "source": {"issue": {"pull_request": {}, "state": "open",
                                                                   "html_url": "https://github.com/o/r/pull/9"}}}]
        v = find.takeability(FakeGitHub(timeline=tl), "o", "r", issue())
        self.assertEqual(v.tier, "Skip")

    def test_vouch_list_makes_repo_unwelcoming(self):
        w = find.repo_welcome(FakeGitHub(files={".github/VOUCHED.td"}), "o", "r")
        self.assertFalse(w.welcoming)

    def test_outside_merge_rate(self):
        pulls = [{"author_association": "NONE", "merged_at": None} for _ in range(10)]
        w = find.repo_welcome(FakeGitHub(pulls=pulls), "o", "r")
        self.assertFalse(w.welcoming)


class Responsiveness(unittest.TestCase):
    def test_slow_and_stalled_repo(self):
        open_prs = [{"number": i, "author_association": "NONE", "created_at": ago(60), "updated_at": ago(45),
                     "user": user(f"u{i}")} for i in range(6)]
        gh = FakeGitHub(open_pulls=open_prs, workflows=["stale.yml"], me="u1")
        w = find.repo_welcome(gh, "o", "r")
        text = " ".join(s.text for s in w.signals)
        self.assertIn("never got a human reply", text)
        self.assertIn("no activity for 30+ days", text)
        self.assertIn("stale bot", text)
        self.assertIn("already have 1 open PR", text)
        self.assertFalse(w.welcoming)

    def test_first_human_response_ignores_bots_and_author(self):
        pr = {"number": 1, "created_at": ago(10), "user": user("me")}
        gh = FakeGitHub(comments=[
            {"user": {"login": "ci-bot", "type": "Bot"}, "created_at": ago(10)},
            {"user": user("me"), "created_at": ago(9)},
            {"user": user("maint"), "created_at": ago(8)}])
        self.assertAlmostEqual(find.first_human_response_days(gh, "o", "r", pr), 2, places=1)


class Claim(unittest.TestCase):
    def test_mentions_ai_policy_and_never_posts(self):
        pol = Policy("o/r", [Finding("AI policy", "Allows AI help but requires you to disclose it.",
                                     [Evidence("AI_POLICY.md:3", "Disclose AI use.")])])
        text = draft("Crash", None, pol)
        self.assertIn("AI policy", text)
        self.assertIn("never posts", text)


class Review(unittest.TestCase):
    def setUp(self):
        self.rc = [
            {"id": 1, "user": user("maint"), "body": "Please add a test for the empty case.", "path": "src/a.py",
             "line": 3, "created_at": ago(10), "html_url": "c1"},
            {"id": 2, "user": user("me"), "body": "Done", "in_reply_to_id": 1, "created_at": ago(9)},
            {"id": 3, "user": user("maint"), "body": "Why a dict here?", "path": "src/a.py", "line": 5,
             "created_at": ago(9), "html_url": "c3"},
        ]

    def test_follow_through(self):
        items, notes = review.analyze("me", self.rc, [], [{"state": "CHANGES_REQUESTED"}],
                                      lambda p: "\n".join(f"line{i}" for i in range(1, 10)))
        by_id = {i.url: i for i in items}
        self.assertTrue(by_id["c1"].answered)
        self.assertFalse(by_id["c3"].answered)
        self.assertEqual(by_id["c3"].kind, "question")
        self.assertEqual(by_id["c1"].rule[0], "Tests")
        self.assertTrue(any("abandoned" in n for n in notes))
        self.assertIn("line5", "\n".join(by_id["c3"].snippet))
        md = review.to_markdown("pr", items, notes)
        self.assertIn("Draft reply", md)


class Record(unittest.TestCase):
    def test_record_counts_and_links(self):
        items = [
            {"url": "https://api.github.com/repos/pallets/flask/issues/10", "html_url": "https://github.com/pallets/flask/pull/10",
             "title": "Fix x", "state": "closed", "created_at": ago(40), "closed_at": ago(30),
             "pull_request": {"merged_at": ago(30)}, "body": "## Understanding\nBecause...\n## AI assistance\nNone."},
            {"url": "https://api.github.com/repos/me/own/issues/1", "html_url": "h", "title": "own", "state": "closed",
             "created_at": ago(5), "closed_at": ago(4), "pull_request": {"merged_at": ago(4)}, "body": ""},
        ]
        gh = FakeGitHub(search=items, reviews={10: [{"state": "CHANGES_REQUESTED"}]},
                        review_comments={10: [{"id": 1, "user": user("maint"), "body": "x"},
                                              {"id": 2, "user": user("me"), "in_reply_to_id": 1, "body": "done"}]})
        r = record.build(gh, "me")
        self.assertEqual(len(r.prs), 1)  # own repo excluded
        p = r.prs[0]
        self.assertEqual((p.state, p.review_rounds, p.answered_reviews, p.explained, p.ai_disclosed),
                         ("merged", 1, True, True, True))
        self.assertIn("https://github.com/pallets/flask/pull/10", record.to_markdown(r))
        self.assertIn("<table>", record.to_html(r))


class Diagnose(unittest.TestCase):
    def test_kinds(self):
        cases = {
            "E   ModuleNotFoundError: No module named 'werkzeug'": "Missing dependency",
            "psycopg.OperationalError: connection failed: Connection refused (port 5432)": "Service not running",
            "KeyError: 'DATABASE_URL'": "Missing environment variable",
            "FAILED tests/test_app.py::test_x - assert 1 == 2": "Test failure",
        }
        for log, kind in cases.items():
            self.assertEqual(setup_doctor.diagnose(log)[0].kind, kind, log)


class Grounding(unittest.TestCase):
    def test_drops_uncited_paths(self):
        text, dropped = llm.grounded("Look at `src/a.py`. Also `src/ghost.py` is wrong.", {"src/a.py"})
        self.assertEqual(dropped, 1)
        self.assertIn("src/a.py", text)
        self.assertNotIn("ghost", text)

    def test_disabled_by_default(self):
        import os
        old = os.environ.pop("CONTRIBOS_LLM", None)
        try:
            with self.assertRaises(llm.LLMUnavailable):
                llm.complete("hi")
        finally:
            if old is not None:
                os.environ["CONTRIBOS_LLM"] = old


class MCP(unittest.TestCase):
    def test_handshake_and_tools(self):
        msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                 "params": {"name": "contribos_diagnose", "arguments": {}}}]
        out = io.StringIO()
        mcp_server.serve(io.StringIO("\n".join(json.dumps(m) for m in msgs) + "\n"), out)
        replies = [json.loads(l) for l in out.getvalue().splitlines()]
        self.assertEqual(len(replies), 3)  # the notification gets no reply
        self.assertEqual(replies[0]["result"]["serverInfo"]["name"], "contribos")
        names = {t["name"] for t in replies[1]["result"]["tools"]}
        self.assertIn("contribos_brief", names)
        self.assertTrue(replies[2]["result"]["isError"])  # missing argument is reported, not a crash


if __name__ == "__main__":
    unittest.main()
