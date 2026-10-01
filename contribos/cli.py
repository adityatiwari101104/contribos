"""contribos: command-line entry point.

    contribos policy <owner/repo>          what this repo expects from outside contributors
    contribos brief <issue-url>            where to look, precedent, tests, house rules
    contribos brief <owner/repo> --title "..." [--body-file issue.md]
    contribos bench <owner/repo> [-n 20]   replay past changes and score the brief
    contribos check [--explain explain.md] [--pr-draft pr.md]   review your branch before submitting
    contribos setup <owner/repo> | --diagnose output.txt          get running, or explain a failure
    contribos find --lang python [--topic web] [--repo o/r]       issues where you're welcome
    contribos claim <issue-url>                                   draft a proposal to post before coding
    contribos review <pr-url> | --data review.json                coach for reviewer feedback
    contribos record <github-user> [--html record.html]           verifiable contribution record
    contribos agent-rules [path] [--ai "Claude Code"]            guardrails for your coding agent
    contribos tone <file> [--kind comment|intro|reply]            does your message sound like you?
    contribos mcp                                                 run as an MCP server for coding agents
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from . import bench, brief, check, claim, find, llm, policy, record, review, setup_doctor, tone
from .github import GitHub
from .precedent import PrecedentIndex
from .repo import open_repo


def _load(spec: str, update: bool):
    repo = open_repo(spec, update=update)
    index = PrecedentIndex(repo)
    index.build()
    return repo, index.changes()


def cmd_policy(args) -> int:
    repo, history = _load(args.repo, args.update)
    gh = None if args.offline else GitHub()
    p = policy.analyze(repo, history, gh=gh)
    if args.json:
        print(json.dumps(policy.structured(p), indent=2))
        return 0
    print(f"# Contribution policy: {repo.slug}\n")
    for f in p.findings:
        print(f"## {f.topic}\n\n{f.verdict}\n")
        for e in f.evidence:
            print(f"- `{e.source}`: {e.quote}")
        print()
    print("Docs read: " + (", ".join(f"`{d}`" for d in p.docs) or "none found"))
    for n in p.notes:
        print(f"\n_Note: {n}_")
    return 0


def _issue_text(args, repo, gh) -> tuple[str, str, str | None, dict | None]:
    """Title, body (with comments), URL and raw issue, from the API or from flags."""
    title, body, url, raw = getattr(args, "title", None), "", None, None
    if getattr(args, "body_file", None):
        body = Path(args.body_file).read_text()
    m = re.search(r"/(?:issues|pull)/(\d+)", args.target)
    if m and not title:
        url = args.target
        raw = gh.issue(repo.owner, repo.name, int(m.group(1))) if gh else None
        if raw is None:
            raise SystemExit("Could not read the issue from the GitHub API"
                             + (f" ({gh.last_error})" if gh else "")
                             + ". Set GITHUB_TOKEN, or pass --title and --body-file instead.")
        title, body = raw["title"], raw.get("body") or ""
        raw["_comments"] = gh.issue_comments(repo.owner, repo.name, int(m.group(1)))
        body += "\n" + "\n".join((c.get("body") or "")[:1000] for c in raw["_comments"][:10])
    if not title:
        raise SystemExit("Give an issue URL, or --title (and optionally --body-file).")
    return title, body, url, raw


def cmd_brief(args) -> int:
    repo, history = _load(args.target, args.update)
    gh = None if args.offline else GitHub()
    title, body, url, _ = _issue_text(args, repo, gh)
    p = policy.analyze(repo, history, gh=gh)
    b = brief.build(repo, title, body, history, p, top=args.top)
    quotes = {}
    if gh:
        for _, c in b.precedents[:3]:
            if c.pr:
                comments = gh.pr_review_comments(repo.owner, repo.name, c.pr)
                quotes[c.pr] = [re.sub(r"\s+", " ", x["body"])[:200] for x in comments
                                if x.get("author_association") in ("OWNER", "MEMBER", "COLLABORATOR")][:2]
    out = brief.to_markdown(b, p, url, quotes)
    if args.llm:
        paths = {h.path for h in b.files + b.tests} | {f for _, c in b.precedents for f in c.files}
        try:
            summary = llm.explain_issue(title, body, out, paths)
            out = out.replace("\n## Where to look", f"\n## In plain words (AI, grounded in the evidence below)\n\n"
                                                      f"{summary}\n\n## Where to look", 1)
        except llm.LLMUnavailable as e:
            print(f"(AI summary skipped: {e})", file=sys.stderr)
    if args.out:
        Path(args.out).write_text(out, encoding="utf-8")
        print(f"Brief written to {args.out}")
    else:
        print(out)
    return 0


def cmd_claim(args) -> int:
    repo, history = _load(args.target, args.update)
    gh = None if args.offline else GitHub()
    title, body, _, raw = _issue_text(args, repo, gh)
    p = policy.analyze(repo, history, gh=gh)
    b = brief.build(repo, title, body, history, p)
    verdict, labels = None, []
    if raw and gh:
        verdict = find.takeability(gh, repo.owner, repo.name, raw, raw.get("_comments"))
        labels = [l["name"] if isinstance(l, dict) else l for l in raw.get("labels", [])]
    print(claim.draft(title, b, p, verdict, labels))
    return 0


def cmd_find(args) -> int:
    if args.offline:
        print("`find` needs the GitHub API, so it can't run with --offline.", file=sys.stderr)
        return 2
    gh = GitHub()
    verdicts, welcomes = find.run(gh, args.lang, args.topic, repos=args.repo, limit=args.limit)
    if not gh.available:
        print(f"GitHub API unavailable ({gh.last_error}). `find` needs it: set GITHUB_TOKEN.", file=sys.stderr)
        return 2
    print(find.to_markdown(verdicts, welcomes))
    return 0


def cmd_review(args) -> int:
    read_file = None
    if args.data:
        data = json.loads(Path(args.data).read_text())
        author, url = data["author"], data.get("url", args.data)
        rc, ic, rv, checks = data.get("review_comments", []), data.get("issue_comments", []), \
            data.get("reviews", []), data.get("check_runs", [])
    else:
        m = re.search(r"github\.com/([^/]+)/([^/]+)/pull/(\d+)", args.pr or "")
        if not m:
            print("Give a PR URL, or --data with saved review JSON.", file=sys.stderr)
            return 2
        owner, name, num = m.group(1), m.group(2), int(m.group(3))
        gh = GitHub()
        pr = gh.pull(owner, name, num)
        if pr is None:
            print(f"Could not read the PR ({gh.last_error}). Set GITHUB_TOKEN.", file=sys.stderr)
            return 2
        author, url = pr["user"]["login"], pr["html_url"]
        rc = gh.pr_review_comments(owner, name, num)
        ic = gh.issue_comments(owner, name, num)
        rv = gh.pr_reviews(owner, name, num)
        checks = gh.check_runs(owner, name, pr["head"]["sha"])
    if args.path:
        root = Path(args.path)
        read_file = lambda p: (root / p).read_text(errors="replace") if (root / p).is_file() else None  # noqa: E731
    items, notes = review.analyze(author, rc, ic, rv, read_file)
    print(review.to_markdown(url, items, notes, checks))
    return 0


def cmd_record(args) -> int:
    if args.offline:
        print("`record` needs the GitHub API, so it can't run with --offline.", file=sys.stderr)
        return 2
    gh = GitHub()
    r = record.build(gh, args.user, limit=args.limit, include_own=args.include_own)
    if not gh.available:
        print(f"GitHub API unavailable ({gh.last_error}). `record` needs it: set GITHUB_TOKEN.", file=sys.stderr)
        return 2
    print(record.to_markdown(r))
    if args.html:
        Path(args.html).write_text(record.to_html(r), encoding="utf-8")
        print(f"\nHTML page written to {args.html}")
    return 0


def cmd_agent_rules(args) -> int:
    from . import agent_rules
    repo = check.local_repo(args.path)
    index = PrecedentIndex(repo)
    index.build()
    p = policy.analyze(repo, index.changes())
    written = agent_rules.install(repo.path, p, setup_doctor.plan(repo), args.ai)
    print("Installed ContribOS guardrails for your coding agent (kept out of your PR via .git/info/exclude):")
    print("\n".join(f"- {w}" for w in written))
    print("\nYour agent now loads this repo's rules, can't open PRs or post comments on its own, and commits "
          "are checked for sign-off and AI disclosure where the repo requires them.")
    return 0


def cmd_tone(args) -> int:
    text = Path(args.file).read_text(errors="replace")
    print(tone.to_markdown(tone.check(text, args.kind)))
    return 0


def cmd_mcp(args) -> int:
    from .mcp_server import serve
    return serve()


def cmd_bench(args) -> int:
    repo, history = _load(args.repo, args.update)
    print(f"Replaying {args.n} past changes from {repo.slug} "
          f"(✓ = a changed file in top 5; columns: brief, grep-only)")
    results = bench.run(repo, history, n=args.n)
    s = bench.summary(results)
    print(json.dumps(s, indent=2))
    return 0


def cmd_check(args) -> int:
    repo = check.local_repo(args.path)
    base = check.find_base(repo, args.base)
    index = PrecedentIndex(repo)
    index.build(rev=base)
    history = index.changes()
    gh = None if args.offline else GitHub()
    p = policy.analyze(repo, history, rev=base, gh=gh)
    diff = check.read_diff(repo, base)
    if not diff.files:
        print("No changes found against the base branch.")
        return 0
    explanation = Path(args.explain).read_text() if args.explain else None
    items, similar = check.run_checks(diff, history, p, explanation)
    title = args.title or check.current_title(repo, diff)
    for extra in (check.check_disclosure(diff, p, args.ai),
                  check.check_duplicates(gh, repo, args.issue, title)):
        if extra:
            items.append(extra)
    proof_result = None
    if args.verify_test:
        from . import proof
        print(f"Running `{args.verify_test}` with and without your change...", file=sys.stderr)
        proof_result = proof.verify(repo, base, diff.files, args.verify_test)
        items.append(check.Item("ok" if proof_result.proves else "fail", "Proof",
                                proof.summary_lines(proof_result)[0]))
    print(check.to_markdown(items))
    if args.pr_draft:
        Path(args.pr_draft).write_text(check.pr_draft(repo, diff, similar, explanation, p, title,
                                                      proof_result, args.ai), encoding="utf-8")
        print(f"\nPR description draft written to {args.pr_draft}")
    return 1 if any(i.level == "fail" for i in items) else 0


def cmd_setup(args) -> int:
    if args.diagnose:
        text = Path(args.diagnose).read_text(errors="replace")
        print(setup_doctor.diagnosis_markdown(setup_doctor.diagnose(text)))
        return 0
    if not args.repo:
        print("Give a repo, or --diagnose <output file>.", file=sys.stderr)
        return 2
    repo = open_repo(args.repo, update=args.update)
    print(setup_doctor.to_markdown(setup_doctor.plan(repo)))
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(prog="contribos", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--update", action="store_true", help="fetch the latest commits first")
    ap.add_argument("--offline", action="store_true", help="don't call the GitHub API")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("policy", help="what this repo expects from contributors")
    p.add_argument("repo")
    p.add_argument("--json", action="store_true", help="machine-readable output for agents")
    p.set_defaults(fn=cmd_policy)

    b = sub.add_parser("brief", help="contribution brief for an issue")
    b.add_argument("target", help="issue URL, or owner/repo with --title")
    b.add_argument("--title")
    b.add_argument("--body-file")
    b.add_argument("--top", type=int, default=5)
    b.add_argument("-o", "--out")
    b.add_argument("--llm", action="store_true", help="add a plain-words summary (needs CONTRIBOS_LLM)")
    b.set_defaults(fn=cmd_brief)

    t = sub.add_parser("bench", help="score the brief against past changes")
    t.add_argument("repo")
    t.add_argument("-n", type=int, default=20)
    t.set_defaults(fn=cmd_bench)

    s = sub.add_parser("setup", help="exact setup steps, or diagnose a failure")
    s.add_argument("repo", nargs="?")
    s.add_argument("--diagnose", metavar="OUTPUT_FILE", help="explain a failed install or test run")
    s.set_defaults(fn=cmd_setup)

    c = sub.add_parser("check", help="review your branch before submitting")
    c.add_argument("path", nargs="?", default=".", help="your local checkout")
    c.add_argument("--base", help="branch you started from (default: auto-detect)")
    c.add_argument("--explain", help="your explanation of the change, in your own words")
    c.add_argument("--pr-draft", help="write a PR description draft to this file")
    c.add_argument("--title", help="PR title for the draft")
    c.add_argument("--verify-test", metavar="CMD", help="test command; proves it passes with and fails without your fix")
    c.add_argument("--ai", metavar="NOTE", help="how you used AI, e.g. \"Claude Code: found files, drafted test\"")
    c.add_argument("--issue", type=int, help="issue number, to look for competing PRs")
    c.set_defaults(fn=cmd_check)

    f = sub.add_parser("find", help="issues where you're welcome")
    f.add_argument("--lang", action="append", default=[], help="language, e.g. python (repeatable)")
    f.add_argument("--topic", action="append", default=[], help="interest keyword (repeatable)")
    f.add_argument("--repo", action="append", help="only look in these repos (repeatable)")
    f.add_argument("--limit", type=int, default=10)
    f.set_defaults(fn=cmd_find)

    cl = sub.add_parser("claim", help="draft a proposal to post on an issue before coding")
    cl.add_argument("target", help="issue URL, or owner/repo with --title")
    cl.add_argument("--title")
    cl.add_argument("--body-file")
    cl.set_defaults(fn=cmd_claim)

    r = sub.add_parser("review", help="coach for reviewer feedback on your PR")
    r.add_argument("pr", nargs="?", help="PR URL")
    r.add_argument("--data", help="saved JSON: author, review_comments, issue_comments, reviews")
    r.add_argument("--path", help="your local checkout, to show the code each comment points at")
    r.set_defaults(fn=cmd_review)

    rc = sub.add_parser("record", help="your verifiable contribution record")
    rc.add_argument("user")
    rc.add_argument("--html", help="also write a shareable HTML page")
    rc.add_argument("--limit", type=int, default=30)
    rc.add_argument("--include-own", action="store_true", help="include PRs to your own repos")
    rc.set_defaults(fn=cmd_record)

    ar = sub.add_parser("agent-rules", help="make your coding agent follow this repo's rules")
    ar.add_argument("path", nargs="?", default=".", help="your local checkout")
    ar.add_argument("--ai", metavar="TOOL", help="the AI tool you use, for disclosure lines (e.g. \"Claude Code\")")
    ar.set_defaults(fn=cmd_agent_rules)

    tn = sub.add_parser("tone", help="check a message you're about to post")
    tn.add_argument("file")
    tn.add_argument("--kind", choices=["comment", "intro", "reply", "explanation"], default="comment")
    tn.set_defaults(fn=cmd_tone)

    m = sub.add_parser("mcp", help="run as an MCP server over stdio")
    m.set_defaults(fn=cmd_mcp)

    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except (ValueError, RuntimeError, OSError) as e:
        if os.environ.get("CONTRIBOS_DEBUG"):
            raise
        msg = str(e)
        if isinstance(e, FileNotFoundError) and e.filename:
            msg = f"File not found: {e.filename}"
        print(f"contribos: {msg}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
