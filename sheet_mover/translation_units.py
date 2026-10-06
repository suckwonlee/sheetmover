"""Semantic translation-unit metadata for normalized D&D Beyond sheet data.

The normalized sheet already knows *what* a string is (class feature, racial
feature, spell, skill proficiency, ...).  Keep that information out of the
Google translation text itself and pass it through the pipeline as metadata.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

TRANSLATION_UNIT_VERSION = "2026-09-21-semantic-units-v16"


@dataclass(frozen=True)
class TranslationUnit:
    category: str
    kind: str
    item_name: str
    field: str
    source: str
    source_id: str = ""
    policy: str = "prose"

    def prompt_dict(self) -> dict[str, str]:
        payload = {
            "category": self.category,
            "field": self.field,
            "policy": self.policy,
        }
        if self.kind:
            payload["kind"] = self.kind
        if self.item_name:
            payload["item_name"] = self.item_name
        if self.source_id:
            payload["source_id"] = self.source_id
        return payload


# High-confidence fixed labels.  These are not prose and should not consume an
# NMT/LLM call when the source is exactly one of these labels.
FIXED_DND_LABELS = {
    # Skills
    "acrobatics": "곡예",
    "animal handling": "동물 조련",
    "arcana": "비전학",
    "athletics": "운동",
    "deception": "기만",
    "history": "역사",
    "insight": "통찰",
    "intimidation": "위협",
    "investigation": "조사",
    "medicine": "의학",
    "nature": "자연",
    "perception": "지각",
    "performance": "공연",
    "persuasion": "설득",
    "religion": "종교학",
    "sleight of hand": "손재주",
    "stealth": "은신",
    "survival": "생존",
    # Saving throws / abilities
    "strength": "근력",
    "dexterity": "민첩",
    "constitution": "건강",
    "intelligence": "지능",
    "wisdom": "지혜",
    "charisma": "매력",
    "strength saving throws": "근력 내성 굴림",
    "dexterity saving throws": "민첩 내성 굴림",
    "constitution saving throws": "건강 내성 굴림",
    "intelligence saving throws": "지능 내성 굴림",
    "wisdom saving throws": "지혜 내성 굴림",
    "charisma saving throws": "매력 내성 굴림",
    # Common languages
    "common": "공용어",
    "common sign language": "공용 수어",
    "dwarvish": "드워프어",
    "elvish": "엘프어",
    "giant": "거인어",
    "gnomish": "노움어",
    "goblin": "고블린어",
    "halfling": "하플링어",
    "orc": "오크어",
    "abyssal": "심연어",
    "celestial": "천상어",
    "draconic": "용언",
    "deep speech": "심층어",
    "infernal": "지옥어",
    "primordial": "원시어",
    "sylvan": "실반어",
    "undercommon": "지하 공용어",
}


def fixed_label_translation(value: str) -> str | None:
    if not isinstance(value, str):
        return None
    return FIXED_DND_LABELS.get(value.strip().casefold())


def _policy_for_field(field: str, *, fixed: bool = False) -> str:
    if fixed:
        return "fixed_label"
    if field == "name" or field.endswith("_name") or field in {"base_name", "subrace_name"}:
        return "name"
    if field == "components_description":
        return "components"
    if "description" in field:
        return "rules"
    return "prose"


def _source_id(item: dict) -> str:
    for key in ("source_id", "definition_id", "id", "component_id"):
        value = item.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _item_name(item: dict) -> str:
    value = item.get("original_name") or item.get("name") or ""
    return str(value) if value is not None else ""


def collect_translation_units(character: dict) -> list[TranslationUnit]:
    """Collect the exact strings translated by ``Translator.translate_character``.

    Classification uses normalized JSON location/kind, never guesses from prose
    or HTML contents.  This lets the translation layer know that the same text
    is a class feature, racial feature, action, spell, proficiency label, etc.
    """
    if not isinstance(character, dict):
        return []

    units: list[TranslationUnit] = []

    def add(
        container,
        field: str,
        category: str,
        *,
        kind: str = "",
        item_name: str = "",
        source_id: str = "",
        fixed: bool = False,
    ):
        if not isinstance(container, dict):
            return
        value = container.get(field)
        if not isinstance(value, str) or not value.strip():
            return
        units.append(
            TranslationUnit(
                category=category,
                kind=kind,
                item_name=item_name,
                field=field,
                source=value,
                source_id=source_id,
                policy=_policy_for_field(field, fixed=fixed),
            )
        )

    race = character.get("race")
    if isinstance(race, dict):
        name = _item_name(race)
        sid = _source_id(race)
        for field in ("name", "base_name", "subrace_name", "description"):
            add(race, field, "race", item_name=name, source_id=sid)

    background = character.get("background")
    if isinstance(background, dict):
        name = _item_name(background)
        sid = _source_id(background)
        for field in ("name", "description", "feature_name", "feature_description"):
            add(background, field, "background", item_name=name, source_id=sid)

    for item in character.get("classes", []) or []:
        if not isinstance(item, dict):
            continue
        name = _item_name(item)
        sid = _source_id(item)
        for field in ("name", "subclass_name"):
            add(item, field, "class", item_name=name, source_id=sid)

    for category in ("equipment", "spells", "features", "actions", "resources"):
        for item in character.get(category, []) or []:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind") or "")
            if category == "features":
                mapped = {
                    "class_feature": "class_feature",
                    "racial_trait": "racial_feature",
                    "feat": "feat",
                    "background": "background_feature",
                }.get(kind, "feature")
                unit_category = mapped
            elif category == "actions":
                unit_category = kind or "action"
            else:
                unit_category = category[:-1] if category.endswith("s") else category
            name = _item_name(item)
            sid = _source_id(item)
            for field in ("name", "description", "components_description"):
                add(
                    item,
                    field,
                    unit_category,
                    kind=kind,
                    item_name=name,
                    source_id=sid,
                )

            if category == "equipment":
                for prop in item.get("properties", []) or []:
                    if not isinstance(prop, dict):
                        continue
                    prop_name = _item_name(prop)
                    prop_sid = _source_id(prop) or sid
                    add(
                        prop,
                        "name",
                        "weapon_property",
                        kind=kind,
                        item_name=prop_name,
                        source_id=prop_sid,
                        fixed=True,
                    )
                    for field in ("description", "notes"):
                        add(
                            prop,
                            field,
                            "weapon_property",
                            kind=kind,
                            item_name=prop_name,
                            source_id=prop_sid,
                        )

    for value in character.get("proficiencies", []) or []:
        if isinstance(value, str) and value.strip():
            units.append(
                TranslationUnit(
                    category="proficiency",
                    kind="",
                    item_name=value,
                    field="value",
                    source=value,
                    policy="fixed_label",
                )
            )

    for value in character.get("languages", []) or []:
        if isinstance(value, str) and value.strip():
            units.append(
                TranslationUnit(
                    category="language",
                    kind="",
                    item_name=value,
                    field="value",
                    source=value,
                    policy="fixed_label",
                )
            )

    for value in character.get("saving_throw_proficiencies", []) or []:
        if isinstance(value, str) and value.strip():
            units.append(
                TranslationUnit(
                    category="saving_throw_proficiency",
                    kind="",
                    item_name=value,
                    field="value",
                    source=value,
                    policy="fixed_label",
                )
            )

    for item in character.get("skill_proficiencies", []) or []:
        if not isinstance(item, dict):
            continue
        add(
            item,
            "name",
            "skill_proficiency",
            kind=str(item.get("type") or ""),
            item_name=_item_name(item),
            source_id=_source_id(item),
            fixed=True,
        )

    return units


def contexts_by_source(units: Iterable[TranslationUnit]) -> dict[str, tuple[TranslationUnit, ...]]:
    grouped: dict[str, list[TranslationUnit]] = {}
    for unit in units:
        grouped.setdefault(unit.source, []).append(unit)
    return {source: tuple(rows) for source, rows in grouped.items()}


def primary_context(contexts: Iterable[TranslationUnit]) -> TranslationUnit | None:
    rows = list(contexts)
    if not rows:
        return None
    priority = {"rules": 0, "components": 1, "fixed_label": 2, "name": 3, "prose": 4}
    return min(rows, key=lambda row: (priority.get(row.policy, 9), row.category, row.field))
