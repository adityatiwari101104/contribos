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


# curl/curl .github/pull_request_template.md at 70c084a01, verbatim.
CURL_TEMPLATE = """<!--
        IMPORTANT:
        If you cannot understand or explain your work without using
        Artificial Intelligence (AI) then do not file here. Do not paste
        massive AI generated explanations. We accept the use of AI as long as
        it is digestible. Please explain your issues or improvements briefly
        and clearly in your own human voice.
-->
"""


class MultilineAiPolicy(unittest.TestCase):
    """Issue #9: wrapped contributor instructions retain their source lines."""

    def ai(self, text):
        return _policy_for({".github/pull_request_template.md": text}).get("AI policy")

    def test_curl_actual_policy_is_responsibility_not_a_ban(self):
        finding = self.ai(CURL_TEMPLATE)
        self.assertEqual(finding.verdict,
                         "Allows AI help if you understand and take responsibility for the change.")
        self.assertEqual(len(finding.evidence), 1)
        evidence = finding.evidence[0]
        self.assertEqual(evidence.source, ".github/pull_request_template.md:3-4")
        self.assertEqual(evidence.quote,
                         "If you cannot understand or explain your work without using "
                         "Artificial Intelligence (AI) then do not file here.")
        self.assertIn(evidence.quote, " ".join(
            line.strip() for line in CURL_TEMPLATE.splitlines()[2:4]))

    def test_wrapped_comment_and_source_range(self):
        text = ("# Pull request\n\n<!--\n"
                "If you use AI tools to prepare your contribution,\n"
                "you must understand and be able to explain the change\n"
                "in your own voice.\n-->\n")
        finding = self.ai(text)
        self.assertIn("responsibility", finding.verdict)
        evidence = finding.evidence[0]
        self.assertEqual(evidence.source, ".github/pull_request_template.md:4-6")
        quoted = " ".join(text.splitlines()[3:6])
        self.assertEqual(evidence.quote, quoted[:159] + "…" if len(quoted) > 160 else quoted)

    def test_wrapped_markdown_requirement(self):
        finding = self.ai("If you use AI for contributions, you must\nunderstand the changes.\n")
        self.assertIn("responsibility", finding.verdict)
        self.assertEqual(finding.evidence[0].source, ".github/pull_request_template.md:1-2")
        self.assertEqual(finding.evidence[0].quote,
                         "If you use AI for contributions, you must understand the changes.")

    def test_own_voice_requirement(self):
        self.assertIn("responsibility", self.ai(
            "If you use AI, explain the changes in your own voice.\n").verdict)

    def test_own_human_voice_requirement(self):
        finding = self.ai("If you use AI, please explain the changes in your own human voice.")
        self.assertIn("responsibility", finding.verdict)
        self.assertEqual(finding.evidence[0].source, ".github/pull_request_template.md:1")

    def test_unpunctuated_unrelated_lines(self):
        finding = self.ai("AI tools are useful\nYou must understand the build system\n")
        self.assertEqual(finding.verdict,
                         "Mentions AI but states no clear rule. Disclose AI use to be safe.")

    def test_single_line_rules(self):
        for text, verdict in (("You must understand AI-assisted changes.", "responsibility"),
                              ("You must disclose AI assistance.", "disclose it"),
                              ("Do not submit AI-generated contributions.", "Restricts")):
            with self.subTest(text=text):
                self.assertIn(verdict, self.ai(text).verdict)

    def test_wrapped_ban_and_disclosure(self):
        for text, verdict in (("AI-generated contributions\nare banned.", "Restricts"),
                              ("You must disclose\nAI assistance.", "disclose it")):
            with self.subTest(text=text):
                self.assertIn(verdict, self.ai(text).verdict)

    def test_mentions_and_unrelated_statements(self):
        for text in ("This documentation mentions AI tools.\n",
                     "AI tools are useful.\nYou must understand the build system.\n",
                     "AI tools are useful\n\nYou must understand the build system.\n",
                     "AI tools are useful\n# Development\nYou must understand the build system.\n",
                     "- AI tools are useful\n- You must understand the build system.\n",
                     "<!-- AI tools are useful -->\nYou must understand the build system.\n",
                     "Researchers study AI to\nunderstand language models.\n"):
            with self.subTest(text=text):
                self.assertTrue(self.ai(text).verdict.startswith("Mentions AI"))
