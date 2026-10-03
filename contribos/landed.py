"""Did a closed PR land anyway?

Many maintainers cherry-pick, rebase or squash a PR locally, push it, and
close the PR. GitHub then shows it as closed, not merged, so counting only
`merged_at` makes a welcoming repo look like it rejects everyone. A closed PR
counts as landed when a commit made after it was opened carries its title or
its number "(#123)".
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from .github import GitHub
from .repo import Repo


def norm_title(title: str) -> str:
    title = re.sub(r"\s*\(#\d+\)\s*$", "", title or "")
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def _specific(title: str) -> bool:
    # "Update README.md" or "fix typo" would match unrelated commits.
    return len(title) >= 20 and len(title.split()) >= 4


def _ts(iso: str | None) -> float:
    if not iso:
        return 0.0
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def _match(pr: dict, subject: str, when: float) -> bool:
    if when and when < _ts(pr.get("created_at")) - 86400:
        return False
    if re.search(rf"#{pr['number']}\b", subject):
        return True
    t = norm_title(pr.get("title", ""))
    return _specific(t) and norm_title(subject) == t


def in_git(repo: Repo, prs: list[dict]) -> dict[int, str]:
    """PR number -> short sha, for closed PRs whose change is in any branch of the local clone."""
    prs = [p for p in prs if "number" in p]
    if not prs:
        return {}
    oldest = min(_ts(p.get("created_at")) for p in prs) - 86400
    since = datetime.fromtimestamp(oldest, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    log = repo.git("log", "--all", f"--since={since}", "--format=%h\x1f%ct\x1f%s\x1e")
    commits = []
    for rec in log.split("\x1e"):
        parts = rec.strip("\n").split("\x1f")
        if len(parts) == 3:
            commits.append((parts[0], float(parts[1] or 0), parts[2]))
    found = {}
    for p in prs:
        hit = next((sha for sha, when, subj in commits if _match(p, subj, when)), None)
        if hit:
            found[p["number"]] = hit
    return found


def via_api(gh: GitHub, owner: str, name: str, prs: list[dict], limit: int = 10) -> dict[int, str]:
    """Same check without a clone: the PR author's commits on the default branch since the PR opened."""
    found = {}
    for p in [p for p in prs if "number" in p and (p.get("user") or {}).get("login")][:limit]:
        commits = gh.get(f"/repos/{owner}/{name}/commits",
                         {"author": p["user"]["login"], "since": p.get("created_at", ""), "per_page": 20}) or []
        for c in commits:
            subject = ((c.get("commit") or {}).get("message") or "").split("\n", 1)[0]
            if _match(p, subject, 0):
                found[p["number"]] = c.get("sha", "")[:7]
                break
    return found


def summarize(closed_outside: list[dict], repo: Repo | None = None, gh: GitHub | None = None,
              owner: str = "", name: str = "") -> tuple[int, int, dict[int, str]]:
    """(landed, merged on GitHub, {number: sha} landed some other way)."""
    merged = [p for p in closed_outside if p.get("merged_at")]
    rest = [p for p in closed_outside if not p.get("merged_at")]
    other = {}
    if rest and repo is not None:
        other = in_git(repo, rest)
    elif rest and gh is not None:
        other = via_api(gh, owner, name, rest)
    return len(merged) + len(other), len(merged), other


def verdict(total: int, landed: int, merged: int, other: dict[int, str], noun: str) -> str:
    text = f"{landed} of the last {total} closed {noun} landed"
    if other:
        text += (f" ({merged} merged on GitHub, {len(other)} applied by a maintainer another way, such as a "
                 "cherry-pick, which GitHub shows as closed)")
    return text + "."
