"""Tone check for text you are about to post: does it sound like you, and is it easy to answer?

Maintainers say they'd rather read imperfect human writing than polished text
that "smells of AI". This flags common machine-written tells and things that
make a message hard to answer. It points; you rewrite.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

TELLS = [
    (r"\b(delve|delving|tapestry)\b", "word strongly associated with AI text"),
    (r"\b(seamless(ly)?|robust|comprehensive|leverag(e|ing)|utiliz(e|ing)|holistic|streamlin(e|ing))\b",
     "buzzwords"),
    (r"\b(I hope this (message|comment) finds you well|I hope this helps)\b", "stock pleasantry"),
    (r"\b(Certainly|Absolutely|Great question)[!,.]", "assistant-style opener"),
    (r"\b(It('|’)s worth noting|It is important to note|In summary|In conclusion|Overall,)\b", "filler phrase"),
    (r"\bas an AI\b", "mentions being an AI"),
    (r"\b(game[- ]changer|cutting[- ]edge|state[- ]of[- ]the[- ]art)\b", "hype words"),
    (r"(—.*){3,}", "lots of em dashes"),
]


@dataclass
class ToneReport:
    words: int
    flags: list[str]
    tips: list[str]


def check(text: str, kind: str = "comment") -> ToneReport:
    words = len(re.findall(r"\w+", text))
    flags = []
    for rx, label in TELLS:
        m = re.search(rx, text, re.I)
        if m:
            flags.append(f"{label}: “{m.group(0)[:40]}”")
    headings = len(re.findall(r"^\s*(#+ |\*\*[^*]+\*\*\s*$)", text, re.M))
    bullets = len(re.findall(r"^\s*[-*] ", text, re.M))
    if kind == "comment" and headings >= 2:
        flags.append(f"{headings} headings in a short comment reads like a generated report")
    tips = []
    limit = {"comment": 150, "intro": 150, "reply": 80, "explanation": 300}.get(kind, 150)
    if words > limit:
        tips.append(f"{words} words. Aim for under {limit}; maintainers skim.")
    if kind in ("comment", "intro") and "?" not in text:
        tips.append("No question in it. Ask one specific thing so the maintainer knows how to reply.")
    if kind == "comment" and bullets > 6:
        tips.append("Many bullet points. Two or three short sentences are friendlier.")
    if re.search(r"<[^>]{3,}>", text):
        tips.append("There are still <placeholders> to fill in.")
    return ToneReport(words, flags, tips)


def to_markdown(r: ToneReport) -> str:
    if not r.flags and not r.tips:
        return f"✅ Sounds like a person, and it's easy to answer ({r.words} words). Post it."
    L = [f"# Tone check ({r.words} words)", ""]
    if r.flags:
        L += ["**Might read as machine-written. Rewrite these in your own words:**"]
        L += [f"- {f}" for f in r.flags] + [""]
    if r.tips:
        L += ["**Easier to answer if you:**"] + [f"- {t}" for t in r.tips]
    return "\n".join(L)
