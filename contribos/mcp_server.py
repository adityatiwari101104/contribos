"""MCP server: gives coding agents (Claude Code, Cursor, Codex, ...) ContribOS as tools.

Speaks the Model Context Protocol over stdio (newline-delimited JSON-RPC 2.0),
with no dependencies. Register it with your agent, for example:

    claude mcp add contribos -- python -m contribos mcp

Each tool runs the matching CLI command and returns its markdown, so the
agent reads the same evidence a person would.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile

from . import __version__

PROTOCOL = "2025-06-18"
_TEMPS: list[str] = []


def _tmp(content: str | dict, suffix: str = ".md") -> str:
    """Agents pass text, the CLI reads files: write it to a temp file."""
    f = tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False, prefix="contribos-mcp-")
    f.write(json.dumps(content) if isinstance(content, dict) else content)
    f.close()
    _TEMPS.append(f.name)
    return f.name


def _check_argv(a: dict) -> list[str]:
    argv = ["check", a.get("path", ".")]
    if a.get("explanation"):
        argv += ["--explain", _tmp(a["explanation"])]
    elif a.get("explain_file"):
        argv += ["--explain", a["explain_file"]]
    for key, flag in (("base", "--base"), ("verify_test", "--verify-test"), ("ai_note", "--ai"),
                      ("issue", "--issue")):
        if a.get(key) not in (None, ""):
            argv += [flag, str(a[key])]
    return argv

TOOLS = [
    {"name": "contribos_policy",
     "description": "What a GitHub repository expects from outside contributors: AI policy, vouch/trust gates, "
                    "claim-first norms, tests, changelog, sign-off, activity. Every finding cites file:line. "
                    "Call this before starting work on any repo.",
     "inputSchema": {"type": "object", "properties": {
         "repo": {"type": "string", "description": "owner/name"},
         "json": {"type": "boolean", "description": "return machine-readable rules instead of markdown"}},
         "required": ["repo"]},
     "argv": lambda a: ["policy", a["repo"]] + (["--json"] if a.get("json") else [])},
    {"name": "contribos_brief",
     "description": "Evidence-backed contribution brief for an issue: files to read with reasons, similar past "
                    "PRs, related tests, look-alike files to avoid, house rules and likely review questions.",
     "inputSchema": {"type": "object", "properties": {
         "issue_url": {"type": "string", "description": "GitHub issue URL (needs API access)"},
         "repo": {"type": "string", "description": "owner/name, when giving title instead of issue_url"},
         "title": {"type": "string"}}},
     "argv": lambda a: ["brief", a["issue_url"]] if a.get("issue_url") else ["brief", a["repo"], "--title", a["title"]]},
    {"name": "contribos_setup",
     "description": "Exact steps to set up and test a repository, taken from its CI and contributing docs.",
     "inputSchema": {"type": "object", "properties": {"repo": {"type": "string"}}, "required": ["repo"]},
     "argv": lambda a: ["setup", a["repo"]]},
    {"name": "contribos_diagnose",
     "description": "Explain a failed install or test run: missing dependency, wrong version, service not "
                    "running, missing env var, flaky test, or a real test failure.",
     "inputSchema": {"type": "object", "properties": {
         "log": {"type": "string", "description": "the failing command's output"},
         "output_file": {"type": "string", "description": "or a file holding that output"}}},
     "argv": lambda a: ["setup", "--diagnose", _tmp(a["log"], ".txt") if a.get("log") else a["output_file"]]},
    {"name": "contribos_check",
     "description": "Pre-submit review of the local branch against the repo's rules and history: scope versus "
                    "similar past changes, tests, changelog, sign-off, debug leftovers, AI policy, and whether the "
                    "contributor's own explanation covers the change, AI-disclosure trailers, competing PRs, and "
                    "(with verify_test) whether the test fails without the fix. Do not write the explanation for "
                    "the user.",
     "inputSchema": {"type": "object", "properties": {
         "path": {"type": "string", "description": "local checkout", "default": "."},
         "base": {"type": "string", "description": "branch the work started from (default: auto-detect)"},
         "explanation": {"type": "string", "description": "the contributor's own explanation, as text"},
         "explain_file": {"type": "string", "description": "or a file holding it"},
         "verify_test": {"type": "string", "description": "test command to run with and without the fix"},
         "ai_note": {"type": "string", "description": "how AI was used, e.g. 'Claude Code: drafted the test'"},
         "issue": {"type": "integer", "description": "issue number, to look for competing PRs"}}},
     "argv": _check_argv},
    {"name": "contribos_review",
     "description": "Review coach for an open PR: classifies each reviewer comment, shows the code it points at, "
                    "drafts replies, and lists comments still waiting on the author.",
     "inputSchema": {"type": "object", "properties": {
         "pr_url": {"type": "string", "description": "PR URL (needs API access)"},
         "data": {"type": "object", "description": "or saved review data: author, review_comments, "
                                                  "issue_comments, reviews"},
         "path": {"type": "string", "description": "local checkout, to show the code each comment points at"}}},
     "argv": lambda a: (["review", a["pr_url"]] if a.get("pr_url") else ["review", "--data", _tmp(a["data"], ".json")])
                       + (["--path", a["path"]] if a.get("path") else [])},
    {"name": "contribos_claim",
     "description": "Draft the short proposal a contributor posts on an issue before coding (and a vouch "
                    "introduction where the repo needs one). The human rewrites and posts it; never post it.",
     "inputSchema": {"type": "object", "properties": {
         "issue_url": {"type": "string", "description": "GitHub issue URL (needs API access)"},
         "repo": {"type": "string", "description": "owner/name, when giving title instead of issue_url"},
         "title": {"type": "string"}}},
     "argv": lambda a: ["claim", a["issue_url"]] if a.get("issue_url") else ["claim", a["repo"], "--title", a["title"]]},
    {"name": "contribos_tone",
     "description": "Check text the human is about to post for machine-written tells, length, and a clear "
                    "question. Points at problems; the human rewrites.",
     "inputSchema": {"type": "object", "properties": {
         "text": {"type": "string"},
         "kind": {"type": "string", "enum": ["comment", "intro", "reply", "explanation"]}},
         "required": ["text"]},
     "argv": lambda a: ["tone", _tmp(a["text"]), "--kind", a.get("kind") or "comment"]},
    {"name": "contribos_find",
     "description": "Find open issues where a newcomer is actually welcome, with takeability evidence.",
     "inputSchema": {"type": "object", "properties": {
         "language": {"type": "string"}, "topic": {"type": "string"},
         "repos": {"type": "array", "items": {"type": "string"}}}},
     "argv": lambda a: ["find"] + (["--lang", a["language"]] if a.get("language") else [])
                       + (["--topic", a["topic"]] if a.get("topic") else [])
                       + sum((["--repo", r] for r in a.get("repos", [])), [])},
]


def _run_cli(argv: list[str]) -> tuple[str, bool]:
    from .cli import main
    out, err = io.StringIO(), io.StringIO()
    code = 1
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(argv)
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 1
        except Exception as e:  # report, don't crash the server
            print(f"Error: {e}", file=sys.stderr)
    text = out.getvalue() + (("\n" + err.getvalue()) if err.getvalue() else "")
    # check exits 1 when it finds problems; that is a result, not an error.
    return text.strip() or "(no output)", code not in (0, 1) or not out.getvalue()


def handle(msg: dict) -> dict | None:
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:
        return None  # notification
    if method == "initialize":
        result = {"protocolVersion": msg.get("params", {}).get("protocolVersion", PROTOCOL),
                  "capabilities": {"tools": {}},
                  "serverInfo": {"name": "contribos", "version": __version__}}
    elif method == "tools/list":
        result = {"tools": [{k: v for k, v in t.items() if k != "argv"} for t in TOOLS]}
    elif method == "tools/call":
        params = msg.get("params", {})
        tool = next((t for t in TOOLS if t["name"] == params.get("name")), None)
        if tool is None:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "unknown tool"}}
        try:
            argv = tool["argv"](params.get("arguments") or {})
        except KeyError as e:
            return {"jsonrpc": "2.0", "id": mid,
                    "result": {"content": [{"type": "text", "text": f"Missing argument: {e}"}], "isError": True}}
        try:
            text, is_error = _run_cli(argv)
        finally:
            while _TEMPS:
                os.unlink(_TEMPS.pop())
        result = {"content": [{"type": "text", "text": text}], "isError": is_error}
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"unknown method {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def serve(stdin=sys.stdin, stdout=sys.stdout) -> int:
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            reply = handle(msg)
        if reply is not None:
            stdout.write(json.dumps(reply) + "\n")
            stdout.flush()
    return 0
