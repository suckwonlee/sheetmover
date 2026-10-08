"""Stage 3 Roll20-neutral repeating-row payload builder.

This module does not touch Roll20 or a browser.  It reshapes the translated
Stage 1 data plus Stage 2 final numeric values into stable rows that later
Roll20 writer stages can consume without re-reading D&D Beyond internals.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import re
from typing import Any


STAGE3_PAYLOAD_VERSION = "2026-10-06-stage3-roll20-payload-v1"


def _dict(value):
    return value if isinstance(value, dict) else {}


def _list(value):
    return value if isinstance(value, list) else []


def _text(value):
    return str(value or "").strip()


def _slug(value):
    value = re.sub(r"[^0-9A-Za-z._-]+", "-", _text(value)).strip("-")
    return value[:80] or "unnamed"


def _source_key(category, item, index, seen):
    source_id = _text(item.get("source_id"))
    definition_id = _text(item.get("definition_id"))
    original_name = _text(item.get("original_name") or item.get("name"))

    if source_id:
        base = f"{category}:{source_id}"
    elif definition_id:
        base = f"{category}:definition:{definition_id}"
    else:
        digest = hashlib.sha1(
            f"{category}|{original_name}|{index}".encode("utf-8")
        ).hexdigest()[:12]
        base = f"{category}:fallback:{_slug(original_name)}:{digest}"

    count = seen.get(base, 0) + 1
    seen[base] = count
    return base if count == 1 else f"{base}#{count}"


def _row(category, item, index, seen, fields):
    if not isinstance(item, dict):
        return None
    row = {
        "source_key": _source_key(category, item, index, seen),
        "source_id": _text(item.get("source_id")),
        "definition_id": _text(item.get("definition_id")),
        "kind": _text(item.get("kind")),
        "name": _text(item.get("name")),
        "original_name": _text(item.get("original_name") or item.get("name")),
    }
    for field in fields:
        if field in item:
            row[field] = deepcopy(item.get(field))
    return row


def _rows(category, values, fields):
    rows = []
    seen = {}
    for index, item in enumerate(_list(values)):
        row = _row(category, item, index, seen, fields)
        if row is not None:
            rows.append(row)
    return rows


_EQUIPMENT_FIELDS = (
    "description", "quantity", "equipped", "attuned", "weight", "item_type",
    "rarity", "magic", "armor_class", "armor_type_id", "base_armor_name",
    "damage", "damage_type", "range", "properties",
)

_SPELL_FIELDS = (
    "description", "level", "prepared", "always_prepared", "uses_spell_slot",
    "casting_time", "range", "duration", "components", "components_description",
    "school", "ritual", "concentration", "save_dc_ability_id", "attack_type",
    "damage_effect", "counts_as_known_spell", "spellcasting_ability_id",
    "cast_only_as_ritual", "ritual_casting_type", "restriction",
    "display_as_attack", "source_group", "character_class_id", "component_id",
    "component_type_id", "definition_is_legacy", "grant_type",
    "grant_feature_id", "grant_feature_name",
)

_FEATURE_FIELDS = (
    "description", "required_level", "limited_use",
)

_ACTION_FIELDS = (
    "description", "activation", "range", "attack_type",
    "ability_modifier_stat_id", "dice", "damage_type_id", "limited_use",
)


def build_roll20_payload(translated: dict[str, Any], original: dict[str, Any] | None = None):
    """Return stable Stage 3 rows using translated display text.

    The payload intentionally stops before Roll20-specific attribute names or
    repeating-section IDs.  Those belong to later mapping/writer stages.
    """
    translated = _dict(translated)
    original = _dict(original)
    source = translated or original

    character = {
        "source_id": _text(source.get("source_id") or original.get("source_id")),
        "name": _text(source.get("name") or original.get("name")),
        "race": deepcopy(source.get("race") or {}),
        "background": deepcopy(source.get("background") or {}),
        "classes": deepcopy(source.get("classes") or []),
        "total_level": source.get("total_level"),
        "experience": source.get("experience"),
        "alignment": source.get("alignment"),
        "ability_scores": deepcopy(source.get("ability_scores") or {}),
        "proficiency_bonus": source.get("proficiency_bonus"),
        "armor_class": source.get("armor_class"),
        "initiative": source.get("initiative"),
        "speed": deepcopy(source.get("speed") or {}),
        "hp": source.get("hp"),
        "max_hp": source.get("max_hp"),
        "temp_hp": source.get("temp_hp"),
        "death_saves": deepcopy(source.get("death_saves") or {}),
        "inspiration": source.get("inspiration"),
        "spellcasting": deepcopy(source.get("spellcasting") or {}),
        "currencies": deepcopy(source.get("currencies") or {}),
    }

    equipment = _rows("equipment", source.get("equipment"), _EQUIPMENT_FIELDS)
    spells = _rows("spell", source.get("spells"), _SPELL_FIELDS)
    features = _rows("feature", source.get("features"), _FEATURE_FIELDS)
    actions = _rows("action", source.get("actions"), _ACTION_FIELDS)

    return {
        "version": STAGE3_PAYLOAD_VERSION,
        "source_character_id": character["source_id"],
        "character": character,
        "equipment": equipment,
        "spells": spells,
        "features": features,
        "actions": actions,
        "counts": {
            "equipment": len(equipment),
            "spells": len(spells),
            "features": len(features),
            "actions": len(actions),
        },
        "deferred_to_later_stages": [
            "attacks",
            "skill_save_expertise_rows",
            "class_resources",
            "roll20_attribute_names",
            "roll20_repeating_row_ids",
        ],
    }
