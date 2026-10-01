"""History benchmark: replay past merged changes and check whether the brief finds their files.

For each sampled change we rebuild the brief at its parent commit, using only
history older than the change, and the PR title as a stand-in for the issue.
Titles are shorter and more precise than real issues, so treat scores as an
upper-bound smoke test; the real benchmark uses linked issue text (needs the
GitHub API).
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from . import brief as brief_mod
from .policy import is_code
from .precedent import Change
from .repo import Repo


@dataclass
class Case:
    change: Change
    code_files: list[str]


@dataclass
class Result:
    case: Case
    full_hit: bool
    full_recall: float
    grep_hit: bool
    grep_recall: float


def sample(history: list[Change], n: int, seed: int = 7) -> list[Case]:
    pool = []
    for i, c in enumerate(history[:-50]):  # keep older history as precedent
        code = [f for f in c.files if is_code(f)]
        if c.pr and 1 <= len(code) <= 4 and len(c.title.split()) >= 4 \
                and not c.title.lower().startswith(("bump", "release", "merge", "revert", "update dependencies")):
            pool.append(Case(c, code))
    random.Random(seed).shuffle(pool)
    return pool[:n]


def _score(top: list[str], truth: list[str]) -> tuple[bool, float]:
    found = len(set(top) & set(truth))
    return found > 0, found / len(truth)


def run(repo: Repo, history: list[Change], n: int = 20, k: int = 5, log=print) -> list[Result]:
    results = []
    order = {c.sha: i for i, c in enumerate(history)}
    for case in sample(history, n):
        c = case.change
        parent = f"{c.sha}^1"
        older = history[order[c.sha] + 1:]
        full = brief_mod.build(repo, c.title, "", older, None, rev=parent, top=k)
        grep = brief_mod.build(repo, c.title, "", [], None, rev=parent, top=k)
        fh, fr = _score([h.path for h in full.files], case.code_files)
        gh, gr = _score([h.path for h in grep.files], case.code_files)
        results.append(Result(case, fh, fr, gh, gr))
        log(f"  #{c.pr} {'✓' if fh else '·'} {'✓' if gh else '·'}  {_short(c.title, 70)}")
    return results


def summary(results: list[Result]) -> dict:
    n = len(results) or 1
    return {
        "cases": len(results),
        "brief_hit@5": sum(r.full_hit for r in results) / n,
        "brief_recall@5": sum(r.full_recall for r in results) / n,
        "grep_only_hit@5": sum(r.grep_hit for r in results) / n,
        "grep_only_recall@5": sum(r.grep_recall for r in results) / n,
    }


def _short(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"
