"""Policy radar: what a repository expects from outside contributors, with evidence.

Reads contribution docs, AI policies, templates, trust lists and CI config
from the git tree, plus main-line history. Every finding carries the file
and line it came from, so a contributor can check it.
"""

from __future__ import annotations

import re
import time
from collections import Counter
from dataclasses import dataclass, field

from . import landed
from .github import GitHub
from .precedent import Change
from .repo import Repo

DOC_PATTERNS = [
    r"(^|/)contributing(\.\w+)?$",
    r"(^|/)(ai|llm|genai)[-_ ]?(policy|usage|contributions?|contribution-policy)(\.\w+)?$",
    r"(^|/)agents\.md$",
    r"(^|/)(claude|copilot-instructions)\.md$",
    r"(^|/)pull_request_template(\.\w+)?$",
    r"(^|/)code_of_conduct(\.\w+)?$",
    r"(^|/)(dev(elopment)?|hacking)(\.\w+)?$",
]
TRUST_LISTS = [r"(^|/)vouched\.td$", r"(^|/)vouch(ed)?(\.\w+)?$"]
CODEOWNERS = r"(^|/)codeowners$"

AI_WORDS = re.compile(
    r"\b(AI|A\.I\.|LLMs?|ChatGPT|GPT-?\d?|Copilot|Claude|Gemini|Cursor|"
    r"generative|AI[- ]generated|machine[- ]generated|language models?)\b")
RULES = {
    "ai_ban": re.compile(r"\b(prohibit|forbid|ban(ned)?|not (be )?(accept|allow|permit)|"
                         r"do not (use|submit)|(must|should|may) not (be )?used?|never use|no (AI|LLM)|will be (closed|rejected))", re.I),
    "ai_disclose": re.compile(r"\b(disclos|declare|must (state|mention|note)|label|indicate)", re.I),
    "ai_understand": re.compile(r"\b(understand|responsib|review(ed)? (it|them|the output)|"
                                r"human[- ]in[- ]the[- ]loop|able to explain)", re.I),
    "claim": re.compile(r"\b(get assigned|assign(ed)? to you|claim|comment on the issue|"
                        r"before (you )?(start|begin|work)|open an issue first|discuss (it |this )?"
                        r"(first|before)|wait for (a )?maintainer)", re.I),
    "tests": re.compile(r"\b(tests? (are|is) required|(add|include|write) (a |new )?"
                        r"(regression )?tests?|must (be )?(covered|tested))", re.I),
    "changelog": re.compile(r"\b(changelog|changes\.(rst|md)|news fragment|towncrier|changeset)", re.I),
    "small": re.compile(r"\b(small|focused|atomic|one (logical )?(change|thing)|single (issue|purpose))"
                        r"\b.{0,40}\b(PRs?|pull requests?|changes?|commits?)", re.I),
    "dco": re.compile(r"(signed-off-by|\bDCO\b|developer certificate of origin|git commit -s)", re.I),
    "cla": re.compile(r"\b(CLA|contributor license agreement)\b"),
}
STYLE_TOOLS = {
    ".pre-commit-config.yaml": "pre-commit",
    "ruff.toml": "ruff", ".ruff.toml": "ruff", ".flake8": "flake8",
    ".eslintrc": "eslint", ".prettierrc": "prettier", "tox.ini": "tox", "noxfile.py": "nox",
}


@dataclass
class Evidence:
    source: str   # path:line, a URL, or "git history"
    quote: str


@dataclass
class Finding:
    topic: str
    verdict: str
    evidence: list[Evidence] = field(default_factory=list)


@dataclass
class Policy:
    repo: str
    findings: list[Finding] = field(default_factory=list)
    docs: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def get(self, topic: str) -> Finding | None:
        return next((f for f in self.findings if f.topic == topic), None)


def _match_any(path: str, patterns: list[str]) -> bool:
    low = path.lower()
    return any(re.search(p, low) for p in patterns)


def _scan(repo: Repo, rev: str, paths: list[str]):
    """Yield (path, lineno, line) for each line of each doc."""
    for path in paths:
        text = repo.read(path, rev) or ""
        for i, line in enumerate(text.splitlines(), 1):
            if line.strip():
                yield path, i, line.strip()


def _quote(line: str, limit: int = 160) -> str:
    line = re.sub(r"\s+", " ", line)
    return line if len(line) <= limit else line[: limit - 1] + "…"


def analyze(repo: Repo, history: list[Change], rev: str = "HEAD",
            gh: GitHub | None = None) -> Policy:
    files = repo.files(rev)
    docs = sorted(p for p in files if _match_any(p, DOC_PATTERNS)
                  and not p.lower().startswith(("node_modules/", "vendor/", "third_party/")))
    policy = Policy(repo.slug, docs=docs)

    hits: dict[str, list[Evidence]] = {k: [] for k in RULES}
    ai_lines: list[tuple[str, int, str]] = []
    for path, i, line in _scan(repo, rev, docs):
        if AI_WORDS.search(line):
            ai_lines.append((path, i, line))
        for key, rx in RULES.items():
            if key.startswith("ai_"):
                continue
            if rx.search(line) and len(hits[key]) < 3:
                hits[key].append(Evidence(f"{path}:{i}", _quote(line)))

    # AI policy: only judge lines that actually talk about AI.
    for path, i, line in ai_lines:
        for key in ("ai_ban", "ai_disclose", "ai_understand"):
            if RULES[key].search(line) and len(hits[key]) < 3:
                hits[key].append(Evidence(f"{path}:{i}", _quote(line)))
    ai_files = [p for p in docs if re.search(r"(ai|llm|genai)[-_ ]?(policy|usage)", p.lower())]
    if hits["ai_ban"] and hits["ai_disclose"]:
        verdict = ("Strict: some AI use is banned and the rest must be disclosed. "
                   "Read the quoted lines before using AI.")
    elif hits["ai_ban"]:
        verdict = "Restricts or bans AI-generated contributions. Read the quoted lines before using AI."
    elif hits["ai_disclose"]:
        verdict = "Allows AI help but requires you to disclose it."
    elif hits["ai_understand"]:
        verdict = "Allows AI help if you understand and take responsibility for the change."
    elif ai_lines or ai_files:
        verdict = "Mentions AI but states no clear rule. Disclose AI use to be safe."
    else:
        verdict = "No written AI policy found. Disclosing AI use is still the courteous default."
    ev = hits["ai_ban"] + hits["ai_disclose"] + hits["ai_understand"]
    if not ev:
        ev = [Evidence(f"{p}:{i}", _quote(l)) for p, i, l in ai_lines[:2]]
    ev += [Evidence(p, "dedicated AI policy file") for p in ai_files]
    policy.findings.append(Finding("AI policy", verdict, ev[:4]))

    trust = [p for p in files if _match_any(p, TRUST_LISTS)]
    if trust:
        policy.findings.append(Finding(
            "Trust gate",
            "Uses a vouch list: you may need a maintainer to vouch for you before PRs are accepted.",
            [Evidence(p, "trust list file") for p in trust]))

    labels = {
        "claim": ("Claim first", "Ask or get assigned on the issue before starting work."),
        "tests": ("Tests", "Changes are expected to come with tests."),
        "changelog": ("Changelog", "Changes usually need a changelog or news entry."),
        "small": ("PR size", "Keep PRs small and focused."),
        "dco": ("Sign-off (DCO)", "Commits need a Signed-off-by line (git commit -s)."),
        "cla": ("CLA", "You must sign a Contributor License Agreement."),
    }
    for key, (topic, verdict) in labels.items():
        if hits[key]:
            policy.findings.append(Finding(topic, verdict, hits[key]))

    # Signals from history and config, not just prose.
    recent = history[:200]
    if recent:
        signed = sum("Signed-off-by:" in c.body for c in recent)
        if signed / len(recent) > 0.5 and not policy.get("Sign-off (DCO)"):
            policy.findings.append(Finding(
                "Sign-off (DCO)", "Most commits carry Signed-off-by, so sign yours (git commit -s).",
                [Evidence("git history", f"{signed} of the last {len(recent)} main-line commits are signed off")]))
        with_tests = [c for c in recent if c.pr and any(_is_test(f) for f in c.files)]
        code_prs = [c for c in recent if c.pr and any(_is_code(f) for f in c.files)]
        if code_prs:
            share = len(with_tests) / len(code_prs)
            policy.findings.append(Finding(
                "Tests in practice",
                f"{share:.0%} of recent code PRs also changed tests.",
                [Evidence("git history", f"{len(with_tests)} of {len(code_prs)} recent code-changing PRs")]))
        policy.findings.append(_activity(recent))

    tools = sorted({STYLE_TOOLS[p] for p in files if p in STYLE_TOOLS})
    if tools:
        policy.findings.append(Finding("Style tools", "Run these before pushing: " + ", ".join(tools),
                                       [Evidence(p, "config file") for p in files if p in STYLE_TOOLS][:4]))
    owners = [p for p in files if _match_any(p, [CODEOWNERS])]
    if owners:
        policy.findings.append(Finding("Code owners", "Specific people review specific paths.",
                                       [Evidence(p, "CODEOWNERS") for p in owners]))

    _v2_signals(policy, repo, rev, docs, history)
    if gh is not None:
        _api_signals(policy, repo, gh)
    return policy


TRAILER = re.compile(r"\b(Assisted-by|AI-used-for|Generated-by|AI-assisted-by|Co-developed-by|"
                     r"Co-authored-by)(?:\s*:|`?\s+(?:commit\s+)?(?:trailers?|lines?)\b)", re.I)
GFI_WORDS = re.compile(r"good[- ]first[- ]issues?|first[- ]timers?|newcomer issues?", re.I)
ISSUE_FIRST = re.compile(r"\b(open|file|create) an issue (first|before)|must (reference|link)( to)? an issue|"
                         r"(link|reference)s? (a|an|the) (related )?issue|without (a |an )?(linked |related )?issue|"
                         r"discuss(ed)? (in|on) an issue (first|before)", re.I)


def _v2_signals(policy: Policy, repo: Repo, rev: str, docs: list[str], history: list[Change]) -> None:
    """Disclosure format, AI on good-first-issues, issue-first rules, machine-readable policy files."""
    trailers: dict[str, Evidence] = {}
    gfi_ai: list[Evidence] = []
    issue_first: list[Evidence] = []
    pr_section: list[Evidence] = []
    for path, i, line in _scan(repo, rev, docs):
        for m in TRAILER.finditer(line):
            name = m.group(1)
            if name.lower() == "co-authored-by" and not AI_WORDS.search(line):
                continue  # plain co-authorship, not an AI rule
            trailers.setdefault(name.title(), Evidence(f"{path}:{i}", _quote(line)))
        if GFI_WORDS.search(line) and AI_WORDS.search(line) and RULES["ai_ban"].search(line):
            gfi_ai.append(Evidence(f"{path}:{i}", _quote(line)))
        if ISSUE_FIRST.search(line) and len(issue_first) < 2:
            issue_first.append(Evidence(f"{path}:{i}", _quote(line)))
        if "pull_request_template" in path.lower() and AI_WORDS.search(line):
            pr_section.append(Evidence(f"{path}:{i}", _quote(line)))
        elif re.search(r"(state|name|list|mention|say) (the|which) (tool|model|assistant)|extent|how much", line, re.I) \
                and (AI_WORDS.search(line) or "ai" in path.lower()):
            pr_section.append(Evidence(f"{path}:{i}", _quote(line)))
        if re.search(r"\bvouch", line, re.I) and (gate := policy.get("Trust gate")) and len(gate.evidence) < 3:
            gate.evidence.append(Evidence(f"{path}:{i}", _quote(line)))

    # Machine-readable policy (ai-contribution-policy.yml draft schema and similar).
    for path in repo.files(rev):
        if re.search(r"(^|/)\.?ai[-_]contribution[-_]policy\.ya?ml$", path.lower()):
            text = repo.read(path, rev) or ""
            fields = dict(re.findall(r"^\s*(stance|disclosure|trailer|attestation)s?\s*:\s*(.+)$", text, re.M | re.I))
            if fields:
                policy.findings.append(Finding(
                    "Machine-readable AI policy",
                    "; ".join(f"{k}: {v.strip()}" for k, v in fields.items()),
                    [Evidence(path, "ai-contribution-policy file")]))

    recent = history[:200]
    used = Counter(m.group(1).title() for c in recent for m in TRAILER.finditer(c.body)
                   if m.group(1).lower() != "co-authored-by" or AI_WORDS.search(c.body))
    if trailers:
        name = next(iter(trailers))
        policy.findings.append(Finding(
            "Disclosure format", f"Disclose AI help with a `{name}:` line in each commit message.",
            list(trailers.values())[:2]))
    elif pr_section:
        policy.findings.append(Finding(
            "Disclosure format", "Disclose AI help in the PR description: name the tool and how much of the "
                                 "work it did.",
            pr_section[:2]))
    elif used:
        name, n = used.most_common(1)[0]
        policy.findings.append(Finding(
            "Disclosure format", f"No written rule, but {n} recent commits use `{name}:` to disclose AI help. "
                                 "Follow the same convention.",
            [Evidence("git history", f"{n} of the last {len(recent)} main-line commits")]))
    if gfi_ai:
        policy.findings.insert(0, Finding(
            "No AI on good first issues",
            "Good-first-issues here are reserved for people learning by hand. Don't use AI on them.",
            gfi_ai[:2]))
    if issue_first:
        policy.findings.append(Finding(
            "Issue first", "Open or discuss an issue before sending a PR; PRs without one may be closed.",
            issue_first))


def structured(policy: Policy) -> dict:
    """A normalised, machine-readable view of the policy (for agents and the --json flag)."""
    ai = policy.get("AI policy")
    verdict = ai.verdict if ai else ""
    stance = ("restricted" if verdict.startswith(("Strict", "Restricts")) else
              "disclose" if "disclose it" in verdict else
              "allowed-with-responsibility" if "responsibility" in verdict else "unstated")
    disc = policy.get("Disclosure format")
    trailer = re.search(r"`([\w-]+):`", disc.verdict).group(1) if disc and "`" in disc.verdict else None
    return {
        "repo": policy.repo,
        "ai": {"stance": stance, "disclosure_trailer": trailer,
               "disclose_in_pr": bool(disc and "PR description" in disc.verdict),
               "no_ai_on_good_first_issues": policy.get("No AI on good first issues") is not None},
        "trust_gate": policy.get("Trust gate") is not None,
        "claim_first": policy.get("Claim first") is not None,
        "issue_first": policy.get("Issue first") is not None,
        "tests_expected": policy.get("Tests") is not None,
        "changelog": policy.get("Changelog") is not None,
        "dco": policy.get("Sign-off (DCO)") is not None,
        "cla": policy.get("CLA") is not None,
        "archived": policy.get("Archived") is not None,
        "findings": [{"topic": f.topic, "verdict": f.verdict,
                      "evidence": [{"source": e.source, "quote": e.quote} for e in f.evidence]}
                     for f in policy.findings],
    }


def _activity(recent: list[Change]) -> Finding:
    now = time.time()
    last = recent[0].date
    days = int((now - last) / 86400)
    authors = Counter(c.author for c in recent)
    one_timers = sum(1 for c in recent if authors[c.author] == 1)
    verdict = f"Last main-line change {days} days ago. {one_timers} of the last {len(recent)} changes came from one-time authors."
    if days > 180:
        verdict += " The repo looks inactive, so a PR may never be reviewed."
    return Finding("Activity", verdict, [Evidence("git history", f"{len(authors)} distinct authors in the last {len(recent)} changes")])


def _api_signals(policy: Policy, repo: Repo, gh: GitHub) -> None:
    info = gh.repo(repo.owner, repo.name)
    if info is None:
        policy.notes.append(f"GitHub API unavailable ({gh.last_error}); PR-level signals skipped.")
        return
    if info.get("archived"):
        policy.findings.insert(0, Finding("Archived", "The repository is archived and accepts no PRs.",
                                          [Evidence(info["html_url"], "archived: true")]))
    closed = gh.pulls(repo.owner, repo.name, "closed", 50)
    outside = [p for p in closed if p.get("author_association") in ("NONE", "FIRST_TIME_CONTRIBUTOR", "FIRST_TIMER", "CONTRIBUTOR")]
    if outside:
        total, merged, other = landed.summarize(outside, repo=repo)
        ev = [Evidence(p["html_url"], f"closed on GitHub, landed as commit {other[p['number']]}")
              for p in outside if p.get("number") in other][:2]
        ev += [Evidence(p["html_url"], _quote(p["title"], 80)) for p in outside
               if not p.get("merged_at") and p.get("number") not in other][:3 - len(ev)]
        policy.findings.append(Finding(
            "Outside PRs", landed.verdict(len(outside), total, merged, other, "PRs from non-members"), ev))


def _is_test(path: str) -> bool:
    low = path.lower()
    name = low.rsplit("/", 1)[-1]
    return ("/tests/" in f"/{low}" or "/test/" in f"/{low}" or name.startswith("test_")
            or re.search(r"(_test|\.test|\.spec)\.\w+$", name) is not None)


CODE_EXT = (".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".rb", ".c", ".cc", ".cpp", ".h", ".kt", ".swift", ".php", ".cs", ".zig", ".scala", ".ex", ".hs", ".ml", ".lua", ".dart", ".vue", ".svelte")


def _is_code(path: str) -> bool:
    return path.endswith(CODE_EXT) and not _is_test(path)


is_test, is_code = _is_test, _is_code
