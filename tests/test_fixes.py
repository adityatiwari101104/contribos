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




class Version(unittest.TestCase):
    def test_version_flag(self):
        import contextlib
        import io
        from contribos import __version__, cli
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as cm:
            cli.main(["--version"])
        self.assertEqual(cm.exception.code, 0)
        self.assertIn(__version__, out.getvalue())


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


class ReposThatRefuse(unittest.TestCase):
    """A green check on a repo that will reject the PR is the worst thing this tool can do."""

    def diff(self, commits=None):
        return check.DiffInfo("base", ["a.py"], {}, {}, commits or [("abc", "change", "")], 1, 0)

    def levels(self, items):
        return {i.topic: i.level for i in items}

    def test_closed_repo_fails(self):
        p = _policy_for({"CONTRIBUTING.md": "Pull requests are disabled.\n"})
        items, _ = check.run_checks(self.diff(), [], p, None)
        self.assertEqual(self.levels(items)["Repository"], "fail")

    def test_open_repo_has_no_blocker(self):
        p = _policy_for({"CONTRIBUTING.md": "We welcome pull requests.\n"})
        items, _ = check.run_checks(self.diff(), [], p, None)
        self.assertNotIn("Repository", self.levels(items))

    def test_scoped_refusals_do_not_close_the_repo(self):
        for text in ("We do not accept contributions of AI-generated code.",
                     "We do not accept contributions under the GPL.",
                     "We don't accept pull requests without tests.",
                     "We do not accept PRs generated by bots."):
            with self.subTest(text=text):
                self.assertIsNone(_policy_for({"CONTRIBUTING.md": text + "\n"}).get("Not accepting contributions"))

    def test_real_closures_still_detected(self):
        for text in ("We are not accepting contributions.", "We do not accept pull requests.",
                     "Pull requests are turned off."):
            with self.subTest(text=text):
                self.assertIsNotNone(_policy_for({"CONTRIBUTING.md": text + "\n"}).get("Not accepting contributions"))

    def test_ai_use_is_not_green_when_ai_is_restricted(self):
        p = _policy_for({"CONTRIBUTING.md": "Do not submit AI-generated contributions.\n"})
        item = check.check_disclosure(self.diff(), p, "Claude Code: wrote all of it")
        self.assertEqual(item.level, "warn")
        self.assertIn("restricts", item.message)

    def test_ai_use_is_green_when_ai_is_allowed(self):
        p = _policy_for({"CONTRIBUTING.md": "You must disclose AI assistance.\n"})
        self.assertEqual(check.check_disclosure(self.diff(), p, "Claude Code: tests").level, "ok")

    def test_issue_first_needs_a_link(self):
        p = _policy_for({"CONTRIBUTING.md": "Please open an issue first.\n"})
        self.assertIn("Issue first", self.levels(check.check_requirements(self.diff(), p, None)))
        self.assertNotIn("Issue first", self.levels(check.check_requirements(self.diff(), p, 12)))
        linked = self.diff([("abc", "fix crash (#12)", "")])
        self.assertNotIn("Issue first", self.levels(check.check_requirements(linked, p, None)))

    def test_cla_is_flagged_in_any_case(self):
        for text in ("You must sign the CLA.", "Sign the Contributor License Agreement.",
                     "Sign the contributor license agreement."):
            with self.subTest(text=text):
                p = _policy_for({"CONTRIBUTING.md": text + "\n"})
                self.assertIn("CLA", self.levels(check.check_requirements(self.diff(), p, None)))


class Forks(unittest.TestCase):
    def repo(self):
        def run(cwd, *args):
            subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                           cwd=cwd, check=True, capture_output=True)
        top = Path(tempfile.mkdtemp(prefix="contribos-fork-"))
        up = top / "up"
        up.mkdir()
        run(up, "init", "-q", "-b", "main")
        (up / "a.py").write_text("a\n", encoding="utf-8")
        run(up, "add", "-A")
        run(up, "commit", "-qm", "base")
        run(top, "clone", "-q", str(up), "fork")
        fork = top / "fork"
        for i in range(5):  # the project moves on; the fork doesn't
            (up / f"f{i}.py").write_text("x\n", encoding="utf-8")
            run(up, "add", "-A")
            run(up, "commit", "-qm", f"up {i}")
        run(fork, "remote", "set-url", "origin", "https://github.com/me/proj.git")
        run(fork, "remote", "add", "upstream", str(up))
        run(fork, "fetch", "-q", "upstream")
        run(fork, "checkout", "-q", "-b", "feat", "upstream/main")
        (fork / "mine.py").write_text("mine\n", encoding="utf-8")
        run(fork, "add", "-A")
        run(fork, "commit", "-qm", "mine")
        return fork, run

    def test_diff_is_against_upstream_not_a_stale_fork(self):
        fork, _ = self.repo()
        repo = check.Repo("me", "proj", fork)
        diff = check.read_diff(repo, check.find_base(repo, None))
        self.assertEqual(diff.files, ["mine.py"])
        self.assertEqual(len(diff.commits), 1)

    def test_slug_is_the_upstream_project(self):
        fork, run = self.repo()
        run(fork, "remote", "set-url", "upstream", "https://github.com/acme/proj.git")
        self.assertEqual(check.local_repo(str(fork)).slug, "acme/proj")

    def test_no_upstream_uses_origin(self):
        fork, run = self.repo()
        run(fork, "remote", "remove", "upstream")
        self.assertEqual(check.local_repo(str(fork)).slug, "me/proj")


class ReviewRequests(unittest.TestCase):
    def test_polite_requests_are_not_praise(self):
        from contribos import review
        for text, kind in (("Thanks for the PR. Please add tests and fix the failing build.", "change"),
                           ("Nice work, but this breaks Windows and must be fixed before merge.", "blocking"),
                           ("Looks good, but could you rename this?", "change")):
            with self.subTest(text=text):
                self.assertEqual(review.classify(text), kind)

    def test_real_praise_stays_praise(self):
        from contribos import review
        for text in ("LGTM", "Thanks!", "Looks good to me.", "Thanks for the fix!", "Nice use of a dict here."):
            with self.subTest(text=text):
                self.assertEqual(review.classify(text), "praise")

    def test_review_summary_is_read(self):
        from contribos import review
        rv = [{"state": "CHANGES_REQUESTED", "user": {"login": "maint"}, "body": "Thanks, but the build fails.",
               "submitted_at": "2026-10-01T00:00:00Z"}]
        items, notes = review.analyze("me", [], [], rv)
        self.assertEqual(len(items), 1)
        self.assertFalse(items[0].answered)
        self.assertTrue(any("still need your reply" in n for n in notes))

    def test_own_and_empty_reviews_are_ignored(self):
        from contribos import review
        rv = [{"state": "COMMENTED", "user": {"login": "me"}, "body": "note", "submitted_at": "2026-10-01T00:00:00Z"},
              {"state": "APPROVED", "user": {"login": "maint"}, "body": "", "submitted_at": "2026-10-01T00:00:00Z"}]
        self.assertEqual(review.analyze("me", [], [], rv)[0], [])


class Cache(unittest.TestCase):
    def setUp(self):
        from contribos import repo
        self.repo = repo
        self.old = repo.CACHE_DIR
        repo.CACHE_DIR = Path(tempfile.mkdtemp(prefix="contribos-cache-"))
        self.path = repo.CACHE_DIR / "repos" / "o__r"

    def tearDown(self):
        self.repo.CACHE_DIR = self.old

    def clone(self):
        self.path.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.path)], check=True)
        subprocess.run(["git", "-C", str(self.path), "-c", "user.name=t", "-c", "user.email=t@t",
                        "commit", "-q", "--allow-empty", "-m", "x"], check=True)

    def age(self, seconds):
        import os
        import time
        marker = self.path / ".git" / "HEAD"
        then = time.time() - seconds
        os.utime(marker, (then, then))

    def fetched(self, **kw):
        from unittest import mock
        with mock.patch.object(self.repo, "_fetch") as f:
            self.repo.open_repo("o/r", **kw)
        return f.called

    def test_old_cache_refreshes(self):
        self.clone()
        self.age(2 * self.repo.MAX_AGE)
        self.assertTrue(self.fetched())

    def test_recent_cache_does_not(self):
        self.clone()
        self.assertFalse(self.fetched())

    def test_offline_never_refreshes(self):
        self.clone()
        self.age(2 * self.repo.MAX_AGE)
        self.assertFalse(self.fetched(offline=True))

    def test_failed_refresh_keeps_the_cache(self):
        from unittest import mock
        self.clone()
        self.age(2 * self.repo.MAX_AGE)
        with mock.patch.object(self.repo, "_fetch", side_effect=RuntimeError("no network")):
            self.assertEqual(self.repo.open_repo("o/r").path, self.path)

    def test_broken_clone_is_replaced(self):
        from unittest import mock
        (self.path / ".git").mkdir(parents=True)
        (self.path / ".git" / "config").write_text("[core]\n", encoding="utf-8")

        def fake_clone(cmd, **kw):
            Path(cmd[-1], ".git").mkdir(parents=True)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        with mock.patch.object(self.repo.subprocess, "run", side_effect=fake_clone) as run:
            with mock.patch.object(self.repo, "git", return_value=""):
                self.repo.open_repo("o/r")
        self.assertEqual(run.call_args[0][0][1], "clone")
        self.assertFalse((self.path / ".git" / "config").exists())


class RuleFiles(unittest.TestCase):
    def seen(self, path):
        return bool(_policy_for({path: "Rules.\n"}).docs)

    def test_common_locations_are_read(self):
        for path in ("docs/CONTRIBUTE.md", "CONTRIBUTING_GUIDE.md", "docs/contributing/testing.md",
                     ".github/PULL_REQUEST_TEMPLATE/feature.md", "llvm/docs/AIToolPolicy.md",
                     "AI_POLICY.md", "docs/intro/contributing.txt", "HACKING"):
            with self.subTest(path=path):
                self.assertTrue(self.seen(path))

    def test_other_files_are_not(self):
        for path in ("tests/fixtures/CONTRIBUTING.md", "src/dev.py", "src/contributing.py",
                     "CONTRIBUTORS.md", "vendor/x/CONTRIBUTING.md", "examples/app/CONTRIBUTING.md"):
            with self.subTest(path=path):
                self.assertFalse(self.seen(path))

    def test_ai_rule_in_a_newly_read_file_is_found(self):
        p = _policy_for({"docs/CONTRIBUTE.md": "Do not submit AI-generated contributions.\n"})
        self.assertTrue(p.get("AI policy").verdict.startswith("Restricts"))


class ClaimRule(unittest.TestCase):
    def found(self, text):
        return _policy_for({"CONTRIBUTING.md": text + "\n"}).get("Claim first") is not None

    def test_real_rules(self):
        for text in ("Get assigned before you open a PR.", "Comment on the issue first.",
                     "Learn how to claim tickets for yourself.", "Please claim an issue.",
                     "Ask before you start working on a feature.", "Discuss it before you start writing code.", "Talk to us before starting work.",
                     "Open an issue first."):
            with self.subTest(text=text):
                self.assertTrue(self.found(text))

    def test_not_a_claim_rule(self):
        for text in ("Read this file before you start sending patches.",
                     "Performance claims need benchmarks.", "We never claim copyright."):
            with self.subTest(text=text):
                self.assertFalse(self.found(text))


if __name__ == "__main__":
    unittest.main()
