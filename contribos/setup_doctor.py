"""Setup doctor: exact steps to get a repository running, and what a failure means.

The ground truth for "how do I run this" is what the repo's own CI runs,
so steps come from workflow files first, then task runners, lockfiles and
contribution docs. Every step cites the file it came from.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .repo import Repo

SERVICE_IMAGES = {
    "postgres": "PostgreSQL", "postgis": "PostgreSQL", "mysql": "MySQL", "mariadb": "MariaDB",
    "redis": "Redis", "valkey": "Valkey", "mongo": "MongoDB", "rabbitmq": "RabbitMQ",
    "elasticsearch": "Elasticsearch", "opensearch": "OpenSearch", "memcached": "Memcached",
    "minio": "MinIO", "kafka": "Kafka", "localstack": "LocalStack",
}
RELEASE_WF = re.compile(r"(release|publish|deploy|lock|stale|label|triage|backport|update|prepare|docs|"
                        r"doc|link|zizmor|codeql|scorecard|welcome|greet|sponsor|milestone|changelog|nightly|tag)", re.I)
RUN_TOOLS = re.compile(r"^(uv|pip|pip3|python|python3|poetry|pdm|hatch|tox|nox|pytest|make|just|npm|npx|"
                       r"pnpm|yarn|bun|go|cargo|zig|bundle|rake|mvn|gradle|\./gradlew|docker|pre-commit|"
                       r"ruff|mypy|pyright)\b")


@dataclass
class Step:
    title: str
    commands: list[str] = field(default_factory=list)
    source: str = ""
    note: str = ""


@dataclass
class SetupPlan:
    repo: str
    languages: list[str]
    steps: list[Step]
    services: dict[str, str]          # name -> source
    env_vars: dict[str, str]          # name -> source
    ci_commands: list[tuple[str, str]]
    warnings: list[str]


def _read(repo: Repo, path: str, rev: str) -> str:
    return repo.read(path, rev) or ""


def _ci_runs(repo: Repo, rev: str, files: list[str]) -> tuple[list[tuple[str, str]], dict[str, str], dict[str, str], list[str]]:
    """Extract run commands, services, env names and runtime versions from CI workflows."""
    runs, services, envs, versions = [], {}, {}, []
    workflows = [f for f in files if re.match(r"\.github/workflows/.+\.ya?ml$", f)
                 and not RELEASE_WF.search(f.rsplit("/", 1)[-1])]
    # Test workflows first, so their commands win.
    workflows.sort(key=lambda f: 0 if re.search(r"test|ci|check|main|build", f.rsplit("/", 1)[-1]) else 1)
    for wf in workflows:
        text = _read(repo, wf, rev)
        lines = text.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            m = re.match(r"^(\s*)-?\s*run:\s*(.*)$", line)
            if m:
                indent, cmd = len(m.group(1)), m.group(2).strip()
                if cmd in ("|", ">", "|-", ">-"):
                    block = []
                    i += 1
                    while i < len(lines) and (not lines[i].strip() or len(lines[i]) - len(lines[i].lstrip()) > indent):
                        if lines[i].strip():
                            block.append(lines[i].strip())
                        i += 1
                    for b in block:
                        if RUN_TOOLS.match(b) and (c := _clean(b)):
                            runs.append((c, wf))
                    continue
                if RUN_TOOLS.match(cmd) and (c := _clean(cmd)):
                    runs.append((c, wf))
            if m := re.search(r"image:\s*['\"]?([\w./-]+)", line):
                _add_service(services, m.group(1), wf)
            if m := re.match(r"^\s*(python-version|node-version|go-version|java-version|ruby-version):\s*(.+)$", line):
                val = m.group(2).strip().strip("'\"")
                if "${{" not in val:
                    versions.append(f"{m.group(1).split('-')[0]} {val} ({wf})")
            env_line = re.match(r"^\s+([A-Z][A-Z0-9]*_[A-Z0-9_]+)\s*:\s*\S", line)
            for name in ([env_line.group(1)] if env_line else []):
                if name not in ("TOX_ENV", "FORCE_COLOR", "PIP_DISABLE_PIP_VERSION_CHECK", "CI", "PY_COLORS",
                                "GITHUB_TOKEN", "UV_PYTHON") and "secrets." not in line:
                    envs.setdefault(name, wf)
            i += 1
    return runs, services, envs, versions


def _clean(cmd: str) -> str | None:
    """Turn CI-only syntax into something a person can run, or drop the command."""
    cmd = re.sub(r"\$\{\{\s*matrix\.(\w+)\s*\}\}", r"<\1>", cmd)
    if "${{" in cmd or "`" in cmd or "dist/" in cmd:
        return None
    return cmd


def _add_service(services: dict[str, str], image: str, source: str) -> None:
    base = image.rsplit("/", 1)[-1].split(":")[0].lower()
    for key, label in SERVICE_IMAGES.items():
        if key in base:
            services.setdefault(label, source)


def _matrix_pythons(repo: Repo, rev: str, files: list[str]) -> list[str]:
    found = set()
    for wf in [f for f in files if re.match(r"\.github/workflows/.+\.ya?ml$", f)]:
        found |= set(re.findall(r"python:\s*['\"]?(3\.\d+)['\"]?", _read(repo, wf, rev)))
    return sorted(found, key=lambda v: int(v.split(".")[1]))


def _doc_commands(repo: Repo, rev: str, files: list[str]) -> list[tuple[str, str]]:
    """Shell commands inside fenced code blocks of contribution docs."""
    docs = [f for f in files if re.search(r"(^|/)(contributing|hacking|development|dev)(\.\w+)?$", f.lower())
            or f.lower() in ("docs/contributing.rst", "docs/development.md")]
    out = []
    for d in docs:
        text = _read(repo, d, rev)
        in_block = False
        for line in text.splitlines():
            s = line.strip()
            if s.startswith(("```", "~~~")) or s.startswith(".. code-block::") or s.endswith("::"):
                in_block = not in_block if s.startswith(("```", "~~~")) else True
                continue
            cmd = s.lstrip("$> ").strip()
            if (in_block or line.startswith(("    ", "\t"))) and RUN_TOOLS.match(cmd):
                if (cmd, d) not in out:
                    out.append((cmd, d))
    return out[:12]


def plan(repo: Repo, rev: str = "HEAD") -> SetupPlan:
    files = repo.files(rev)
    fset = set(files)
    langs, steps, warnings = [], [], []
    runs, services, envs, versions = _ci_runs(repo, rev, files)

    # Compose files and example env files.
    for f in files:
        low = f.lower()
        if re.search(r"(^|/)(docker-)?compose[\w.-]*\.ya?ml$", low):
            for img in re.findall(r"image:\s*['\"]?([\w./-]+)", _read(repo, f, rev)):
                _add_service(services, img, f)
        if re.search(r"(^|/)\.env\.(example|sample|template|dist)$", low) or low.endswith("env.example"):
            for name in re.findall(r"^\s*([A-Z][A-Z0-9_]+)\s*=", _read(repo, f, rev), re.M):
                envs[name] = f

    # Runtime.
    py = "pyproject.toml" in fset or "setup.py" in fset or any(f.startswith("requirements") for f in files)
    node = "package.json" in fset
    if py:
        langs.append("Python")
        want = None
        if ".python-version" in fset:
            want = (_read(repo, ".python-version", rev).strip(), ".python-version")
        elif m := re.search(r'requires-python\s*=\s*"([^"]+)"', _read(repo, "pyproject.toml", rev)):
            want = (m.group(1), "pyproject.toml")
        pythons = _matrix_pythons(repo, rev, files)
        safe = pythons[-2] if len(pythons) >= 3 else (pythons[-1] if pythons else None)
        note = (f"CI tests {pythons[0]} to {pythons[-1]}; {safe} is a safe choice "
                "(the newest may be a pre-release).") if pythons else ""
        if want:
            steps.append(Step("Install Python " + want[0], [], want[1], note))
    if node:
        langs.append("Node.js")
        pkg = {}
        try:
            pkg = json.loads(_read(repo, "package.json", rev) or "{}")
        except json.JSONDecodeError:
            warnings.append("package.json could not be parsed")
        ver = None
        for f in (".nvmrc", ".node-version"):
            if f in fset:
                ver = (_read(repo, f, rev).strip(), f)
        if not ver and (eng := pkg.get("engines", {}).get("node")):
            ver = (eng, "package.json engines")
        if ver:
            steps.append(Step(f"Install Node.js {ver[0]}", [], ver[1]))
    for marker, lang in (("go.mod", "Go"), ("Cargo.toml", "Rust"), ("build.zig", "Zig"), ("Gemfile", "Ruby"),
                         ("pom.xml", "Java"), ("build.gradle", "Java"), ("build.gradle.kts", "Kotlin")):
        if marker in fset and lang not in langs:
            langs.append(lang)
            if marker == "go.mod" and (m := re.search(r"^go\s+(\S+)", _read(repo, "go.mod", rev), re.M)):
                steps.append(Step(f"Install Go {m.group(1)}", [], "go.mod"))
            if marker == "build.zig" and (m := re.search(r'minimum_zig_version\s*=\s*"([^"]+)"',
                                                          _read(repo, "build.zig.zon", rev))):
                steps.append(Step(f"Install Zig {m.group(1)}", [], "build.zig.zon"))
    if "flake.nix" in fset:
        steps.append(Step("Or get every tool at once with Nix", ["nix develop"], "flake.nix",
                          "The maintainers pin the whole toolchain here; this is the most reliable route if you use Nix."))
    for f in ("rust-toolchain.toml", "rust-toolchain", ".tool-versions"):
        if f in fset:
            steps.append(Step("Use the pinned toolchain", [f"cat {f}"], f))

    # Dependencies.
    if py:
        pyproject = _read(repo, "pyproject.toml", rev)
        if "uv.lock" in fset:
            groups = re.findall(r"^\s*(\w[\w-]*)\s*=\s*\[", pyproject.split("[dependency-groups]")[1], re.M) \
                if "[dependency-groups]" in pyproject else []
            cmd = "uv sync" + ("" if not groups else "  # installs the default 'dev' group")
            steps.append(Step("Install dependencies", [cmd], "uv.lock"))
        elif "poetry.lock" in fset:
            steps.append(Step("Install dependencies", ["poetry install"], "poetry.lock"))
        elif "pdm.lock" in fset:
            steps.append(Step("Install dependencies", ["pdm install"], "pdm.lock"))
        elif "Pipfile" in fset:
            steps.append(Step("Install dependencies", ["pipenv install --dev"], "Pipfile"))
        else:
            reqs = [f for f in files if re.match(r"requirements.*(dev|test).*\.txt$", f)]
            cmds = ["python -m venv .venv", "source .venv/bin/activate", "pip install -e ."]
            cmds += [f"pip install -r {r}" for r in reqs[:2]]
            steps.append(Step("Install dependencies", cmds, reqs[0] if reqs else "pyproject.toml"))
    if node:
        mgr = ("pnpm install" if "pnpm-lock.yaml" in fset else "yarn install" if "yarn.lock" in fset
               else "bun install" if "bun.lockb" in fset or "bun.lock" in fset else "npm ci")
        steps.append(Step("Install dependencies", [mgr], "lockfile"))

    if services:
        steps.append(Step("Start services", [f"# {s}" for s in services],
                          ", ".join(sorted(set(services.values()))),
                          "Easiest with Docker: the repo's compose file or the CI service images."))
    if envs:
        keep = {k: v for k, v in envs.items() if not k.startswith(("GITHUB_", "RUNNER_", "ACTIONS_"))}
        if keep:
            sample = next((v for v in keep.values() if ".env" in v), None)
            cmds = [f"cp {sample} .env"] if sample else []
            steps.append(Step("Set environment variables", cmds + [f"# {k}" for k in list(keep)[:8]],
                              ", ".join(sorted(set(keep.values())))[:120]))

    # How to test: prefer what CI runs.
    test_runs = [(c, s) for c, s in runs if re.search(r"\b(test|pytest|tox|nox|check)\b", c)]
    lint_runs = [(c, s) for c, s in runs if re.search(r"\b(lint|ruff|flake8|mypy|pyright|eslint|pre-commit|fmt)\b", c)]
    doc_cmds = _doc_commands(repo, rev, files)
    doc_tests = [(c, s) for c, s in doc_cmds if re.search(r"\b(test|pytest|tox|nox)\b", c)
                 and not re.search(r"\b(install|docs?|lint)\b", c)]
    if doc_tests:
        note = ("CI runs: " + "; ".join(f"`{c}`" for c, _ in test_runs[:2])) if test_runs else ""
        steps.append(Step("Run the tests (as the contributing guide says)", [c for c, _ in doc_tests[:3]],
                          doc_tests[0][1], note))
    elif test_runs:
        steps.append(Step("Run the tests the way CI does", [c for c, _ in test_runs[:3]], test_runs[0][1]))
    elif (m := _test_from_runners(repo, rev, fset)):
        steps.append(m)
    if ".pre-commit-config.yaml" in fset:
        steps.append(Step("Install the pre-commit hooks", ["pre-commit install", "pre-commit run --all-files"],
                          ".pre-commit-config.yaml"))
    elif lint_runs:
        steps.append(Step("Run the linters", [c for c, _ in lint_runs[:2]], lint_runs[0][1]))

    if any(f.startswith(".devcontainer/") for f in files):
        warnings.append("This repo has a devcontainer: opening it in VS Code or Codespaces skips most of these steps.")
    for cmd, src in doc_cmds:
        if not any(cmd in s.commands for s in steps):
            warnings.append(f"The docs also mention `{cmd}` ({src}).")
    return SetupPlan(repo.slug, langs, steps, services, envs, runs, warnings[:8])


def _test_from_runners(repo: Repo, rev: str, fset: set[str]) -> Step | None:
    if "noxfile.py" in fset:
        return Step("Run the tests", ["nox"], "noxfile.py")
    if "tox.ini" in fset or "[tool.tox" in _read(repo, "pyproject.toml", rev):
        return Step("Run the tests", ["tox"], "tox.ini")
    if "Makefile" in fset and re.search(r"^test:", _read(repo, "Makefile", rev), re.M):
        return Step("Run the tests", ["make test"], "Makefile")
    if "package.json" in fset:
        try:
            scripts = json.loads(_read(repo, "package.json", rev)).get("scripts", {})
        except json.JSONDecodeError:
            scripts = {}
        if "test" in scripts:
            return Step("Run the tests", ["npm test"], "package.json scripts.test")
    if "Cargo.toml" in fset:
        return Step("Run the tests", ["cargo test"], "Cargo.toml")
    if "go.mod" in fset:
        return Step("Run the tests", ["go test ./..."], "go.mod")
    if "build.zig" in fset:
        return Step("Run the tests", ["zig build test"], "build.zig")
    return None


def to_markdown(p: SetupPlan) -> str:
    L = [f"# Setup: {p.repo}", "", f"Languages: {', '.join(p.languages) or 'unknown'}", ""]
    for i, s in enumerate(p.steps, 1):
        L.append(f"{i}. **{s.title}**" + (f" _(from `{s.source}`)_" if s.source else ""))
        if s.commands:
            L += ["   ```sh"] + [f"   {c}" for c in s.commands] + ["   ```"]
        if s.note:
            L.append(f"   {s.note}")
    if p.warnings:
        L += ["", "## Notes", ""] + [f"- {w}" for w in p.warnings]
    L += ["", "If something fails, save the output and run `contribos setup --diagnose output.txt`."]
    return "\n".join(L)


# ---- Failure diagnosis -------------------------------------------------------

DIAGNOSES = [
    (re.compile(r"(?:Could not find a version that satisfies the requirement|No matching distribution found for) "
                r"([\w.\-\[\]]+)"), "Package version not available",
     "pip can't find a release of `{0}` for your Python and platform. Usually your Python version is too new "
     "or too old for the pinned version: use the Python version from step 1 of `contribos setup`."),
    (re.compile(r"ModuleNotFoundError: No module named ['\"]([\w.]+)['\"]"), "Missing dependency",
     "Python can't find `{0}`. Dependencies aren't installed in the environment running the tests. "
     "Rerun the install step from `contribos setup`, and make sure you run the tests inside that environment "
     "(e.g. `uv run pytest` or activate the venv)."),
    (re.compile(r"(requires a different Python|Python \d\.\d+ is not supported|requires-python|"
                r"SyntaxError: invalid syntax.*\n.*(match|:=)|unsupported operand type\(s\) for \|)"),
     "Wrong Python version", "Your Python is older or newer than this repo supports. Check the version in step 1."),
    (re.compile(r"(Connection refused|could not connect to server|ECONNREFUSED).*?(5432|3306|6379|27017|9200|5672)?",
                re.S), "Service not running",
     "A test tried to reach a database or service that isn't running. Start the services from `contribos setup`."),
    (re.compile(r"(KeyError: '([A-Z][A-Z0-9_]+)'|environment variable ['\"]?([A-Z][A-Z0-9_]+)|"
                r"([A-Z][A-Z0-9_]+) (is not set|must be set))"), "Missing environment variable",
     "A required environment variable is missing. Copy the example env file or export it."),
    (re.compile(r"(command not found|is not recognized as an internal|No such file or directory: '(\w+)')"),
     "Tool not installed", "A command-line tool the repo uses isn't installed or isn't on your PATH."),
    (re.compile(r"(npm ERR! engine|Unsupported engine|The engine \"node\" is incompatible)"),
     "Wrong Node.js version", "Your Node.js version doesn't match the repo's `engines` or `.nvmrc`."),
    (re.compile(r"(Permission denied|EACCES)"), "Permissions",
     "Don't use sudo for installs; use a virtualenv or a user-level install instead."),
    (re.compile(r"(error: Microsoft Visual C\+\+|gcc: error|fatal error: [\w./]+\.h: No such file|"
                r"Failed building wheel)"), "Native build tools missing",
     "A dependency needs a C compiler or system headers. Install your platform's build tools, "
     "or use a Python version that has prebuilt wheels."),
    (re.compile(r"(Timeout|timed out|flaky)", re.I), "Possibly flaky",
     "The failure looks timing-related. Rerun the single test; if it passes sometimes, it's flaky, not you."),
]


@dataclass
class Diagnosis:
    kind: str
    advice: str
    evidence: str


def diagnose(log: str) -> list[Diagnosis]:
    out = []
    for rx, kind, advice in DIAGNOSES:
        m = rx.search(log)
        if m:
            groups = [g for g in m.groups() if g] or [""]
            line = next((l for l in log.splitlines() if m.group(0).splitlines()[0][:40] in l), m.group(0))
            out.append(Diagnosis(kind, advice.format(*groups), line.strip()[:200]))
    failed = re.findall(r"^(?:FAILED|ERROR) ([\w/.\-]+::[\w\[\]\-.]+)", log, re.M)
    if failed and not out:
        out.append(Diagnosis(
            "Test failure", f"{len(failed)} test(s) failed with no setup problem detected. Check whether they "
            "fail on the base branch too (`git stash`, rerun, `git stash pop`). If they do, it isn't your change.",
            failed[0]))
    return out


def diagnosis_markdown(ds: list[Diagnosis]) -> str:
    if not ds:
        return ("No known setup problem found in this output. If a test fails, rerun it on the base branch: "
                "if it fails there too, the problem isn't your change.")
    L = ["# What went wrong", ""]
    for d in ds:
        L += [f"**{d.kind}.** {d.advice}", f"> `{d.evidence}`", ""]
    return "\n".join(L)
