"""Agent guardrails: make your coding agent follow this repo's rules, where slop starts.

Writes, into your local checkout:
- a skill (`.claude/skills/contribos/SKILL.md`, mirrored to `.agents/skills/`)
  that Claude Code, Codex, Copilot and Cursor load, with this repo's rules;
- a Claude Code hook that blocks the agent from opening PRs or posting
  comments on its own;
- a git `commit-msg` hook that enforces sign-off and the AI-disclosure line
  this repo asks for.

None of these files are part of your contribution: they're added to
`.git/info/exclude` so they never end up in your PR.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from .policy import Policy, structured
from .setup_doctor import SetupPlan

GUARD = '''#!/usr/bin/env python3
"""ContribOS guard: stop the coding agent from acting publicly on its own."""
import json, re, sys

data = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace"))
cmd = (data.get("tool_input") or {}).get("command", "")
if re.search(r"\\bgh\\s+(pr\\s+(create|comment|review|merge|edit|ready|close|reopen)|"
             r"issue\\s+(create|comment|edit|close|reopen)|"
             r"api\\b.*(-X|--method)\\s*(POST|PATCH|PUT|DELETE))", cmd, re.I):
    print("ContribOS: opening PRs, posting comments or claiming issues must be done by the human "
          "contributor, not the agent. Prepare the text and let the human post it.", file=sys.stderr)
    sys.exit(2)
sys.exit(0)
'''

COMMIT_MSG = '''#!/bin/sh
# ContribOS commit-msg hook: enforce this repo's sign-off and AI-disclosure rules.
msg="$1"
{dco}
if [ -f "$(git rev-parse --git-dir)/contribos-ai-used" ] && [ -n "{trailer}" ]; then
  if ! grep -qi "^{trailer}:" "$msg"; then
    echo "ContribOS: this repo asks for a '{trailer}' line when AI helped. Add, for example:" >&2
    echo "  {trailer}: $(cat "$(git rev-parse --git-dir)/contribos-ai-used")" >&2
    exit 1
  fi
fi
exit 0
'''

DCO_CHECK = '''if ! grep -q "^Signed-off-by:" "$msg"; then
  echo "ContribOS: this repo requires sign-off. Commit with: git commit -s" >&2
  exit 1
fi'''


def skill_text(policy: Policy, setup: SetupPlan | None) -> str:
    s = structured(policy)
    ai = s["ai"]
    rules = ["Never open pull requests, post comments, or claim issues yourself. Draft text; the human posts it.",
             "Keep the change minimal and focused on the issue. Do not refactor unrelated code.",
             "The human must understand every line. Explain your changes plainly and point out anything uncertain.",
             "Never write the human's PR explanation, issue comments or review replies as final text; offer a "
             "short draft they will rewrite."]
    if ai["stance"] == "restricted":
        rules.append("This repo restricts AI use. Read the AI policy evidence below and stop if the task is not allowed.")
    if s["trust_gate"]:
        rules.append("This repo only accepts PRs from vouched contributors. If the human isn't vouched yet, "
                     "help them write a short introduction instead of code.")
    if ai["no_ai_on_good_first_issues"]:
        rules.append("Do not work on issues labelled good-first-issue: this repo reserves them for humans learning.")
    if ai["disclosure_trailer"]:
        rules.append(f"Every commit you help with must include a `{ai['disclosure_trailer']}` line naming the tool.")
    elif ai["disclose_in_pr"]:
        rules.append("Remind the human to disclose in the PR description which tool was used and for what.")
    if s["dco"]:
        rules.append("Commits must be signed off by the human (`git commit -s`). Never add Signed-off-by yourself.")
    if s["tests_expected"]:
        rules.append("Add or update a test that fails without the fix.")
    if s["changelog"]:
        rules.append("Add a changelog entry in the format the repo uses.")
    if s["issue_first"] or s["claim_first"]:
        rules.append("Only work on an issue a maintainer has agreed to. If unsure, stop and ask the human.")
    steps = []
    if setup:
        for st in setup.steps:
            if st.commands and ("test" in st.title.lower() or "lint" in st.title.lower() or "pre-commit" in st.title.lower()):
                steps += [f"`{c}`" for c in st.commands[:2]]
    L = ["---", "name: contribos", "description: Rules for contributing to this open-source repository. Use before "
         "writing or committing any change here.", "---", "",
         f"# Contributing to {policy.repo}", "",
         "You are helping a human contributor. Maintainers here review human work and reject unexplained AI output.",
         "", "## Rules", ""] + [f"- {r}" for r in rules]
    if steps:
        L += ["", "## Run before saying you're done", ""] + [f"- {c}" for c in steps]
    L += ["", "## Tools", "",
          "If the ContribOS MCP server is available, use `contribos_brief` to find relevant files and past PRs, "
          "and `contribos_check` before the human submits.", "", "## Evidence for these rules", ""]
    for f in policy.findings[:8]:
        src = f" ({f.evidence[0].source})" if f.evidence else ""
        L.append(f"- {f.topic}: {f.verdict}{src}")
    return "\n".join(L) + "\n"


def install(root: Path, policy: Policy, setup: SetupPlan | None, ai_note: str | None = None) -> list[str]:
    written = []
    text = skill_text(policy, setup)
    for d in (root / ".claude/skills/contribos", root / ".agents/skills/contribos"):
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(text, encoding="utf-8", newline="\n")
        written.append((d / "SKILL.md").relative_to(root).as_posix())

    guard = root / ".contribos/guard.py"
    guard.parent.mkdir(exist_ok=True)
    guard.write_text(GUARD, encoding="utf-8", newline="\n")
    guard.chmod(guard.stat().st_mode | stat.S_IEXEC)
    written.append(".contribos/guard.py")
    settings_path = root / ".claude/settings.local.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}
    hooks = settings.setdefault("hooks", {}).setdefault("PreToolUse", [])
    python = "python" if os.name == "nt" else "python3"  # Windows installs rarely have python3 on PATH
    entry = {"matcher": "Bash", "hooks": [{"type": "command", "command": f"{python} .contribos/guard.py"}]}
    if entry not in hooks:
        hooks.append(entry)
    settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8", newline="\n")
    written.append(".claude/settings.local.json")

    s = structured(policy)
    git_dir = root / ".git"
    if git_dir.is_dir():
        hook = git_dir / "hooks" / "commit-msg"
        target = hook if not hook.exists() or "ContribOS" in hook.read_text(encoding="utf-8") else hook.with_name("commit-msg.contribos")
        target.write_text(COMMIT_MSG.format(dco=DCO_CHECK if s["dco"] else "", trailer=s["ai"]["disclosure_trailer"] or ""), encoding="utf-8", newline="\n")
        target.chmod(target.stat().st_mode | stat.S_IEXEC)
        written.append(target.relative_to(root).as_posix())
        marker = git_dir / "contribos-ai-used"
        if ai_note:
            marker.write_text(ai_note + "\n", encoding="utf-8", newline="\n")
        exclude = git_dir / "info" / "exclude"
        exclude.parent.mkdir(exist_ok=True)
        current = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        add = [p for p in (".claude/skills/contribos/", ".agents/skills/contribos/", ".contribos/",
                           ".claude/settings.local.json") if p not in current]
        if add:
            exclude.write_text(current.rstrip("\n") + ("\n" if current else "") + "# ContribOS (local only)\n"
                               + "\n".join(add) + "\n", encoding="utf-8", newline="\n")
    return written
