import unittest

from contribos.brief import extract_terms, _scope_hint_from_sizes
from contribos.policy import is_code, is_test
from contribos.precedent import Change, parse_log
from contribos.repo import parse_repo

REC, UNIT = "\x1e", "\x1f"


def rec(sha, subject, body="", files=()):
    return f"{REC}{sha}{UNIT}1700000000{UNIT}alice{UNIT}{subject}{UNIT}{body}\x1d\n" + "\n".join(files)


def _make_precedents(file_lists):
    """Build a list of (score, Change) tuples for scope hint testing."""
    result = []
    for i, files in enumerate(file_lists):
        c = Change(sha=f"s{i}", date=1700000000, author="a", title="t", body="", pr=None, files=files)
        result.append((1.0, c))
    return result


class ParseRepo(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(parse_repo("pallets/flask"), ("pallets", "flask"))
        self.assertEqual(parse_repo("https://github.com/pallets/flask/issues/12"), ("pallets", "flask"))
        self.assertEqual(parse_repo("git@github.com:pallets/flask.git"), ("pallets", "flask"))
        with self.assertRaises(ValueError):
            parse_repo("not a repo")


class ParseLog(unittest.TestCase):
    def test_squash_and_merge(self):
        log = rec("a1", "Fix nested blueprints (#4935)", "Fixes #4900", ["src/x.py", "tests/test_x.py"]) + \
              rec("b2", "Merge pull request #12 from bob/fix", "Handle empty config", ["cfg.py"])
        a, b = parse_log(log)
        self.assertEqual((a.pr, a.title, a.issues), (4935, "Fix nested blueprints", [4900]))
        self.assertEqual(a.files, ["src/x.py", "tests/test_x.py"])
        self.assertEqual((b.pr, b.title, b.author), (12, "Handle empty config", "bob"))


class Terms(unittest.TestCase):
    def test_identifiers_rank_above_words(self):
        terms = extract_terms("`url_prefix` is ignored in Blueprint.register when nested")
        texts = [t.text for t in terms]
        self.assertIn("url_prefix", texts)
        self.assertIn("register", texts)
        self.assertLess(texts.index("url_prefix"), texts.index("nested"))


class FileKinds(unittest.TestCase):
    def test_kinds(self):
        self.assertTrue(is_test("tests/test_app.py"))
        self.assertTrue(is_test("src/app.spec.ts"))
        self.assertFalse(is_code("tests/test_app.py"))
        self.assertTrue(is_code("src/flask/app.py"))
        self.assertFalse(is_code("README.md"))


class ScopeHint(unittest.TestCase):
    """Issue #5: scope hint should only appear when backed by >= 3 precedents."""

    def test_no_hint_with_fewer_than_3_precedents(self):
        """With 2 similar past changes, scope_hint must be None."""
        precedents = _make_precedents([
            ["src/a.py", "src/b.py"],
            ["src/c.py"],
        ])
        from contribos.brief import build as _build  # noqa: import guard
        # We test the helper directly
        hint = _scope_hint_from_sizes(precedents)
        self.assertIsNone(hint)

    def test_no_hint_with_zero_precedents(self):
        """With no similar past changes, scope_hint must be None."""
        hint = _scope_hint_from_sizes([])
        self.assertIsNone(hint)

    def test_hint_with_3_or_more_precedents(self):
        """With 3 similar past changes, hint appears and mentions the count."""
        precedents = _make_precedents([
            ["src/a.py", "src/b.py"],
            ["src/c.py", "src/d.py", "src/e.py"],
            ["src/f.py"],
        ])
        hint = _scope_hint_from_sizes(precedents)
        self.assertIsNotNone(hint)
        self.assertIn("based on 3 similar changes", hint)

    def test_hint_with_exactly_3_precedents_shows_median(self):
        """Median of [1, 2, 3] sorted is 2; hint text must reflect that."""
        precedents = _make_precedents([
            ["src/a.py"],
            ["src/b.py", "src/c.py"],
            ["src/d.py", "src/e.py", "src/f.py"],
        ])
        hint = _scope_hint_from_sizes(precedents)
        self.assertIsNotNone(hint)
        self.assertIn("median of 2 code files", hint)


if __name__ == "__main__":
    unittest.main()
