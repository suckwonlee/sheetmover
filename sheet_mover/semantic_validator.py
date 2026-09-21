"""High-confidence D&D rule semantics checks.

This module intentionally validates only relations whose loss can change play.
Awkward Korean and ordinary terminology drift remain review warnings, not hard
failures.  Validation is performed per HTML block/sentence where possible so a
keyword in a later paragraph cannot hide an omission in an earlier rule.
"""
from __future__ import annotations
import html
import re
from dataclasses import dataclass

from . import translator as base

BLOCK_END = re.compile(r"</(?:p|li|td|th|caption|h[1-6])\s*>", re.I)
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

TARGETS = {
    "exception": re.compile(r"(?:제외|빼고|외에는|외의|이외|아닌\s+경우|아니면)"),
    "unless": re.compile(r"(?:않는\s*(?:한|이상)|아닌\s*(?:한|이상)|경우에만|때만|아니면|경우를\s*제외하면)"),
    "instead": re.compile(r"(?:대신|대체|하지\s*않고|아니라|아닌)"),
    "at-least": re.compile(r"(?:최소|이상)"),
    "negation": re.compile(r"(?:수\s*없|못\s*|불가|않(?:습니다|는다|다|고|으면|으면)|아니)"),
    "must": re.compile(r"(?:해야|하여야|[어아여]야|필요|반드시|해야만)"),
    "until": re.compile(r"(?:까지|동안)"),
    "before": re.compile(r"(?:전(?:에|\b|\()|이전)"),
    "after": re.compile(r"(?:후|뒤|이후|하면|되면)"),
    "half": re.compile(r"(?:절반|반(?:만|의|으로|만큼)?)"),
    "once-per-turn": re.compile(r"(?:턴당\s*한\s*번|한\s*턴(?:에|당)\s*한\s*번|턴마다\s*한\s*번|매\s*턴\s*한\s*번)"),
    "twice": re.compile(r"(?:두\s*번|2\s*회|두\s*배|2\s*배)"),
    "up-to": re.compile(r"(?:최대|까지)"),
    "additional-action": re.compile(r"(?:추가\s*(?:적인\s*)?행동|행동(?:을|를)?\s*(?:하나|한\s*번)?\s*더|(?:하나|한\s*번)\s*더\s*행동|추가로\s*행동)"),
    "extra-attack": re.compile(r"(?:추가\s*(?:적인\s*)?공격|공격(?:을|를)?\s*(?:하나|한\s*번)?\s*더|(?:하나|한\s*번)\s*더\s*공격|추가로\s*공격)"),
}

SOURCE_RULES = (
    ("missing-exception", re.compile(r"\b(?:except|excluding|other than)\b", re.I), "exception", True),
    ("missing-unless", re.compile(r"\bunless\b", re.I), "unless", True),
    ("missing-instead", re.compile(r"\binstead(?:\s+of)?\b", re.I), "instead", True),
    ("missing-at-least", re.compile(r"\bat\s+least\b", re.I), "at-least", True),
    ("missing-negation", re.compile(r"\b(?:can(?:not|'t)|cannot|does(?:n't| not)|do(?:n't| not)|is(?:n't| not)|are(?:n't| not)|must\s+not)\b", re.I), "negation", True),
    ("missing-must", re.compile(r"\bmust\b", re.I), "must", False),
    ("missing-until", re.compile(r"\buntil\b", re.I), "until", True),
    ("missing-before", re.compile(r"\bbefore\b", re.I), "before", False),
    ("missing-after", re.compile(r"\bafter\b", re.I), "after", False),
    ("missing-half", re.compile(r"\bhalf\b", re.I), "half", True),
    ("missing-once-per-turn", re.compile(r"\b(?:once\s+per\s+turn|once\s+on\s+a\s+turn|only\s+once\s+(?:per|on\s+a)\s+turn)\b", re.I), "once-per-turn", True),
    ("missing-twice", re.compile(r"\btwice\b", re.I), "twice", True),
    ("missing-up-to", re.compile(r"\bup\s+to\s+(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b", re.I), "up-to", True),
    ("missing-additional-action", re.compile(r"\b(?:one\s+)?(?:additional|extra)\s+action\b", re.I), "additional-action", True),
    ("missing-extra-attack", re.compile(r"\b(?:one\s+)?(?:additional|extra)\s+attack\b", re.I), "extra-attack", True),
)


HIGH_RISK_SOURCE = (
    ("high-risk:exception", re.compile(r"\b(?:except|excluding|other than)\b", re.I)),
    ("high-risk:unless", re.compile(r"\bunless\b", re.I)),
    ("high-risk:instead", re.compile(r"\binstead(?:\s+of)?\b", re.I)),
    ("high-risk:at-least", re.compile(r"\bat\s+least\b", re.I)),
)

SOFT_RELATIONS = (
    ("conditional-if", re.compile(r"\bif\b", re.I), re.compile(r"(?:경우|때|면|라면)")),
    ("conditional-when", re.compile(r"\bwhen\b", re.I), re.compile(r"(?:때|경우|하면|할\s*때)")),
    ("restriction-only", re.compile(r"\bonly\b", re.I), re.compile(r"(?:만|오직|뿐|한정)")),
    ("addition", re.compile(r"\b(?:additional|extra)\b", re.I), re.compile(r"(?:추가|더|하나\s*더|한\s*번\s*더)")),
)

@dataclass(frozen=True)
class Finding:
    code: str
    segment: int
    critical: bool


def visible(value: str) -> str:
    if not isinstance(value, str):
        return ""
    text = html.unescape(value)
    text = base.STRUCTURE_PATTERN.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def _blocks(value: str) -> list[str]:
    if not isinstance(value, str):
        return []
    result, cursor = [], 0
    for m in BLOCK_END.finditer(value):
        end = m.end()
        result.append(value[cursor:end])
        cursor = end
    if cursor < len(value):
        result.append(value[cursor:])
    return [item for item in result if visible(item)] or ([value] if visible(value) else [])


def aligned_segments(source: str, translated: str) -> list[tuple[str, str]]:
    sb, tb = _blocks(source), _blocks(translated)
    if len(sb) != len(tb) or not sb:
        return [(source, translated)]
    pairs: list[tuple[str, str]] = []
    for s_block, t_block in zip(sb, tb):
        sv, tv = visible(s_block), visible(t_block)
        ss, ts = SENTENCE_SPLIT.split(sv), SENTENCE_SPLIT.split(tv)
        if len(ss) == len(ts) and len(ss) > 1:
            pairs.extend(zip(ss, ts))
        else:
            pairs.append((sv, tv))
    return pairs


def semantic_findings(source: str, translated: str) -> list[Finding]:
    findings: list[Finding] = []
    for index, (s, t) in enumerate(aligned_segments(source, translated), start=1):
        s = visible(s)
        t = visible(t)
        if not s or not t:
            continue

        # A prose/rule segment that comes back entirely in English is not a
        # successful Korean translation.  Keep short labels/proper names out of
        # this hard rule; they are handled by glossary/bilingual-name logic.
        english_words = re.findall(r"[A-Za-z]+", s)
        ruleish = (
            re.search(r"[.!?;,:]", s) is not None
            or re.search(
                r"\b(?:you|your|can|cannot|can't|must|when|if|unless|until|before|after|gain|use|make|take|takes|has|have|deal|roll|creature|target)\b",
                s,
                re.I,
            ) is not None
        )
        if (
            len(s) >= 30
            and len(english_words) >= 4
            and ruleish
            and re.search(r"[A-Za-z]", s)
            and not re.search(r"[가-힣]", t)
        ):
            findings.append(Finding("korean-missing-prose", index, True))

        if s.casefold() == t.casefold():
            continue
        for code, source_pattern, target_key, critical in SOURCE_RULES:
            if not source_pattern.search(s):
                continue
            matched = TARGETS[target_key].search(t) is not None
            # Negative threshold rules are commonly and correctly inverted in
            # Korean: "isn't at least 13" -> "13 미만".  Do not force the
            # literal word "이상" in that case.
            negative_threshold = re.search(
                r"(?:isn['’]?t|is\s+not|aren['’]?t|are\s+not)\s+at\s+least\s+(\d+)",
                s,
                re.I,
            )
            if code == "missing-at-least" and not matched and negative_threshold:
                number = re.escape(negative_threshold.group(1))
                matched = re.search(rf"{number}\s*미만", t) is not None
            # The same Korean threshold inversion also carries the source
            # negation, so do not demand an extra 않/없 marker.
            if code == "missing-negation" and not matched and negative_threshold:
                number = re.escape(negative_threshold.group(1))
                matched = re.search(rf"{number}\s*미만", t) is not None
            if not matched:
                findings.append(Finding(code, index, critical))

        # Per-segment collapse guard.  Korean is compact, so keep this threshold
        # deliberately low; it only catches catastrophic sentence loss.
        compact_s = re.sub(r"\s+", "", s)
        compact_t = re.sub(r"\s+", "", t)
        if len(compact_s) >= 55 and len(compact_t) < max(8, int(len(compact_s) * 0.23)):
            findings.append(Finding("segment-collapsed", index, True))
    # stable dedupe
    seen, out = set(), []
    for item in findings:
        key = (item.code, item.segment, item.critical)
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def critical_codes(source: str, translated: str) -> list[str]:
    return [f"s{f.segment}:{f.code}" for f in semantic_findings(source, translated) if f.critical]


def review_reasons(source: str, translated: str) -> list[str]:
    reasons = [f"s{f.segment}:{f.code}" for f in semantic_findings(source, translated)]
    text = visible(source)
    target = visible(translated)
    for name, source_pattern in HIGH_RISK_SOURCE:
        if source_pattern.search(text):
            reasons.append(name)
    for name, source_pattern, target_pattern in SOFT_RELATIONS:
        if source_pattern.search(text) and not target_pattern.search(target):
            reasons.append(name)
    if re.search(r"[A-Za-z]", text) and not re.search(r"[가-힣]", target):
        reasons.append("korean-missing")
    return list(dict.fromkeys(reasons))
