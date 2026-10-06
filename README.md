# ContribOS

<!-- mcp-name: io.github.adityatiwari101104/contribos -->

[![PyPI version](https://img.shields.io/pypi/v/contribos.svg?color=blue)](https://pypi.org/project/contribos/)
[![Downloads](https://static.pepy.tech/badge/contribos)](https://pepy.tech/project/contribos)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python versions](https://img.shields.io/pypi/pyversions/contribos.svg)](https://pypi.org/project/contribos/)
[![Dependencies](https://img.shields.io/badge/dependencies-zero-brightgreen)](https://pypi.org/project/contribos/)

**Get trusted. Get merged. Come back.** ContribOS helps new open-source contributors earn trust. It finds where you're welcome, shows how this repo wants the change done, checks that you truly understand your change, coaches you through review, and turns merged PRs into a record the next maintainer can verify.

In 2026, writing code is not the hard part of contributing. AI made PRs cheap, maintainers answered with PR caps, vouch lists and AI policies, and newcomer merge rates fell. ContribOS makes your PR worth a maintainer's time.

ContribOS **never** opens PRs, posts comments or claims issues for you, and it never writes your explanation. It points, and you decide.

## See it work

Before you open a PR, ask the repo what it expects. This is real output from `contribos policy pallets/flask`, trimmed (snapshot from 6 Oct 2026; run it yourself for current results):

````text
# Contribution policy: pallets/flask

## AI policy
No written AI policy found. Disclosing AI use is still the courteous default.

## Tests
Changes are expected to come with tests.
- `.github/pull_request_template.md:20`: - Add tests that demonstrate the correct behavior of the change. Tests

## Changelog
Changes usually need a changelog or news entry.
- `.github/pull_request_template.md:23`: - Add an entry in CHANGES.rst summarizing the change and linking to the issue.

## Tests in practice
50% of recent code PRs also changed tests.
- `git history`: 19 of 38 recent code-changing PRs

## Outside PRs
2 of the last 49 closed PRs from non-members landed.
````

Every claim points at a file and line (or git history), so you can check it yourself. ContribOS points, you decide.

````bash
pip install contribos
contribos policy pallets/flask
````

## The journey

| Step | Command | What you get |
|---|---|---|
| Find | `contribos find --lang python --topic web` | Open issues sorted into Good bet / Possible / Skip, each with evidence: claimed? open PR? maintainer active? clear? Plus repo welcome: archived, vouch list, AI policy, outside-PR merge rate, how long outside PRs wait for a first human reply, how many stall, a stale bot, and your own open PRs there |
| Rules | `contribos policy owner/repo` | The repo's AI policy, disclosure format (`Assisted-by:` and similar trailers), vouch gate, "no AI on good first issues", issue-first and claim-first norms, tests, changelog, DCO/CLA, style tools and activity, each quoted with `file:line`. `--json` gives the same rules in a form agents can read |
| Propose | `contribos claim <issue-url>` | A short proposal to post *before* coding: likely files, a similar-size past PR, where the test goes, and one real question. Warns about taken issues, no maintainer yet, good-first-issue AI rules and issue-first rules. If the repo has a vouch list, it drafts the introduction first |
| Tone | `contribos tone comment.md --kind comment` | Flags machine-written tells (delve, buzzwords, assistant openers, filler), length, a missing question and leftover placeholders in anything you're about to post |
| Agent rules | `contribos agent-rules . --ai "Claude Code"` | Teaches your coding agent this repo's rules: a SKILL.md for Claude Code, Codex, Copilot and Cursor, a hook that stops the agent opening PRs or posting comments, and a commit-msg hook for sign-off and the AI-disclosure trailer. All local-only, excluded from git |
| Setup | `contribos setup owner/repo` | Exact steps taken from the repo's CI and contributing guide: runtime version, install, services, env vars, test and lint commands |
| Diagnose | `contribos setup --diagnose output.txt` | What a failure means: missing dependency, wrong version, service down, env var, native build tools, flaky test, or a real test failure (and how to check it isn't yours) |
| Understand | `contribos brief <issue-url>` | Files to read with the reasons each was picked, similar past PRs and what reviewers said, related tests, look-alike files to leave alone, house rules, and likely review questions. Add `--llm` for a plain-words summary that is grounded in the evidence |
| Prove | `contribos check --explain explain.md --verify-test "pytest tests/test_x.py" --issue 123 --ai "Claude Code: drafted the test" --pr-draft pr.md` | Pre-submit review: scope versus similar past changes, tests, changelog, sign-off, debug leftovers, AI policy and disclosure trailers, competing PRs for the same issue. `--verify-test` runs your test with the fix and again with your source files swapped back to the base version, so you can show it fails without your change. It checks that *your* explanation covers every changed file and states what you're unsure of, then drafts the PR: what and why, how you verified it, what you're unsure about, related past changes, AI assistance |
| Respond | `contribos review <pr-url> --path .` | Each reviewer comment classified (blocking, change, question, nit…), the code it points at, the house rule it echoes, a reply draft, what's still unanswered and for how long, and failing CI |
| Grow | `contribos record <github-user> --html me.html` | A public, linkable record: merged PRs, change requests, whether you answered every review comment, whether you explained your change and disclosed AI use |
| Agents | `contribos mcp` | Nine MCP tools for Claude Code, Cursor, Codex and others (policy, brief, setup, diagnose, check, review, find, claim, tone), taking text as input where an agent has text: `claude mcp add contribos -- python -m contribos mcp` |

## Install

```sh
pip install contribos     # Python 3.10+, git. Zero external dependencies.
export GITHUB_TOKEN=...   # needed for find, claim/brief from an issue URL, review, record
`policy` also runs without a token, but it skips pull-request signals and says so at the top of its output.
```

Everything that can run offline does: `policy`, `brief --title`, `claim --title`, `setup`, `check`, `bench` and `review --data` need only `git`. Add `--offline` to skip the API, and `--update` to fetch new commits. Clones and indexes are cached in `~/.cache/contribos` (`CONTRIBOS_CACHE`).

Optional AI summaries: `CONTRIBOS_LLM=anthropic` (with `ANTHROPIC_API_KEY`) or `CONTRIBOS_LLM=openai` (with `OPENAI_API_KEY`), plus optional `CONTRIBOS_MODEL`. The model only explains evidence ContribOS already gathered. Any sentence citing a file outside that evidence is dropped.

## How it works

- `repo.py` handles cached clones. It reads any revision with `git show` and `git grep`, so no checkout is needed.
- `precedent.py` builds a SQLite index of main-line changes (squash and merge PRs), the files they touched, and the issues they fixed.
- `policy.py` is the policy radar, built from contribution docs, AI policies, templates, vouch lists, CI config and history.
- `brief.py` ranks files by rare-term matches in code, path matches, and files touched by similar past PRs. It also finds related tests via co-change history.
- `check.py` is the pre-submit check, the proof-of-understanding check and the PR draft.
- `setup_doctor.py` produces setup steps from CI and docs, and diagnoses failures.
- `find.py` checks issue takeability, repo welcome and responsiveness.
- `claim.py` drafts the proposal and vouch introduction.
- `proof.py` runs a test with and without your change (files restored in `finally`).
- `tone.py` checks text you're about to post.
- `agent_rules.py` writes the local agent skill and hooks.
- `review.py` classifies review comments, drafts replies and tracks follow-through.
- `record.py` builds the contribution record (markdown plus a self-contained HTML page).
- `llm.py` is the optional provider layer (Anthropic or OpenAI) with citation grounding.
- `mcp_server.py` is a dependency-free MCP stdio server.
- `github.py` is the optional API client. Every caller handles its absence.
- `bench.py` runs the history benchmark.

## Benchmark (2026-09-28, offline, PR titles standing in for issues)

| Repo | Brief hit@5 | Grep-only hit@5 |
|---|---|---|
| pallets/flask (30 PRs) | 0.77 | 0.73 |
| pytest-dev/pytest (30 PRs) | 0.87 | 0.87 |

Matching against past PRs barely improves file finding, which supports the plan's bet that finding files is a commodity. The value is in how past PRs did it and what reviewers asked. The next benchmark needs real issue text and review comments, which requires `GITHUB_TOKEN`.

## Contributing

Contributions are welcome, including for Hacktoberfest. Start with issues labelled `good first issue`, comment on one to claim it, and read [CONTRIBUTING.md](CONTRIBUTING.md) first. It covers claiming an issue, disclosing AI use, and running the tests. To see these rules the way ContribOS reads them:

```sh
contribos policy adityatiwari101104/contribos
```


## Status

- Tested on real repos (Flask, pytest, Ghostty): `policy`, `brief`, `claim --title`, `setup`, `check`, `bench`, `mcp`.
- Tested against the live GitHub API on 2026-10-01: `find` (scored issue takeability and maintainer response medians on `maximilianfeix/proxy-scraper` and `brekkylab/backlot`), `claim <url>` (drafted pre-coding proposals with past similar PRs and file targets), and `record` (verified public merged PR portfolio for `@adityatiwari101104`).
- Tested on temporary git repos: `agent-rules` (git status stays clean, the guard blocks `gh pr create`, the commit-msg hook enforces sign-off and the trailer), `check --verify-test`, `tone`, policy radar v2.
- Tests: `python -m unittest discover -s tests` (42 tests, CI matrix on Linux, macOS, and Windows).

## Releasing

1. Bump the version in `pyproject.toml`, `contribos/__init__.py` and both places in `server.json`.
2. Run `python -m unittest discover -s tests`, then commit and push.
3. Tag and publish:

```sh
git tag v<version>
git push origin v<version>
python -m build
twine upload dist/*
mcp-publisher login github
mcp-publisher publish
```
