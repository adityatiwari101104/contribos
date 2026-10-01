import unittest

from contribos.check import DiffInfo, check_understanding, similar_changes
from contribos.precedent import Change


def diff(files, functions=None, added=None):
    return DiffInfo("base", files, added or {}, functions or {}, [], 10, 0)


class Understanding(unittest.TestCase):
    def test_prompts_without_explanation(self):
        item = check_understanding(diff(["src/app.py"], {"src/app.py": ["run"]}), None)
        self.assertEqual(item.level, "warn")
        self.assertTrue(any("`run` in `src/app.py`" in d for d in item.details))

    def test_gaps_are_named(self):
        d = diff(["src/app.py", "src/cli.py", "tests/test_app.py"])
        item = check_understanding(d, "I changed app.py so it works.")
        self.assertEqual(item.level, "warn")
        joined = " ".join(item.details)
        self.assertIn("src/cli.py", joined)
        self.assertIn("tests", joined)

    def test_complete_explanation_passes(self):
        d = diff(["src/app.py", "tests/test_app.py"], {"src/app.py": ["run"]})
        text = ("run() in app.py crashed when the config was empty because it indexed the first key. "
                "The fix checks for an empty config before indexing, which is the only place the key "
                "is read. The new test starts the app with an empty config and fails on main.")
        self.assertEqual(check_understanding(d, text).level, "ok")


class Similar(unittest.TestCase):
    def test_sweeping_changes_ignored(self):
        focused = Change("a", 1, "x", "fix", "", 1, ["src/app.py", "tests/test_app.py"])
        sweeping = Change("b", 1, "x", "reformat", "", 2, [f"src/m{i}.py" for i in range(30)] + ["src/app.py"])
        got = similar_changes(diff(["src/app.py"]), [sweeping, focused])
        self.assertEqual([c.sha for c in got], ["a"])


if __name__ == "__main__":
    unittest.main()
