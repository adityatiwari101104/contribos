# Contributing to ContribOS

Thanks for helping. ContribOS exists to make outside contributions easier for maintainers to review, so this repo tries to follow its own advice. Run `contribos policy adityatiwari101104/contribos` to see these rules as the tool reads them.

## Before you start

- **Claim the issue first.** Comment on the issue saying you want to work on it, with one or two sentences on your approach. Wait for a maintainer reply before opening a PR. Issues labelled `good first issue` are open to newcomers, one person at a time.
- **One issue, one PR.** Keep changes small and focused. If you find something unrelated, open a separate issue.
- **Ask when unsure.** A specific question on the issue is welcome and cheaper than a rejected PR.

## AI policy

AI tools are allowed, but you are responsible for every line you submit.

- Disclose AI assistance in the PR description (tool name and what it helped with) and add an `Assisted-by: <tool>` line to the commit message.
- Read, run and understand your change before opening the PR. You must be able to answer review questions yourself.
- Write PR descriptions and comments in your own words. Run `contribos tone <file>` on them if you like.
- Bulk or unreviewed AI-generated changes are a burden on reviewers. Submit only work you have read, run and can explain.

## Development setup

```bash
git clone https://github.com/<you>/contribos.git
cd contribos
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
python -m unittest discover -s tests
```

ContribOS has no runtime dependencies and supports Python 3.10 to 3.13 on Linux, macOS and Windows. Please keep it that way: no new runtime dependencies without discussing it in an issue first.

Many commands call the GitHub API. Unauthenticated requests are rate-limited, so set `GITHUB_TOKEN` (no scopes needed for public repos) if you hit HTTP 403.

## Making a change

1. Fork, then branch from `main`.
2. Add or update tests in `tests/`. Bug fixes need a regression test.
3. Run the full test suite and `contribos check` on your branch.
4. Keep commits focused, with messages like `fix(brief): ...` or `docs: ...`.

## Pull requests

Use the PR template. Link the issue (`Closes #N`), say how you tested, and disclose AI use. Expect a first response within a few days. Review comments are about the code, not you; reply to each one or push a fix.

## Reporting bugs

Open an issue with the command you ran, the output, your OS and Python version, and `contribos --version` output if available.
