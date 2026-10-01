"""Find where you're welcome: issues worth your time, with the evidence for each.

Two layers:
- takeability of one issue: is it claimed, is there already a PR, is a
  maintainer around, is it clear enough to start?
- welcome of its repository: archived, trust gates, AI rules, whether
  outside PRs actually get merged.

Issues are sorted into "Good bet", "Possible" and "Skip" with reasons, never
a made-up percentage.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime

from .github import GitHub

MAINTAINER = ("OWNER", "MEMBER", "COLLABORATOR")
CLAIM = re.compile(r"\b(i('| a)?d like to (work|take|tackle)|can i (work|take|pick)|i('| wi)?ll (work|take)|"
                   r"assign (it |this )?(to )?me|i('| a)?m working on|working on (it|this)|may i work|"
                   r"let me (work|take)|i want to work|i would like to contribute)", re.I)
BEGINNER_LABELS = ("good first issue", "good-first-issue", "beginner", "easy", "starter", "first-timers-only",
                   "help wanted", "up-for-grabs", "low-hanging-fruit")


def _age_days(ts: str | None) -> float | None:
    if not ts:
        return None
    return (time.time() - datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()) / 86400


@dataclass
class Signal:
    good: bool | None   # True helps, False hurts, None neutral
    text: str


@dataclass
class IssueVerdict:
    repo: str
    number: int
    title: str
    url: str
    tier: str = "Possible"
    score: float = 0.0
    signals: list[Signal] = field(default_factory=list)


def takeability(gh: GitHub, owner: str, name: str, issue: dict,
                comments: list[dict] | None = None, timeline: list[dict] | None = None) -> IssueVerdict:
    num = issue["number"]
    v = IssueVerdict(f"{owner}/{name}", num, issue["title"], issue["html_url"])
    comments = comments if comments is not None else gh.issue_comments(owner, name, num)
    timeline = timeline if timeline is not None else gh.timeline(owner, name, num)

    blockers = 0
    if issue.get("state") == "closed":
        v.signals.append(Signal(False, "The issue is closed."))
        blockers += 1
    if issue.get("assignees") or issue.get("assignee"):
        who = ", ".join(a["login"] for a in issue.get("assignees") or [issue["assignee"]])
        v.signals.append(Signal(False, f"Already assigned to {who}."))
        blockers += 1

    open_prs = []
    for ev in timeline:
        src = (ev.get("source") or {}).get("issue") or {}
        if ev.get("event") == "cross-referenced" and "pull_request" in src and src.get("state") == "open":
            open_prs.append(src)
    if open_prs:
        v.signals.append(Signal(False, f"An open PR already references it: {open_prs[0]['html_url']}"))
        blockers += 1

    claims = [c for c in comments if CLAIM.search(c.get("body") or "")
              and c.get("author_association") not in MAINTAINER]
    if claims:
        last = claims[-1]
        age = _age_days(last.get("created_at")) or 0
        if age < 30:
            v.signals.append(Signal(False, f"@{last['user']['login']} said they're working on it "
                                           f"{int(age)} days ago ({last['html_url']})."))
            blockers += 1
        else:
            v.signals.append(Signal(None, f"Someone claimed it {int(age)} days ago with no PR since. "
                                          "Ask politely whether it's still taken."))

    maint = [c for c in comments if c.get("author_association") in MAINTAINER]
    if maint:
        age = _age_days(maint[-1].get("created_at")) or 0
        v.signals.append(Signal(age < 90, f"A maintainer last commented {int(age)} days ago."))
        v.score += 2 if age < 30 else 1 if age < 90 else 0
    elif issue.get("author_association") in MAINTAINER:
        v.signals.append(Signal(True, "Opened by a maintainer, so it's a real, wanted change."))
        v.score += 1.5
    else:
        v.signals.append(Signal(None, "No maintainer has commented yet; ask before starting."))

    labels = [l["name"] if isinstance(l, dict) else l for l in issue.get("labels", [])]
    if any(re.search(r"\bbug\b", l, re.I) for l in labels):
        v.signals.append(Signal(True, "A bug fix: newcomer bug-fix PRs are merged most often."))
        v.score += 1
    friendly = [l for l in labels if l.lower() in BEGINNER_LABELS]
    if friendly:
        v.signals.append(Signal(True, "Labelled " + ", ".join(f"'{l}'" for l in friendly) + "."))
        v.score += 1.5
    if any(re.search(r"(needs|awaiting)[- ](triage|discussion|design|decision)|question|wontfix|invalid|duplicate",
                     l, re.I) for l in labels):
        v.signals.append(Signal(False, "Labels say it isn't decided yet: " + ", ".join(labels)))
        v.score -= 2

    body = issue.get("body") or ""
    clarity = sum([len(body) > 300, "```" in body, bool(re.search(r"(expected|actual|steps to reproduce|"
                                                                     r"traceback|repro)", body, re.I))])
    v.signals.append(Signal(clarity >= 2 if clarity != 1 else None,
                            ["Very little detail; expect to ask questions first.",
                             "Some detail, but you may need to ask questions.",
                             "Clear description with code or reproduction steps.",
                             "Clear description with code or reproduction steps."][clarity]))
    v.score += clarity
    age = _age_days(issue.get("created_at")) or 0
    if age > 730:
        v.signals.append(Signal(False, f"Opened {int(age / 365)} years ago; check it still applies."))
        v.score -= 1

    v.tier = "Skip" if blockers else ("Good bet" if v.score >= 4 else "Possible")
    return v


@dataclass
class RepoWelcome:
    repo: str
    welcoming: bool
    signals: list[Signal] = field(default_factory=list)


def repo_welcome(gh: GitHub, owner: str, name: str) -> RepoWelcome:
    w = RepoWelcome(f"{owner}/{name}", True)
    info = gh.repo(owner, name) or {}
    if info.get("archived"):
        w.signals.append(Signal(False, "Archived: no PRs accepted."))
        w.welcoming = False
    if pushed := _age_days(info.get("pushed_at")):
        w.signals.append(Signal(pushed < 60, f"Last push {int(pushed)} days ago."))
        if pushed > 180:
            w.welcoming = False
    for path, text in ((".github/VOUCHED.td", "Needs a maintainer to vouch for you first (vouch list)."),
                       ("VOUCHED.td", "Needs a maintainer to vouch for you first (vouch list).")):
        if gh.has_file(owner, name, path):
            w.signals.append(Signal(False, text))
            w.welcoming = False
            break
    for path in ("AI_POLICY.md", ".github/AI_POLICY.md", "docs/AI_POLICY.md"):
        if gh.has_file(owner, name, path):
            w.signals.append(Signal(None, f"Has an AI policy ({path}); run `contribos policy` before using AI."))
            break
    closed = gh.pulls(owner, name, "closed", 50)
    outside = [p for p in closed if p.get("author_association") in ("NONE", "FIRST_TIME_CONTRIBUTOR",
                                                                    "FIRST_TIMER", "CONTRIBUTOR")]
    if len(outside) >= 5:
        merged = sum(bool(p.get("merged_at")) for p in outside)
        share = merged / len(outside)
        w.signals.append(Signal(share >= 0.3, f"{merged} of the last {len(outside)} closed outside PRs were merged."))
        if share < 0.1:
            w.welcoming = False
    elif closed:
        w.signals.append(Signal(None, "Few recent PRs from outside contributors, so there's little to go on."))
    _responsiveness(gh, owner, name, w, outside)
    return w


OUTSIDE = ("NONE", "FIRST_TIME_CONTRIBUTOR", "FIRST_TIMER", "CONTRIBUTOR")


def _is_bot(user: dict | None) -> bool:
    login = (user or {}).get("login", "")
    return (user or {}).get("type") == "Bot" or login.endswith("[bot]") or login.endswith("-bot")


def first_human_response_days(gh: GitHub, owner: str, name: str, pr: dict) -> float | None:
    """Days from a PR being opened to the first comment or review by a human other than its author."""
    author = (pr.get("user") or {}).get("login")
    stamps = []
    for r in gh.pr_reviews(owner, name, pr["number"]):
        if not _is_bot(r.get("user")) and (r.get("user") or {}).get("login") != author and r.get("submitted_at"):
            stamps.append(r["submitted_at"])
    for c in gh.issue_comments(owner, name, pr["number"]):
        if not _is_bot(c.get("user")) and (c.get("user") or {}).get("login") != author:
            stamps.append(c["created_at"])
    if not stamps:
        return None
    opened = _age_days(pr["created_at"]) or 0
    return max(0.0, opened - (_age_days(min(stamps)) or 0))


def _responsiveness(gh: GitHub, owner: str, name: str, w: RepoWelcome, outside_closed: list[dict]) -> None:
    """How fast humans answer newcomers, how many outside PRs stall, and stale bots."""
    open_prs = gh.pulls(owner, name, "open", 50)
    outside_open = [p for p in open_prs if p.get("author_association") in OUTSIDE]
    sample = [p for p in outside_closed[:5] + outside_open[:5] if "number" in p and p.get("created_at")][:8]
    waits, unanswered = [], 0
    for pr in sample:
        d = first_human_response_days(gh, owner, name, pr)
        if d is None:
            unanswered += 1
        else:
            waits.append(d)
    if waits:
        waits.sort()
        median = waits[len(waits) // 2]
        when = f"{median * 24:.0f} hours" if median < 2 else f"{median:.0f} days"
        w.signals.append(Signal(median <= 3, f"Outside PRs get a first human reply in a median of {when} "
                                             f"(sample of {len(sample)})."))
    if sample and unanswered / len(sample) >= 0.5:
        w.signals.append(Signal(False, f"{unanswered} of {len(sample)} sampled outside PRs never got a human reply."))
    if outside_open:
        stalled = [p for p in outside_open if (_age_days(p.get("updated_at")) or 0) > 30]
        if stalled:
            share = len(stalled) / len(outside_open)
            w.signals.append(Signal(share < 0.5, f"{len(stalled)} of {len(outside_open)} open outside PRs have "
                                                 "had no activity for 30+ days."))
            if share >= 0.7 and len(outside_open) >= 5:
                w.welcoming = False
    workflows = gh.list_dir(owner, name, ".github/workflows")
    if gh.has_file(owner, name, ".github/stale.yml") or any("stale" in f.lower() for f in workflows):
        w.signals.append(Signal(None, "A stale bot closes inactive PRs here: reply to reviews promptly."))
    me = gh.me()
    if me:
        mine = [p for p in open_prs if (p.get("user") or {}).get("login") == me]
        if mine:
            w.signals.append(Signal(False, f"You already have {len(mine)} open PR(s) here. Finish those first; "
                                           "GitHub can cap open PRs per outside contributor."))


def search(gh: GitHub, languages: list[str], topics: list[str], labels: list[str] | None = None,
           repos: list[str] | None = None, limit: int = 30) -> list[dict]:
    labels = labels or ["good first issue", "help wanted"]
    found: dict[str, dict] = {}
    scopes = [f"repo:{r}" for r in repos] if repos else [""]
    for scope in scopes:
        for label in labels:
            q = [f'label:"{label}"', "is:issue", "is:open", "no:assignee", "-linked:pr", "archived:false"]
            q += [f"language:{l}" for l in languages[:1]] if not repos else []
            q += topics[:2] if not repos else []
            if scope:
                q.append(scope)
            for item in gh.search_issues(" ".join(q), per_page=limit):
                found[item["html_url"]] = item
    return list(found.values())


def run(gh: GitHub, languages: list[str], topics: list[str], repos: list[str] | None = None,
        limit: int = 10) -> tuple[list[IssueVerdict], dict[str, RepoWelcome]]:
    items = search(gh, languages, topics, repos=repos)
    verdicts, welcomes = [], {}
    for item in items[: limit * 3]:
        m = re.search(r"repos/([^/]+)/([^/]+)/issues", item["url"])
        if not m:
            continue
        owner, name = m.group(1), m.group(2)
        slug = f"{owner}/{name}"
        if slug not in welcomes:
            welcomes[slug] = repo_welcome(gh, owner, name)
        v = takeability(gh, owner, name, item)
        if not welcomes[slug].welcoming:
            v.tier = "Skip"
            v.signals.insert(0, Signal(False, "The repository isn't a good place for a newcomer right now."))
        verdicts.append(v)
    order = {"Good bet": 0, "Possible": 1, "Skip": 2}
    verdicts.sort(key=lambda v: (order[v.tier], -v.score))
    return verdicts[:limit], welcomes


def to_markdown(verdicts: list[IssueVerdict], welcomes: dict[str, RepoWelcome]) -> str:
    mark = {True: "✅", False: "⛔", None: "•"}
    L = ["# Issues for you", ""]
    if not verdicts:
        L.append("No matching open issues found. Try other languages or topics, or name repos with --repo.")
    for tier in ("Good bet", "Possible", "Skip"):
        group = [v for v in verdicts if v.tier == tier]
        if not group:
            continue
        L += [f"## {tier}", ""]
        for v in group:
            L.append(f"### [{v.repo}#{v.number}]({v.url}): {v.title}")
            L += [f"- {mark[s.good]} {s.text}" for s in v.signals]
            w = welcomes.get(v.repo)
            if w:
                L += [f"- {mark[s.good]} Repo: {s.text}" for s in w.signals]
            L.append("")
    L.append("Next: `contribos claim <issue-url>` drafts a comment asking to work on it.")
    return "\n".join(L)
