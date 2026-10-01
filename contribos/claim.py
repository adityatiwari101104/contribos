"""Propose before you code: draft the first message on an issue. You rewrite it and post it yourself.

Research and maintainers agree that the best predictor of a merged, welcomed
first PR is agreeing on the work first. So the draft is a short proposal that
ends in a real question, not "can I work on this?". Where the repo gates
newcomers with a vouch list, it drafts the introduction that gate asks for.
"""

from __future__ import annotations

from .brief import Brief
from .find import IssueVerdict
from .policy import Policy, is_code


def draft(issue_title: str, brief: Brief | None, policy: Policy | None,
          verdict: IssueVerdict | None = None, labels: list[str] | None = None) -> str:
    L: list[str] = []
    warnings = _warnings(policy, verdict, labels or [])
    if warnings:
        L += ["## Before you write anything", ""] + [f"- {w}" for w in warnings] + [""]

    gate = policy.get("Trust gate") if policy else None
    if gate:
        L += ["## Step 1: get vouched", "",
              "This repo only accepts PRs from vouched contributors. Introduce yourself first"
              + (f" (see `{gate.evidence[-1].source}`)." if len(gate.evidence) > 1 else "."), "",
              "**Draft introduction (rewrite it in your own words):**", "", "---", "",
              "Hi! I'm <name>. I use <project> for <what you use it for>, and I'd like to start contributing.",
              f"I'm interested in “{issue_title}”. <One sentence on what you'd do.>",
              "<Optional: a link to something you've built or contributed before.>",
              "Would someone be willing to vouch for me so I can send a PR for this?", "", "---", ""]
        L += ["## Step 2: propose the change on the issue", ""]

    L += ["**Draft proposal (rewrite it; ContribOS never posts for you):**", "", "---", ""]
    L.append("Hi! I'd like to work on this, if nobody else is.")
    L.append("")
    if brief and brief.files:
        top = brief.files[0].path
        line = f"Proposed approach: the change looks like it belongs in `{top}`"
        others = [h.path for h in brief.files[1:2]]
        line += (f" (maybe `{others[0]}` too)" if others else "") + ". <One sentence on what you'd change.>"
        L.append(line)
        c = next((c for _, c in brief.precedents if c.pr), None)
        if c:
            size = len([f for f in c.files if is_code(f)])
            L.append(f"It would be similar in size to #{c.pr} (“{c.title}”, {size} code file"
                     f"{'s' if size != 1 else ''}).")
        if brief.tests:
            L.append(f"I'd add a test in `{brief.tests[0].path}` that fails before the fix.")
        L.append("")
    L.append("Question: <one specific question about the expected behaviour or the approach>?")
    L.append("")
    if policy and (ai := policy.get("AI policy")):
        disc = policy.get("Disclosure format")
        if "No written" not in ai.verdict:
            if disc and "`" in disc.verdict:
                trailer = disc.verdict.split("`")[1]
                how = f"I'll add a `{trailer}` line to my commits"
            elif disc:
                how = "I'll say in the PR which tool I used and how much of the work it did"
            else:
                how = "I'll disclose exactly how I used it"
            L.append(f"I've read the AI policy. If I use AI, {how}.")
        else:
            L.append("If I use an AI assistant for any part of this, I'll say so in the PR.")
    L += ["", "---", "", "## Tips", ""]
    L.append("- Wait for a maintainer to agree before writing code. Discussion first is the strongest "
             "predictor of a welcomed first PR.")
    L.append("- Replace every <…> in your own words, then run `contribos tone <file>` before posting.")
    L.append("- Keep it short. A specific question gets a faster answer than a long plan.")
    return "\n".join(L)


def _warnings(policy: Policy | None, verdict: IssueVerdict | None, labels: list[str]) -> list[str]:
    out = []
    if verdict and verdict.tier == "Skip":
        out += [s.text for s in verdict.signals if s.good is False]
        out.append("This issue doesn't look free. Ask whether it's still available instead of claiming it.")
    if verdict and not any("maintainer" in s.text.lower() and s.good for s in verdict.signals):
        out.append("No maintainer has engaged on this issue yet. Propose first and wait for a yes before coding.")
    if policy:
        gfi = any("good first" in l.lower() or "first-timers" in l.lower() for l in labels)
        rule = policy.get("No AI on good first issues")
        if rule and (gfi or not labels):
            out.append("This repo reserves good-first-issues for learning by hand: don't use AI on them "
                       f"({rule.evidence[0].source}).")
        if policy.get("Archived"):
            out.append("The repository is archived. It won't accept PRs.")
        if policy.get("Issue first"):
            out.append("PRs here need a linked, discussed issue first.")
    return out
