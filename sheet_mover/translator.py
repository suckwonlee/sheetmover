"""Google Cloud Translation based D&D text translation pipeline."""
import copy
import hashlib
import html
import json
import os
import re
import tempfile
import unicodedata
from collections import Counter
from pathlib import Path

TRANSLATION_PROVIDER = "google-cloud-translation-v3"
TRANSLATABLE_FIELDS = ("name", "description", "components_description")
BATCH_MAX_ITEMS = max(1, int(os.getenv("SHEETMOVER_BATCH_ITEMS", "10")))
BATCH_MAX_CHARS = max(1000, int(os.getenv("SHEETMOVER_BATCH_CHARS", "4500")))
GOOGLE_TRANSLATION_TIMEOUT = max(10, int(os.getenv("SHEETMOVER_GOOGLE_TIMEOUT", "60")))
TRANSLATION_CACHE_VERSION = "2026-09-20-google-v13-semantic-guards"
GOOGLE_GLOSSARY_LOCATION = "us-central1"
GOOGLE_GLOSSARY_PREFIX = "sheetmover-dnd-en-ko"


class TranslationError(RuntimeError):
    pass


STRUCTURE_PATTERN = re.compile(
    r"<[^>]*>|\[/?[A-Za-z][A-Za-z0-9_-]*\]",
    re.I | re.S,
)

# High-confidence rule relations whose loss can silently change D&D mechanics
# even when every number and markup token survives.  Keep this deliberately
# small: these are relation/constraint words, not ordinary vocabulary.
SEMANTIC_RULE_ANCHORS = (
    (
        "exception",
        re.compile(r"\b(?:except|excluding|other than)\b", re.I),
        re.compile(r"(?:제외|빼고|외에는|외의|이외)", re.I),
    ),
    (
        "unless",
        re.compile(r"\bunless\b", re.I),
        re.compile(r"(?:않는 한|아닌 한|경우에만|때만|아니면)", re.I),
    ),
    (
        "instead",
        re.compile(r"\binstead(?: of)?\b", re.I),
        re.compile(r"(?:대신|대체)", re.I),
    ),
    (
        "additional",
        re.compile(r"\b(?:additional|extra)\b", re.I),
        re.compile(r"(?:추가|하나 더|한 번 더|더 )", re.I),
    ),
    (
        "at-least",
        re.compile(r"\bat least\b", re.I),
        re.compile(r"(?:최소|이상)", re.I),
    ),
)

MECHANICAL_PATTERN = re.compile(
    r"\[\[.*?\]\]|"
    r"[+-]?\d+(?:\.\d+)?(?:d\d+(?:\s*[+-]\s*\d+)?)?|"
    r"&(?:#\d+|#x[0-9A-Fa-f]+|[A-Za-z][A-Za-z0-9]+);",
    re.I | re.S,
)

PROTECTED_PATTERN = re.compile(
    r"<[^>]*>|"
    r"\[/?[A-Za-z][A-Za-z0-9_-]*\]|"
    r"\[\[.*?\]\]|"
    r"[+-]?\d+(?:\.\d+)?(?:d\d+(?:\s*[+-]\s*\d+)?)?|"
    r"&(?:#\d+|#x[0-9A-Fa-f]+|[A-Za-z][A-Za-z0-9]+);",
    re.I | re.S,
)


# Runtime translation no longer hides ordinary numbers/dice from the model.
# Small local models were much more likely to drop opaque placeholders when a
# rule sentence contained several numbers.  Roll20 formulas remain protected;
# ordinary mechanics stay visible and are validated after translation.
MODEL_PROTECTED_PATTERN = re.compile(r"\[\[.*?\]\]", re.S)
VALIDATION_MECHANICAL_PATTERN = re.compile(
    r"\[\[.*?\]\]|"
    r"[+-]?\d+(?:\.\d+)?(?:d\d+(?:\s*[+-]\s*\d+)?)?",
    re.I | re.S,
)

# Google sometimes treats a number-only no-translate span and the unit/noun
# beside it as separate translation material.  In live v10 this showed up as
# duplicated distance numbers, metric conversion echoes, and lost ``0 Hit
# Points`` values.  Lock semantic mechanical atoms together so the model never
# has to reconstruct their internal relation.  The restorer localizes the few
# English unit labels deterministically.
GOOGLE_MECHANICAL_LOCK_PATTERN = re.compile(
    r"\[\[.*?\]\]|"
    r"(?<![A-Za-z0-9])[+-]?\d+(?:\.\d+)?d\d+(?:\s*[+-]\s*\d+)?(?![A-Za-z0-9])|"
    r"(?<![A-Za-z0-9])DC\s*[+-]?\d+(?:\.\d+)?(?![A-Za-z0-9])|"
    r"(?<![A-Za-z0-9])[+-]?\d+(?:\.\d+)?(?:st|nd|rd|th)\s+level\b|"
    r"(?<![A-Za-z0-9])[+-]?\d+(?:\.\d+)?\s*(?:-\s*)?(?:feet|foot|ft\.?|miles?|inches?|pounds?|lbs?\.?|pints?|gallons?|hours?|minutes?|seconds?|rounds?|days?)\b|"
    r"(?<![A-Za-z0-9])[+-]?\d+(?:\.\d+)?\s+Hit\s+Points?\b|"
    r"(?<![A-Za-z0-9])[+-]?\d+(?:\.\d+)?\s*(?:GP|SP|CP|EP|PP)\b|"
    r"[+-]?\d+(?:\.\d+)?",
    re.I | re.S,
)

DISTANCE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?P<value>[+-]?\d+(?:\.\d+)?)\s*(?:-\s*)?"
    r"(?P<unit>feet|foot|ft\.?|피트|meters?|metres?|미터|m)(?![A-Za-z가-힣])",
    re.I,
)

# Only context-stable single-word terms are hard requirements inside prose.
# Other one-word glossary entries are still used for exact names/composites,
# but are not forced inside sentences (e.g. reach, force, uses, common).
WEAPON_PROPERTY_NAME_MAP = {
    "ammunition": "탄약",
    "finesse": "기교",
    "heavy": "중량",
    "light": "경량",
    "loading": "장전",
    "reach": "도달거리",
    "thrown": "투척",
    "two-handed": "양손",
    "versatile": "다용도",
    "cleave": "가르기",
    "graze": "스침",
    "nick": "닉",
    "push": "밀치기",
    "sap": "약화",
    "slow": "감속",
    "topple": "넘어뜨리기",
    "vex": "교란",
}

# D&D Beyond inline tags carry semantic context that ordinary machine
# translation cannot infer from the enclosed word alone.  Keep the original
# tags byte-for-byte while normalizing their visible labels deterministically.
DND_ACTION_NAME_MAP = {
    "attack": "공격",
    "magic": "마법",
    "utilize": "활용",
    "dash": "질주",
    "disengage": "이탈",
    "dodge": "회피",
    "help": "도움",
    "hide": "숨기",
    "ready": "준비",
    "search": "수색",
    "study": "연구",
}

DND_CONDITION_NAME_MAP = {
    "blinded": "실명",
    "charmed": "매혹",
    "deafened": "청각 상실",
    "exhaustion": "피로",
    "frightened": "공포",
    "grappled": "붙잡힘",
    "incapacitated": "행동 불능",
    "invisible": "투명",
    "paralyzed": "마비",
    "petrified": "석화",
    "poisoned": "중독",
    "prone": "넘어짐",
    "restrained": "구속",
    "stunned": "기절",
    "unconscious": "의식 불명",
}

DND_RULE_NAME_MAP = {
    "advantage": "이점",
    "disadvantage": "불리점",
    "short rest": "짧은 휴식",
    "long rest": "긴 휴식",
}


SPELLCASTING_ABILITY_NAME_MAP = {
    "strength": "근력",
    "dexterity": "민첩",
    "constitution": "건강",
    "intelligence": "지능",
    "wisdom": "지혜",
    "charisma": "매력",
}

SPELL_LIST_NAME_MAP = {
    "wizard": "위저드",
    "cleric": "성직자",
    "druid": "드루이드",
    "sorcerer": "소서러",
    "bard": "바드",
    "warlock": "워락",
    "paladin": "팔라딘",
    "ranger": "레인저",
}

SPELLCASTING_FOCUS_NAME_MAP = {
    "arcane focus": "비전 매개체",
    "druidic focus": "드루이드 매개체",
    "holy symbol": "성표",
}

# Short table/label values are safer to translate deterministically than to
# let general NMT reinterpret them.  This is especially important for the
# Fighter core-traits table where Medium/Heavy armor and ability names are
# mechanical data rather than prose.
STRUCTURED_EXACT_LABEL_MAP = {
    "+1 to all ability scores, extra language": "모든 능력치 +1, 추가 언어",
    "strength or dexterity": "근력 또는 민첩",
    "d10 per fighter level": "전사 레벨당 d10",
    "saving throw proficiencies": "내성 굴림 숙련",
    "spells prepared": "준비된 주문",
    "strength and constitution": "근력 및 건강",
    "simple and martial weapons": "단순 무기 및 군용 무기",
    "light, medium, and heavy armor and shields": "경갑, 평갑, 중갑 및 방패",
    "acrobatics, animal handling, athletics, history, insight, intimidation, persuasion, perception, or survival":
        "곡예, 동물 조련, 운동, 역사, 통찰, 위협, 설득, 지각 또는 생존",
}

DND_TAG_PAIR_PATTERN = re.compile(
    r"\[(?P<tag>[A-Za-z][A-Za-z0-9_-]*)\](?P<body>.*?)\[/\1\]",
    re.I | re.S,
)

DND_HTML_SPAN_PATTERN = re.compile(
    r"<span\b(?=[^>]*\bid=[\"']sm-dnd-(?P<id>\d+)[\"'])[^>]*>"
    r"(?P<body>.*?)</span>",
    re.I | re.S,
)


MECHANIC_HTML_SPAN_PATTERN = re.compile(
    r"<span\b(?=[^>]*\bid=[\"']sm-mech-(?P<id>\d+)[\"'])[^>]*>"
    r"(?P<body>.*?)</span>",
    re.I | re.S,
)


PROSE_HARD_SINGLE_TERMS = {
    "strength",
    "dexterity",
    "constitution",
    "intelligence",
    "wisdom",
    "charisma",
    "feat",
    "familiar",
    "javelin",
    "sickle",
    "flail",
    "greatsword",
    "backpack",
    "shovel",
    "rations",
    "rope",
    "tinderbox",
    "torch",
    "waterskin",
    "caltrops",
    "crowbar",
    "trident",
    "scimitar",
    "shortsword",
    "longbow",
    "arrow",
    "quiver",
    "advantage",
    "disadvantage",
    "reaction",
    "concentration",
    "ritual",
    "cantrip",
    "prone",
    "incapacitated",
    "unconscious",
    "restrained",
    "grappled",
    "creature",
}


STRUCTURED_BLOCK_END_PATTERN = re.compile(
    r"</(?:p|li|td|th|caption|h[1-6])\s*>",
    re.I,
)


PROSE_GLOSSARY_EXCLUDED = {
    # Phrases that tend to damage Korean syntax when forced by Cloud Glossary.
    # Exact labels/names are still translated locally from ``self.glossary``.
    "proficiency with",
    "one die",
    "one additional die",
    "damage increases by one die",
    "for each spell slot level above 1",
    "half as much damage",
    "once per turn",
    "once on a turn",
    "at the start of your next turn",
    "until the start of your next turn",
    "until the end of your next turn",
    "at the end of your next turn",
    "expended use",
    "expended uses",
    "regain all expended uses",
    "regain all expended slots",
    "hit points",
    "hit point",
    "hit point maximum",
    "maximum hit points",
    "current hit points",
    "temporary hit points",
    # Korean normally expresses this rule as "한 마리만"; requiring the
    # literal glossary target "둘 이상의 사역마" creates a false warning.
    "more than one familiar",
    "hit points equal to",
    "regain hit points",
    "reduce its speed",
    "reduce your speed",
}


def glossary_term_is_prose_safe(source_term):
    normalized = source_term.strip().casefold()
    if normalized in PROSE_GLOSSARY_EXCLUDED:
        return False
    if re.search(r"[\s-]", normalized):
        return True
    return normalized in PROSE_HARD_SINGLE_TERMS


def cloud_glossary_entries(glossary):
    """Return only context-stable terms suitable for Google's glossary.

    The local glossary is intentionally broader because exact field names can
    be translated deterministically. Cloud prose glossary entries must be
    narrower: syntactic fragments such as ``proficiency with`` can destroy
    Korean word order even when their individual translation is technically
    correct.
    """
    return {
        source: target
        for source, target in glossary.items()
        if isinstance(source, str)
        and isinstance(target, str)
        and source.strip()
        and target.strip()
        and glossary_term_is_prose_safe(source)
    }


def glossary_fingerprint(glossary):
    material = json.dumps(
        glossary,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def glossary_id_for(glossary):
    # The remote resource only contains the prose-safe subset.  Fingerprinting
    # that exact subset means changing the filtering policy automatically
    # creates a new glossary instead of silently reusing stale Cloud content.
    return (
        f"{GOOGLE_GLOSSARY_PREFIX}-"
        f"{glossary_fingerprint(cloud_glossary_entries(glossary))[:12]}"
    )


# Chinese/Japanese ideographs are not valid output for the Korean sheet.
# Hangul syllables are outside these ranges and are unaffected.
HANJA_PATTERN = re.compile(
    r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]"
)


def contains_hanja(text):
    return isinstance(text, str) and HANJA_PATTERN.search(text) is not None


def contains_unexpected_script(text):
    """Reject alphabetic scripts other than Hangul and Latin in Korean output."""
    if not isinstance(text, str):
        return False
    for ch in text:
        if not ch.isalpha():
            continue
        name = unicodedata.name(ch, "")
        if name.startswith("HANGUL ") or name.startswith("LATIN "):
            continue
        return True
    return False


META_PREFIX_PATTERN = re.compile(r"^(?:\s*)(?:직역|번역|설명)\s*[:：]\s*", re.I)


def clean_translation_output(text):
    if not isinstance(text, str):
        return text
    match = re.match(r"(\s*)(.*)", text, re.S)
    if not match:
        return text
    leading, cleaned = match.groups()
    while META_PREFIX_PATTERN.match(cleaned):
        cleaned = META_PREFIX_PATTERN.sub("", cleaned, count=1)
    cleaned = normalize_translation_spacing(cleaned)
    return leading + cleaned


def protected_tokens(text):
    """Return protected fragments in exact source order."""
    return tuple(PROTECTED_PATTERN.findall(text))


def protect_text(text):
    """Hide HTML, numbers, dice and Roll20 expressions from the translator.

    The model only sees stable placeholder tokens.  After translation the exact
    original fragments are restored byte-for-byte.
    """
    replacements = []

    def replace(match):
        index = len(replacements)
        token = f"__SHEETMOVER_PROTECTED_{index:04d}__"
        # Extremely unlikely, but never generate a placeholder already present
        # in the source text.
        while token in text:
            index += 1
            token = f"__SHEETMOVER_PROTECTED_{index:04d}__"
        replacements.append((token, match.group(0)))
        return token

    protected = PROTECTED_PATTERN.sub(replace, text)
    return protected, replacements


def restore_text(text, replacements):
    """Restore every placeholder and reject missing/duplicated placeholders."""
    restored = text
    for token, original in replacements:
        count = restored.count(token)
        if count != 1:
            raise TranslationError(
                "번역 서비스가 보호 토큰을 변경했습니다: "
                f"{token} (발견 {count}회)"
            )
        restored = restored.replace(token, original, 1)
    return restored



def _model_text(text):
    """Normalize entities for the model while leaving HTML tags to the caller."""
    return html.unescape(text).replace("\xa0", " ")


def protect_model_text(text):
    """Protect only Roll20 formulas during live translation.

    Numbers and dice expressions stay visible to the model and are verified
    after translation instead of being represented by fragile placeholders.
    """
    replacements = []

    def replace(match):
        token = f"__SHEETMOVER_PROTECTED_{len(replacements):04d}__"
        replacements.append((token, match.group(0)))
        return token

    return MODEL_PROTECTED_PATTERN.sub(replace, text), replacements


def _visible_text_for_mechanics(text):
    """Return only user-visible text for mechanical validation.

    HTML attributes can contain unrelated numbers (URLs, ids, colors and
    inline styles). Those belong to structural validation, not D&D rule
    mechanics. Keeping the invariants separate prevents harmless markup
    normalization from being misclassified as a changed save DC or distance.
    """
    if not isinstance(text, str):
        return ""
    return STRUCTURE_PATTERN.sub(" ", _model_text(text))


def mechanical_tokens(text):
    if not isinstance(text, str):
        return ()
    return tuple(
        VALIDATION_MECHANICAL_PATTERN.findall(_visible_text_for_mechanics(text))
    )


ENGLISH_NUMBER_WORDS = {
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
    "eleven": "11",
    "twelve": "12",
    "thirteen": "13",
    "fourteen": "14",
    "fifteen": "15",
    "sixteen": "16",
    "seventeen": "17",
    "eighteen": "18",
    "nineteen": "19",
    "twenty": "20",
    "first": "1",
    "second": "2",
    "third": "3",
    "fourth": "4",
    "fifth": "5",
    "sixth": "6",
    "seventh": "7",
    "eighth": "8",
    "ninth": "9",
    "tenth": "10",
    "eleventh": "11",
    "twelfth": "12",
    "thirteenth": "13",
    "fourteenth": "14",
    "fifteenth": "15",
    "sixteenth": "16",
    "seventeenth": "17",
    "eighteenth": "18",
    "nineteenth": "19",
    "twentieth": "20",
    "once": "1",
    "twice": "2",
    "thrice": "3",
    "both": "2",
}


def mechanical_signature(text):
    """Return normalized visible mechanical tokens."""
    return Counter(mechanical_tokens(text))


def _distance_occurrences(text):
    visible = _visible_text_for_mechanics(text)
    rows = []
    for match in DISTANCE_PATTERN.finditer(visible):
        raw_value = match.group("value")
        try:
            value = float(raw_value)
        except ValueError:
            continue
        unit_raw = match.group("unit").casefold().rstrip(".")
        if unit_raw in {"feet", "foot", "ft", "피트"}:
            family = "ft"
            meters = value * 0.3048
        else:
            family = "m"
            meters = value
        rows.append(
            {
                "raw_value": raw_value.lstrip("+"),
                "raw": match.group(0),
                "family": family,
                "meters": meters,
                "span": match.span(),
            }
        )
    return visible, rows


def _distance_equivalent(left, right):
    if left["family"] == right["family"]:
        return abs(left["meters"] - right["meters"]) <= 1e-6
    tolerance = max(0.03, abs(left["meters"]) * 0.02)
    return abs(left["meters"] - right["meters"]) <= tolerance


def _distance_mismatch(source, translated):
    """Compare visible ft/m distances by physical value, not spelling.

    Korean Cloud output is allowed to keep feet, convert feet to metres, or
    show both forms (for example ``300피트(약 91미터)``).  Wrong conversions
    such as ``300 feet -> 300 m`` still fail.  Counts are preserved: one source
    occurrence cannot silently disappear merely because another equal distance
    exists elsewhere in the paragraph.
    """
    _source_visible, source_rows = _distance_occurrences(source)
    _target_visible, target_rows = _distance_occurrences(translated)
    if not source_rows and not target_rows:
        return Counter(), Counter()
    if not source_rows:
        return Counter(), Counter(row["raw"] for row in target_rows)
    if not target_rows:
        return Counter(row["raw"] for row in source_rows), Counter()

    unmatched_targets = set(range(len(target_rows)))
    missing = Counter()

    # Prefer an exact-unit/value match.  If none exists, accept a physically
    # equivalent ft<->m conversion within normal display-rounding tolerance.
    for source_row in source_rows:
        candidates = [
            index
            for index in unmatched_targets
            if _distance_equivalent(source_row, target_rows[index])
        ]
        if not candidates:
            missing[source_row["raw"]] += 1
            continue
        candidates.sort(
            key=lambda index: (
                target_rows[index]["family"] != source_row["family"],
                abs(target_rows[index]["meters"] - source_row["meters"]),
            )
        )
        unmatched_targets.remove(candidates[0])

    unexpected = Counter()
    for index in sorted(unmatched_targets):
        target_row = target_rows[index]
        # A second representation in the *other* unit is a harmless metric
        # echo.  A duplicate in the same unit is not ignored because it may be
        # a real duplicated rule value.
        if any(
            source_row["family"] != target_row["family"]
            and _distance_equivalent(source_row, target_row)
            for source_row in source_rows
        ):
            continue
        unexpected[target_row["raw"]] += 1

    return missing, unexpected


def _mechanical_signature_without_distances(text):
    visible, distances = _distance_occurrences(text)
    spans = [row["span"] for row in distances]
    counter = Counter()
    for match in VALIDATION_MECHANICAL_PATTERN.finditer(visible):
        if any(start <= match.start() and match.end() <= end for start, end in spans):
            continue
        counter[match.group(0)] += 1
    return counter


def _numeric_echo_allowance(text):
    """Numbers Google may legitimately render as Arabic digits in Korean."""
    if not isinstance(text, str):
        return Counter()

    normalized = _visible_text_for_mechanics(text).casefold()
    allowed = Counter()
    for word, value in ENGLISH_NUMBER_WORDS.items():
        allowed[value] += len(re.findall(rf"(?<![a-z]){word}(?![a-z])", normalized))

    half_count = len(re.findall(r"(?<![a-z])half(?![a-z])", normalized))
    if half_count:
        allowed["1"] += half_count
        allowed["2"] += half_count

    # Google may explain XdY as "Y-sided dice, X dice" while retaining XdY.
    # Either echo is descriptive, not a changed rule value.
    for match in re.finditer(
        r"(?<![A-Za-z0-9])([1-9]\d*)d([1-9]\d*)", normalized, re.I
    ):
        allowed[match.group(1)] += 1
        allowed[match.group(2)] += 1

    return allowed


def mechanics_mismatch(source, translated):
    """Return (missing, unexpected) visible rule tokens after safe allowances."""
    source_counter = _mechanical_signature_without_distances(source)
    target_counter = _mechanical_signature_without_distances(translated)

    missing = Counter()
    remaining = Counter(target_counter)
    for token, count in source_counter.items():
        have = remaining.get(token, 0)
        if have < count:
            missing[token] = count - have
        consume = min(have, count)
        if consume:
            remaining[token] -= consume
            if remaining[token] <= 0:
                del remaining[token]

    allowance = _numeric_echo_allowance(source)
    unexpected = Counter()
    for token, count in remaining.items():
        if re.fullmatch(r"[+-]?\d+(?:\.\d+)?", token):
            normalized = token.lstrip("+")
            allowed = min(allowance.get(normalized, 0), count)
            if allowed:
                allowance[normalized] -= allowed
                count -= allowed
        if count:
            unexpected[token] += count

    distance_missing, distance_unexpected = _distance_mismatch(source, translated)
    missing.update(distance_missing)
    unexpected.update(distance_unexpected)
    return missing, unexpected

def mechanics_mismatch_text(source, translated):
    missing, unexpected = mechanics_mismatch(source, translated)
    parts = []
    if missing:
        parts.append(
            "누락=" + ", ".join(
                f"{token}×{count}" for token, count in sorted(missing.items())
            )
        )
    if unexpected:
        parts.append(
            "추가=" + ", ".join(
                f"{token}×{count}" for token, count in sorted(unexpected.items())
            )
        )
    return "; ".join(parts) or "차이 없음"


def mechanics_compatible(source, translated):
    """Validate only visible D&D mechanics; markup is checked separately."""
    missing, unexpected = mechanics_mismatch(source, translated)
    return not missing and not unexpected


KOREAN_PARTICLE_PATTERN = re.compile(
    r"(?<=[0-9A-Za-z가-힣)\]])[ \t]+"
    r"(으로|에서|에게|께서|부터|까지|보다|처럼|마다|조차|마저|밖에|"
    r"은|는|가|을|를|에|의|와|과|로|도|만)"
    r"(?=[ \t\r\n,.;:!?)]|$)"
)


def normalize_translation_spacing(text):
    """Remove safe Korean spacing artifacts without touching line breaks."""
    if not isinstance(text, str):
        return text
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"[ \t]+([,.;:!?])", r"\1", text)
    text = re.sub(r"([.!?])(?=[가-힣])", r"\1 ", text)
    text = re.sub(r"\([ \t]+", "(", text)
    text = re.sub(r"[ \t]+\)", ")", text)
    text = re.sub(r"([,;:])(?=[^\s<])", r"\1 ", text)
    text = KOREAN_PARTICLE_PATTERN.sub(r"\1", text)
    text = re.sub(r"HP[ \t]+HP", "HP", text)
    return text


def _transform_text_outside_structure(text, transform):
    """Apply ``transform`` only to visible text, never to HTML/D&D tags."""
    if not isinstance(text, str) or not text:
        return text
    pieces = []
    cursor = 0
    for match in STRUCTURE_PATTERN.finditer(text):
        if match.start() > cursor:
            pieces.append(transform(text[cursor:match.start()]))
        pieces.append(match.group(0))
        cursor = match.end()
    if cursor < len(text):
        pieces.append(transform(text[cursor:]))
    return "".join(pieces)


def unescape_text_outside_structure(text):
    return _transform_text_outside_structure(text, html.unescape)


def structure_tokens(text):
    if not isinstance(text, str):
        return ()
    return tuple(STRUCTURE_PATTERN.findall(text))


def restore_glossary_text(text, replacements):
    """Restore hard-locked glossary placeholders to exact Korean terms."""
    restored = text
    for token, target in replacements:
        count = restored.count(token)
        if count != 1:
            raise TranslationError(
                "번역 서비스가 용어집 보호 토큰을 변경했습니다: "
                f"{token} (발견 {count}회)"
            )
        restored = restored.replace(token, target, 1)
    return restored



class Translator:
    def __init__(
        self,
        glossary_path=None,
        client=None,
        project_id=None,
        location=None,
    ):
        self.glossary_path = (
            Path(glossary_path)
            if glossary_path
            else Path(__file__).resolve().parent.parent / "glossary.json"
        )
        if not self.glossary_path.is_file():
            raise TranslationError(
                f"번역 사전 파일이 없습니다: {self.glossary_path}"
            )

        glossary = json.loads(self.glossary_path.read_text(encoding="utf-8"))
        if not isinstance(glossary, dict) or any(
            not isinstance(k, str) or not isinstance(v, str)
            for k, v in glossary.items()
        ):
            raise TranslationError(
                "번역 사전은 영문: 한글 문자열 쌍으로 구성해야 합니다."
            )

        self.glossary_source = dict(glossary)
        self.glossary = {k.casefold(): v for k, v in glossary.items()}
        self.glossary_hash = glossary_fingerprint(self.glossary_source)
        self.glossary_id = os.environ.get(
            "SHEETMOVER_GOOGLE_GLOSSARY",
            glossary_id_for(self.glossary_source),
        )
        self.client = client
        self._client_injected = client is not None
        self._glossary_preflight_done = False
        self.project_id = (
            project_id
            or os.environ.get("SHEETMOVER_GOOGLE_PROJECT")
            or os.environ.get("GOOGLE_CLOUD_PROJECT")
        )
        self.location = (
            location
            or os.environ.get("SHEETMOVER_GOOGLE_LOCATION")
            or GOOGLE_GLOSSARY_LOCATION
        )
        self.glossary_name = None
        if self.project_id:
            self.glossary_name = (
                f"projects/{self.project_id}/locations/{self.location}/"
                f"glossaries/{self.glossary_id}"
            )
        self._legacy_test_client = (
            client is not None
            and hasattr(client, "chat")
            and not hasattr(client, "translate_text")
        )
        self.warnings = []
        self.original_preserved = []
        # Keep an internal, full-fidelity record of fragments that were
        # explicitly preserved after a soft translation failure.  Public
        # summaries intentionally store only previews, which are not precise
        # enough to protect the exact text node from later canonicalization.
        self._preserved_fragment_keys = set()
        # A structured description can now preserve only one bad block while
        # translating the rest. Such a partially translated top-level value is
        # useful output, but it must not become a success-cache hit that prevents
        # the preserved block from being retried on the next run.
        self._uncacheable_sources = set()

        cache_material = json.dumps(
            {
                "version": TRANSLATION_CACHE_VERSION,
                "provider": TRANSLATION_PROVIDER,
                "location": self.location,
                "glossary_id": self.glossary_id,
                "glossary": self.glossary,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        self._cache_fingerprint = hashlib.sha256(
            cache_material.encode("utf-8")
        ).hexdigest()

        default_cache = (
            Path(__file__).resolve().parent.parent
            / ".sheetmover-google-translation-cache.json"
        )
        self.cache_path = Path(
            os.environ.get(
                "SHEETMOVER_TRANSLATION_CACHE",
                str(default_cache),
            )
        )
        self.cache = self._load_persistent_cache()
        self._import_resume_partial()

    @staticmethod
    def _collect_parallel_string_pairs(original, translated, output):
        if isinstance(original, str) and isinstance(translated, str):
            if (
                original.strip()
                and translated.strip()
                and original != translated
            ):
                output.setdefault(original, translated)
            return

        if isinstance(original, dict) and isinstance(translated, dict):
            for key in original.keys() & translated.keys():
                Translator._collect_parallel_string_pairs(
                    original[key],
                    translated[key],
                    output,
                )
            return

        if isinstance(original, list) and isinstance(translated, list):
            for left, right in zip(original, translated):
                Translator._collect_parallel_string_pairs(
                    left,
                    right,
                    output,
                )

    def _import_resume_partial(self):
        partial_path = os.environ.get("SHEETMOVER_RESUME_PARTIAL")
        if not partial_path:
            return

        path = Path(partial_path)
        if not path.is_file():
            self.warnings.append(
                f"지정한 부분 번역 파일을 찾을 수 없습니다: {path}"
            )
            return

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            partial_fingerprint = payload.get("translation_fingerprint")
            if partial_fingerprint != self._cache_fingerprint:
                self.warnings.append(
                    "부분 번역 파일이 현재 번역 파이프라인 버전과 달라 "
                    f"재사용하지 않았습니다: {path.name}"
                )
                return

            original = payload.get("original")
            translated = payload.get("translated")

            if not isinstance(original, dict) or not isinstance(translated, dict):
                raise ValueError("original/translated 구조가 없습니다.")

            imported = {}
            self._collect_parallel_string_pairs(
                original,
                translated,
                imported,
            )

            # Existing persistent entries win. The partial is only a seed for
            # translations that are not already cached.
            added = 0
            for source, value in imported.items():
                if source not in self.cache:
                    self.cache[source] = value
                    added += 1

            if added:
                self._save_persistent_cache()

            self.warnings.append(
                f"부분 번역 파일에서 기존 번역 {added}개를 재사용하도록 불러왔습니다: "
                f"{path.name}"
            )
        except Exception as exc:
            self.warnings.append(
                f"부분 번역 파일 재사용에 실패했습니다: {exc}"
            )

    def _load_persistent_cache(self):
        try:
            if not self.cache_path.is_file():
                return {}

            payload = json.loads(
                self.cache_path.read_text(encoding="utf-8")
            )
            if (
                not isinstance(payload, dict)
                or payload.get("fingerprint") != self._cache_fingerprint
                or not isinstance(payload.get("entries"), dict)
            ):
                return {}

            return {
                source: translated
                for source, translated in payload["entries"].items()
                if isinstance(source, str)
                and isinstance(translated, str)
                and translated.strip()
                and translated != source
            }
        except Exception:
            return {}

    def _save_persistent_cache(self):
        temp_path = None
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(
                {
                    "fingerprint": self._cache_fingerprint,
                    "entries": self.cache,
                },
                ensure_ascii=False,
                indent=2,
            )

            # Use a unique temporary file instead of one fixed ``.tmp`` path.
            # On Windows the old path could be held briefly by antivirus/file
            # indexing and ``replace`` then failed with WinError 5.  If atomic
            # replacement itself is denied, fall back to a direct overwrite;
            # a partially written cache is harmless because loading already
            # treats malformed JSON as an empty cache.
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=str(self.cache_path.parent),
                prefix=self.cache_path.name + ".",
                suffix=".tmp",
                delete=False,
            ) as handle:
                handle.write(payload)
                temp_path = Path(handle.name)

            try:
                os.replace(temp_path, self.cache_path)
                temp_path = None
            except PermissionError:
                self.cache_path.write_text(payload, encoding="utf-8")
        except Exception as exc:
            marker = "번역 캐시 저장 실패"
            if not any(marker in warning for warning in self.warnings):
                self.warnings.append(
                    f"{marker}: {exc}"
                )
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except Exception:
                    pass

    def _remember_translation(self, source, translated):
        if (
            isinstance(source, str)
            and isinstance(translated, str)
            and translated.strip()
            and translated != source
            and source not in self._uncacheable_sources
        ):
            self.cache[source] = translated
            self._save_persistent_cache()
            return

        # An original-preserved fallback is not a successful translation.
        # Never let a transient validation/API failure become a permanent
        # identity cache entry that suppresses retries on the next run.
        if isinstance(source, str) and source in self.cache:
            self.cache.pop(source, None)
            self._save_persistent_cache()

    def _ensure_project_id(self):
        if self.project_id:
            if not self.glossary_name:
                self.glossary_name = (
                    f"projects/{self.project_id}/locations/{self.location}/"
                    f"glossaries/{self.glossary_id}"
                )
            return self.project_id

        try:
            import google.auth
        except ImportError as exc:
            raise TranslationError(
                "Google Cloud Translation 라이브러리가 없습니다. "
                "현재 가상환경에서 `python -m pip install -U google-cloud-translate`를 실행하세요."
            ) from exc

        try:
            _credentials, detected_project = google.auth.default()
        except Exception as exc:
            raise TranslationError(
                "Google Cloud 인증을 찾지 못했습니다. `gcloud auth application-default login`을 "
                "실행한 뒤 SHEETMOVER_GOOGLE_PROJECT 환경변수에 프로젝트 ID를 지정하세요."
            ) from exc

        if detected_project:
            self.project_id = detected_project
            self.glossary_name = (
                f"projects/{self.project_id}/locations/{self.location}/"
                f"glossaries/{self.glossary_id}"
            )
            return self.project_id

        raise TranslationError(
            "Google Cloud 프로젝트 ID가 없습니다. PowerShell에서 "
            "`$env:SHEETMOVER_GOOGLE_PROJECT=\"프로젝트_ID\"`를 지정하세요."
        )

    def _client(self):
        if self.client is not None:
            return self.client

        try:
            from google.cloud import translate_v3
        except ImportError as exc:
            raise TranslationError(
                "Google Cloud Translation 라이브러리가 없습니다. "
                "현재 가상환경에서 `python -m pip install -U google-cloud-translate`를 실행하세요."
            ) from exc

        try:
            self.client = translate_v3.TranslationServiceClient()
        except Exception as exc:
            raise TranslationError(
                "Google Cloud Translation 클라이언트 생성에 실패했습니다. "
                "`gcloud auth application-default login` 인증 상태를 확인하세요: "
                f"{exc}"
            ) from exc

        return self.client

    def _ensure_remote_glossary(self):
        """Ensure the required Cloud glossary exists before translation starts.

        A glossary ID is derived from the local glossary contents.  When the
        local glossary changes, the next normal translation run can therefore
        point at a glossary that has not been provisioned yet.  Treat that as
        a recoverable setup condition: provision the exact current glossary
        automatically, then verify it once more before sending billable text.
        """
        if (
            self._glossary_preflight_done
            or self._legacy_test_client
            or self._client_injected
        ):
            return

        project_id = self._ensure_project_id()
        client = self._client()
        if not self.glossary_name:
            self.glossary_name = (
                f"projects/{project_id}/locations/{self.location}/"
                f"glossaries/{self.glossary_id}"
            )

        def get_remote_glossary():
            try:
                return client.get_glossary(
                    request={"name": self.glossary_name}
                )
            except TypeError:
                return client.get_glossary(name=self.glossary_name)

        try:
            get_remote_glossary()
        except Exception as exc:
            class_name = exc.__class__.__name__.casefold()
            message = str(exc)
            missing = (
                "notfound" in class_name
                or "not found" in message.casefold()
            )
            if not missing:
                raise TranslationError(
                    "Google 번역 용어집 사전 확인에 실패했습니다: "
                    f"{message}"
                ) from exc

            try:
                # Local import avoids a module-level cycle: google_glossary
                # intentionally reuses glossary helpers from this module.
                from .google_glossary import setup_google_glossary

                setup_result = setup_google_glossary(
                    glossary_path=self.glossary_path,
                    project_id=project_id,
                )
            except Exception as setup_exc:
                raise TranslationError(
                    "필요한 Google 번역 용어집이 없어 자동 생성을 "
                    "시도했지만 실패했습니다. "
                    f"필요한 용어집: {self.glossary_id}. "
                    f"자동 생성 오류: {setup_exc}"
                ) from setup_exc

            created_id = str(setup_result.get("glossary_id") or "")
            if created_id and created_id != self.glossary_id:
                raise TranslationError(
                    "Google 번역 용어집 자동 생성 결과가 현재 사전과 "
                    "일치하지 않습니다. "
                    f"필요: {self.glossary_id}, 생성: {created_id}"
                )

            try:
                get_remote_glossary()
            except Exception as verify_exc:
                raise TranslationError(
                    "Google 번역 용어집을 자동 생성했지만 다시 조회할 "
                    "수 없습니다. "
                    f"필요한 용어집: {self.glossary_id}. "
                    f"조회 오류: {verify_exc}"
                ) from verify_exc

        self._glossary_preflight_done = True

    @staticmethod
    def _message_field(response, field):
        """Compatibility helper for existing injected unit-test doubles."""
        message = (
            response.message
            if hasattr(response, "message")
            else response.get("message", {})
        )
        if hasattr(message, field):
            return getattr(message, field) or ""
        if isinstance(message, dict):
            return message.get(field) or ""
        return ""

    @staticmethod
    def _translation_field(row):
        if hasattr(row, "translated_text"):
            return getattr(row, "translated_text") or ""
        if isinstance(row, dict):
            return row.get("translated_text") or row.get("translatedText") or ""
        return ""

    @staticmethod
    def _is_soft_model_error(exc):
        """Backward-compatible name for content-level translation failures."""
        message = str(exc)
        return any(
            marker in message
            for marker in (
                "빈 번역문",
                "최종 번역문을 비워",
                "JSON이 아닌 응답",
                "배치 번역 결과를 비워",
                "translations 배열이 없습니다",
                "응답 항목 형식이 올바르지",
                "id/translation이 올바르지",
                "항목 수 또는 id가 입력과 일치하지",
                "허용되지 않은 문자 체계",
                "한자 또는 중국어 문자가 포함",
                "보호 토큰을 변경했습니다",
                "보호 구간을 변경했습니다",
                "숫자·주사위식·Roll20 수식을 변경했습니다",
                "숫자·주사위식·Roll20 수식이 달라졌습니다",
                "숫자·주사위식·Roll20 수식이 바뀌었습니다",
                "구조화 번역 중 숫자·주사위식·Roll20 수식",
                "구조화 번역 중 HTML/D&D 태그",
                "기계적 값용 HTML span",
                "기계적 값 태그 식별자",
                "기계적 값 임시 태그",
                "규칙 의미 앵커",
            )
        )

    @staticmethod
    def _preserved_fragment_key(value):
        if not isinstance(value, str):
            return ""
        visible = html.unescape(_model_text(value))
        return re.sub(r"\s+", " ", visible).strip()

    def _warn_original_preserved(self, source, reason):
        key = self._preserved_fragment_key(source)
        if key:
            self._preserved_fragment_keys.add(key)
        preview = re.sub(r"\s+", " ", source).strip()[:120]
        entry = {
            "reason": str(reason),
            "source_preview": preview,
        }
        if entry not in self.original_preserved:
            self.original_preserved.append(entry)

        warning = (
            "번역 서비스가 해당 설명 조각을 안정적으로 반환하지 못해 "
            f"원문으로 유지했습니다 ({reason}): {preview}"
        )
        if warning not in self.warnings:
            self.warnings.append(warning)

    def translation_summary(self):
        preserved = copy.deepcopy(self.original_preserved)
        return {
            "status": "partial" if preserved else "complete",
            "original_preserved_count": len(preserved),
            "original_preserved": preserved,
        }

    def _safe_fragment_model_translate(
        self,
        source,
        placeholder_mode=False,
        strict_plain=False,
        strict_terms=False,
        warn_on_failure=True,
    ):
        """Keep the old method name; fragment failures may degrade to original."""
        last_error = None
        for _ in range(2):
            try:
                return self._model_translate(
                    source,
                    placeholder_mode=placeholder_mode,
                    strict_plain=strict_plain,
                    strict_terms=strict_terms,
                )
            except TranslationError as exc:
                if not self._is_soft_model_error(exc):
                    raise
                last_error = exc

        if warn_on_failure:
            self._warn_original_preserved(
                source,
                str(last_error) if last_error else "알 수 없는 응답 오류",
            )
        return None

    def _google_request(self, contents, mime_type):
        project_id = self._ensure_project_id()
        client = self._client()
        parent = f"projects/{project_id}/locations/{self.location}"
        if not self.glossary_name:
            self.glossary_name = (
                f"projects/{project_id}/locations/{self.location}/"
                f"glossaries/{self.glossary_id}"
            )

        request = {
            "parent": parent,
            "contents": contents,
            "mime_type": mime_type,
            "source_language_code": "en",
            "target_language_code": "ko",
            "glossary_config": {
                "glossary": self.glossary_name,
                "ignore_case": True,
            },
        }

        try:
            try:
                response = client.translate_text(
                    request=request,
                    timeout=GOOGLE_TRANSLATION_TIMEOUT,
                )
            except TypeError:
                response = client.translate_text(request=request)
        except Exception as exc:
            class_name = exc.__class__.__name__.casefold()
            message = str(exc)
            message_lower = message.casefold()
            if "defaultcredential" in class_name or "unauthenticated" in class_name:
                raise TranslationError(
                    "Google Cloud 인증에 실패했습니다. "
                    "`gcloud auth application-default login`을 실행하세요: "
                    f"{message}"
                ) from exc
            if "notfound" in class_name or "glossary" in message_lower and "not found" in message_lower:
                raise TranslationError(
                    "Sheet Mover용 Google 번역 용어집이 없습니다. "
                    "`python -m sheet_mover --setup-google-glossary`를 한 번 실행하세요. "
                    f"필요한 용어집: {self.glossary_id}"
                ) from exc
            if "permissiondenied" in class_name:
                raise TranslationError(
                    "Cloud Translation API 권한이 없습니다. 프로젝트에서 "
                    "translate.googleapis.com 사용 설정과 결제 연결을 확인하세요: "
                    f"{message}"
                ) from exc
            if "resourceexhausted" in class_name:
                raise TranslationError(
                    f"Google Cloud Translation 할당량을 초과했습니다: {message}"
                ) from exc
            raise TranslationError(
                f"Google Cloud Translation 호출에 실패했습니다: {message}"
            ) from exc

        rows = getattr(response, "glossary_translations", None)
        if not rows:
            rows = getattr(response, "translations", None)
        if rows is None and isinstance(response, dict):
            rows = (
                response.get("glossary_translations")
                or response.get("glossaryTranslations")
                or response.get("translations")
            )
        if not isinstance(rows, (list, tuple)):
            try:
                rows = list(rows)
            except Exception as exc:
                raise TranslationError(
                    "Google Cloud Translation 응답에 번역 목록이 없습니다."
                ) from exc

        if len(rows) != len(contents):
            raise TranslationError(
                "Google Cloud Translation 응답 개수가 요청 개수와 다릅니다."
            )
        return [self._translation_field(row) for row in rows]

    @staticmethod
    def _localize_mechanical_lock(token):
        """Render a protected English mechanical atom safely in Korean.

        The numeric value is never translated.  Only a tiny set of adjacent
        mechanical labels is localized deterministically so the model cannot
        split ``30 feet`` or ``0 Hit Points`` and then lose/duplicate the
        number while reordering Korean word order.
        """
        if not isinstance(token, str):
            return token
        value = token.strip()
        match = re.fullmatch(
            r"([+-]?\d+(?:\.\d+)?)\s*(?:-\s*)?"
            r"(feet|foot|ft\.?|miles?|inches?|pounds?|lbs?\.?|pints?|gallons?|"
            r"hours?|minutes?|seconds?|rounds?|days?)",
            value,
            re.I,
        )
        if match:
            number, unit = match.groups()
            unit_key = unit.casefold().rstrip(".")
            unit_map = {
                "feet": "피트", "foot": "피트", "ft": "피트",
                "mile": "마일", "miles": "마일",
                "inch": "인치", "inches": "인치",
                "pound": "파운드", "pounds": "파운드", "lb": "파운드", "lbs": "파운드",
                "pint": "파인트", "pints": "파인트",
                "gallon": "갤런", "gallons": "갤런",
                "hour": "시간", "hours": "시간",
                "minute": "분", "minutes": "분",
                "second": "초", "seconds": "초",
                "round": "라운드", "rounds": "라운드",
                "day": "일", "days": "일",
            }
            return f"{number}{unit_map[unit_key]}"
        match = re.fullmatch(
            r"([+-]?\d+(?:\.\d+)?)(?:st|nd|rd|th)\s+level",
            value,
            re.I,
        )
        if match:
            return f"{match.group(1)}레벨"
        match = re.fullmatch(
            r"([+-]?\d+(?:\.\d+)?)\s+Hit\s+Points?",
            value,
            re.I,
        )
        if match:
            return f"HP {match.group(1)}"
        match = re.fullmatch(r"DC\s*([+-]?\d+(?:\.\d+)?)", value, re.I)
        if match:
            return f"DC {match.group(1)}"
        return value

    @staticmethod
    def _protect_visible_mechanics_html(source):
        """Wrap visible rule atoms in real HTML spans before Cloud NMT.

        v10 locked bare numbers.  Live Cloud output showed why that boundary is
        too small: ``300`` and ``feet`` could be interpreted separately, which
        allowed a metric echo to be generated beside the protected number, and
        ``0`` could be detached from ``Hit Points``.  v11 therefore locks the
        smallest semantic atom (distance, HP value, DC, level, dice, currency)
        before falling back to a standalone number.
        """
        locks = {}

        def protect_text(text):
            def replace(match):
                index = len(locks)
                token = match.group(0)
                locks[index] = token
                return (
                    f'<span id="sm-mech-{index}" class="notranslate" '
                    f'translate="no">{token}</span>'
                )
            return GOOGLE_MECHANICAL_LOCK_PATTERN.sub(replace, text)

        pieces = []
        cursor = 0
        for match in re.finditer(r"<[^>]*>", source, re.S):
            if match.start() > cursor:
                pieces.append(protect_text(source[cursor:match.start()]))
            pieces.append(match.group(0))
            cursor = match.end()
        if cursor < len(source):
            pieces.append(protect_text(source[cursor:]))
        return "".join(pieces), locks

    @staticmethod
    def _restore_google_mechanics_html(translated, locks):
        if not locks:
            return translated
        seen = set()

        def restore_span(match):
            index = int(match.group("id"))
            if index not in locks or index in seen:
                raise TranslationError(
                    "Google Cloud Translation이 기계적 값 태그 식별자를 변경했습니다."
                )
            seen.add(index)
            return Translator._localize_mechanical_lock(locks[index])

        restored = MECHANIC_HTML_SPAN_PATTERN.sub(restore_span, translated)
        if seen != set(locks):
            missing = sorted(set(locks) - seen)
            raise TranslationError(
                "Google Cloud Translation이 기계적 값용 HTML span을 변경했습니다: "
                + ", ".join(str(index) for index in missing[:6])
            )
        if "sm-mech-" in restored:
            raise TranslationError(
                "Google Cloud Translation 응답에 기계적 값 임시 태그가 남았습니다."
            )
        return restored

    def _prepare_google_plain_mechanics_html(self, source):
        escaped = html.escape(_model_text(source), quote=False)
        return self._protect_visible_mechanics_html(escaped)

    def _restore_google_plain_mechanics_html(self, translated, locks):
        restored = self._restore_google_mechanics_html(translated, locks)
        return self._clean_google_plain(html.unescape(restored))

    def _prepare_google_html(self, source):
        """Convert D&D inline pseudo-tags to ordinary HTML spans.

        Cloud Translation already understands HTML and preserves real markup.
        The only structure it does not understand is D&D Beyond's custom
        ``[action]...[/action]`` style markup, so convert those pairs to
        normal ``span`` elements for the request and restore them afterwards.

        Unlike the v6 private-use-marker path, this deliberately leaves dice,
        distances, DCs, currency and ordinary HTML visible to Google.  Those
        values are verified after translation instead of depending on an
        opaque token that the live service may delete.
        """
        source = unescape_text_outside_structure(source).replace("\xa0", " ")
        locks = {}

        def replace_pair(match):
            index = len(locks)
            tag = match.group("tag")
            body = match.group("body")
            locks[index] = (tag, body)
            return (
                f'<span id="sm-dnd-{index}" data-sm-dnd="{index}">'
                f'{body}</span>'
            )

        prepared = DND_TAG_PAIR_PATTERN.sub(replace_pair, source)
        prepared, mechanic_locks = self._protect_visible_mechanics_html(prepared)
        return prepared, locks, mechanic_locks

    def _restore_google_html(self, translated, locks, mechanic_locks):
        """Restore temporary HTML spans back to exact D&D pseudo-tags."""
        if not isinstance(translated, str) or not translated.strip():
            raise TranslationError(
                "Google Cloud Translation이 빈 번역문을 반환했습니다."
            )

        translated = self._restore_google_mechanics_html(
            translated, mechanic_locks
        )
        seen = set()

        def restore_span(match):
            index = int(match.group("id"))
            if index in seen or index not in locks:
                raise TranslationError(
                    "Google Cloud Translation이 D&D 태그 식별자를 변경했습니다."
                )
            seen.add(index)
            tag, source_body = locks[index]
            translated_body = match.group("body")
            target = self._dnd_tag_target(tag, source_body)
            body = target if target is not None else translated_body
            return f"[{tag}]{body}[/{tag}]"

        restored = DND_HTML_SPAN_PATTERN.sub(restore_span, translated)
        if seen != set(locks):
            missing = sorted(set(locks) - seen)
            raise TranslationError(
                "Google Cloud Translation이 D&D 태그용 HTML span을 변경했습니다: "
                + ", ".join(str(index) for index in missing[:6])
            )
        if "data-sm-dnd=" in restored or "sm-dnd-" in restored:
            raise TranslationError(
                "Google Cloud Translation 응답에 D&D 임시 태그가 남았습니다."
            )

        restored = clean_translation_output(restored)
        if contains_unexpected_script(restored):
            raise TranslationError(
                "번역문에 허용되지 않은 문자 체계가 포함되었습니다."
            )
        return restored

    def _clean_google_plain(self, translated):
        if not isinstance(translated, str) or not translated.strip():
            raise TranslationError(
                "Google Cloud Translation이 빈 번역문을 반환했습니다."
            )
        translated = clean_translation_output(translated)
        if contains_unexpected_script(translated):
            raise TranslationError(
                "번역문에 허용되지 않은 문자 체계가 포함되었습니다."
            )
        return translated

    def _google_translate_batch(self, sources, forced_terms_by_source=None):
        """Translate without opaque mechanical/structure placeholders.

        Live Cloud Translation proved that both long ASCII markers and Unicode
        private-use markers can be normalized or dropped.  v7 therefore sends
        normal prose and mechanics directly to Google and validates the result
        afterwards. Structured input is sent as ``text/html``; D&D Beyond's
        custom square-bracket tags are temporarily represented by real HTML
        spans whose *attributes* survive translation.
        """
        if not sources:
            return []

        results = [None] * len(sources)
        plain_items = []
        mechanic_plain_items = []
        html_items = []

        for index, source in enumerate(sources):
            if STRUCTURE_PATTERN.search(source):
                prepared, locks, mechanic_locks = self._prepare_google_html(source)
                html_items.append((index, prepared, locks, mechanic_locks))
            elif mechanical_tokens(source):
                prepared, mechanic_locks = self._prepare_google_plain_mechanics_html(
                    source
                )
                mechanic_plain_items.append((index, prepared, mechanic_locks))
            else:
                plain_items.append((index, _model_text(source)))

        if plain_items:
            raw_values = self._google_request(
                [prepared for _index, prepared in plain_items],
                "text/plain",
            )
            for (index, _prepared), raw in zip(plain_items, raw_values):
                results[index] = self._clean_google_plain(raw)

        if mechanic_plain_items:
            raw_values = self._google_request(
                [prepared for _index, prepared, _locks in mechanic_plain_items],
                "text/html",
            )
            for (index, _prepared, mechanic_locks), raw in zip(
                mechanic_plain_items, raw_values
            ):
                results[index] = self._restore_google_plain_mechanics_html(
                    raw, mechanic_locks
                )

        if html_items:
            raw_values = self._google_request(
                [prepared for _index, prepared, _locks, _mechanics in html_items],
                "text/html",
            )
            for (index, _prepared, locks, mechanic_locks), raw in zip(
                html_items, raw_values
            ):
                results[index] = self._restore_google_html(
                    raw, locks, mechanic_locks
                )

        if any(value is None for value in results):
            raise TranslationError(
                "Google Cloud Translation 배치 결과를 모두 복원하지 못했습니다."
            )
        return results

    @staticmethod
    def _visible_rule_text(value):
        """Return only user-visible prose for semantic rule validation."""
        if not isinstance(value, str):
            return ""
        visible = html.unescape(value)
        visible = STRUCTURE_PATTERN.sub(" ", visible)
        visible = re.sub(r"\s+", " ", visible).strip()
        return visible

    @classmethod
    def _missing_semantic_rule_anchors(cls, source, translated):
        """Detect high-confidence loss of exception/addition relations.

        Numbers, dice and markup are validated elsewhere.  This guard covers
        the complementary failure mode seen in live translation: Cloud can
        preserve ``[action]Magic[/action]`` while dropping the surrounding
        ``one additional action, except ...`` rule clause.
        """
        source_text = cls._visible_rule_text(source)
        target_text = cls._visible_rule_text(translated)
        if not source_text or not target_text:
            return []

        # Unit-test doubles and explicit English fallbacks may intentionally
        # leave the source untouched.  Semantic anchors matter only once the
        # output is actually translated/localized.
        if source_text.casefold() == target_text.casefold():
            return []
        if not re.search(r"[가-힣]", target_text):
            return []

        missing = []
        for name, source_pattern, target_pattern in SEMANTIC_RULE_ANCHORS:
            if source_pattern.search(source_text) and not target_pattern.search(target_text):
                missing.append(name)
        return missing

    @classmethod
    def _validate_semantic_rule_anchors(cls, source, translated):
        missing = cls._missing_semantic_rule_anchors(source, translated)
        if missing:
            raise TranslationError(
                "규칙 의미 앵커가 번역에서 누락되었습니다: "
                + ", ".join(missing)
                + f"; 원문={cls._visible_rule_text(source)[:120]}"
            )
        return translated

    @classmethod
    def _rule_equivalence_key(cls, value):
        """Normalize markup-only variants of the same D&D rule sentence.

        Feature text and action text often differ only by D&D inline tags.
        This key lets tests/diagnostics recognize them as the same rule without
        forcing their rendered markup to be identical.
        """
        visible = cls._visible_rule_text(value)
        visible = visible.replace("’", "'").replace("‘", "'")
        visible = re.sub(r"\s+", " ", visible).strip().casefold()
        return visible

    def _google_retry_missing_terms(self, source, missing):
        """Do not retranslate a sentence just to force a glossary phrase.

        The managed Cloud glossary remains the terminology layer. Retrying the
        same sentence with opaque term markers was the last remaining source of
        live-service marker loss, and a second identical request cannot improve
        a context-sensitive glossary miss. The caller keeps the otherwise-safe
        translation and records a warning when a genuinely required target is
        absent.
        """
        return None

    def _relevant_glossary(self, sources):
        """Send only context-safe glossary entries present in prose."""
        result = {}
        for source in sources:
            if not isinstance(source, str):
                continue
            for source_term, target in self._required_glossary_pairs(source):
                result.setdefault(source_term.casefold(), target)
        return result

    def _protect_glossary_terms(self, text):
        """Hard-lock glossary phrases, preferring the longest phrase."""
        keys = sorted(
            (key for key in self.glossary if isinstance(key, str) and key.strip()),
            key=len,
            reverse=True,
        )
        if not text or not keys:
            return text, []
        pattern = re.compile(
            r"(?<![A-Za-z0-9_])(?:" + "|".join(re.escape(k) for k in keys) + r")(?![A-Za-z0-9_])",
            re.I,
        )
        replacements = []
        def repl(match):
            token = f"__SHEETMOVER_GLOSSARY_{len(replacements):04d}__"
            replacements.append((token, self.glossary[match.group(0).casefold()]))
            return token
        return pattern.sub(repl, text), replacements

    def _translate_glossary_fallback(self, text):
        """Fallback that never lets a glossary term drift."""
        keys = sorted((k for k in self.glossary if k), key=len, reverse=True)
        if not text or not keys:
            return text
        pattern = re.compile(
            r"(?<![A-Za-z0-9_])(?:" + "|".join(re.escape(k) for k in keys) + r")(?![A-Za-z0-9_])",
            re.I,
        )
        parts, cursor = [], 0
        for match in pattern.finditer(text):
            if match.start() > cursor:
                plain = text[cursor:match.start()]
                if plain.strip() and re.search(r"[A-Za-z]", plain):
                    tr = self._safe_fragment_model_translate(plain, placeholder_mode=False, strict_plain=True)
                    parts.append(plain if tr is None else tr)
                else:
                    parts.append(plain)
            parts.append(self.glossary[match.group(0).casefold()])
            cursor = match.end()
        if cursor < len(text):
            plain = text[cursor:]
            if plain.strip() and re.search(r"[A-Za-z]", plain):
                tr = self._safe_fragment_model_translate(plain, placeholder_mode=False, strict_plain=True)
                parts.append(plain if tr is None else tr)
            else:
                parts.append(plain)
        return "".join(parts)

    def _glossary_pattern(self):
        keys = sorted(
            (key for key in self.glossary if isinstance(key, str) and key.strip()),
            key=len,
            reverse=True,
        )
        if not keys:
            return None
        return re.compile(
            r"(?<![A-Za-z0-9_])(?:"
            + "|".join(re.escape(key) for key in keys)
            + r")(?![A-Za-z0-9_])",
            re.I,
        )

    def _required_glossary_pairs(self, text):
        """Return non-overlapping glossary terms actually present in source."""
        pattern = self._glossary_pattern()
        if pattern is None or not isinstance(text, str):
            return []

        pairs = []
        seen = set()
        for match in pattern.finditer(text):
            source_term = match.group(0)
            target = self.glossary.get(source_term.casefold())
            if not isinstance(target, str) or not target:
                continue
            normalized = source_term.strip().casefold()
            if not glossary_term_is_prose_safe(source_term):
                continue

            key = (normalized, target)
            if key in seen:
                continue
            seen.add(key)
            pairs.append((source_term, target))
        return pairs

    def _translate_glossary_composite(self, text):
        """Translate a string locally if glossary covers all words.

        Examples:
        Topple (Trident) -> 넘어뜨리기 (삼지창)
        Fighter Level -> 전사 레벨

        Punctuation, whitespace and numbers are preserved verbatim.
        """
        pattern = self._glossary_pattern()
        if pattern is None or not isinstance(text, str) or not text:
            return None

        parts = []
        cursor = 0
        matched = False

        for match in pattern.finditer(text):
            gap = text[cursor:match.start()]
            # Any unmatched alphabetic text means this is real prose and must
            # be translated naturally by the model.
            if re.search(r"[A-Za-z]", gap):
                return None

            parts.append(gap)
            parts.append(self.glossary[match.group(0).casefold()])
            matched = True
            cursor = match.end()

        tail = text[cursor:]
        if re.search(r"[A-Za-z]", tail):
            return None

        if not matched:
            return None

        parts.append(tail)
        return "".join(parts)

    def _missing_glossary_targets(self, source, translated):
        """Check whether model respected non-overlapping glossary choices."""
        if not isinstance(translated, str):
            return []
        missing = []
        for source_term, target in self._required_glossary_pairs(source):
            if target not in translated:
                missing.append((source_term, target))
        return missing

    def _dnd_tag_target(self, tag, source_body):
        tag = (tag or "").casefold()
        source_body = html.unescape(source_body or "").strip()
        key = source_body.casefold()
        if not source_body:
            return None
        if tag == "wprop":
            return WEAPON_PROPERTY_NAME_MAP.get(key)
        if tag == "action":
            return DND_ACTION_NAME_MAP.get(key) or self.glossary.get(key)
        if tag == "condition":
            return DND_CONDITION_NAME_MAP.get(key) or self.glossary.get(key)
        if tag == "rules":
            return DND_RULE_NAME_MAP.get(key) or self.glossary.get(key)
        if tag == "items":
            return self.glossary.get(key)
        return None

    def _normalize_dnd_tag_contents(self, source, translated):
        """Use the source tag type to disambiguate inline D&D labels."""
        source_matches = list(DND_TAG_PAIR_PATTERN.finditer(source or ""))
        target_matches = list(DND_TAG_PAIR_PATTERN.finditer(translated or ""))
        if len(source_matches) != len(target_matches):
            return translated

        replacements = []
        for source_match, target_match in zip(source_matches, target_matches):
            source_tag = source_match.group("tag").casefold()
            target_tag = target_match.group("tag").casefold()
            if source_tag != target_tag:
                return translated
            target = self._dnd_tag_target(
                source_tag,
                source_match.group("body"),
            )
            if target is not None:
                replacements.append(
                    (target_match.start("body"), target_match.end("body"), target)
                )

        result = translated
        for start, end, target in reversed(replacements):
            result = result[:start] + target + result[end:]
        return result

    def _normalize_structured_exact_labels(self, source, translated):
        """Normalize short labels that are isolated by markup.

        Mastery headings such as ``<em>Sap.</em>`` should never become the
        ordinary Korean verb/noun Google happens to prefer.  Because the
        source and translated markup must have the exact same token sequence,
        corresponding visible-text spans can be aligned safely and short
        glossary labels can be restored deterministically without touching
        surrounding prose.
        """
        if structure_tokens(source) != structure_tokens(translated):
            return translated

        def visible_spans(text):
            spans = []
            cursor = 0
            for match in STRUCTURE_PATTERN.finditer(text):
                # Keep empty/whitespace-only gaps too. Google may insert spaces
                # around protected markers; interval-by-interval alignment is
                # still valid as long as the structure-token sequence matches.
                spans.append((cursor, match.start()))
                cursor = match.end()
            spans.append((cursor, len(text)))
            return spans

        source_spans = visible_spans(source)
        target_spans = visible_spans(translated)
        if len(source_spans) != len(target_spans):
            return translated

        replacements = []
        for (s0, s1), (t0, t1) in zip(source_spans, target_spans):
            source_text = html.unescape(source[s0:s1])
            match = re.fullmatch(r"(\s*)(.*?)(\s*)", source_text, re.S)
            if not match:
                continue
            _leading, core, _trailing = match.groups()
            stripped_core = core.strip()
            key = stripped_core.casefold()
            target_label = STRUCTURED_EXACT_LABEL_MAP.get(key)
            punctuation = ""

            if target_label is None:
                label_match = re.fullmatch(
                    r"([A-Za-z][A-Za-z0-9 '&’+\-]*?)([.:])?",
                    stripped_core,
                )
                if not label_match:
                    continue
                english_label, punctuation = label_match.groups()
                key = english_label.casefold()
                target_label = (
                    WEAPON_PROPERTY_NAME_MAP.get(key)
                    or self.glossary.get(key)
                )
            if not target_label:
                continue

            target_text = translated[t0:t1]
            target_match = re.fullmatch(r"(\s*)(.*?)(\s*)", target_text, re.S)
            if not target_match:
                continue
            leading, _target_core, trailing = target_match.groups()
            replacements.append(
                (t0, t1, leading + target_label + (punctuation or "") + trailing)
            )

        result = translated
        for start, end, replacement in reversed(replacements):
            result = result[:start] + replacement + result[end:]
        return result

    @staticmethod
    def _is_2024_light_property_rule(source):
        source_lower = html.unescape(source or "").casefold()
        return (
            source_lower.count("<p") == 1
            and "when you take the [action]attack[/action] action on your turn" in source_lower
            and "that extra attack must be made with a different light weapon" in source_lower
            and "[items]shortsword[/items]" in source_lower
            and "[items]dagger[/items]" in source_lower
        )

    @staticmethod
    def _canonical_light_property_rule():
        return (
            "<p>자신의 턴에 [action]공격[/action] 행동을 취하고 경량 무기로 공격하면, "
            "같은 턴 후반에 추가 행동으로 추가 공격을 한 번 할 수 있습니다. "
            "이 추가 공격은 다른 경량 무기로 해야 하며, 해당 능력치 수정치가 음수인 경우를 "
            "제외하면 추가 공격의 피해에 능력치 수정치를 더하지 않습니다. 예를 들어 한 손의 "
            "[items]소검[/items]과 다른 손의 [items]단검[/items]으로 [action]공격[/action] 행동과 "
            "추가 행동을 사용해 각각 공격할 수 있지만, 해당 수정치가 음수가 아닌 한 추가 행동 "
            "공격의 피해 굴림에는 근력 또는 민첩 수정치를 더하지 않습니다.</p>"
        )

    def _canonicalize_structured_layout(self, source, translated):
        """Repair a few known HTML reorderings without changing rule data.

        Google is allowed to move visible text around inline tags while still
        preserving the tag sequence.  That is linguistically reasonable but
        unsafe for D&D headings/links because a heading can land in the middle
        of its sentence.  These repairs are gated by the exact English source
        rule, so unrelated markup is never rewritten.
        """
        if not isinstance(translated, str):
            return translated

        source_lower = html.unescape(source or "").casefold()
        result = translated

        # Preserve punctuation/spacing that belongs immediately after nested
        # inline links. Cloud occasionally keeps the tags but drops the comma
        # and whitespace before the next clause (for example the PHB link in
        # Variant Human). This is structural punctuation, not translated prose.
        if re.search(r"</a>\s*</em>\s*,\s*your\s+", source_lower):
            result = re.sub(
                r"</a>\s*</em>\s*(?=[가-힣])",
                "</a></em>, ",
                result,
                count=1,
                flags=re.I,
            )

        # Find Familiar: the period after the linked beast option belongs
        # before the sentence beginning "Appearing ...".  Google sometimes
        # glues the translated next sentence directly to </a>.
        if "</a>. appearing in an unoccupied space within range" in source_lower:
            result = re.sub(r"</a>(?=사정거리)", "</a>. ", result, count=1)

        # Find Familiar: keep the emphasized paragraph heading at the start and
        # state the one-familiar limit unambiguously.  v11 used a broad regex
        # that matched *any* paragraph whose heading contained "사역마".  On
        # the live document it matched "Disappearance of the Familiar" first
        # and replaced that entire paragraph, deleting both 0-HP values and the
        # 30-foot reappearance distance.  Match the exact source block by its
        # position instead.  If structure no longer lines up, do nothing rather
        # than risk replacing the wrong paragraph.
        if (
            "<strong><em>one familiar only.</em></strong>" in source_lower
            and "more than one familiar at a time" in source_lower
        ):
            canonical = (
                "<p><strong><em>사역마는 하나만.</em></strong> "
                "한 번에 사역마를 한 마리만 거느릴 수 있습니다. "
                "사역마를 거느린 상태에서 이 주문을 시전하면, 대신 사역마가 새로운 적합한 "
                "형태로 변합니다.</p>"
            )
            source_chunks = self._structured_chunks(source)
            translated_chunks = self._structured_chunks(result)
            if len(source_chunks) == len(translated_chunks):
                target_indexes = [
                    index
                    for index, chunk in enumerate(source_chunks)
                    if "<strong><em>one familiar only.</em></strong>"
                    in html.unescape(chunk).casefold()
                ]
                if len(target_indexes) == 1:
                    index = target_indexes[0]
                    leading = re.match(r"\s*", translated_chunks[index]).group(0)
                    translated_chunks[index] = leading + canonical
                    result = "".join(translated_chunks)

        # Core Fighter Traits: Google can move the skill list before the
        # emphasized "Choose 2" label.  Rebuild that one mechanical table row
        # from the exact source list instead of concatenating both copies.
        if (
            "skill proficiencies" in source_lower
            and "acrobatics, animal handling, athletics, history, insight, intimidation, "
                "persuasion, perception, or survival" in source_lower
        ):
            skill_row = re.compile(
                r"(<tr>\s*<th>\s*기술 숙련\s*</th>\s*<td>)(?:(?!</td>).)*(</td>\s*</tr>)",
                re.I | re.S,
            )
            result = skill_row.sub(
                r"\1<em>2개 선택:</em> 곡예, 동물 조련, 운동, 역사, 통찰, 위협, 설득, 지각 또는 생존\2",
                result,
                count=1,
            )

        return result

    def _canonicalize_translation(self, source, translated):
        """Apply source-aware, semantics-safe cleanup after Google NMT."""
        if not isinstance(translated, str):
            return translated

        # The 2024 Light weapon-property paragraph is a fixed rules block.
        # Live NMT has repeatedly interpreted "take the Attack action" as the
        # Korean verb for taking medicine, so use the deterministic rule text
        # whenever the exact source paragraph is present.
        if self._is_2024_light_property_rule(source):
            return self._canonical_light_property_rule()

        translated = self._normalize_dnd_tag_contents(source, translated)
        translated = self._normalize_structured_exact_labels(source, translated)
        source_lower = html.unescape(source or "").casefold()

        def fix_visible(text):
            text = normalize_translation_spacing(text)

            # Volume conversions still create noisy extra rule values, so
            # keep the original pint quantity.  Distance is different: ft->m
            # is a legitimate localization and the mechanics validator now
            # checks physical equivalence instead of requiring identical digits.
            if "pint" in source_lower and "liter" not in source_lower:
                text = re.sub(
                    r"(?<=파인트)\s*\(\s*(?:약\s*)?\d+(?:\.\d+)?\s*(?:리터|l)\s*\)",
                    "",
                    text,
                    flags=re.I,
                )

            # Damage-type drift is mechanically significant, so correct only
            # when the English source names the corresponding damage type.
            if "thunder damage" in source_lower and "lightning damage" not in source_lower:
                text = re.sub(r"(?:번개|전격) 피해", "천둥 피해", text)
            if "lightning damage" in source_lower and "thunder damage" not in source_lower:
                text = re.sub(r"천둥 피해", "전격 피해", text)
            if "necrotic damage" in source_lower:
                text = re.sub(r"(?:괴사|네크로틱) 피해", "사령 피해", text)

            # Booming Blade and similar rules distinguish voluntary movement
            # from forced movement.  Losing "willingly" changes the trigger.
            if "willingly moves" in source_lower and not re.search(
                r"자발적으로\s*\d+(?:\.\d+)?피트 이상 이동",
                text,
            ):
                text = re.sub(
                    r"(?<!자발적으로\s)(\d+(?:\.\d+)?피트 이상 이동하면)",
                    r"자발적으로 \1",
                    text,
                    count=1,
                )

            # A cube "originating from you" is not necessarily centered on
            # you.  Keep the distance before Cube and collapse duplicated word
            # order such as "정육면체 15피트 정육면체".  This is phrasing-
            # based rather than spell-name based and therefore applies to any
            # rule that uses the same geometry wording.
            cube = re.search(
                r"(\d+(?:\.\d+)?)-foot cube originating from you",
                source_lower,
            )
            if cube:
                distance = cube.group(1)
                cube_variants = re.compile(
                    rf"(?:당신(?:을 중심으로|에게서 시작되는)\s*)?"
                    rf"(?:정육면체\s*)?{re.escape(distance)}\s*피트\s*정육면체",
                    re.I,
                )
                text = cube_variants.sub(
                    f"당신에게서 시작되는 {distance}피트 정육면체",
                    text,
                    count=1,
                )

                if re.search(
                    rf"each creature in a {re.escape(distance)}-foot cube "
                    r"originating from you makes a constitution saving throw",
                    source_lower,
                ):
                    text = re.sub(
                        rf"당신에게서 시작되는 {re.escape(distance)}피트 정육면체"
                        r"(?:\s*범위)?\s*내의\s*(?:모든|각)\s*생명체"
                        r"(?:는)?\s*건강\s*내성\s*굴림(?:을)?\s*합니다\.",
                        f"당신에게서 시작되는 {distance}피트 정육면체 내의 "
                        "각 생명체는 건강 내성 굴림을 합니다.",
                        text,
                        count=1,
                    )

            if "away from you" in source_lower:
                text = text.replace("사용자로부터", "당신으로부터")

            # Rules text says creature, not enemy.  Do not narrow targeting
            # just because the common combat use is hostile.
            if re.search(r"reduce a creature to 0 (?:hit points|hp)\b", source_lower):
                text = re.sub(
                    r"적의\s*(?:체력|HP)(?:을|를)\s*0",
                    "생명체의 HP를 0",
                    text,
                    count=1,
                )

            # If the source contains Booming Blade's exact voluntary-movement
            # trigger, rebuild only that Korean sentence.  NMT sometimes moves
            # the adverb after the distance ("5피트 이상 자발적으로 이동") and
            # drops the conditional ending, which changes readability even
            # though the underlying numbers survive.
            trigger = re.search(
                r"if the target willingly moves (\d+(?:\.\d+)?) feet or more "
                r"before then, the target takes (\d+d\d+) thunder damage, and "
                r"the spell ends",
                source_lower,
            )
            if trigger:
                distance, damage = trigger.groups()
                canonical_trigger = (
                    f"대상이 그 전에 자발적으로 {distance}피트 이상 이동하면, "
                    f"대상은 {damage} 천둥 피해를 입고 주문이 종료됩니다."
                )
                text = re.sub(
                    r"대상이 그 전에[^.!?]*?주문이 종료됩니다\.",
                    canonical_trigger,
                    text,
                    count=1,
                )

            # Mechanical level labels must not become "5레벨 레벨" after a
            # protected English ordinal has already been restored as 5레벨.
            if "level" in source_lower:
                text = re.sub(r"(\d+레벨)\s*레벨", r"\1", text)
                text = re.sub(r"(\d+레벨\s*\([^)]*\))\s*레벨", r"\1", text)

            # "Audible within X feet" describes hearing range, not sound
            # size.  Keep an optional, physically equivalent metric rendering
            # if Cloud added one, but repair the semantics generically for any
            # source distance rather than hard-coding Thunderwave's 300 feet.
            audible = re.search(
                r"audible within (\d+(?:\.\d+)?) feet", source_lower
            )
            if audible:
                distance = re.escape(audible.group(1))
                sound_size = re.compile(
                    rf"{distance}\s*피트"
                    r"(?P<metric>\s*\(\s*(?:약\s*)?\d+(?:\.\d+)?\s*(?:미터|m)\s*\))?"
                    r"(?:만큼)?\s*(?:큰\s*)?(?:천둥(?:\s*같은)?\s*)?"
                    r"폭발음(?:이|가)\s*들립니다",
                    re.I,
                )
                match = sound_size.search(text)
                if match:
                    metric = match.group("metric") or ""
                    replacement = (
                        f"천둥 같은 폭발음이 {audible.group(1)}피트"
                        f"{metric} 이내에서 들립니다"
                    )
                    text = text[:match.start()] + replacement + text[match.end():]

            # Core Fighter equipment uses Javelins, not generic spears.
            if "8 javelins" in source_lower:
                text = re.sub(r"(?<!투)(?:창|재블린)\s*8개", "투창 8개", text)
                text = re.sub(r"(?:투){2,}창\s*8개", "투창 8개", text)

            # Common NMT surface glitches seen in otherwise-correct rules.
            text = text.replace("능력 수정치 정치만큼", "능력 수정치만큼")
            text = text.replace("능력 수정치 정치 만큼", "능력 수정치만큼")
            text = text.replace("능력 수정치 정치", "능력 수정치")
            text = re.sub(
                r"\b(근력|민첩|건강|지능|지혜|매력)\s+\1\b",
                r"\1",
                text,
            )
            if "fighter level" in source_lower:
                text = re.sub(r"(?:전투원|전투기|파이터)\s*레벨", "전사 레벨", text)
            if "the bond fails if" in source_lower:
                text = text.replace("결속이 끊어집니다", "결속 의식은 실패합니다")
            if "hit point" in source_lower:
                text = re.sub(r"히트\s*포인트", "HP", text, flags=re.I)
            if re.search(r"\bfeats?\b", source_lower):
                text = text.replace("재주", "특기")
                text = re.sub(r"이 특기\s+얻으면", "이 특기를 얻으면", text)
                text = re.sub(r"특기\s+하나\s+얻", "특기 하나를 얻", text)
            if "casting bright light" in source_lower:
                text = re.sub(r"희미한 빛\s*춥니다", "희미한 빛을 비춥니다", text)

            # Low-risk Korean particle/spacing repairs that are common across
            # D&D rules. These are intentionally narrow: they fix a missing
            # case marker immediately around well-known rules nouns without
            # rephrasing the sentence or touching mechanics.
            text = re.sub(r"생명체\s+이나\b", "생명체나", text)
            text = re.sub(r"피해\s+입(?=[으으면고었])", "피해를 입", text)
            text = re.sub(r"공격 행동\s+할 때", "공격 행동을 할 때", text)
            text = re.sub(r"활용 행동\s+사용", "활용 행동을 사용", text)
            text = re.sub(r"추가 행동\s+필요합니다", "추가 행동이 필요합니다", text)
            text = re.sub(r"(?<!\d)(\d+(?:\.\d+)?)\s+레벨", r"\1레벨", text)
            text = re.sub(r"레벨\s+(\d+(?:\.\d+)?)\s*\+\s*주문", r"\1레벨 이상 주문", text)
            if "a creature takes" in source_lower:
                text = re.sub(
                    r"생명체\s+((?:\d+d\d+|\d+)\s+[^.!?]{0,30}?피해를)",
                    r"생명체는 \1",
                    text,
                )
            if "a creature takes half as much damage" in source_lower:
                text = re.sub(r"생명체\s+절반의 피해", "생명체는 절반의 피해", text)
            if "whenever you gain a fighter level" in source_lower:
                text = re.sub(r"전사 레벨\s+올릴 때마다", "전사 레벨이 오를 때마다", text)
            if "finish a long rest" in source_lower:
                text = re.sub(r"긴 휴식\s+취하면", "긴 휴식을 마치면", text)
            if "have spell slots" in source_lower:
                text = re.sub(r"주문 슬롯\s+있는", "주문 슬롯이 있는", text)

            # Condition lists are often translated from comma-separated English
            # nouns. Cloud occasionally leaves stray closing parentheses after
            # each condition even though the source has no parentheses. Repair
            # the semantic list only when the source contains that exact list.
            if "grappled, incapacitated, or restrained condition" in source_lower:
                text = re.sub(
                    r"붙잡힘\)?\s*,\s*행동 불능\)?\s*(?:,?\s*또는)\s*구속\)?\s*상태",
                    "붙잡힘, 행동 불능 또는 구속 상태",
                    text,
                    count=1,
                )
                text = re.sub(r"밧줄\s+사용하면", "밧줄을 사용하면", text)
                text = re.sub(r"생명체\s+묶을 수", "생명체를 묶을 수", text)
                text = re.sub(r"밧줄\s+탈출하려면", "밧줄에서 탈출하려면", text)
                text = re.sub(r"생명체\s+행동으로", "생명체가 행동으로", text)
            if "the rope can be burst with" in source_lower:
                text = re.sub(r"이 밧줄\s+DC", "이 밧줄은 DC", text)

            # Standard Healer's Kit rule. The two protected mechanical atoms
            # (one use / 0 HP) can otherwise be reordered into the nonsensical
            # sequence "사용 횟수 1 HP 0". Rebuild only the source-matched
            # clause, preserving the original mechanics exactly.
            if (
                "expend one of its uses to stabilize an unconscious creature"
                in source_lower
                and "0 hit points" in source_lower
                and "wisdom (medicine) check" in source_lower
            ):
                text = re.sub(
                    r"(?:활용 행동으로,?\s*)?[^.]*?(?:HP\s*0|0\s*HP)[^.]*?"
                    r"(?:안정|안정화)[^.]*?\.",
                    "활용 행동으로, 사용 횟수 1회를 소모하여 지혜(의학) 판정 없이 "
                    "HP가 0인 의식 불명 생명체를 안정화할 수 있습니다.",
                    text,
                    count=1,
                )

            # Weapon-property prose should not acquire quote entities around
            # canonical property names. This template occurs across features,
            # not just on one character.
            if "two-handed or versatile property" in source_lower:
                text = re.sub(
                    r"(?:&#39;|['’])?\s*양손\s*(?:&#39;|['’])?\s*또는\s*"
                    r"(?:&#39;|['’])?\s*다용도(?:\s*속성)?\s*(?:&#39;|['’])?\s*"
                    r"(?:속성)?\s*있어야 합니다",
                    "양손 또는 다용도 속성이 있어야 합니다",
                    text,
                    count=1,
                )

            # Common spellcasting templates. These are parameterized from the
            # English source rather than tied to Eldritch Knight specifically.
            # The goal is to keep subject/object order stable when Cloud turns
            # "Intelligence is your spellcasting ability" into a noun pile.
            ability_rule = re.search(
                r"\b(strength|dexterity|constitution|intelligence|wisdom|charisma) "
                r"is your spellcasting ability for your ([a-z]+) spells\.",
                source_lower,
            )
            if ability_rule:
                ability_en, list_en = ability_rule.groups()
                ability_ko = SPELLCASTING_ABILITY_NAME_MAP.get(ability_en, ability_en)
                list_ko = SPELL_LIST_NAME_MAP.get(list_en, list_en)
                text = re.sub(
                    r"(?:근력|민첩|건강|지능|지혜|매력)[^.]{0,100}"
                    r"주문시전 능력치[^.]*\.",
                    f"{ability_ko}은 당신의 {list_ko} 주문의 주문시전 능력치입니다.",
                    text,
                    count=1,
                )

            focus_rule = re.search(
                r"you can use an? ([a-z ]+?) as a spellcasting focus for your "
                r"([a-z]+) spells\.",
                source_lower,
            )
            if focus_rule:
                focus_en, list_en = focus_rule.groups()
                focus_ko = SPELLCASTING_FOCUS_NAME_MAP.get(
                    focus_en.strip(), focus_en.strip()
                )
                list_ko = SPELL_LIST_NAME_MAP.get(list_en, list_en)
                text = re.sub(
                    r"(?P<lead>\s*)[^.]{0,100}(?:주문시전 매개체|주문 시전 매개체)"
                    r"[^.]{0,100}사용할 수 있습니다\.",
                    rf"\g<lead>{list_ko} 주문의 주문시전 매개체로 {focus_ko}를 사용할 수 있습니다.",
                    text,
                    count=1,
                )

            if (
                "prepared spells column of the eldritch knight spellcasting table"
                in source_lower
            ):
                text = re.sub(
                    r"엘드리치 나이트\s*주문시전(?:\s*표)?\s*"
                    r"(?:준비된 주문|주문 준비 완료)\s*열(?:\s*(?:&#39;|['’])\s*열)?",
                    "엘드리치 나이트 주문시전 표의 준비된 주문 열",
                    text,
                    count=1,
                )

            # Familiar is a game entity, not the adjective "familiar".  Google
            # sometimes changes pronouns back to 소환수 even when the glossary
            # correctly handles the noun; keep one term throughout the rule.
            if "familiar" in source_lower:
                text = text.replace("소환수", "사역마")
                text = text.replace("사역마 것의", "사역마의")
                text = re.sub(r"사역마(?:의)? HP 체력이", "사역마의 HP가", text)
                text = re.sub(r"사역마(?:의)? HP(?:\s+이|이)", "사역마의 HP가", text)
                text = re.sub(r"사역마(?:의)? HP가가", "사역마의 HP가", text)
                text = re.sub(r"추가 행동\s+사용", "추가 행동을 사용", text)
                text = re.sub(r"반응 행동\s+해야", "반응 행동을 해야", text)
                text = re.sub(r"사역마\s+공격할", "사역마는 공격할", text)
                text = re.sub(r"사역마\s+거느린", "사역마를 거느린", text)
                if "(your choice) instead of a beast" in source_lower:
                    text = text.replace("(선택 사항)", "(선택)")
                if "other actions as normal" in source_lower:
                    text = text.replace(
                        "다른 행동은 일반적인 사역마와 마찬가지로 할 수 있습니다.",
                        "다른 행동은 정상적으로 할 수 있습니다.",
                    )
                if "rolls its own initiative" in source_lower:
                    text = re.sub(
                        r"(?:스스로|자신의)\s*(?:행동 순서를 정하고|이니셔티브를 굴리고|주도권을 굴리고)",
                        "스스로 주도권 굴림을 하고",
                        text,
                        count=1,
                    )

            if "more than one familiar at a time" in source_lower:
                text = re.sub(
                    r"한 번에[^.!?\n]*사역마[^.!?\n]*없습니다\.",
                    "한 번에 사역마를 한 마리만 거느릴 수 있습니다.",
                    text,
                    count=1,
                )

            # Google occasionally associates the numeral for "one use" with
            # the later HP term.  Only repair this when that exact source rule
            # is present.
            if "one of its uses" in source_lower and "0 hit points" in source_lower:
                text = re.sub(
                    r"사용 횟수\s*1(?:\s*HP)?(?:을|를)?\s*소모",
                    "사용 횟수 1회를 소모",
                    text,
                )
                text = re.sub(r"치유사의 도구\s+(?=10)", "치유사의 도구는 ", text)
                text = re.sub(r"체력이\s*0인", "HP가 0인", text)

            if "hit point maximum" in source_lower:
                text = re.sub(r"이 재주\s+얻으면", "이 재주를 얻으면", text)
                text = re.sub(
                    r"최대 HP\s*캐릭터 레벨(?:의)?\s*두 배",
                    "최대 HP가 캐릭터 레벨의 두 배",
                    text,
                )
                text = re.sub(r"캐릭터 레벨\s+오를 때마다", "캐릭터 레벨이 오를 때마다", text)
                text = re.sub(
                    r"최대 HP(?:로|이|가)?\s*2씩 증가",
                    "최대 HP가 추가로 2씩 증가",
                    text,
                )

            return normalize_translation_spacing(text)

        translated = _transform_text_outside_structure(translated, fix_visible)
        return self._canonicalize_structured_layout(source, translated)

    def _translate_prose_core(self, core, soft_fail=False, allow_fragment_fallback=True):
        """Translate natural prose while preserving D&D mechanics and terms."""
        if not core:
            return core

        normalized_core = _model_text(core)
        if not re.search(r"[A-Za-z]", normalized_core):
            return normalized_core

        stripped = normalized_core.strip()
        exact = self.glossary.get(stripped.casefold())
        if exact is not None and normalized_core == stripped:
            return exact

        composite = self._translate_glossary_composite(normalized_core)
        if composite is not None:
            return composite

        # Existing unit-test doubles use the old chat-shaped protocol. Keep
        # Roll20 placeholder behavior only for those injected test clients.
        if self._legacy_test_client:
            protected, replacements = protect_model_text(normalized_core)
        else:
            protected, replacements = normalized_core, []

        try:
            if soft_fail:
                translated = self._safe_fragment_model_translate(
                    protected,
                    placeholder_mode=bool(replacements),
                    strict_plain=True,
                    strict_terms=False,
                    warn_on_failure=False,
                )
            else:
                translated = self._model_translate(
                    protected,
                    placeholder_mode=bool(replacements),
                    strict_plain=True,
                    strict_terms=False,
                )
            if translated is None:
                if soft_fail and allow_fragment_fallback and mechanical_tokens(normalized_core):
                    fragmented, translated_prose = self._translate_fragmented(
                        normalized_core
                    )
                    fragmented = self._canonicalize_translation(
                        normalized_core, fragmented
                    )
                    if (
                        translated_prose
                        and mechanics_compatible(normalized_core, fragmented)
                    ):
                        return fragmented
                if soft_fail:
                    self._warn_original_preserved(
                        core,
                        "번역 조각 재시도 실패",
                    )
                return core
            if replacements:
                translated = restore_text(translated, replacements)
        except TranslationError as exc:
            if soft_fail:
                if allow_fragment_fallback and mechanical_tokens(normalized_core):
                    fragmented, translated_prose = self._translate_fragmented(
                        normalized_core
                    )
                    fragmented = self._canonicalize_translation(
                        normalized_core, fragmented
                    )
                    if (
                        translated_prose
                        and mechanics_compatible(normalized_core, fragmented)
                    ):
                        return fragmented
                self._warn_original_preserved(
                    core,
                    f"번역 조각 재시도 실패 ({exc})",
                )
                return core
            raise

        translated = self._canonicalize_translation(normalized_core, translated)
        if not mechanics_compatible(normalized_core, translated):
            if soft_fail:
                if allow_fragment_fallback and mechanical_tokens(normalized_core):
                    fragmented, translated_prose = self._translate_fragmented(
                        normalized_core
                    )
                    fragmented = self._canonicalize_translation(
                        normalized_core, fragmented
                    )
                    if (
                        translated_prose
                        and mechanics_compatible(normalized_core, fragmented)
                    ):
                        return fragmented
                self._warn_original_preserved(
                    core,
                    "숫자·주사위식·Roll20 수식 불일치 ("
                    + mechanics_mismatch_text(normalized_core, translated) + ")",
                )
                return core
            raise TranslationError(
                "번역 서비스가 숫자·주사위식·Roll20 수식을 변경했습니다: "
                + mechanics_mismatch_text(normalized_core, translated)
            )

        try:
            self._validate_semantic_rule_anchors(normalized_core, translated)
        except TranslationError as exc:
            if soft_fail:
                self._warn_original_preserved(core, str(exc))
                return core
            raise

        missing = self._missing_glossary_targets(normalized_core, translated)
        if missing and self._legacy_test_client:
            # Preserve legacy regression semantics for existing unit tests.
            try:
                retry = (
                    self._safe_fragment_model_translate(
                        protected,
                        placeholder_mode=bool(replacements),
                        strict_plain=True,
                        strict_terms=True,
                        warn_on_failure=False,
                    )
                    if soft_fail
                    else self._model_translate(
                        protected,
                        placeholder_mode=bool(replacements),
                        strict_plain=True,
                        strict_terms=True,
                    )
                )
                if retry is not None and replacements:
                    retry = restore_text(retry, replacements)
            except TranslationError:
                retry = None

            if retry is not None:
                retry_missing = self._missing_glossary_targets(
                    normalized_core,
                    retry,
                )
                if not retry_missing:
                    translated = retry
                    missing = []

        elif missing:
            # The official Google glossary is the primary terminology layer.
            # If a stopword/context edge case is ignored, retry only the missing
            # terms as English no-translate spans and replace them after Google
            # has translated the surrounding sentence. This keeps grammar intact.
            try:
                retry = self._google_retry_missing_terms(
                    normalized_core,
                    missing,
                )
            except TranslationError:
                retry = None

            if retry is not None:
                retry = self._canonicalize_translation(normalized_core, retry)
            if (
                retry is not None
                and mechanics_compatible(normalized_core, retry)
                and not self._missing_glossary_targets(normalized_core, retry)
            ):
                translated = retry
                missing = []

        if missing:
            self.warnings.append(
                "용어집 지정 용어가 일부 반영되지 않았지만 문장 구조 보존을 위해 "
                "번역문을 유지했습니다: "
                + ", ".join(f"{src}→{dst}" for src, dst in missing[:6])
            )

        return self._canonicalize_translation(normalized_core, translated)

    def _model_translate(
        self,
        source,
        placeholder_mode=False,
        strict_plain=False,
        strict_terms=False,
    ):
        if not self._legacy_test_client:
            return self._google_translate_batch([source])[0]

        # Compatibility path for the existing injected unit-test doubles.
        try:
            response = self._client().chat(
                messages=[
                    {"role": "system", "content": "translate to Korean"},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {"source": source},
                            ensure_ascii=False,
                        ),
                    },
                ]
            )
        except Exception as exc:
            raise TranslationError(
                f"번역 호출에 실패했습니다: {exc}"
            ) from exc

        content = self._message_field(response, "content").strip()
        if not content:
            raise TranslationError("번역 서비스가 최종 번역문을 비워서 반환했습니다.")

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            preview = content[:200].replace("\n", "\\n")
            raise TranslationError(
                f"번역 서비스가 JSON이 아닌 응답을 반환했습니다: {preview!r}"
            ) from exc

        translated = parsed.get("translation")
        if not isinstance(translated, str) or not translated.strip():
            raise TranslationError("번역 서비스가 빈 번역문을 반환했습니다.")

        translated = clean_translation_output(translated)
        if contains_unexpected_script(translated):
            raise TranslationError(
                "번역문에 허용되지 않은 문자 체계가 포함되었습니다."
            )
        return translated

    def _model_translate_batch(self, entries):
        if not entries:
            return {}

        if not self._legacy_test_client:
            values = [entry["protected"] for entry in entries]
            translated = self._google_translate_batch(values)
            return {
                entry["id"]: value
                for entry, value in zip(entries, translated)
            }

        expected_ids = {entry["id"] for entry in entries}
        try:
            response = self._client().chat(
                messages=[
                    {"role": "system", "content": "translate each item to Korean"},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "items": [
                                    {
                                        "id": entry["id"],
                                        "source": entry["protected"],
                                    }
                                    for entry in entries
                                ]
                            },
                            ensure_ascii=False,
                        ),
                    },
                ]
            )
        except Exception as exc:
            raise TranslationError(
                f"배치 번역 호출에 실패했습니다: {exc}"
            ) from exc

        content = self._message_field(response, "content").strip()
        if not content:
            raise TranslationError("번역 서비스가 배치 번역 결과를 비워서 반환했습니다.")

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise TranslationError(
                "번역 서비스가 배치 번역에서 JSON이 아닌 응답을 반환했습니다."
            ) from exc

        rows = parsed.get("translations")
        if not isinstance(rows, list):
            raise TranslationError("배치 번역 응답에 translations 배열이 없습니다.")

        by_id = {}
        for row in rows:
            if not isinstance(row, dict):
                raise TranslationError("배치 번역 응답 항목 형식이 올바르지 않습니다.")
            item_id = row.get("id")
            value = row.get("translation")
            if (
                item_id in by_id
                or item_id not in expected_ids
                or not isinstance(value, str)
                or not value.strip()
            ):
                raise TranslationError(
                    "배치 번역 응답의 id/translation이 올바르지 않습니다."
                )
            value = clean_translation_output(value)
            if contains_unexpected_script(value):
                raise TranslationError(
                    "배치 번역문에 허용되지 않은 문자 체계가 포함되었습니다."
                )
            by_id[item_id] = value

        if set(by_id) != expected_ids:
            raise TranslationError(
                "배치 번역 응답의 항목 수 또는 id가 입력과 일치하지 않습니다."
            )
        return by_id

    def _translate_batch_once(self, values):
        entries = []
        for index, value in enumerate(values):
            normalized = _model_text(value)
            if self._legacy_test_client:
                protected, replacements = protect_model_text(normalized)
            else:
                protected, replacements = normalized, []
            entries.append(
                {
                    "id": index,
                    "source": value,
                    "validation_source": normalized,
                    "protected": protected,
                    "replacements": replacements,
                }
            )

        raw = self._model_translate_batch(entries)
        resolved = {}
        failed = []

        for entry in entries:
            candidate = raw[entry["id"]]
            try:
                if entry["replacements"]:
                    candidate = restore_text(
                        candidate,
                        entry["replacements"],
                    )

                candidate = self._canonicalize_translation(
                    entry["source"], candidate
                )

                if contains_unexpected_script(candidate):
                    raise TranslationError(
                        "배치 번역문에 허용되지 않은 문자 체계가 포함되었습니다."
                    )

                if structure_tokens(entry["source"]) != structure_tokens(candidate):
                    raise TranslationError(
                        "배치 번역 중 HTML/D&D 태그의 순서가 바뀌었습니다."
                    )

                if self._missing_glossary_targets(
                    entry["source"],
                    candidate,
                ):
                    raise TranslationError(
                        "배치 번역에서 용어집 지정 용어가 누락되었습니다."
                    )

                if not mechanics_compatible(
                    entry["validation_source"], candidate
                ):
                    raise TranslationError(
                        "배치 번역 중 숫자·주사위식·Roll20 수식이 달라졌습니다."
                    )

                self._validate_semantic_rule_anchors(entry["source"], candidate)
                resolved[entry["source"]] = candidate
            except TranslationError:
                failed.append(entry["source"])

        return resolved, failed

    def _translate_batch_resilient(self, values):
        """Batch first; split failures recursively; single items use safe path."""
        values = list(values)
        if not values:
            return {}

        if len(values) == 1:
            value = values[0]
            try:
                return {value: self.translate(value)}
            except TranslationError as exc:
                # One malformed/empty translation response must not discard an
                # otherwise-complete character translation. Network/runtime
                # failures still propagate because they are not soft errors.
                if not self._is_soft_model_error(exc):
                    raise

                # Retry at plain-fragment granularity. This path already
                # preserves mechanics/glossary terms and, after two repeated
                # empty/malformed replies, keeps only this value in English
                # while recording a warning.
                try:
                    if STRUCTURE_PATTERN.search(value):
                        translated = self._translate_structured(value)
                    else:
                        translated = self._translate_plain_segment(value)
                except TranslationError as fallback_exc:
                    if not self._is_soft_model_error(fallback_exc):
                        raise

                    self._warn_original_preserved(
                        value,
                        "단일 항목 재시도에서도 번역 출력 형식이 불안정함: "
                        + str(fallback_exc),
                    )
                    translated = value

                if not isinstance(translated, str) or not translated.strip():
                    self._warn_original_preserved(
                        value,
                        "단일 항목 fallback 결과가 비어 있음",
                    )
                    translated = value

                # Final mechanical safety check. If fallback somehow changes a
                # number/die/tag, keep the original rather than corrupt data.
                if (
                    structure_tokens(value) != structure_tokens(translated)
                    or not mechanics_compatible(value, translated)
                    or self._missing_semantic_rule_anchors(value, translated)
                ):
                    self._warn_original_preserved(
                        value,
                        "단일 항목 fallback에서 구조/기계적 값/규칙 의미 불일치",
                    )
                    translated = value

                # Cache only a real translated value.  If the safe fallback
                # had to preserve the English source, leave it uncached so a
                # later run can retry Google Translation.
                self._remember_translation(value, translated)
                return {value: translated}

        try:
            resolved, failed = self._translate_batch_once(values)
        except Exception as exc:
            message = str(exc).casefold()

            # Split only content-level batch failures. Authentication, quota,
            # permission and network failures must stop immediately so the API
            # is not called repeatedly for the same payload.
            connectivity_markers = (
                "connection refused",
                "failed to connect",
                "connection error",
                "connecterror",
                "timed out",
                "timeout",
                "google cloud translation 호출에 실패",
                "google cloud 인증",
                "권한이 없습니다",
                "할당량",
                "프로젝트 id",
                "라이브러리가 없습니다",
            )
            if any(marker in message for marker in connectivity_markers):
                raise

            middle = len(values) // 2
            left = self._translate_batch_resilient(values[:middle])
            right = self._translate_batch_resilient(values[middle:])
            left.update(right)
            return left

        for source, translated in resolved.items():
            if translated != source:
                self.cache[source] = translated
            else:
                self.cache.pop(source, None)

        if failed:
            retry = self._translate_batch_resilient(failed)
            resolved.update(retry)

        return resolved

    @staticmethod
    def _make_batches(values):
        """Bound batches and isolate HTML/D&D structured documents."""
        batches = []
        current = []
        current_chars = 0

        def flush():
            nonlocal current, current_chars
            if current:
                batches.append(current)
                current = []
                current_chars = 0

        for value in values:
            if STRUCTURE_PATTERN.search(value):
                flush()
                batches.append([value])
                continue

            size = len(value)
            if current and (
                len(current) >= BATCH_MAX_ITEMS
                or current_chars + size > BATCH_MAX_CHARS
            ):
                flush()

            current.append(value)
            current_chars += size

            if (
                len(current) >= BATCH_MAX_ITEMS
                or current_chars >= BATCH_MAX_CHARS
            ):
                flush()

        flush()
        return batches

    def _translate_plain_segment(self, segment):
        """Translate one structured-document text node naturally."""
        if not segment or not segment.strip():
            return segment

        match = re.fullmatch(r"(\s*)(.*?)(\s*)", segment, re.S)
        if not match:
            return segment

        leading, core, trailing = match.groups()
        if not core or not re.search(r"[A-Za-z]", core):
            return segment

        translated_core = self._translate_prose_core(
            core,
            soft_fail=True,
        )

        if not mechanics_compatible(core, translated_core):
            self._warn_original_preserved(
                core,
                "숫자·주사위식·Roll20 수식 불일치",
            )
            translated_core = core

        return leading + translated_core + trailing

    def _translate_text_nodes(self, nodes):
        """Batch unique text nodes while tags stay outside the model."""
        result = {}
        unresolved = []
        seen = set()

        for node in nodes:
            if node in seen:
                continue
            seen.add(node)

            normalized_node = _model_text(node)
            if not normalized_node.strip() or not re.search(
                r"[A-Za-z]", normalized_node
            ):
                result[node] = normalized_node
                continue

            match = re.fullmatch(
                r"(\s*)(.*?)(\s*)", normalized_node, re.S
            )
            if not match:
                result[node] = normalized_node
                continue

            leading, core, trailing = match.groups()
            glossary_value = self.glossary.get(core.strip().casefold())
            composite = self._translate_glossary_composite(core)

            if glossary_value is not None and core == core.strip():
                result[node] = leading + glossary_value + trailing
            elif composite is not None:
                result[node] = leading + composite + trailing
            else:
                unresolved.append(node)

        for batch in self._make_batches(unresolved):
            if len(batch) == 1:
                node = batch[0]
                result[node] = self._translate_plain_segment(node)
                continue

            try:
                resolved, failed = self._translate_batch_once(batch)
                result.update(resolved)

                for node in failed:
                    result[node] = self._translate_plain_segment(node)
            except Exception as exc:
                if (
                    isinstance(exc, TranslationError)
                    and not self._is_soft_model_error(exc)
                ):
                    raise

                # Invalid/empty batch response: keep splitting at text-node
                # granularity. A bad node cannot terminate the whole document.
                for node in batch:
                    result[node] = self._translate_plain_segment(node)

        return result

    @staticmethod
    def _structured_chunks(value):
        """Split structured HTML into sentence-sized semantic blocks.

        Paragraphs/list items/table cells stay intact, so Google keeps sentence
        context, while very large class/spell descriptions are no longer one
        enormous markup payload. Inline D&D tags stay in the same paragraph
        and are represented as ordinary HTML spans for the Google request.
        """
        chunks = []
        cursor = 0
        for match in STRUCTURED_BLOCK_END_PATTERN.finditer(value):
            end = match.end()
            if end > cursor:
                chunks.append(value[cursor:end])
            cursor = end
        if cursor < len(value):
            chunks.append(value[cursor:])
        return chunks or [value]

    @staticmethod
    def _has_visible_english(value):
        visible = STRUCTURE_PATTERN.sub(" ", html.unescape(value or ""))
        return re.search(r"[A-Za-z]", visible) is not None

    @staticmethod
    def _visible_text_spans(value):
        spans = []
        cursor = 0
        for match in STRUCTURE_PATTERN.finditer(value):
            spans.append((cursor, match.start()))
            cursor = match.end()
        spans.append((cursor, len(value)))
        return spans

    def _restore_explicitly_preserved_structured_fragments(
        self, source, raw_translated, candidate
    ):
        """Do not let post-processing disguise an explicit source fallback.

        A soft failure is intentionally visible in the returned document so the
        caller can see which fragment still needs a retry.  Exact-label and
        source-aware cleanup runs after translation; without this guard it can
        translate that preserved English fragment locally and make a partial
        result look complete.  Restore only aligned text slots that (1) were
        explicitly recorded as preserved and (2) are still byte-for-byte
        equivalent to the source at the visible-text level before cleanup.
        """
        if not self._preserved_fragment_keys:
            return candidate
        if (
            structure_tokens(source) != structure_tokens(raw_translated)
            or structure_tokens(source) != structure_tokens(candidate)
        ):
            return candidate

        source_spans = self._visible_text_spans(source)
        raw_spans = self._visible_text_spans(raw_translated)
        candidate_spans = self._visible_text_spans(candidate)
        if not (
            len(source_spans) == len(raw_spans) == len(candidate_spans)
        ):
            return candidate

        replacements = []
        for (s0, s1), (r0, r1), (c0, c1) in zip(
            source_spans, raw_spans, candidate_spans
        ):
            source_piece = source[s0:s1]
            raw_piece = raw_translated[r0:r1]
            source_key = self._preserved_fragment_key(source_piece)
            if not source_key or source_key not in self._preserved_fragment_keys:
                continue
            if self._preserved_fragment_key(raw_piece) != source_key:
                continue
            replacements.append((c0, c1, raw_piece))

        result = candidate
        for start, end, replacement in reversed(replacements):
            result = result[:start] + replacement + result[end:]
        return result

    def _canonicalize_structured_safely(self, source, translated):
        """Apply post-processing without letting it corrupt valid mechanics.

        Translation and canonicalization are separate trust boundaries.  The
        Cloud response may already have passed structure/mechanics validation,
        while a later source-aware cleanup rule can still be too broad.  v11's
        Find Familiar regression was exactly that: a cleanup regex replaced the
        wrong paragraph after every translated chunk had been validated.

        If post-processing would change tags or rule values, keep the valid
        pre-canonicalized translation and record a diagnostic warning instead
        of escalating the whole description to an English fallback.
        """
        if not isinstance(translated, str):
            return translated

        raw_structure_ok = structure_tokens(source) == structure_tokens(translated)
        raw_mechanics_ok = mechanics_compatible(source, translated)
        candidate = self._canonicalize_translation(source, translated)
        candidate = self._restore_explicitly_preserved_structured_fragments(
            source, translated, candidate
        )
        candidate_structure_ok = (
            structure_tokens(source) == structure_tokens(candidate)
        )
        candidate_mechanics_ok = mechanics_compatible(source, candidate)
        raw_semantics_ok = not self._missing_semantic_rule_anchors(
            source, translated
        )
        candidate_semantics_ok = not self._missing_semantic_rule_anchors(
            source, candidate
        )

        if (
            candidate_structure_ok
            and candidate_mechanics_ok
            and candidate_semantics_ok
        ):
            return candidate

        if raw_structure_ok and raw_mechanics_ok and raw_semantics_ok:
            detail = []
            if not candidate_structure_ok:
                detail.append("HTML/D&D 태그")
            if not candidate_mechanics_ok:
                detail.append(
                    "기계적 값(" + mechanics_mismatch_text(source, candidate) + ")"
                )
            if not candidate_semantics_ok:
                detail.append(
                    "규칙 의미 앵커("
                    + ", ".join(
                        self._missing_semantic_rule_anchors(source, candidate)
                    )
                    + ")"
                )
            warning = (
                "번역 후처리 보정이 안전한 번역의 "
                + " 및 ".join(detail)
                + "을 변경하려 해 해당 보정만 건너뛰었습니다."
            )
            if warning not in self.warnings:
                self.warnings.append(warning)
            return translated

        return candidate

    def _translate_structured_text_nodes(self, value):
        """Last-resort structured fallback that never moves original tags."""
        pieces = []
        text_nodes = []
        cursor = 0

        for match in STRUCTURE_PATTERN.finditer(value):
            if match.start() > cursor:
                text = value[cursor:match.start()]
                pieces.append(("text", text))
                text_nodes.append(text)
            pieces.append(("structure", match.group(0)))
            cursor = match.end()

        if cursor < len(value):
            text = value[cursor:]
            pieces.append(("text", text))
            text_nodes.append(text)

        translated_nodes = self._translate_text_nodes(text_nodes)
        translated = "".join(
            translated_nodes.get(piece, piece) if kind == "text" else piece
            for kind, piece in pieces
        )
        translated = self._canonicalize_structured_safely(value, translated)

        if structure_tokens(value) != structure_tokens(translated):
            raise TranslationError(
                "구조화 fallback 중 HTML/D&D 태그의 순서가 바뀌었습니다: "
                f"{value[:80]}"
            )
        if not mechanics_compatible(value, translated):
            raise TranslationError(
                "구조화 fallback 중 숫자·주사위식·Roll20 수식이 바뀌었습니다 ("
                + mechanics_mismatch_text(value, translated)
                + f"): {value[:80]}"
            )
        return translated

    def _translate_structured_chunk(self, chunk):
        if not self._has_visible_english(chunk):
            return chunk

        try:
            translated = self._google_translate_batch([chunk])[0]
            return self._validate_structured_candidate(chunk, translated)
        except TranslationError as exc:
            if not self._is_soft_model_error(exc):
                raise
            # Marker/format instability should affect at most this block, not
            # the entire item.  Text-node fallback preserves every source tag
            # exactly and still translates the surrounding visible text.
            return self._translate_structured_text_nodes(chunk)

    def _validate_structured_candidate(self, source, translated):
        translated = self._canonicalize_structured_safely(source, translated)
        if structure_tokens(source) != structure_tokens(translated):
            raise TranslationError(
                "구조화 번역 중 HTML/D&D 태그의 순서가 바뀌었습니다: "
                f"{source[:80]}"
            )
        if not mechanics_compatible(source, translated):
            raise TranslationError(
                "구조화 번역 중 숫자·주사위식·Roll20 수식이 바뀌었습니다 ("
                + mechanics_mismatch_text(source, translated)
                + f"): {source[:80]}"
            )
        self._validate_semantic_rule_anchors(source, translated)
        return translated

    @staticmethod
    def _structured_chunk_batches(indexed_chunks):
        batches = []
        current = []
        current_chars = 0
        for item in indexed_chunks:
            size = len(item[1])
            if current and (
                len(current) >= BATCH_MAX_ITEMS
                or current_chars + size > BATCH_MAX_CHARS
            ):
                batches.append(current)
                current = []
                current_chars = 0
            current.append(item)
            current_chars += size
        if current:
            batches.append(current)
        return batches

    def _translate_structured(self, value):
        """Translate structured text without allowing Google to own the markup."""
        if self._legacy_test_client:
            # Legacy/injected test clients use the text-node path directly.
            # Keep the same partial-result cache semantics as the live Google
            # path: if any node had to preserve the English source, the whole
            # structured value is useful output but is not a successful cache
            # entry and must be retried next time.
            preserved_before = len(self.original_preserved)
            translated = self._translate_structured_text_nodes(value)
            if len(self.original_preserved) > preserved_before:
                self._uncacheable_sources.add(value)
            return translated

        preserved_before = len(self.original_preserved)

        chunks = self._structured_chunks(value)
        translated_chunks = list(chunks)
        indexed = [
            (index, chunk)
            for index, chunk in enumerate(chunks)
            if self._has_visible_english(chunk)
        ]

        for batch in self._structured_chunk_batches(indexed):
            sources = [chunk for _index, chunk in batch]
            try:
                candidates = self._google_translate_batch(sources)
            except TranslationError as exc:
                if not self._is_soft_model_error(exc):
                    raise
                for index, chunk in batch:
                    translated_chunks[index] = self._translate_structured_chunk(chunk)
                continue

            for (index, chunk), candidate in zip(batch, candidates):
                try:
                    translated_chunks[index] = self._validate_structured_candidate(
                        chunk,
                        candidate,
                    )
                except TranslationError as exc:
                    if not self._is_soft_model_error(exc):
                        raise
                    translated_chunks[index] = self._translate_structured_text_nodes(
                        chunk
                    )

        translated = "".join(translated_chunks)
        translated = self._canonicalize_structured_safely(value, translated)
        if structure_tokens(value) != structure_tokens(translated):
            raise TranslationError(
                "구조화 번역 중 HTML/D&D 태그의 순서가 바뀌었습니다: "
                f"{value[:80]}"
            )
        if not mechanics_compatible(value, translated):
            raise TranslationError(
                "구조화 번역 중 숫자·주사위식·Roll20 수식이 바뀌었습니다 ("
                + mechanics_mismatch_text(value, translated)
                + f"): {value[:80]}"
            )

        if len(self.original_preserved) > preserved_before:
            self._uncacheable_sources.add(value)
        return translated

    def _translate_fragmented(self, value):
        """Translate prose around mechanical atoms and report real progress.

        A damaged Cloud span should not force the entire rule back to English,
        but merely localizing ``30 feet`` to ``30피트`` is not enough to call
        the fallback successful.  ``translated_prose`` lets the caller reject
        a fallback where every surrounding English fragment stayed unchanged.
        """
        parts = []
        cursor = 0
        translated_prose = False

        def translate_plain(plain):
            nonlocal translated_prose
            stripped = re.fullmatch(r"(\s*)(.*?)(\s*)", plain, re.S)
            if not stripped:
                return plain
            leading, core, trailing = stripped.groups()
            translated_core = self._translate_prose_core(
                core,
                soft_fail=True,
                allow_fragment_fallback=False,
            )
            if (
                core.strip()
                and re.search(r"[A-Za-z]", core)
                and translated_core.strip().casefold() != core.strip().casefold()
            ):
                translated_prose = True
            return leading + translated_core + trailing

        for match in GOOGLE_MECHANICAL_LOCK_PATTERN.finditer(value):
            if match.start() > cursor:
                parts.append(translate_plain(value[cursor:match.start()]))
            parts.append(self._localize_mechanical_lock(match.group(0)))
            cursor = match.end()

        if cursor < len(value):
            parts.append(translate_plain(value[cursor:]))

        return "".join(parts), translated_prose

    def translate(self, value):
        if not isinstance(value, str) or not value.strip():
            return value

        if value in self.cache:
            return self.cache[value]

        translated = self.glossary.get(value.strip().casefold())

        if translated is None:
            composite = self._translate_glossary_composite(value)
            if composite is not None:
                translated = composite
            elif STRUCTURE_PATTERN.search(value):
                translated = self._translate_structured(value)
            else:
                translated = self._translate_prose_core(
                    value,
                    soft_fail=False,
                )

        if not isinstance(translated, str) or not translated.strip():
            raise TranslationError(
                "모델이 빈 번역문을 반환했습니다."
            )

        if contains_unexpected_script(translated):
            raise TranslationError(
                "최종 번역문에 허용되지 않은 문자 체계가 포함되었습니다."
            )

        if structure_tokens(value) != structure_tokens(translated):
            raise TranslationError(
                "번역 중 HTML/D&D 태그가 바뀌어 중단했습니다: "
                f"{value[:80]}"
            )
        if not mechanics_compatible(value, translated):
            raise TranslationError(
                "번역 중 숫자·주사위식·Roll20 수식이 바뀌어 중단했습니다 ("
                + mechanics_mismatch_text(value, translated)
                + f"): {value[:80]}"
            )
        self._validate_semantic_rule_anchors(value, translated)

        self._remember_translation(value, translated)
        return translated

    @staticmethod
    def _bilingual_name(translated, original):
        """Display translated sheet names together with their D&D Beyond original.

        Character identity is handled separately and is intentionally never passed
        through this helper. If translation failed and the two values are equal,
        do not produce redundant text such as "Thunderwave (Thunderwave)".
        """
        if not isinstance(translated, str) or not translated.strip():
            return translated
        if not isinstance(original, str) or not original.strip():
            return translated

        translated = translated.strip()
        original = original.strip()

        if translated.casefold() == original.casefold():
            return translated

        suffix = f" ({original})"
        if translated.endswith(suffix):
            return translated

        return translated + suffix

    def _apply_bilingual_names(self, result):
        """Append original English names to translated display-name fields only."""
        race = result.get("race")
        if isinstance(race, dict):
            race["name"] = self._bilingual_name(
                race.get("name"),
                race.get("original_name"),
            )

        background = result.get("background")
        if isinstance(background, dict):
            background["name"] = self._bilingual_name(
                background.get("name"),
                background.get("original_name"),
            )
            background["feature_name"] = self._bilingual_name(
                background.get("feature_name"),
                background.get("original_feature_name"),
            )

        for character_class in result.get("classes", []):
            if not isinstance(character_class, dict):
                continue
            character_class["name"] = self._bilingual_name(
                character_class.get("name"),
                character_class.get("original_name"),
            )
            character_class["subclass_name"] = self._bilingual_name(
                character_class.get("subclass_name"),
                character_class.get("original_subclass_name"),
            )

        for category in (
            "equipment",
            "spells",
            "features",
            "actions",
            "resources",
        ):
            for item in result.get(category, []):
                if not isinstance(item, dict):
                    continue
                item["name"] = self._bilingual_name(
                    item.get("name"),
                    item.get("original_name"),
                )

    def translate_character(self, source, on_progress=None):
        self._ensure_remote_glossary()
        result = copy.deepcopy(source)
        targets = []

        def add(container, *keys):
            if not isinstance(container, dict):
                return
            for key in keys:
                if isinstance(container.get(key), str) and container[key].strip():
                    targets.append((container, key))

        # Character name is the Roll20 matching key and must never be translated.
        add(result.get("race"), "name", "base_name", "subrace_name", "description")
        add(
            result.get("background"),
            "name",
            "description",
            "feature_name",
            "feature_description",
        )

        for character_class in result.get("classes", []):
            add(character_class, "name", "subclass_name")

        for category in (
            "equipment",
            "spells",
            "features",
            "actions",
            "resources",
        ):
            for item in result.get(category, []):
                add(item, *TRANSLATABLE_FIELDS)

                if category == "equipment":
                    for prop in item.get("properties", []):
                        if not isinstance(prop, dict):
                            continue
                        if (
                            isinstance(prop.get("name"), str)
                            and prop.get("name")
                            and not prop.get("original_name")
                        ):
                            prop["original_name"] = prop["name"]

                        original_prop_name = prop.get("original_name") or prop.get("name")
                        mapped_property = (
                            WEAPON_PROPERTY_NAME_MAP.get(
                                original_prop_name.strip().casefold()
                            )
                            if isinstance(original_prop_name, str)
                            else None
                        )
                        if mapped_property:
                            prop["name"] = mapped_property
                            add(prop, "description", "notes")
                        else:
                            add(prop, "name", "description", "notes")

        for index, text in enumerate(result.get("proficiencies", [])):
            if isinstance(text, str) and text.strip():
                targets.append((result["proficiencies"], index))

        for index, text in enumerate(result.get("languages", [])):
            if isinstance(text, str) and text.strip():
                targets.append((result["languages"], index))

        for index, text in enumerate(
            result.get("saving_throw_proficiencies", [])
        ):
            if isinstance(text, str) and text.strip():
                targets.append((result["saving_throw_proficiencies"], index))

        for skill in result.get("skill_proficiencies", []):
            if not isinstance(skill, dict):
                continue
            if (
                isinstance(skill.get("name"), str)
                and skill.get("name")
                and not skill.get("original_name")
            ):
                skill["original_name"] = skill["name"]
            add(skill, "name")

        total = len(targets)
        completed = 0

        # One source string can occur in several places. Translate it once and
        # write the result to every occurrence.
        grouped = {}
        for container, key in targets:
            source_value = container[key]
            grouped.setdefault(source_value, []).append((container, key))

        def apply_translation(source_value, translated_value):
            nonlocal completed
            locations = grouped[source_value]
            for container, key in locations:
                container[key] = translated_value
            completed += len(locations)
            if on_progress:
                on_progress(completed, total)

        unresolved = []

        # Resolve cache/glossary hits without calling the model.
        for source_value in grouped:
            if (
                source_value in self.cache
                or source_value.strip().casefold() in self.glossary
            ):
                try:
                    translated_value = self.translate(source_value)
                except Exception as exc:
                    try:
                        exc.partial_translated = copy.deepcopy(result)
                        exc.translation_fingerprint = self._cache_fingerprint
                        exc.failed_text = source_value
                        exc.progress_index = completed + 1
                        exc.progress_total = total
                    except Exception:
                        pass
                    raise
                apply_translation(source_value, translated_value)
            else:
                unresolved.append(source_value)

        for batch in self._make_batches(unresolved):
            try:
                translated_batch = self._translate_batch_resilient(batch)
            except Exception as exc:
                try:
                    exc.partial_translated = copy.deepcopy(result)
                    exc.translation_fingerprint = self._cache_fingerprint
                    exc.failed_text = getattr(
                        exc,
                        "failed_text",
                        batch[0] if batch else "",
                    )
                    exc.progress_index = completed + 1
                    exc.progress_total = total
                except Exception:
                    pass
                raise

            # Checkpoint every successful batch so a later API/network failure does
            # not force the entire character to be translated again.
            for source_value in batch:
                self._remember_translation(
                    source_value,
                    translated_batch[source_value],
                )

            # Apply in original order for predictable progress reporting.
            for source_value in batch:
                apply_translation(
                    source_value,
                    translated_batch[source_value],
                )

        # Names shown to the user/Roll20 keep the Korean translation and the
        # exact D&D Beyond original side by side. The character's own name is
        # deliberately untouched because it is the Roll20 matching key.
        self._apply_bilingual_names(result)

        if self.warnings:
            result.setdefault("warnings", [])
            for warning in self.warnings:
                if warning not in result["warnings"]:
                    result["warnings"].append(warning)

        result["translation_summary"] = self.translation_summary()
        return result

