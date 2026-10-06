"""Preprocess structured D&D text without sending markup to the translator."""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Callable, Iterable

PREPROCESSOR_VERSION = "2026-09-21-plain-semantic-html-v16"

HTML_TAG_RE = re.compile(r"<[^>]*>", re.S)
DND_TAG_PAIR_RE = re.compile(
    r"\[(?P<tag>[A-Za-z][A-Za-z0-9_-]*)\](?P<body>.*?)\[/(?P=tag)\]",
    re.I | re.S,
)


class SemanticPreprocessError(ValueError):
    pass


@dataclass(frozen=True)
class DndAtom:
    tag: str
    source_body: str
    target_body: str


@dataclass(frozen=True)
class TextSlot:
    raw: str
    leading: str
    core: str
    trailing: str
    plain_core: str
    atoms: tuple[DndAtom, ...]


@dataclass(frozen=True)
class StructuredTemplate:
    parts: tuple[tuple[str, str | int], ...]
    slots: tuple[TextSlot, ...]


def _flatten_dnd_tags(text: str, resolver: Callable[[str, str], str | None]) -> tuple[str, tuple[DndAtom, ...]]:
    atoms: list[DndAtom] = []

    def repl(match: re.Match) -> str:
        tag = match.group("tag").casefold()
        body = html.unescape(match.group("body")).strip()
        target = resolver(tag, body)
        if not target:
            raise SemanticPreprocessError(
                f"D&D 태그 [{tag}]의 고정 번역을 결정할 수 없습니다: {body}"
            )
        atoms.append(DndAtom(tag=tag, source_body=body, target_body=target))
        return body

    flattened = DND_TAG_PAIR_RE.sub(repl, text)
    # HTML entities belong to visible prose, not to the structural template.
    flattened = html.unescape(flattened).replace("\xa0", " ")
    return flattened, tuple(atoms)


def build_template(value: str, resolver: Callable[[str, str], str | None]) -> StructuredTemplate:
    """Split only real HTML tags; D&D semantic tags stay in surrounding prose.

    This is deliberate: ``except the [action]Magic[/action] action`` reaches the
    translator as one natural sentence ``except the Magic action`` rather than
    three unrelated text nodes.
    """
    if not isinstance(value, str):
        raise SemanticPreprocessError("구조화 번역 입력이 문자열이 아닙니다.")

    parts: list[tuple[str, str | int]] = []
    slots: list[TextSlot] = []
    cursor = 0

    def add_text(raw: str):
        if raw == "":
            return
        match = re.fullmatch(r"(\s*)(.*?)(\s*)", raw, re.S)
        if not match:
            raise SemanticPreprocessError("구조화 텍스트 공백 경계를 분석하지 못했습니다.")
        leading, core, trailing = match.groups()
        plain, atoms = _flatten_dnd_tags(core, resolver)
        index = len(slots)
        slots.append(
            TextSlot(
                raw=raw,
                leading=leading,
                core=core,
                trailing=trailing,
                plain_core=plain,
                atoms=atoms,
            )
        )
        parts.append(("slot", index))

    for match in HTML_TAG_RE.finditer(value):
        add_text(value[cursor:match.start()])
        parts.append(("html", match.group(0)))
        cursor = match.end()
    add_text(value[cursor:])

    return StructuredTemplate(parts=tuple(parts), slots=tuple(slots))


def restore_dnd_tags(translated: str, atoms: Iterable[DndAtom]) -> str:
    atoms = tuple(atoms)
    if not atoms:
        return translated

    grouped: dict[str, list[DndAtom]] = {}
    for atom in atoms:
        grouped.setdefault(atom.target_body, []).append(atom)

    replacements: list[tuple[int, int, str]] = []
    occupied: list[tuple[int, int]] = []
    for target, rows in grouped.items():
        signatures = {(row.tag, row.target_body) for row in rows}
        if len(signatures) != 1:
            raise SemanticPreprocessError(
                f"동일 번역어 '{target}'에 서로 다른 D&D 태그가 겹칩니다."
            )
        matches = list(re.finditer(re.escape(target), translated))
        if len(matches) != len(rows):
            raise SemanticPreprocessError(
                f"D&D 태그 복원 위치가 모호합니다: '{target}' 필요 {len(rows)}회, 발견 {len(matches)}회"
            )
        tag = rows[0].tag
        for match in matches:
            span = match.span()
            if any(not (span[1] <= a or span[0] >= b) for a, b in occupied):
                raise SemanticPreprocessError("D&D 태그 복원 구간이 서로 겹칩니다.")
            occupied.append(span)
            replacements.append(
                (span[0], span[1], f"[{tag}]{match.group(0)}[/{tag}]")
            )

    result = translated
    for start, end, replacement in sorted(replacements, reverse=True):
        result = result[:start] + replacement + result[end:]
    return result


def render_template(template: StructuredTemplate, translated_slots: dict[int, str]) -> str:
    pieces: list[str] = []
    for kind, payload in template.parts:
        if kind == "html":
            pieces.append(str(payload))
            continue
        index = int(payload)
        slot = template.slots[index]
        core = translated_slots.get(index, slot.core)
        pieces.append(slot.leading + core + slot.trailing)
    return "".join(pieces)
