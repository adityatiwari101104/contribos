"""Contribution brief: where to look, how this repo did it before, and what to watch for.

Ranking is deterministic and every file comes with the reasons it was picked:
term matches in the code, term matches in its path, and similar past changes
that touched it. No LLM is needed for the ranking itself.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .precedent import Change
from .repo import Repo
from .policy import is_code, is_test, Policy

STOP = set("""
a an and are as at be been but by can could did do does doesn't don't for from get gets
got has have having how i if in into is isn't it it's its just like make makes may me might
more most my no not now of on one or our out should so some such than that the their them
then there these they this those to too up us use used using via was we were what when where
which while who why will with without would you your also only any all each other same very
issue bug fix fixed fixes problem error errors expected actual behavior behaviour steps
reproduce version works working work please thanks thank hi hello example code case cases
new need needs want run running returns return value true false none null
""".split())
SKIP_DIRS = ("node_modules/", "vendor/", "third_party/", "dist/", "build/", ".git/")
LOOKALIKE_DIRS = ("legacy", "deprecated", "compat", "examples", "example", "docs", "doc",
                  "migrations", "benchmarks", "vendor", "_vendor", "contrib")
IDENT = re.compile(r"`([^`\n]{2,80})`|([A-Za-z_][\w]*(?:\.[A-Za-z_]\w*)+)|"
                   r"\b([a-z]+_[a-z_0-9]+)\b|\b([A-Z][a-z0-9]+(?:[A-Z][a-z0-9]*)+)\b|"
                   r"\b(\w+(?:Error|Exception|Warning))\b")
PRECEDENT_WEIGHT = float(__import__("os").environ.get("CONTRIBOS_PW", "2"))
WORD = re.compile(r"[A-Za-z][A-Za-z0-9]{3,}")


@dataclass
class Term:
    text: str
    weight: float   # identifiers count more than plain words


@dataclass
class FileHit:
    path: str
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)


@dataclass
class Brief:
    repo: str
    title: str
    terms: list[Term]
    files: list[FileHit]
    tests: list[FileHit]
    precedents: list[tuple[float, Change]]
    lookalikes: list[tuple[str, str]]
    questions: list[str]
    scope_hint: str | None


def extract_terms(text: str, limit: int = 20) -> list[Term]:
    idents: Counter[str] = Counter()
    for m in IDENT.finditer(text):
        tok = next(g for g in m.groups() if g)
        tok = tok.strip().strip("()")
        if len(tok) < 3 or " " in tok and len(tok.split()) > 3:
            continue
        idents[tok] += 1
        # Also index the last part of dotted names (module.func -> func).
        if "." in tok and not tok.endswith((".py", ".js", ".ts", ".md")):
            idents[tok.rsplit(".", 1)[-1]] += 1
    words = Counter(w.lower() for w in WORD.findall(text) if w.lower() not in STOP)
    terms: dict[str, Term] = {}
    for tok, n in idents.most_common():
        if tok.lower() in STOP:
            continue
        terms.setdefault(tok.lower(), Term(tok, 2.0 + min(n, 3) * 0.3))
    for w, n in words.most_common():
        if w not in terms:
            terms[w] = Term(w, 1.0 + min(n, 3) * 0.2)
    return sorted(terms.values(), key=lambda t: -t.weight)[:limit]


def _tokens(text: str) -> set[str]:
    return {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9]{2,}", text)} - STOP


def _path_tokens(path: str) -> set[str]:
    parts = re.split(r"[/_.\-]", path.lower())
    return {p for p in parts if len(p) > 2}


def rank_precedents(terms: list[Term], history: list[Change], k: int = 8) -> list[tuple[float, Change]]:
    """BM25-style match of issue terms against past change titles and bodies."""
    docs = [(c, _tokens(f"{c.title} {c.title} {c.body[:600]}")) for c in history]
    n = len(docs) or 1
    df: Counter[str] = Counter()
    for _, toks in docs:
        df.update(toks)
    qterms = {}
    for t in terms:
        for tok in _tokens(t.text) | ({t.text.lower()} if "_" in t.text else set()):
            qterms[tok] = max(qterms.get(tok, 0), t.weight)
    scored = []
    for c, toks in docs:
        s = sum(w * math.log(1 + n / (1 + df[q])) for q, w in qterms.items() if q in toks)
        if s > 0 and c.files:
            scored.append((s / math.sqrt(1 + len(toks) / 20), c))
    scored.sort(key=lambda x: -x[0])
    return scored[:k]


def build(repo: Repo, title: str, body: str, history: list[Change], policy: Policy | None,
          rev: str = "HEAD", top: int = 5) -> Brief:
    terms = extract_terms(f"{title}\n{title}\n{body}")
    all_files = [p for p in repo.files(rev) if not p.startswith(SKIP_DIRS)]
    candidates = [p for p in all_files if is_code(p) or is_test(p)]
    cand_set = set(candidates)
    n_files = len(candidates) or 1
    hits: dict[str, FileHit] = {}

    def hit(path: str) -> FileHit:
        return hits.setdefault(path, FileHit(path))

    # 1. Content matches, weighted by how rare the term is in the repo.
    for t in terms[:15]:
        counts = {p: c for p, c in repo.grep(t.text, rev).items() if p in cand_set}
        if not counts or len(counts) > n_files * 0.3:
            continue  # absent, or too common to say anything
        idf = math.log(n_files / len(counts))
        for p, c in counts.items():
            h = hit(p)
            h.score += t.weight * idf * math.log(1 + c)
            if len(h.reasons) < 6:
                h.reasons.append(f"mentions `{t.text}` ({c}×)")

    # 2. Path matches.
    for p in candidates:
        ptoks = _path_tokens(p)
        matched = [t.text for t in terms if t.text.lower() in ptoks]
        if matched:
            h = hit(p)
            h.score += 2.0 * sum(1 for _ in matched)
            h.reasons.append("path matches " + ", ".join(f"'{m}'" for m in matched[:3]))

    # 3. Files touched by similar past changes.
    precedents = rank_precedents(terms, history)
    if precedents:
        best = precedents[0][0]
        for s, c in precedents:
            for p in c.files:
                if p in cand_set:
                    h = hit(p)
                    h.score += PRECEDENT_WEIGHT * s / best
                    label = f"PR #{c.pr}" if c.pr else c.sha[:8]
                    h.reasons.append(f"changed by similar {label}: “{_short(c.title, 70)}”")

    ranked = sorted(hits.values(), key=lambda h: -h.score)
    code = [h for h in ranked if is_code(h.path)][:top]
    tests = _related_tests(code, ranked, history)

    lookalikes = []
    top_paths = {h.path for h in code}
    for h in ranked[: top * 4]:
        if h.path in top_paths or not is_code(h.path):
            continue
        dirs = set(h.path.lower().split("/")[:-1])
        if dirs & set(LOOKALIKE_DIRS):
            lookalikes.append((h.path, "matches the issue's words but lives in "
                               f"'{'/'.join(sorted(dirs & set(LOOKALIKE_DIRS)))}', which is rarely the place for a fix"))
        elif all(r.startswith("path matches") for r in h.reasons):
            lookalikes.append((h.path, "name looks related, but no code or past change links it to this issue"))
        if len(lookalikes) >= 3:
            break

    scope_hint = None
    sizes = sorted(len([f for f in c.files if is_code(f)]) for _, c in precedents[:5] if c.files)
    if sizes:
        median = sizes[len(sizes) // 2]
        scope_hint = (f"Similar past changes touched a median of {median} code file"
                      f"{'s' if median != 1 else ''}. If yours grows well past that, expect questions about scope.")

    return Brief(repo.slug, title, terms, code, tests, precedents[:5], lookalikes,
                 _questions(policy, precedents, scope_hint), scope_hint)


def _related_tests(code: list[FileHit], ranked: list[FileHit], history: list[Change]) -> list[FileHit]:
    """Tests that co-change with the top files, or share their names."""
    top = {h.path for h in code}
    co: dict[str, Counter[str]] = defaultdict(Counter)
    for c in history:
        tfiles = [f for f in c.files if is_test(f)]
        for f in c.files:
            if f in top:
                co[f].update(tfiles)
    out: dict[str, FileHit] = {}
    for rank, src in enumerate(h.path for h in code):
        stem = src.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
        total = sum(co[src].values()) or 1
        weight = 1 / (1 + rank)  # tests of the top file matter most
        for t, n in co[src].most_common(2):
            h = out.setdefault(t, FileHit(t))
            h.score += weight * n / total
            h.reasons.append(f"changed together with `{src}` in {n} past change{'s' if n > 1 else ''}")
        for h2 in ranked:
            if is_test(h2.path) and stem in h2.path.lower() and len(stem) > 3:
                h = out.setdefault(h2.path, FileHit(h2.path))
                h.score += 1.5 * weight
                h.reasons.append(f"named after `{src}`")
    for h2 in ranked:
        if is_test(h2.path) and h2.path not in out and len(out) < 4:
            h = out.setdefault(h2.path, FileHit(h2.path, h2.score * 0.1))
            h.reasons.extend(h2.reasons[:2])
    return sorted(out.values(), key=lambda h: -h.score)[:4]


def _questions(policy: Policy | None, precedents, scope_hint) -> list[str]:
    q = ["What exactly was broken, and how does your test show it is fixed now?"]
    if policy and (policy.get("Tests") or (policy.get("Tests in practice")
                                            and "%" in policy.get("Tests in practice").verdict)):
        q.append("Where is the regression test, and does it fail without your change?")
    if scope_hint:
        q.append("Why does this change need every file it touches?")
    if precedents:
        c = precedents[0][1]
        ref = f"#{c.pr}" if c.pr else c.sha[:8]
        q.append(f"How does this relate to {ref} (“{_short(c.title, 60)}”), and does it follow the same approach?")
    if policy and policy.get("Changelog"):
        q.append("Did you add a changelog entry?")
    if policy and policy.get("AI policy") and "No written" not in policy.get("AI policy").verdict:
        q.append("Did you use AI, and did you disclose it as the project asks?")
    return q


def to_markdown(b: Brief, policy: Policy | None, issue_url: str | None = None,
                review_quotes: dict[int, list[str]] | None = None) -> str:
    L = [f"# Contribution brief: {b.title}", ""]
    L.append(f"Repository: `{b.repo}`" + (f" · Issue: {issue_url}" if issue_url else ""))
    L.append("")
    L.append("Search terms taken from the issue: " + ", ".join(f"`{t.text}`" for t in b.terms[:10]))
    L += ["", "## Where to look", ""]
    if not b.files:
        L.append("No strong matches. The issue may need clarifying before you start; ask the maintainer.")
    for i, h in enumerate(b.files, 1):
        L.append(f"{i}. **`{h.path}`**")
        for r in _dedupe(h.reasons)[:4]:
            L.append(f"   - {r}")
    L += ["", "## How this repo handled similar changes", ""]
    if not b.precedents:
        L.append("No similar past changes found in recent history.")
    for _, c in b.precedents:
        ref = f"PR #{c.pr}" if c.pr else f"commit {c.sha[:8]}"
        link = f"https://github.com/{b.repo}/pull/{c.pr}" if c.pr else f"https://github.com/{b.repo}/commit/{c.sha}"
        code = [f for f in c.files if is_code(f)]
        tests = [f for f in c.files if is_test(f)]
        other = [f for f in c.files if f not in code and f not in tests]
        L.append(f"- [{ref}]({link}): “{c.title}”. Touched {len(code)} code, {len(tests)} test and "
                 f"{len(other)} other file(s): "
                 + ", ".join(f"`{f}`" for f in (code + tests + other)[:4]) + ("…" if len(c.files) > 4 else ""))
        for quote in (review_quotes or {}).get(c.pr or -1, [])[:2]:
            L.append(f"  - Reviewer said: “{quote}”")
    if b.scope_hint:
        L += ["", f"**Scope:** {b.scope_hint}"]
    L += ["", "## Tests to read and run", ""]
    if not b.tests:
        L.append("No related tests found. Ask where a test for this should live.")
    for h in b.tests:
        L.append(f"- `{h.path}`: " + "; ".join(_dedupe(h.reasons)[:2]))
    if b.lookalikes:
        L += ["", "## Probably don't touch", ""]
        for p, why in b.lookalikes:
            L.append(f"- `{p}`: {why}")
    if policy:
        L += ["", "## House rules that apply", ""]
        for f in policy.findings:
            src = f" ({f.evidence[0].source})" if f.evidence else ""
            L.append(f"- **{f.topic}:** {f.verdict}{src}")
    L += ["", "## Questions a reviewer will likely ask", ""]
    L += [f"- {q}" for q in b.questions]
    L += ["", "---", "_Every file above is listed with the evidence that selected it. "
          "Check it yourself; ContribOS points, you decide._"]
    return "\n".join(L)


def _dedupe(items: list[str]) -> list[str]:
    seen, out = set(), []
    for i in items:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def _short(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"
