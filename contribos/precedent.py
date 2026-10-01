"""Precedent index: what changes landed on the main line, and which files they touched.

Built from `git log --first-parent`, so each squash-merge or merge commit is
one entry. PR numbers come from the usual "Title (#123)" and
"Merge pull request #123 from ..." conventions.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

from .repo import CACHE_DIR, Repo

REC, UNIT = "\x1e", "\x1f"
PR_SUFFIX = re.compile(r"\(#(\d+)\)\s*$")
PR_MERGE = re.compile(r"^Merge pull request #(\d+) from ([^/\s]+)")
ISSUE_REF = re.compile(r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)", re.I)


@dataclass
class Change:
    sha: str
    date: int
    author: str
    title: str
    body: str
    pr: int | None
    files: list[str] = field(default_factory=list)
    issues: list[int] = field(default_factory=list)


def parse_log(text: str) -> list[Change]:
    changes = []
    for rec in text.split(REC):
        if not rec.strip():
            continue
        header, _, names = rec.partition("\x1d")
        parts = header.split(UNIT)
        if len(parts) < 5:
            continue
        sha, date, author, subject, body = parts[:5]
        pr = None
        title = subject.strip()
        if m := PR_MERGE.match(title):
            pr = int(m.group(1))
            author = m.group(2)  # the PR author, not the maintainer who merged
            # Merge commits keep the PR title on the first body line.
            first = body.strip().splitlines()[0] if body.strip() else title
            title = first.strip()
        elif m := PR_SUFFIX.search(title):
            pr = int(m.group(1))
            title = PR_SUFFIX.sub("", title).strip()
        files = [f for f in names.splitlines() if f.strip()]
        issues = sorted({int(n) for n in ISSUE_REF.findall(f"{subject}\n{body}")})
        changes.append(Change(sha, int(date), author, title, body.strip(), pr, files, issues))
    return changes


def read_history(repo: Repo, rev: str = "HEAD", limit: int = 3000) -> list[Change]:
    fmt = f"{REC}%H{UNIT}%at{UNIT}%an{UNIT}%s{UNIT}%b\x1d"
    out = repo.git("log", rev, "--first-parent", "-m", "--name-only",
                   f"--max-count={limit}", f"--format={fmt}")
    return parse_log(out)


class PrecedentIndex:
    """SQLite cache of the main-line history of one repository."""

    def __init__(self, repo: Repo):
        self.repo = repo
        db_dir = CACHE_DIR / "index"
        db_dir.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(db_dir / f"{repo.owner}__{repo.name}.sqlite")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS change(
                sha TEXT PRIMARY KEY, date INTEGER, author TEXT, title TEXT,
                body TEXT, pr INTEGER, issues TEXT);
            CREATE TABLE IF NOT EXISTS touched(sha TEXT, path TEXT);
            CREATE INDEX IF NOT EXISTS touched_path ON touched(path);
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
        """)

    def build(self, rev: str = "HEAD", limit: int = 3000) -> int:
        head = self.repo.git("rev-parse", rev).strip()
        row = self.db.execute("SELECT value FROM meta WHERE key='head'").fetchone()
        if row and row[0] == head:
            return self.db.execute("SELECT COUNT(*) FROM change").fetchone()[0]
        changes = read_history(self.repo, rev, limit)
        with self.db:
            self.db.execute("DELETE FROM change")
            self.db.execute("DELETE FROM touched")
            for c in changes:
                self.db.execute("INSERT OR REPLACE INTO change VALUES (?,?,?,?,?,?,?)",
                                (c.sha, c.date, c.author, c.title, c.body, c.pr,
                                 ",".join(map(str, c.issues))))
                self.db.executemany("INSERT INTO touched VALUES (?,?)",
                                    [(c.sha, f) for f in c.files])
            self.db.execute("INSERT OR REPLACE INTO meta VALUES ('head', ?)", (head,))
        return len(changes)

    def changes(self, before: int | None = None) -> list[Change]:
        q = "SELECT sha, date, author, title, body, pr, issues FROM change"
        args: tuple = ()
        if before is not None:
            q += " WHERE date < ?"
            args = (before,)
        out = []
        for sha, date, author, title, body, pr, issues in self.db.execute(q, args):
            files = [p for (p,) in self.db.execute(
                "SELECT path FROM touched WHERE sha=?", (sha,))]
            out.append(Change(sha, date, author, title, body, pr, files,
                              [int(i) for i in issues.split(",") if i]))
        return out
