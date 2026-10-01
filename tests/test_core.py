import unittest

from contribos.brief import extract_terms
from contribos.policy import is_code, is_test
from contribos.precedent import parse_log
from contribos.repo import parse_repo

REC, UNIT = "\x1e", "\x1f"


def rec(sha, subject, body="", files=()):
    return f"{REC}{sha}{UNIT}1700000000{UNIT}alice{UNIT}{subject}{UNIT}{body}\x1d\n" + "\n".join(files)


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


if __name__ == "__main__":
    unittest.main()
