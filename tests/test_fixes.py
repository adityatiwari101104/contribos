"""Regression tests for fixes found while using ContribOS as an outside contributor."""
import subprocess
import tempfile
import unittest
from pathlib import Path

from contribos import brief, check


class EmptyRepo(unittest.TestCase):
    def test_no_commits_gives_a_clear_error(self):
        with tempfile.TemporaryDirectory() as d:
            subprocess.run(["git", "init", "-q", d], check=True)
            with self.assertRaises(RuntimeError) as cm:
                check.local_repo(d)
            self.assertIn("no commits yet", str(cm.exception))


class BriefTerms(unittest.TestCase):
    def test_repo_name_is_not_a_search_term(self):
        class R:  # minimal stand-in for Repo
            owner, name = "acme", "widgetkit"
            def files(self, rev="HEAD"):
                return ["widgetkit/check.py", "widgetkit/policy.py"]
            def grep(self, pattern, rev="HEAD", ignore_case=True):
                return {}
            slug = "acme/widgetkit"
        b = brief.build(R(), "Add --json flag to widgetkit check", "", [], None)
        self.assertNotIn("widgetkit", [t.text.lower() for t in b.terms])
        self.assertEqual(b.files[0].path, "widgetkit/check.py")


if __name__ == "__main__":
    unittest.main()
