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


def _policy_for(files):
    """Analyze a throwaway git repo holding `files`."""
    import subprocess
    from contribos import policy
    from contribos.repo import Repo
    root = Path(tempfile.mkdtemp(prefix="contribos-fix-"))
    for path, text in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text, encoding="utf-8")
    for cmd in (["init", "-q", "-b", "main"], ["add", "-A"],
                ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base"]):
        subprocess.run(["git", "-C", str(root), *cmd], check=True)
    return policy.analyze(Repo("o", "r", root), [])


DJANGO_TEMPLATE = """#### AI Assistance Disclosure (REQUIRED)
<!-- Select exactly ONE of the following: -->
- [ ] **No AI tools were used** in preparing this PR.
- [ ] **If AI tools were used**, I have disclosed which ones, and fully reviewed and verified their output.
"""


class AiBanFalsePositive(unittest.TestCase):
    """Issue #7: a 'No AI tools were used' checkbox is a disclosure, not a ban."""

    def test_django_disclosure_checkbox_is_not_a_ban(self):
        p = _policy_for({".github/pull_request_template.md": DJANGO_TEMPLATE})
        self.assertEqual(p.get("AI policy").verdict, "Allows AI help but requires you to disclose it.")

    def test_real_ban_is_still_detected(self):
        p = _policy_for({"AI_POLICY.md": "- **No AI-generated media is allowed (art, images, videos).**\n"})
        self.assertTrue(p.get("AI policy").verdict.startswith(("Strict", "Restricts")))


class NotAcceptingContributions(unittest.TestCase):
    """Issue #8: say so first when a repo does not take outside PRs."""

    def test_tldraw_style_notice_is_the_first_finding(self):
        p = _policy_for({"CONTRIBUTING.md": "# Contributing\n\nWe are **not accepting contributions** to "
                                            "[tldraw](https://github.com/tldraw/tldraw) at this time. "
                                            "Pull requests are turned off for this repository.\n"})
        first = p.findings[0]
        self.assertEqual(first.topic, "Not accepting contributions")
        self.assertEqual(first.evidence[0].source, "CONTRIBUTING.md:3")
        from contribos import policy
        self.assertFalse(policy.structured(p)["accepting_contributions"])

    def test_conditional_rules_are_not_a_closure(self):
        p = _policy_for({"CONTRIBUTING.md": "We don't accept PRs without tests.\n"
                                            "We do not accept pull requests that only change formatting.\n"})
        self.assertIsNone(p.get("Not accepting contributions"))
