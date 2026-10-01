"""Local git access: clone/cache a repository and read it at any revision.

Everything here works from a plain git clone, so ContribOS runs without
GitHub API access. The API (see github.py) only adds extra evidence.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

CACHE_DIR = Path(os.environ.get("CONTRIBOS_CACHE", Path.home() / ".cache" / "contribos"))


def parse_repo(spec: str) -> tuple[str, str]:
    """Accept 'owner/name', a github.com URL, or an issue/PR URL."""
    m = re.search(r"github\.com[/:]([^/\s]+)/([^/\s#?]+)", spec)
    if m:
        owner, name = m.group(1), m.group(2)
    elif re.fullmatch(r"[\w.-]+/[\w.-]+", spec):
        owner, name = spec.split("/")
    else:
        raise ValueError(f"not a GitHub repo: {spec!r}. Use owner/name or a github.com URL.")
    return owner, name.removesuffix(".git")


def git(repo_dir: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_dir), *args],
        capture_output=True, text=True, errors="replace",
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


@dataclass
class Repo:
    owner: str
    name: str
    path: Path

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.name}"

    def git(self, *args: str, check: bool = True) -> str:
        return git(self.path, *args, check=check)

    def head(self) -> str:
        return self.git("rev-parse", "HEAD").strip()

    def files(self, rev: str = "HEAD") -> list[str]:
        return self.git("ls-tree", "-r", "--name-only", rev).splitlines()

    def read(self, path: str, rev: str = "HEAD") -> str | None:
        out = subprocess.run(
            ["git", "-C", str(self.path), "show", f"{rev}:{path}"],
            capture_output=True, text=True, errors="replace",
        )
        return out.stdout if out.returncode == 0 else None

    def grep(self, pattern: str, rev: str = "HEAD", ignore_case: bool = True,
             fixed: bool = True) -> dict[str, int]:
        """Count matching lines per file at `rev` (no checkout needed)."""
        args = ["grep", "-c", "-I"]
        if ignore_case:
            args.append("-i")
        if fixed:
            args.append("-F")
        args += ["-e", pattern, rev]
        out = self.git(*args, check=False)
        counts: dict[str, int] = {}
        prefix = f"{rev}:"
        for line in out.splitlines():
            if line.startswith(prefix):
                line = line[len(prefix):]
            path, _, n = line.rpartition(":")
            if path and n.isdigit():
                counts[path] = int(n)
        return counts


def open_repo(spec: str, update: bool = False) -> Repo:
    """Clone (or reuse) a repository in the local cache."""
    owner, name = parse_repo(spec)
    path = CACHE_DIR / "repos" / f"{owner}__{name}"
    if not (path / ".git").exists() and not (path / "HEAD").exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(
            ["git", "clone", "--quiet", "--no-checkout",
             f"https://github.com/{owner}/{name}.git", str(path)],
            capture_output=True, text=True,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},  # a missing repo must fail, not ask for a password
        )
        if r.returncode != 0:
            raise RuntimeError(f"Could not clone {owner}/{name}. Check the name, and that the repo is public "
                               f"or your git credentials can read it.\n  git said: {(r.stderr.strip().splitlines() or [''])[-1]}")
    elif update:
        git(path, "fetch", "--quiet", "origin")
        git(path, "reset", "--quiet", "--soft", "origin/HEAD", check=False)
    return Repo(owner, name, path)
