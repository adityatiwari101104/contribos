#!/usr/bin/env python3
"""Apply the ContribOS fixes. Run from the repo root:  python apply_fixes.py

Safe: checks every edit first and changes nothing if any check fails.
"""
import re
import sys
from pathlib import Path

EDITS = [
    ('contribos/check.py',
     r'''    url = git(root, "remote", "get-url", "origin", check=False).strip()
    try:
        owner, name = parse_repo(url)''',
     r'''    if not git(root, "rev-parse", "--verify", "--quiet", "HEAD", check=False).strip():
        raise RuntimeError(f"{root} has no commits yet. ContribOS reads repo history and rules, so "
                           "clone an existing project (git clone <url>) or make a first commit, then try again.")
    url = git(root, "remote", "get-url", "origin", check=False).strip()
    try:
        owner, name = parse_repo(url)'''),
    ('contribos/cli.py',
     r'''    print(f"# Contribution policy: {repo.slug}\n")
    for f in p.findings:''',
     r'''    print(f"# Contribution policy: {repo.slug}\n")
    degraded = [n for n in p.notes if n.startswith("GitHub API unavailable")]
    if degraded:
        print("> **Partial result.** " + degraded[0] + " Pull-request signals (merge rates, review norms, "
              "claim rules seen in PRs) are missing. Set `GITHUB_TOKEN` and re-run for the full picture.\n")
    for f in p.findings:'''),
    ('contribos/cli.py',
     r'''    for n in p.notes:
        print(f"\n_Note: {n}_")
    return 0


def _issue_text''',
     r'''    for n in p.notes:
        if n not in degraded:
            print(f"\n_Note: {n}_")
    return 0


def _issue_text'''),
    ('contribos/cli.py',
     r'''                             + ". Set GITHUB_TOKEN, or pass --title and --body-file instead.")''',
     r'''                             + ". Set GITHUB_TOKEN (unauthenticated requests are rate-limited), or pass "
                               "--title \"...\" and --body-file issue.md and the brief runs without the API.")'''),
    ('contribos/brief.py',
     r'''    terms = extract_terms(f"{title}\n{title}\n{body}")
    all_files''',
     r'''    terms = extract_terms(f"{title}\n{title}\n{body}", limit=30)
    # The repo's own name says nothing about where a change belongs: every path and file matches it.
    own = {t.lower() for t in re.split(r"[^A-Za-z0-9]+", f"{repo.owner} {repo.name}") if len(t) > 2}
    terms = [t for t in terms if t.text.lower() not in own][:20]
    all_files'''),
    ('contribos/brief.py',
     r'''            h.score += 2.0 * sum(1 for _ in matched)
            h.reasons.append("path matches " + ", ".join(f"'{m}'" for m in matched[:3]))''',
     r'''            h.score += 2.0 * sum(1 for _ in matched)
            stem = p.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
            if stem in {t.text.lower() for t in terms}:
                h.score += 6.0  # the issue names this file or module outright
            h.reasons.append("path matches " + ", ".join(f"'{m}'" for m in matched[:3]))'''),
]

NEW_FILES = {
    'tests/test_fixes.py': r'''"""Regression tests for fixes found while using ContribOS as an outside contributor."""
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
''',
}


def main():
    root = Path.cwd()
    if not (root / "contribos" / "brief.py").exists():
        sys.exit("Run this from the contribos repo root (the folder containing the 'contribos' folder).")
    pending = {}
    for rel, old, new in EDITS:
        path = root / rel
        if rel not in pending:
            with open(path, encoding="utf-8", newline="") as fh:
                raw = fh.read()
            pending[rel] = ("\r\n" in raw, raw.replace("\r\n", "\n"))
        crlf, text = pending[rel]
        if text.count(old) != 1:
            sys.exit(f"Could not apply an edit to {rel} (found {text.count(old)} matches, expected 1). "
                     "Nothing was changed. Is your clone up to date with origin/main?")
        pending[rel] = (crlf, text.replace(old, new, 1))
    for rel, (crlf, text) in pending.items():
        if rel.endswith("brief.py") and not re.search(r"^import re$", text, re.M):
            text = text.replace("import math\n", "import math\nimport re\n", 1)
        if crlf:
            text = text.replace("\n", "\r\n")
        with open(root / rel, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        print("edited ", rel)
    for rel, content in NEW_FILES.items():
        path = root / rel
        if path.exists():
            print("skipped", rel, "(already exists)")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
        print("created", rel)
    print("Done. Now run: python -m unittest discover -s tests")


main()
