"""Pre-submit check: review your own branch the way this repo's maintainers would.

Runs in your local checkout. Compares your diff with the repo's own history
and rules: scope versus similar past changes, tests, changelog, sign-off,
leftover debug code, and whether you can explain every part of the change.
It never writes the explanation for you; it only checks that yours covers
the change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .policy import Policy, is_code, is_test
from .precedent import Change
from .repo import Repo, git, parse_repo

DEBUG_PATTERNS = [
    (re.compile(r"^\s*(breakpoint\(\)|import i?pdb|i?pdb\.set_trace\(\))"), "debugger call"),
    (re.compile(r"^\s*console\.log\("), "console.log"),
    (re.compile(r"^\s*print\("), "print() in library code"),
    (re.compile(r"\b(TODO|FIXME|XXX)\b"), "TODO left in"),
    (re.compile(r"^\s*dbg!\("), "dbg! macro"),
]
CHANGELOG = re.compile(r"(^|/)(changelog|changes|news|history)([./_-]|$)|(^|/)\.changeset/|(^|/)changelog\.d/", re.I)
HUNK_FN = re.compile(r"^@@ [^@]+ @@\s*(?:.*?\b(?:def|class|func|fn|function)\s+)?([A-Za-z_]\w*)")


@dataclass
class Item:
    level: str    # "ok", "warn", "fail"
    topic: str
    message: str
    details: list[str] = field(default_factory=list)


@dataclass
class DiffInfo:
    base: str
    files: list[str]
    added: dict[str, list[str]]          # path -> added lines
    functions: dict[str, list[str]]      # path -> touched function names
    commits: list[tuple[str, str, str]]  # sha, subject, body
    lines_added: int
    lines_removed: int


def local_repo(path: str) -> Repo:
    try:
        root = Path(git(Path(path), "rev-parse", "--show-toplevel").strip())
    except RuntimeError:
        raise RuntimeError(f"{path} is not a git checkout. Run this inside your clone, or pass its path.") from None
    if not git(root, "rev-parse", "--verify", "--quiet", "HEAD", check=False).strip():
        raise RuntimeError(f"{root} has no commits yet. ContribOS reads repo history and rules, so "
                           "clone an existing project (git clone <url>) or make a first commit, then try again.")
    url = git(root, "remote", "get-url", "origin", check=False).strip()
    try:
        owner, name = parse_repo(url)
    except ValueError:
        owner, name = "local", root.name
    return Repo(owner, name, root)


def find_base(repo: Repo, base: str | None) -> str:
    candidates = [base] if base else ["origin/HEAD", "upstream/main", "upstream/master",
                                      "origin/main", "origin/master", "main", "master"]
    for ref in candidates:
        mb = repo.git("merge-base", "HEAD", ref, check=False).strip()
        if mb:
            return mb
    raise RuntimeError("Could not find the branch you started from; pass --base <ref>.")


def read_diff(repo: Repo, base: str) -> DiffInfo:
    # Committed and uncommitted work together: diff base against the working tree.
    files = [f for f in repo.git("diff", "--name-only", base).splitlines() if f]
    untracked = repo.git("ls-files", "--others", "--exclude-standard").splitlines()
    files += [f for f in untracked if f not in files]
    patch = repo.git("diff", "-U0", base)
    added: dict[str, list[str]] = {}
    functions: dict[str, list[str]] = {}
    current = None
    plus = minus = 0
    for line in patch.splitlines():
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("@@") and current:
            m = HUNK_FN.match(line)
            if m and m.group(1) not in functions.setdefault(current, []):
                functions[current].append(m.group(1))
        elif line.startswith("+") and not line.startswith("+++") and current:
            added.setdefault(current, []).append(line[1:])
            plus += 1
        elif line.startswith("-") and not line.startswith("---"):
            minus += 1
    for f in untracked:
        text = (repo.path / f).read_text(encoding="utf-8", errors="replace") if (repo.path / f).is_file() else ""
        added[f] = text.splitlines()
        plus += len(added[f])
    log = repo.git("log", f"{base}..HEAD", "--format=%H\x1f%s\x1f%b\x1e")
    commits = []
    for rec in log.split("\x1e"):
        parts = rec.strip("\n").split("\x1f")  # not .strip(): it eats the \x1f of an empty body
        if len(parts) == 3:
            commits.append((parts[0], parts[1], parts[2]))
    return DiffInfo(base, files, added, functions, commits, plus, minus)


def similar_changes(diff: DiffInfo, history: list[Change], k: int = 8) -> list[Change]:
    """Past focused changes that overlap most with this diff's code files.

    Sweeping changes (renames, reformatting, >15 code files) are skipped:
    they touch everything and would make any diff look small.
    """
    mine = {f for f in diff.files if is_code(f)}
    scored = []
    for c in history:
        theirs = {f for f in c.files if is_code(f)}
        if not theirs or len(theirs) > 15:
            continue
        overlap = len(mine & theirs)
        if overlap:
            scored.append((overlap / len(mine | theirs), c))
    scored.sort(key=lambda x: -x[0])
    return [c for _, c in scored[:k]]


def run_checks(diff: DiffInfo, history: list[Change], policy: Policy | None,
               explanation: str | None) -> tuple[list[Item], list[Change]]:
    items: list[Item] = []
    code = [f for f in diff.files if is_code(f)]
    tests = [f for f in diff.files if is_test(f)]
    similar = similar_changes(diff, history)

    # Scope versus precedent.
    if similar:
        sizes = sorted(len([f for f in c.files if is_code(f)]) for c in similar)
        median = sizes[len(sizes) // 2]
        limit = max(3, median + 2)
        refs = ", ".join(f"#{c.pr}" if c.pr else c.sha[:8] for c in similar[:3])
        if len(code) > limit:
            items.append(Item("warn", "Scope",
                              f"You changed {len(code)} code files. Past changes to the same files "
                              f"touched a median of {median} ({refs}). Reviewers will ask why.",
                              _unrelated(code, history)))
        else:
            items.append(Item("ok", "Scope", f"{len(code)} code file(s), in line with past changes "
                                             f"to the same files (median {median}; {refs})."))
    elif code:
        items.append(Item("ok", "Scope", f"{len(code)} code file(s). No past changes touched these files, "
                                         "so there is no precedent to compare against."))
    if diff.lines_added > 400:
        items.append(Item("warn", "Size", f"{diff.lines_added} lines added. Large PRs wait longer for review; "
                                          "consider splitting it."))

    # Tests.
    expects_tests = policy is not None and (
        policy.get("Tests") is not None or _share(policy, "Tests in practice") >= 0.4)
    if code and not tests:
        level = "fail" if expects_tests else "warn"
        why = "This repo expects tests with code changes." if expects_tests else \
              "A test that fails without your change is the strongest evidence it works."
        items.append(Item(level, "Tests", f"Code changed but no test changed. {why}"))
    elif tests:
        items.append(Item("ok", "Tests", "Test changes included: " + ", ".join(f"`{t}`" for t in tests[:3])))

    # Changelog.
    if policy and policy.get("Changelog") and code:
        if any(CHANGELOG.search(f) for f in diff.files):
            items.append(Item("ok", "Changelog", "Changelog entry included."))
        else:
            ev = policy.get("Changelog").evidence
            items.append(Item("fail", "Changelog", "This repo asks for a changelog entry and none was changed.",
                              [f"{e.source}: {e.quote}" for e in ev[:1]]))

    # Sign-off.
    if policy and policy.get("Sign-off (DCO)") and diff.commits:
        unsigned = [s[:8] for s, _, b in diff.commits if "Signed-off-by:" not in b]
        if unsigned:
            items.append(Item("fail", "Sign-off", f"{len(unsigned)} commit(s) lack Signed-off-by. "
                                                  "Fix with: git rebase --signoff " + diff.base[:12],
                              unsigned[:5]))
        else:
            items.append(Item("ok", "Sign-off", "All commits are signed off."))

    # Leftovers in added lines of non-test code.
    leftovers = []
    for f, lines in diff.added.items():
        if is_test(f) or not is_code(f):
            continue
        for line in lines:
            for rx, label in DEBUG_PATTERNS:
                if rx.search(line):
                    leftovers.append(f"`{f}`: {label}: {line.strip()[:80]}")
    if leftovers:
        items.append(Item("warn", "Leftovers", f"{len(leftovers)} line(s) look like debugging leftovers.",
                          leftovers[:6]))

    # AI policy reminder.
    if policy and (ai := policy.get("AI policy")) and "No written" not in ai.verdict:
        items.append(Item("warn", "AI policy", ai.verdict,
                          [f"{e.source}: {e.quote}" for e in ai.evidence[:2]]))

    items.append(check_understanding(diff, explanation))
    return items, similar


def check_disclosure(diff: DiffInfo, policy: Policy | None, ai_note: str | None) -> Item | None:
    """If you used AI, is it disclosed the way this repo asks?"""
    if not policy:
        return None
    disc = policy.get("Disclosure format")
    gfi = policy.get("No AI on good first issues")
    if ai_note is None:
        msg = "Did you use AI? If so, rerun with --ai \"<tool>: <what it did>\" so the disclosure matches this repo."
        return Item("warn", "AI disclosure", msg, [disc.verdict] if disc else [])
    details = []
    if gfi:
        details.append(f"This repo bans AI on good-first-issues ({gfi.evidence[0].source}). "
                       "If this is one, redo it by hand.")
    if disc and "`" in disc.verdict:
        trailer = disc.verdict.split("`")[1]
        missing = [s[:8] for s, _, b in diff.commits if trailer.lower() not in b.lower()]
        if missing:
            return Item("fail", "AI disclosure",
                        f"This repo wants a `{trailer}` line in each commit; {len(missing)} commit(s) lack it.",
                        details + [f"Example: {trailer} {ai_note}", "Add it with: git commit --amend "
                                   "(or git rebase -i for several commits)"])
    return Item("warn" if details else "ok", "AI disclosure",
                "Your AI use will be disclosed in the PR description.", details)


def check_duplicates(gh, repo: Repo, issue: int | None, title: str) -> Item | None:
    """Is someone else already solving this in an open PR? Is this your only open PR here?"""
    if gh is None:
        return None
    open_prs = gh.pulls(repo.owner, repo.name, "open", 100)
    if not gh.available:
        return None
    me = gh.me()
    words = {w for w in re.findall(r"[a-z]{4,}", title.lower())} - {"with", "when", "from", "that", "this", "into"}
    dupes = []
    for pr in open_prs:
        if me and (pr.get("user") or {}).get("login") == me:
            continue
        text = f"{pr.get('title', '')} {pr.get('body') or ''}"
        if issue and re.search(rf"#{issue}\b", text):
            dupes.append(f"#{pr['number']} references issue #{issue}: {pr['html_url']}")
        elif words and len(words & set(re.findall(r"[a-z]{4,}", pr.get("title", "").lower()))) >= max(2, len(words) // 2):
            dupes.append(f"#{pr['number']} has a similar title (“{pr['title'][:60]}”): {pr['html_url']}")
    mine = [p for p in open_prs if me and (p.get("user") or {}).get("login") == me]
    details = dupes[:3]
    if mine:
        details.append(f"You already have {len(mine)} open PR(s) here. Maintainers prefer one at a time, "
                       "and GitHub can cap open PRs per outside contributor.")
    if dupes:
        return Item("fail", "Duplicates", "Someone may already be solving this. Coordinate before submitting.",
                    details)
    if mine:
        return Item("warn", "Open PRs", "Finish your open PRs first.", details)
    return Item("ok", "Duplicates", "No open PR seems to tackle the same thing.")


def check_understanding(diff: DiffInfo, explanation: str | None) -> Item:
    """Check that the contributor's own explanation covers what they changed."""
    code = sorted((f for f in diff.files if is_code(f)),
                  key=lambda f: -len(diff.added.get(f, [])))  # biggest changes first
    targets = [(f, diff.functions.get(f) or []) for f in code]
    if explanation is None:
        prompts = ["What was broken, in one or two sentences?",
                   "Why is the fix in this place, and not somewhere else?"]
        for f, fns in targets[:4]:
            what = f"`{fns[0]}` in `{f}`" if fns else f"`{f}`"
            prompts.append(f"What does your change to {what} do?")
        prompts.append("What does your test prove, and does it fail without your change?")
        return Item("warn", "Understanding",
                    "Write your answers in your own words (e.g. explain.md) and rerun with "
                    "--explain explain.md. They become the 'Understanding' section of your PR.", prompts)
    text = explanation.lower()
    words = len(re.findall(r"\w+", explanation))
    missing = []
    for f, fns in targets:
        names = [Path(f).stem.lower(), f.lower()] + [n.lower() for n in fns]
        if not any(n in text for n in names):
            missing.append(f"`{f}`" + (f" (`{fns[0]}`)" if fns else ""))
    tests_changed = any(is_test(f) for f in diff.files)
    details = []
    if missing:
        details.append("Not covered: " + ", ".join(missing[:5]))
    if tests_changed and "test" not in text:
        details.append("You changed tests but don't say what they prove.")
    if words < 40:
        details.append(f"Only {words} words. A reviewer needs the what, the why, and the proof.")
    if not UNSURE.search(text):
        details.append("Say what you're unsure about (edge cases, platforms you couldn't test). "
                       "Maintainers trust that more than false confidence.")
    from .tone import check as tone_check
    flags = tone_check(explanation, "explanation").flags
    if flags:
        details.append("Parts read as machine-written; rewrite them yourself: " + "; ".join(flags[:3]))
    gaps = [d for d in details if not d.startswith("Say what you're unsure")]
    if gaps:
        return Item("warn", "Understanding", "Your explanation has gaps.", details)
    if details:
        return Item("ok", "Understanding", f"Your explanation covers every changed code file ({words} words).",
                    details)
    return Item("ok", "Understanding", f"Your explanation covers every changed code file ({words} words).")


def _share(policy: Policy, topic: str) -> float:
    f = policy.get(topic)
    m = re.match(r"(\d+)%", f.verdict) if f else None
    return int(m.group(1)) / 100 if m else 0.0


def _unrelated(code: list[str], history: list[Change]) -> list[str]:
    """Changed files that never changed together with any other changed file."""
    together: dict[str, set[str]] = {f: set() for f in code}
    mine = set(code)
    for c in history:
        touched = mine & set(c.files)
        if len(touched) > 1:
            for f in touched:
                together[f] |= touched - {f}
    lonely = [f for f, peers in together.items() if not peers]
    if len(lonely) == len(code):
        return []  # nothing co-changes; no signal
    return [f"`{f}` has never changed together with the other files in this diff" for f in lonely[:5]]


UNSURE = re.compile(r"\b(unsure|not sure|uncertain|don't know|do not know|open question|might|risk)\b", re.I)


def pr_draft(repo: Repo, diff: DiffInfo, similar: list[Change], explanation: str | None,
             policy: Policy | None, title: str, proof=None, ai_note: str | None = None) -> str:
    """A PR description in the repo's own template, built around evidence a reviewer can check quickly."""
    template = None
    for p in repo.files("HEAD"):
        if re.search(r"(^|/)pull_request_template(\.\w+)?$", p.lower()):
            template = repo.read(p)
            break
    L = [f"# {title}", ""]
    if template:
        L += ["<!-- Repo template below. Fill in every section. -->", template.strip(), ""]
    L += ["## What I changed and why", "",
          explanation.strip() if explanation else "_In your own words: what was broken, why the fix belongs "
                                                  "here, and what the test proves._", ""]
    L += ["## How I verified it", ""]
    if proof is not None and proof.with_fix == 0:
        L.append(f"Ran `{proof.command}`:")
        L += ["", "```", proof.with_output.strip()[-1200:] or "(no output)", "```", ""]
        if proof.proves:
            L.append(f"Without the fix (source files reverted to the base branch), the same command fails "
                     f"(exit {proof.without_fix}), so the test covers the bug.")
    else:
        L.append("_Paste the exact command you ran and its real output. Run `contribos check --verify-test "
                 "\"<test command>\"` to show the test fails without your fix._")
    unsure = [s for s in re.split(r"(?<=[.?!])\s+", explanation or "") if UNSURE.search(s)]
    L += ["", "## What I'm unsure about", ""]
    L += ([f"- {s.strip()}" for s in unsure] if unsure else
          ["_Edge cases you couldn't test, platforms you didn't try, or design choices you'd like a "
           "maintainer's view on._"])
    L.append("")
    related = [f"- #{c.pr}: {c.title}" for c in similar[:3] if c.pr]
    if related:
        L += ["## Related past changes", ""] + related + [""]
    L += ["## AI assistance", ""]
    disc = policy.get("Disclosure format") if policy else None
    if ai_note:
        L.append(f"Used: {ai_note}. I reviewed, tested and can explain every line.")
        if disc and "`" in disc.verdict:
            L.append(f"(Commits carry the `{disc.verdict.split('`')[1]}` line this repo asks for.)")
    else:
        L.append("_State plainly whether you used AI tools and for what, or write “No AI tools were used.”_")
    if policy and (ai := policy.get("AI policy")) and ai.evidence:
        L.append(f"_This repo's rule: {ai.verdict} ({ai.evidence[0].source})._")
    return "\n".join(L)


def to_markdown(items: list[Item]) -> str:
    icon = {"ok": "✅", "warn": "⚠️", "fail": "❌"}
    fails = sum(i.level == "fail" for i in items)
    warns = sum(i.level == "warn" for i in items)
    L = [f"# Pre-submit check: {fails} to fix, {warns} to look at", ""]
    for i in sorted(items, key=lambda x: {"fail": 0, "warn": 1, "ok": 2}[x.level]):
        L.append(f"{icon[i.level]} **{i.topic}:** {i.message}")
        L += [f"   - {d}" for d in i.details]
    return "\n".join(L)


def current_title(repo: Repo, diff: DiffInfo) -> str:
    if diff.commits:
        return diff.commits[-1][1]  # oldest commit subject
    branch = repo.git("rev-parse", "--abbrev-ref", "HEAD").strip()
    return branch.replace("-", " ").replace("_", " ")

