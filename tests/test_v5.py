"""Tests for v5: policy radar v2, tone, ask-first claim, proof, disclosure, duplicates, agent rules."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from contribos import agent_rules, check, landed, mcp_server, policy, proof, setup_doctor, tone
from contribos.cli import main
from contribos.claim import draft
from contribos.repo import Repo


def make_repo(files: dict[str, str]) -> Repo:
    root = Path(tempfile.mkdtemp(prefix="contribos-test-"))
    for path, text in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text)
    for cmd in (["init", "-q", "-b", "main"], ["add", "-A"],
                ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base"]):
        subprocess.run(["git", "-C", str(root), *cmd], check=True)
    return Repo("o", "r", root)


def commit(repo: Repo, files: dict[str, str], msg: str = "change") -> None:
    for path, text in files.items():
        (repo.path / path).parent.mkdir(parents=True, exist_ok=True)
        (repo.path / path).write_text(text)
    subprocess.run(["git", "-C", str(repo.path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo.path), "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-qm", msg], check=True)


POLICY_DOCS = {
    "AI_POLICY.md": "# AI policy\n- All AI usage must be disclosed with an `Assisted-by:` trailer naming the AI tool.\n"
                    "- AI must not be used on good first issues; those are for people learning.\n",
    "CONTRIBUTING.md": "Please open an issue first before sending a PR.\nWe use a vouch system for new contributors.\n"
                       "All commits need Signed-off-by (DCO).\n",
    ".github/VOUCHED.td": "alice\n",
    "src/app.py": "def add(a, b):\n    return a - b\n",
    "tests/test_app.py": "",
}


class PolicyV2(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo(POLICY_DOCS)
        self.p = policy.analyze(self.repo, [])

    def test_structured_rules(self):
        s = policy.structured(self.p)
        self.assertEqual(s["ai"]["disclosure_trailer"], "Assisted-By")
        self.assertTrue(s["ai"]["no_ai_on_good_first_issues"])
        self.assertTrue(s["trust_gate"])
        self.assertTrue(s["issue_first"])
        self.assertTrue(s["dco"])
        json.dumps(s)  # serialisable for agents

    def test_vouch_evidence_includes_docs(self):
        gate = self.p.get("Trust gate")
        self.assertTrue(any("CONTRIBUTING.md" in e.source for e in gate.evidence))


class Tone(unittest.TestCase):
    def test_flags_machine_tells(self):
        r = tone.check("Certainly! I'd love to delve into this robust issue.")
        self.assertGreaterEqual(len(r.flags), 2)
        self.assertTrue(any("question" in t for t in r.tips))

    def test_plain_question_passes(self):
        r = tone.check("Hi, I'd like to fix this in parser.py. Should empty input raise or return None?")
        self.assertEqual((r.flags, r.tips), ([], []))


class ClaimV2(unittest.TestCase):
    def test_vouch_intro_and_gfi_warning(self):
        p = policy.analyze(make_repo(POLICY_DOCS), [])
        text = draft("Fix add()", None, p, None, ["good first issue"])
        self.assertIn("get vouched", text)
        self.assertIn("don't use AI", text)
        self.assertIn("`Assisted-By:`", text)


class Proof(unittest.TestCase):
    def test_test_fails_without_fix_and_files_restored(self):
        repo = make_repo({"src/app.py": "def add(a, b):\n    return a - b\n",
                          "tests/test_app.py": "import sys; sys.path.insert(0, 'src')\nfrom app import add\n"
                                               "assert add(2, 2) == 4\n"})
        base = repo.git("rev-parse", "HEAD").strip()
        commit(repo, {"src/app.py": "def add(a, b):\n    return a + b\n"}, "fix add")
        res = proof.verify(repo, base, ["src/app.py"], f"{sys.executable} tests/test_app.py")
        self.assertTrue(res.proves)
        self.assertIn("a + b", (repo.path / "src/app.py").read_text())  # restored

    def test_test_that_always_passes_proves_nothing(self):
        repo = make_repo({"src/app.py": "x = 1\n", "t.py": "pass\n"})
        base = repo.git("rev-parse", "HEAD").strip()
        commit(repo, {"src/app.py": "x = 2\n"})
        res = proof.verify(repo, base, ["src/app.py"], f"{sys.executable} t.py")
        self.assertFalse(res.proves)
        self.assertIn("doesn't prove", proof.summary_lines(res)[0])


class Disclosure(unittest.TestCase):
    def test_missing_trailer_fails(self):
        p = policy.analyze(make_repo(POLICY_DOCS), [])
        d = check.DiffInfo("b", ["src/app.py"], {}, {}, [("abc12345", "fix", "no trailer")], 1, 0)
        item = check.check_disclosure(d, p, "Claude Code: drafted the test")
        self.assertEqual(item.level, "fail")
        d.commits = [("abc12345", "fix", "Assisted-by: Claude Code")]
        self.assertNotEqual(check.check_disclosure(d, p, "Claude Code").level, "fail")


class CommitParsing(unittest.TestCase):
    def test_commit_without_body_is_seen(self):
        repo = make_repo({"src/app.py": "x = 1\n"})
        base = repo.git("rev-parse", "HEAD").strip()
        commit(repo, {"src/app.py": "x = 2\n"}, "subject only")
        d = check.read_diff(repo, base)
        self.assertEqual([c[1] for c in d.commits], ["subject only"])


class EndToEndFixes(unittest.TestCase):
    def test_backticked_trailer_rule_detected(self):
        repo = make_repo({".github/pull_request_template.md":
                          "- [ ] If AI agents were used, they are credited in `Co-authored-by` commit trailers.\n"})
        s = policy.structured(policy.analyze(repo, []))
        self.assertEqual(s["ai"]["disclosure_trailer"], "Co-Authored-By")

    def test_pip_version_error_diagnosed(self):
        d = setup_doctor.diagnose("ERROR: Could not find a version that satisfies the requirement greenlet==3.0")
        self.assertEqual(d[0].kind, "Package version not available")

    def test_user_errors_are_messages_not_tracebacks(self):
        self.assertEqual(main(["--offline", "policy", "not-a-repo"]), 2)
        self.assertEqual(main(["tone", "/nonexistent-contribos.md"]), 2)
        self.assertEqual(main(["--offline", "find", "--lang", "python"]), 2)

    def test_mcp_tools_take_text(self):
        names = [t["name"] for t in mcp_server.handle({"id": 1, "method": "tools/list"})["result"]["tools"]]
        self.assertIn("contribos_tone", names)
        self.assertIn("contribos_claim", names)
        r = mcp_server.handle({"id": 2, "method": "tools/call", "params": {
            "name": "contribos_diagnose", "arguments": {"log": "ModuleNotFoundError: No module named 'blinker'"}}})
        self.assertIn("blinker", r["result"]["content"][0]["text"])
        self.assertEqual(mcp_server._TEMPS, [])


class Landed(unittest.TestCase):
    PRS = [{"number": 41, "title": "fix(cluster): keep hub-only neighbours with their hub",
            "created_at": "2020-01-01T00:00:00Z", "user": {"login": "neo"}},
           {"number": 42, "title": "Add retry to the fetch loop", "created_at": "2020-01-01T00:00:00Z",
            "user": {"login": "amy"}},
           {"number": 43, "title": "Update README.md", "created_at": "2020-01-01T00:00:00Z", "user": {"login": "bo"}},
           {"number": 44, "title": "never landed anywhere at all", "created_at": "2020-01-01T00:00:00Z",
            "user": {"login": "cy"}},
           {"number": 45, "title": "x", "merged_at": "2020-01-02T00:00:00Z", "created_at": "2020-01-01T00:00:00Z"}]

    def test_cherry_picks_count_as_landed(self):
        repo = make_repo({"a.txt": "1\n"})
        commit(repo, {"a.txt": "2\n"}, "fix(cluster): keep hub-only neighbours with their hub")
        commit(repo, {"a.txt": "3\n"}, "Retry fetches on timeout (#42)")
        commit(repo, {"a.txt": "4\n"}, "Update README.md")  # too generic to count as #43
        total, merged, other = landed.summarize(self.PRS, repo=repo)
        self.assertEqual(sorted(other), [41, 42])
        self.assertEqual((total, merged), (3, 1))
        self.assertIn("cherry-pick", landed.verdict(5, total, merged, other, "PRs"))

    def test_via_api(self):
        class GH:
            def get(self, path, params):
                if params["author"] == "neo":
                    return [{"sha": "abcdef123", "commit": {"message": "fix(cluster): keep hub-only neighbours "
                                                                       "with their hub\n\nbody"}}]
                return []
        self.assertEqual(landed.via_api(GH(), "o", "r", self.PRS), {41: "abcdef1"})


class Duplicates(unittest.TestCase):
    def test_competing_pr_found(self):
        class GH:
            available = True

            def pulls(self, *a, **k):
                return [{"number": 7, "title": "Fix add", "body": "Closes #12", "user": {"login": "bob"},
                         "html_url": "u7"},
                        {"number": 8, "title": "Docs", "body": "", "user": {"login": "me"}, "html_url": "u8"}]

            def me(self):
                return "me"
        item = check.check_duplicates(GH(), Repo("o", "r", Path(".")), 12, "Fix add returning difference")
        self.assertEqual(item.level, "fail")
        self.assertTrue(any("#7" in d for d in item.details))
        self.assertTrue(any("open PR" in d for d in item.details))


class AgentRules(unittest.TestCase):
    def test_install_is_local_only_and_hooks_work(self):
        repo = make_repo(POLICY_DOCS)
        p = policy.analyze(repo, [])
        written = agent_rules.install(repo.path, p, None, "Claude Code")
        self.assertIn(".claude/skills/contribos/SKILL.md", written)
        status = repo.git("status", "--short")
        self.assertEqual(status.strip(), "")  # nothing leaks into the contribution
        skill = (repo.path / ".claude/skills/contribos/SKILL.md").read_text()
        self.assertIn("Never open pull requests", skill)
        self.assertIn("Assisted-By", skill)
        guard = subprocess.run([sys.executable, str(repo.path / ".contribos/guard.py")],
                               input=json.dumps({"tool_input": {"command": "gh pr create --fill"}}),
                               text=True, capture_output=True)
        self.assertEqual(guard.returncode, 2)
        bad = subprocess.run(["git", "-C", str(repo.path), "-c", "user.name=t", "-c", "user.email=t@t",
                              "commit", "--allow-empty", "-qm", "no signoff"], capture_output=True, text=True)
        self.assertNotEqual(bad.returncode, 0)
        good = subprocess.run(["git", "-C", str(repo.path), "-c", "user.name=t", "-c", "user.email=t@t",
                               "commit", "--allow-empty", "-s", "-qm", "fix\n\nAssisted-By: Claude Code"],
                              capture_output=True, text=True)
        self.assertEqual(good.returncode, 0, good.stderr)


if __name__ == "__main__":
    unittest.main()
