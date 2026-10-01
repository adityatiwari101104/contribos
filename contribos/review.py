"""Review coach: understand what reviewers want, answer every comment, don't go quiet.

Works from a PR URL (GitHub API) or a saved JSON of review comments. For each
comment it says what kind of ask it is, shows the code it points at, links
any house rule it echoes, and drafts a reply you finish. It also tracks
follow-through: comments you haven't answered and how long a reviewer has
been waiting on you, since silent PRs are the top reason newcomer PRs die.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime

MAINTAINER = ("OWNER", "MEMBER", "COLLABORATOR")

KINDS = [
    ("praise", re.compile(r"^\s*(lgtm|looks good|nice|great|thanks?|thank you|👍|🎉|awesome)\b[^?]*$", re.I)),
    ("nit", re.compile(r"^\s*(nit|minor|optional|small thing|style)\b|\b(nit:|non-?blocking)", re.I)),
    ("blocking", re.compile(r"\b(must|blocker|blocking|required|can't merge|cannot merge|won't merge|"
                            r"breaks|breaking|security|regression)\b", re.I)),
    ("suggestion", re.compile(r"```suggestion", re.I)),
    ("question", re.compile(r"^\s*(why|what|how|where|when|is|are|does|do|did|would|should we)\b[^\n]*\?\s*$",
                            re.I)),
    ("change", re.compile(r"\b(please|should|could you|can you|instead|rename|remove|move|use|add|"
                          r"change|revert|split|drop|avoid|prefer)\b", re.I)),
    ("question", re.compile(r"\?\s*$|\b(why|what|how|is there|does this|do we)\b.*\?", re.I | re.S)),
]
RULE_HINTS = [
    (re.compile(r"\btests?\b", re.I), "Tests", "This echoes the repo's testing expectation."),
    (re.compile(r"\b(changelog|changes\.rst|news)\b", re.I), "Changelog", "The repo asks for a changelog entry."),
    (re.compile(r"\b(sign[- ]?off|dco)\b", re.I), "Sign-off", "Commits need Signed-off-by (git commit -s)."),
    (re.compile(r"\b(scope|unrelated|separate pr|split)\b", re.I), "Scope", "Keep the PR focused on the issue."),
    (re.compile(r"\b(docs?|documentation|docstring)\b", re.I), "Docs", "Update the docs alongside the code."),
    (re.compile(r"\b(format|lint|ruff|black|pre-commit|style)\b", re.I), "Style", "Run the repo's formatters."),
]
REPLY = {
    "praise": "Thanks!",
    "nit": "Fixed in <commit>.",
    "suggestion": "Applied, thanks. (<commit>)",
    "change": "Done in <commit>: <one line on what you changed>.",
    "blocking": "Good catch. <what you changed and how you verified it> (<commit>).",
    "question": "<Your direct answer in one or two sentences, with the reason.>",
}
ADVICE = {
    "praise": "No action needed.",
    "nit": "Small and quick. Just do it; arguing costs more than fixing.",
    "suggestion": "The reviewer wrote the exact change. Apply it with GitHub's “Commit suggestion” button or by hand.",
    "change": "A requested change. Do it, or explain briefly and politely why you'd prefer not to.",
    "blocking": "This must be resolved before merge. Fix it first and say how you verified it.",
    "question": "Answer the question directly. If the answer shows the code is unclear, also add a comment in the code.",
}


@dataclass
class ReviewItem:
    author: str
    body: str
    kind: str
    path: str | None
    line: int | None
    url: str
    created_at: str
    answered: bool
    rule: tuple[str, str] | None = None
    snippet: list[str] = field(default_factory=list)


def classify(body: str) -> str:
    text = body.strip()
    for kind, rx in KINDS:
        if rx.search(text):
            return kind
    return "change" if text else "praise"


def _days(ts: str) -> float:
    return (time.time() - datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()) / 86400


def analyze(pr_author: str, review_comments: list[dict], issue_comments: list[dict],
            reviews: list[dict], read_file=None) -> tuple[list[ReviewItem], list[str]]:
    """Turn raw GitHub review data into coached items plus follow-through notes."""
    items: list[ReviewItem] = []
    replied_to = {c.get("in_reply_to_id") for c in review_comments if c["user"]["login"] == pr_author}
    author_times = [c["created_at"] for c in review_comments + issue_comments if c["user"]["login"] == pr_author]
    last_author = max(author_times) if author_times else None

    threads = [c for c in review_comments if not c.get("in_reply_to_id")]
    for c in threads:
        if c["user"]["login"] == pr_author:
            continue
        item = ReviewItem(c["user"]["login"], c["body"], classify(c["body"]), c.get("path"),
                          c.get("line") or c.get("original_line"), c.get("html_url", ""), c["created_at"],
                          c["id"] in replied_to or c.get("resolved", False))
        for rx, topic, why in RULE_HINTS:
            if rx.search(c["body"]):
                item.rule = (topic, why)
                break
        if read_file and item.path and item.line:
            text = read_file(item.path) or ""
            lines = text.splitlines()
            lo = max(0, item.line - 3)
            item.snippet = [f"{i + 1:>5}  {lines[i]}" for i in range(lo, min(len(lines), item.line + 2))]
        items.append(item)

    for c in issue_comments:
        if c["user"]["login"] == pr_author or c.get("author_association") not in MAINTAINER:
            continue
        if c.get("user", {}).get("type") == "Bot":
            continue
        answered = bool(last_author and last_author > c["created_at"])
        items.append(ReviewItem(c["user"]["login"], c["body"], classify(c["body"]), None, None,
                                c.get("html_url", ""), c["created_at"], answered))

    notes = []
    changes_requested = [r for r in reviews if r.get("state") == "CHANGES_REQUESTED"]
    if changes_requested:
        notes.append(f"{len(changes_requested)} review(s) requested changes. After you push fixes, "
                     "reply to each comment and click “Re-request review”.")
    open_items = [i for i in items if not i.answered and i.kind != "praise"]
    if open_items:
        oldest = max(_days(i.created_at) for i in open_items)
        notes.append(f"{len(open_items)} comment(s) still need your reply; the oldest has waited {int(oldest)} days.")
        if oldest > 7:
            notes.append("A week or more of silence makes maintainers assume the PR is abandoned. "
                         "Even “I'll get to this by <day>” helps.")
    else:
        notes.append("Every review comment has a reply from you.")
    return items, notes


def to_markdown(pr_url: str, items: list[ReviewItem], notes: list[str], checks: list[dict] | None = None) -> str:
    order = {"blocking": 0, "change": 1, "suggestion": 2, "question": 3, "nit": 4, "praise": 5}
    L = [f"# Review coach: {pr_url}", ""]
    L += [f"- {n}" for n in notes]
    failing = [c for c in (checks or []) if c.get("conclusion") in ("failure", "timed_out", "cancelled")]
    if failing:
        L.append("- CI is failing: " + ", ".join(f"[{c['name']}]({c.get('html_url', '')})" for c in failing[:4])
                 + ". Save the log and run `contribos setup --diagnose` on it.")
    L.append("")
    for i in sorted(items, key=lambda x: (x.answered, order[x.kind])):
        status = "✅ answered" if i.answered else "⏳ needs your reply"
        where = f" on `{i.path}:{i.line}`" if i.path else ""
        L.append(f"## {i.kind.capitalize()} from @{i.author}{where} ({status})")
        L.append("")
        L += [f"> {line}" for line in i.body.strip().splitlines()[:8]]
        L.append("")
        if i.snippet:
            L += ["```"] + i.snippet + ["```", ""]
        L.append(f"**What it means:** {ADVICE[i.kind]}")
        if i.rule:
            L.append(f"**House rule:** {i.rule[0]}. {i.rule[1]}")
        if not i.answered and i.kind != "praise":
            L.append(f"**Draft reply:** {REPLY[i.kind]}")
        if i.url:
            L.append(f"[Open the comment]({i.url})")
        L.append("")
    L.append("_Stay friendly and brief. Reviewers are volunteers; a clear “done in abc123” is the best thank-you._")
    return "\n".join(L)
