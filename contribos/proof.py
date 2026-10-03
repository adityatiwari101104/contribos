"""Proof that a change works: run the test with the fix, then without it.

A test that passes with your change and fails without it is the evidence
maintainers ask for most ("tests that fail if the change is reverted").
This swaps your changed source files back to the base version for one run,
then restores them, so nothing you wrote is lost. It works with editable
installs because it changes files in place rather than copying the repo.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .policy import is_code
from .repo import Repo


@dataclass
class ProofResult:
    command: str
    with_fix: int
    without_fix: int | None
    with_output: str
    without_output: str
    reverted: list[str]

    @property
    def proves(self) -> bool:
        return self.with_fix == 0 and self.without_fix not in (None, 0)


def _run(cmd: str, cwd: Path, timeout: int) -> tuple[int, str]:
    try:
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}  # stale .pyc could hide the swap
        r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace", env=env)
        return r.returncode, (r.stdout + r.stderr)[-3000:]
    except subprocess.TimeoutExpired:
        return 124, f"(timed out after {timeout}s)"


def _touch(path: Path) -> None:
    # Bump mtime past any cached bytecode so test runners re-import the swapped file.
    t = max(path.stat().st_mtime, time.time()) + 2
    os.utime(path, (t, t))


def verify(repo: Repo, base: str, files: list[str], cmd: str, timeout: int = 600) -> ProofResult:
    root = repo.path
    code = [f for f in files if is_code(f)]
    with_code, with_out = _run(cmd, root, timeout)
    if with_code != 0 or not code:
        return ProofResult(cmd, with_code, None, with_out, "", [])

    backup = Path(tempfile.mkdtemp(prefix="contribos-proof-"))
    saved: list[tuple[Path, Path | None]] = []
    try:
        for f in code:
            target = root / f
            if target.exists():
                copy = backup / f
                copy.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, copy)
                saved.append((target, copy))
            else:
                saved.append((target, None))
            original = repo.read_bytes(f, base)  # bytes: no re-encoding or newline changes
            if original is None:
                target.unlink(missing_ok=True)  # file is new in this change
            else:
                target.write_bytes(original)
                _touch(target)
        without_code, without_out = _run(cmd, root, timeout)
    finally:
        for target, copy in saved:
            if copy is not None:
                shutil.copyfile(copy, target)
                _touch(target)
            elif target.exists():
                target.unlink()
        # Restore files that existed only in the change.
        for f in code:
            src = backup / f
            if src.exists() and not (root / f).exists():
                shutil.copy2(src, root / f)
        shutil.rmtree(backup, ignore_errors=True)
    return ProofResult(cmd, with_code, without_code, with_out, without_out, code)


def summary_lines(p: ProofResult) -> list[str]:
    if p.with_fix != 0:
        return [f"`{p.command}` fails even with your change (exit {p.with_fix}). Fix that first."]
    if p.without_fix is None:
        return [f"`{p.command}` passes, but no source files changed, so there's nothing to compare against."]
    if p.proves:
        return [f"`{p.command}` passes with your change and fails without it (exit {p.without_fix}). "
                "That's the proof reviewers want."]
    return [f"`{p.command}` also passes without your change, so this test doesn't prove the fix. "
            "Add or adjust a test that fails on the base version."]
