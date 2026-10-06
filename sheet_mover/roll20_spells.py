"""Stage 7 Roll20 Legacy OGL5e spell-list writer.

Scope:
- imports Stage 3 ``roll20_payload.spells`` into the matching Roll20
  repeating spell sections (cantrip, spell-1 ... spell-9)
- imports spell-slot totals and remaining slots
- preserves unrelated/manual Roll20 spell rows
- deterministically reuses Sheet Mover row IDs on rerun
- keeps spell output in SPELLCARD mode and does not create repeating attacks
- backs up all managed attributes before mutation
- verifies every imported spell row and slot field from persisted Roll20 attrs

Default behavior is APPLY. Use ``--dry-run`` only when a read-only plan is
explicitly wanted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .result_store import CURRENT_RESULT_DIR, latest_complete_result, load_result
from .roll20_connection import (
    DEFAULT_CDP_URL,
    _attach_driver,
    _disconnect_driver,
    _ensure_cdp,
    _select_roll20_tab,
)
from .roll20_inventory import (
    UPSERT_ROW_SCRIPT,
    _dict,
    _list,
    _plain_text,
    _save_json,
    _snapshot,
    _text,
    _verify,
)


STAGE7_VERSION = "2026-10-06-stage7-roll20-spells-v2-combat-fields"
ROW_PREFIX = "-SM"
ROW_HASH_LENGTH = 17

ROLLCONTENT = (
    "@{wtype}&{template:spell} "
    "{{level=@{spellschool} @{spelllevel}}}  "
    "{{name=@{spellname}}} "
    "{{castingtime=@{spellcastingtime}}} "
    "{{range=@{spellrange}}} "
    "{{target=@{spelltarget}}} "
    "@{spellcomp_v} @{spellcomp_s} @{spellcomp_m} "
    "{{material=@{spellcomp_materials}}} "
    "{{duration=@{spellduration}}} "
    "{{description=@{spelldescription}}} "
    "{{athigherlevels=@{spellathigherlevels}}} "
    "@{spellritual} {{innate=@{innate}}} "
    "@{spellconcentration} @{charname_output} "
    "{{licensedsheet=@{licensedsheet}}}"
)

SPELL_FIELDS = (
    "rollcontent",
    "spellprepared",
    "spellname",
    "innate",
    "spellritual",
    "spellconcentration",
    "spellcomp_v",
    "spellcomp_s",
    "spellcomp_m",
    "spellschool",
    "spellcastingtime",
    "spellrange",
    "spelltarget",
    "spellcomp_materials",
    "spellduration",
    "spell_ability",
    "spelloutput",
    "spellattack",
    "spelldamage",
    "spelldamagetype",
    "spelldamage2",
    "spelldamagetype2",
    "spellhealing",
    "spelldmgmod",
    "spellsave",
    "spellsavesuccess",
    "spellhldie",
    "spellhldietype",
    "spellhlbonus",
    "includedesc",
    "spelldescription",
    "spellathigherlevels",
    "spellclass",
    "spellsource",
    "spellattackid",
    "spelllevel",
    "spell_damage_progression",
)

_COMPONENT_CODES = {
    1: "v",
    2: "s",
    3: "m",
}

_ACTIVATION_UNITS = {
    0: "",
    1: "Action",
    2: "",
    3: "Bonus Action",
    4: "Reaction",
    5: "Special",
    6: "Minute",
    7: "Hour",
    8: "Special",
}

_ABILITY_BY_ID = {
    1: "Strength",
    2: "Dexterity",
    3: "Constitution",
    4: "Intelligence",
    5: "Wisdom",
    6: "Charisma",
}


def _raw_spell_definitions(result_payload):
    """Return D&D Beyond raw spell definitions keyed by character spell id."""
    raw_source = _dict(_dict(result_payload).get("raw_source"))
    out = {}

    for class_spell_group in _list(raw_source.get("classSpells")):
        class_spell_group = _dict(class_spell_group)
        for spell in _list(class_spell_group.get("spells")):
            spell = _dict(spell)
            source_id = _text(spell.get("id"))
            definition = _dict(spell.get("definition"))
            if source_id and definition:
                out[source_id] = definition

    # Compatibility fallback if a future result exposes top-level spells.
    for spell in _list(raw_source.get("spells")):
        spell = _dict(spell)
        source_id = _text(spell.get("id"))
        definition = _dict(spell.get("definition"))
        if source_id and definition:
            out[source_id] = definition

    return out


def _raw_damage_modifier(raw_definition):
    """Pick the primary DDB damage modifier without guessing conditional alts."""
    modifiers = [
        _dict(mod)
        for mod in _list(_dict(raw_definition).get("modifiers"))
        if _text(_dict(mod).get("type")).casefold() == "damage"
    ]
    if not modifiers:
        return {}

    # Prefer an unrestricted primary modifier. Toll the Dead, for example,
    # carries its d12 exception as restriction text while the base die is d8.
    unrestricted = [
        mod for mod in modifiers
        if not _text(mod.get("restriction"))
    ]
    if unrestricted:
        return unrestricted[0]
    return modifiers[0]


def _damage_type_from_modifier(modifier):
    modifier = _dict(modifier)
    friendly = _text(modifier.get("friendlySubtypeName"))
    if friendly:
        return friendly
    subtype = _text(modifier.get("subType"))
    return subtype.title() if subtype else ""


def _save_success_text(raw_definition):
    """Conservative user-facing save result text from raw English rules text."""
    raw_definition = _dict(raw_definition)
    if not raw_definition.get("requiresSavingThrow"):
        return ""

    description = _text(raw_definition.get("description")).casefold()
    if "successful save" in description and "half" in description:
        return "성공 시 절반 피해"

    modifier = _raw_damage_modifier(raw_definition)
    if modifier:
        return "성공 시 피해 없음"
    return "성공 시 효과 없음"


def _spell_combat_profile(item, raw_definition=None):
    item = _dict(item)
    raw_definition = _dict(raw_definition)

    attack_type = raw_definition.get("attackType")
    if attack_type is None:
        attack_type = item.get("attack_type")

    save_id = raw_definition.get("saveDcAbilityId")
    if save_id is None:
        save_id = item.get("save_dc_ability_id")

    requires_attack = bool(raw_definition.get("requiresAttackRoll"))
    if not raw_definition and attack_type is not None:
        requires_attack = True

    requires_save = bool(raw_definition.get("requiresSavingThrow"))
    if not raw_definition and save_id is not None:
        requires_save = True

    as_part_weapon_attack = bool(raw_definition.get("asPartOfWeaponAttack"))

    damage_modifier = _raw_damage_modifier(raw_definition)
    die = _dict(damage_modifier.get("die"))
    damage = _text(die.get("diceString"))
    damage_type = _damage_type_from_modifier(damage_modifier)

    level = item.get("level")
    cantrip_progression = ""
    if level == 0 and damage:
        higher = _list(
            _dict(damage_modifier.get("atHigherLevels"))
            .get("higherLevelDefinitions")
        )
        levels = {
            _dict(row).get("level")
            for row in higher
            if isinstance(row, dict)
        }
        if {5, 11, 17}.issubset(levels):
            cantrip_progression = "Cantrip Dice"

    higher_die_count = ""
    higher_die_type = ""
    higher_bonus = ""
    if isinstance(level, int) and level > 0 and damage_modifier:
        higher = [
            _dict(row)
            for row in _list(
                _dict(damage_modifier.get("atHigherLevels"))
                .get("higherLevelDefinitions")
            )
        ]
        # DDB spell-scale entries use the base spell level as the "per slot
        # above base" delta. Thunderwave: level 1 -> +1d8.
        delta = next(
            (
                row for row in higher
                if row.get("level") == level
                and _dict(row.get("dice")).get("diceString")
            ),
            None,
        )
        if delta:
            delta_dice = _dict(delta.get("dice"))
            higher_die_count = _scalar(delta_dice.get("diceCount"))
            dice_value = _scalar(delta_dice.get("diceValue"))
            higher_die_type = f"d{dice_value}" if dice_value else ""
            higher_bonus = _scalar(delta_dice.get("fixedValue"))
            if higher_bonus == "0":
                higher_bonus = ""

    # A spell that explicitly delegates to a weapon attack must NOT be
    # converted into a Roll20 Spell Attack. Doing so would use the spellcasting
    # ability rather than the chosen weapon's STR/DEX. Booming Blade is the
    # current sample case.
    attack_output = (
        not as_part_weapon_attack
        and (requires_attack or requires_save)
    )

    if attack_type == 1 and requires_attack and not as_part_weapon_attack:
        spell_attack = "Melee"
    elif attack_type == 2 and requires_attack and not as_part_weapon_attack:
        spell_attack = "Ranged"
    else:
        spell_attack = "None"

    return {
        "output": "ATTACK" if attack_output else "SPELLCARD",
        "spellattack": spell_attack,
        "damage": damage if attack_output else "",
        "damage_type": damage_type if attack_output else "",
        "save": _ABILITY_BY_ID.get(save_id, "") if attack_output else "",
        "save_success": (
            _save_success_text(raw_definition)
            if attack_output
            else ""
        ),
        "cantrip_progression": (
            cantrip_progression if attack_output else ""
        ),
        "higher_die_count": higher_die_count if attack_output else "",
        "higher_die_type": higher_die_type if attack_output else "",
        "higher_bonus": higher_bonus if attack_output else "",
        "requires_attack_roll": requires_attack,
        "requires_saving_throw": requires_save,
        "as_part_of_weapon_attack": as_part_weapon_attack,
    }



def _scalar(value, default=""):
    if value is None:
        return default
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (int, float, str)):
        return str(value).strip()
    return default


def spell_row_id(source_key: str) -> str:
    source_key = _text(source_key)
    if not source_key:
        raise ValueError("spell source_key가 비어 있습니다.")
    digest = hashlib.sha1(source_key.encode("utf-8")).hexdigest()
    row_id = ROW_PREFIX + digest[:ROW_HASH_LENGTH]
    if len(row_id) != 20 or "_" in row_id:
        raise AssertionError(f"잘못된 반복행 ID: {row_id}")
    return row_id


def spell_section(level) -> str:
    if type(level) is not int or level < 0 or level > 9:
        raise ValueError(f"지원하지 않는 주문 레벨: {level!r}")
    return "spell-cantrip" if level == 0 else f"spell-{level}"


def spell_level_value(level) -> str:
    return "cantrip" if level == 0 else str(level)


def _pluralized_unit(value, unit):
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = None

    if number is None:
        return f"{_text(value)} {unit}".strip()

    rendered = str(int(number)) if number.is_integer() else str(number)
    if number != 1 and unit not in {"Special", "Action", "Bonus Action", "Reaction"}:
        unit += "s"
    return f"{rendered} {unit}".strip()


def format_casting_time(value) -> str:
    data = _dict(value)
    activation_type = data.get("activationType")
    activation_time = data.get("activationTime")

    unit = _ACTIVATION_UNITS.get(activation_type)
    if unit is None:
        return ""
    if unit in {"Special", "Action", "Bonus Action", "Reaction"}:
        if activation_time in (None, "", 1):
            return unit
        return _pluralized_unit(activation_time, unit)
    if not unit:
        return ""
    if activation_time in (None, ""):
        return unit
    return _pluralized_unit(activation_time, unit)


def _format_distance(value):
    if value in (None, ""):
        return ""
    scalar = _scalar(value)
    return f"{scalar} ft." if scalar else ""


def format_range(value) -> str:
    data = _dict(value)
    origin = _text(data.get("origin"))
    distance = _format_distance(data.get("rangeValue"))
    aoe_type = _text(data.get("aoeType"))
    aoe_distance = _format_distance(data.get("aoeValue"))

    if origin.casefold() == "self":
        base = "Self"
    elif origin.casefold() == "touch":
        base = "Touch"
    elif origin.casefold() in {"ranged", "range"}:
        base = distance
    elif origin:
        base = origin
        if distance:
            base += f" {distance}"
    else:
        base = distance

    if aoe_type and aoe_distance:
        aoe = f"{aoe_distance} {aoe_type}"
        return f"{base} ({aoe})" if base else aoe

    return base


def format_target(value) -> str:
    data = _dict(value)
    aoe_type = _text(data.get("aoeType"))
    aoe_distance = _format_distance(data.get("aoeValue"))
    if aoe_type and aoe_distance:
        return f"{aoe_distance} {aoe_type}"
    return ""


def format_duration(value, concentration=False) -> str:
    data = _dict(value)
    duration_type = _text(data.get("durationType"))
    interval = data.get("durationInterval")
    unit = _text(data.get("durationUnit"))

    if duration_type.casefold() == "instantaneous":
        return "Instantaneous"
    if duration_type.casefold() == "special":
        return "Special"

    if interval not in (None, "") and unit:
        text = _pluralized_unit(interval, unit)
        if concentration:
            return f"Up to {text}"
        return text

    if unit:
        return unit
    return duration_type


def _component_values(components):
    try:
        codes = {int(value) for value in _list(components)}
    except (TypeError, ValueError):
        codes = set()

    return {
        "spellcomp_v": "{{v=1}}" if 1 in codes else "0",
        "spellcomp_s": "{{s=1}}" if 2 in codes else "0",
        "spellcomp_m": "{{m=1}}" if 3 in codes else "0",
    }


def _known_spell_mode(character):
    spellcasting = _dict(character.get("spellcasting"))
    rules = [
        _dict(_dict(row).get("spell_rules"))
        for row in _list(spellcasting.get("class_rules_source"))
        if isinstance(row, dict)
    ]
    if not rules:
        return False

    has_known = any(
        any(value for value in _list(rule.get("levelSpellKnownMaxes")))
        for rule in rules
    )
    has_prepared = any(
        any(value for value in _list(rule.get("levelPreparedSpellMaxes")))
        for rule in rules
    )
    return has_known and not has_prepared


def _prepared_value(item, known_spell_mode=False):
    level = item.get("level")
    if level == 0:
        return "0"
    if item.get("always_prepared") is True or item.get("prepared") is True:
        return "1"
    if known_spell_mode and item.get("counts_as_known_spell") is True:
        return "1"
    return "0"


def map_spell_row(
    item: dict[str, Any],
    *,
    known_spell_mode=False,
    raw_definition=None,
) -> dict[str, Any]:
    item = _dict(item)
    source_key = _text(item.get("source_key"))
    level = item.get("level")
    section = spell_section(level)
    row_id = spell_row_id(source_key)

    name = _plain_text(item.get("name") or item.get("original_name"))
    if not name:
        raise ValueError(f"주문 이름이 비어 있습니다: {source_key}")

    school = _text(item.get("school")).casefold()
    ritual = item.get("ritual") is True or item.get("cast_only_as_ritual") is True
    concentration = item.get("concentration") is True
    combat = _spell_combat_profile(item, raw_definition)

    components = _component_values(item.get("components"))
    fields = {
        "rollcontent": ROLLCONTENT,
        "spellprepared": _prepared_value(item, known_spell_mode),
        "spellname": name,
        "innate": "",
        "spellritual": "{{ritual=1}}" if ritual else "0",
        "spellconcentration": "{{concentration=1}}" if concentration else "0",
        **components,
        "spellschool": school,
        "spellcastingtime": format_casting_time(item.get("casting_time")),
        "spellrange": format_range(item.get("range")),
        "spelltarget": format_target(item.get("range")),
        "spellcomp_materials": (
            _plain_text(item.get("components_description"))
            if components["spellcomp_m"] != "0"
            else ""
        ),
        "spellduration": format_duration(
            item.get("duration"),
            concentration=concentration,
        ),
        "spell_ability": "spell",

        # Stage 7 now prepares the native Legacy spell combat fields. Stage 10B
        # creates/links the repeating_attack row deterministically because our
        # Backbone writer does not fire Roll20's sheet-worker change event.
        "spelloutput": combat["output"],
        "spellattack": combat["spellattack"],
        "spelldamage": combat["damage"],
        "spelldamagetype": combat["damage_type"],
        "spelldamage2": "",
        "spelldamagetype2": "",
        "spellhealing": "",
        "spelldmgmod": "0",
        "spellsave": combat["save"],
        "spellsavesuccess": combat["save_success"],
        "spellhldie": combat["higher_die_count"],
        "spellhldietype": combat["higher_die_type"],
        "spellhlbonus": combat["higher_bonus"],
        "includedesc": "on" if combat["output"] == "ATTACK" else "off",

        "spelldescription": _plain_text(item.get("description")),
        "spellathigherlevels": "",
        "spellclass": "",
        "spellsource": "",
        "spellattackid": "",
        "spelllevel": spell_level_value(level),
        "spell_damage_progression": combat["cantrip_progression"],
    }

    return {
        "source_key": source_key,
        "source_id": _text(item.get("source_id")),
        "definition_id": _text(item.get("definition_id")),
        "level": level,
        "section": section,
        "row_id": row_id,
        "name": name,
        "source_save_ability": _ABILITY_BY_ID.get(item.get("save_dc_ability_id"), ""),
        "source_attack_type": item.get("attack_type"),
        "combat": combat,
        "fields": fields,
    }


def spell_attribute_name(section: str, row_id: str, field: str) -> str:
    if field not in SPELL_FIELDS:
        raise ValueError(f"허용되지 않은 spell 필드: {field}")
    if not section.startswith("spell-"):
        raise ValueError(f"잘못된 주문 반복구역: {section}")
    if not row_id or "_" in row_id:
        raise ValueError(f"잘못된 반복행 ID: {row_id}")
    return f"repeating_{section}_{row_id}_{field}"


def _row_attributes(row):
    section = _text(row.get("section"))
    row_id = _text(row.get("row_id"))
    fields = _dict(row.get("fields"))
    return {
        spell_attribute_name(section, row_id, field): {
            "current": _text(fields.get(field)),
            "max": "",
        }
        for field in SPELL_FIELDS
    }


def plan_spell_attributes(plan):
    attrs = {}
    for row in _list(plan.get("rows")):
        for name, spec in _row_attributes(row).items():
            if name in attrs:
                raise RuntimeError(f"Roll20 주문 attribute 이름 중복: {name}")
            attrs[name] = spec
    return attrs


def _slot_totals(character):
    """Return reliable slot totals when Stage 3 exposes one unambiguous profile."""
    spellcasting = _dict(character.get("spellcasting"))
    rules_rows = [
        row
        for row in _list(spellcasting.get("class_rules_source"))
        if isinstance(row, dict)
    ]

    profiles = []
    for row in rules_rows:
        profile = {}
        for slot in _list(row.get("slots_at_level")):
            slot = _dict(slot)
            level = slot.get("level")
            available = slot.get("available")
            if type(level) is int and 1 <= level <= 9 and isinstance(available, (int, float)):
                profile[level] = int(available)
        if any(profile.values()):
            profiles.append(profile)

    if len(profiles) == 1:
        return {level: profiles[0].get(level, 0) for level in range(1, 10)}, "class_rules_source"

    # If several rows expose exactly the same already-combined profile, it is
    # still safe. Otherwise do not guess multiclass slot arithmetic.
    if len(profiles) > 1 and all(profile == profiles[0] for profile in profiles[1:]):
        return {level: profiles[0].get(level, 0) for level in range(1, 10)}, "identical_class_profiles"

    source_rows = [
        _dict(row)
        for row in _list(spellcasting.get("spell_slots_source"))
        if isinstance(row, dict)
    ]
    if any((row.get("available") or 0) > 0 for row in source_rows):
        out = {level: 0 for level in range(1, 10)}
        for row in source_rows:
            level = row.get("level")
            available = row.get("available")
            if type(level) is int and 1 <= level <= 9 and isinstance(available, (int, float)):
                out[level] = int(available)
        return out, "spell_slots_source"

    return {}, "unresolved"


def slot_attributes(character):
    spellcasting = _dict(character.get("spellcasting"))
    totals, source = _slot_totals(character)
    if not totals:
        return {}, source

    used_by_level = {level: 0 for level in range(1, 10)}
    for row in _list(spellcasting.get("spell_slots_source")):
        row = _dict(row)
        level = row.get("level")
        used = row.get("used")
        if type(level) is int and 1 <= level <= 9 and isinstance(used, (int, float)):
            used_by_level[level] = max(0, int(used))

    attrs = {}
    for level in range(1, 10):
        total = max(0, int(totals.get(level, 0)))
        used = min(total, max(0, int(used_by_level.get(level, 0))))
        remaining = total - used
        attrs[f"lvl{level}_slots_total"] = {
            "current": str(total),
            "max": "",
        }
        # Despite its historical name, Legacy OGL5e displays this field as
        # SLOTS REMAINING and decrements it when a slot is used.
        attrs[f"lvl{level}_slots_expended"] = {
            "current": str(remaining),
            "max": "",
        }
    return attrs, source


def build_spell_plan(result_payload: dict[str, Any]) -> dict[str, Any]:
    roll20_payload = _dict(_dict(result_payload).get("roll20_payload"))
    character = _dict(roll20_payload.get("character"))
    spells = _list(roll20_payload.get("spells"))
    source_character_id = _text(roll20_payload.get("source_character_id"))
    character_name = _text(character.get("name"))

    if not source_character_id:
        raise RuntimeError("roll20_payload.source_character_id가 없습니다.")
    if not character_name:
        raise RuntimeError("roll20_payload.character.name이 없습니다.")

    known_mode = _known_spell_mode(character)
    raw_spell_definitions = _raw_spell_definitions(result_payload)
    rows = []
    seen_source_keys = set()
    seen_locations = set()

    for item in spells:
        row = map_spell_row(
            item,
            known_spell_mode=known_mode,
            raw_definition=raw_spell_definitions.get(_text(_dict(item).get("source_id"))),
        )
        if row["source_key"] in seen_source_keys:
            raise RuntimeError(f"주문 source_key 중복: {row['source_key']}")
        location = (row["section"], row["row_id"])
        if location in seen_locations:
            raise RuntimeError(
                f"주문 반복행 충돌: {row['section']} / {row['row_id']}"
            )
        seen_source_keys.add(row["source_key"])
        seen_locations.add(location)
        rows.append(row)

    slots, slot_source = slot_attributes(character)

    return {
        "version": STAGE7_VERSION,
        "source_character_id": source_character_id,
        "character_name": character_name,
        "row_count": len(rows),
        "rows": rows,
        "slot_attributes": slots,
        "slot_source": slot_source,
        "known_spell_mode": known_mode,
        "policy": {
            "preserve_unmanaged_rows": True,
            "delete_existing_rows": False,
            "stable_row_ids": True,
            "spelloutput": "combat-aware",
            "combat_fields_prepared": True,
            "create_attacks": False,
        },
        "deferred": [
            "features",
            "actions",
            "attacks",
            "skill_save_proficiency_expertise",
            "class_resources",
        ],
    }


def _load_target(source_id: str):
    path = Path(CURRENT_RESULT_DIR) / f"roll20-target-{source_id}.json"
    if not path.is_file():
        raise RuntimeError(f"4단계 연결 파일이 없습니다: {path.resolve()}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if _text(payload.get("source_character_id")) != source_id:
        raise RuntimeError("Roll20 연결 파일 source ID가 다릅니다.")
    if _text(payload.get("sheet_type")) != "ogl5e":
        raise RuntimeError(
            f"7단계는 ogl5e만 지원합니다: {_text(payload.get('sheet_type')) or '미확인'}"
        )
    if not _text(payload.get("roll20_character_id")):
        raise RuntimeError("Roll20 Character ID가 없습니다.")
    return payload, path


def _is_complete(snapshot, attrs):
    _, mismatches = _verify(snapshot, attrs)
    return not mismatches


def _upsert_and_verify(driver, target, attrs, label):
    driver.set_script_timeout(45)
    outcome = driver.execute_async_script(
        UPSERT_ROW_SCRIPT,
        _text(target.get("roll20_character_id")),
        _text(target.get("character_name")),
        attrs,
    )
    if not isinstance(outcome, dict) or not outcome.get("ok"):
        raise RuntimeError(
            f"{label} 저장 실패: "
            + json.dumps(outcome, ensure_ascii=False)
        )

    after = _snapshot(driver, target, attrs.keys())
    actual, mismatches = _verify(after, attrs)
    if mismatches:
        raise RuntimeError(
            f"{label} 서버 재검증 실패: "
            + json.dumps(mismatches, ensure_ascii=False)
        )
    return outcome, actual


def apply_spells(
    *,
    result_path: str | Path | None = None,
    source_id: str | None = None,
    cdp_url: str = DEFAULT_CDP_URL,
    dry_run: bool = False,
):
    source_id = _text(source_id)
    path = Path(result_path) if result_path else latest_complete_result(source_id=source_id or None)
    if path is None or not path.is_file():
        raise RuntimeError("사용할 정상 sheet-result JSON이 없습니다.")

    payload = load_result(path)
    plan = build_spell_plan(payload)
    actual_source_id = plan["source_character_id"]
    target, target_path = _load_target(actual_source_id)

    if _text(target.get("character_name")) != plan["character_name"]:
        raise RuntimeError("D&D Beyond 결과와 Roll20 대상 캐릭터 이름이 다릅니다.")

    spell_attrs = plan_spell_attributes(plan)
    slot_attrs = _dict(plan.get("slot_attributes"))
    all_attrs = {**spell_attrs, **slot_attrs}
    output_path = Path(CURRENT_RESULT_DIR) / f"roll20-spells-{actual_source_id}.json"

    report = {
        "version": STAGE7_VERSION,
        "mode": "dry-run" if dry_run else "apply",
        "source_character_id": actual_source_id,
        "character_name": plan["character_name"],
        "roll20_character_id": target["roll20_character_id"],
        "result_path": str(path.resolve()),
        "target_path": str(Path(target_path).resolve()),
        "row_count": plan["row_count"],
        "managed_attribute_count": len(all_attrs),
        "slot_source": plan["slot_source"],
        "slot_attributes": slot_attrs,
        "known_spell_mode": plan["known_spell_mode"],
        "policy": plan["policy"],
        "rows": [
            {
                "source_key": row["source_key"],
                "row_id": row["row_id"],
                "section": row["section"],
                "level": row["level"],
                "name": row["name"],
                "source_save_ability": row["source_save_ability"],
                "source_attack_type": row["source_attack_type"],
                "fields": row["fields"],
            }
            for row in plan["rows"]
        ],
        "backup_path": None,
        "mutated": False,
        "row_results": [],
        "slot_result": None,
        "verification": {},
        "status": "running",
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }

    _ensure_cdp(cdp_url)
    driver = _attach_driver(cdp_url)

    try:
        _select_roll20_tab(driver)
        before = _snapshot(driver, target, all_attrs.keys())
        report["before"] = before["attributes"]

        pending_rows = []
        for row in plan["rows"]:
            attrs = _row_attributes(row)
            if not _is_complete(before, attrs):
                pending_rows.append(row)

        slots_pending = bool(slot_attrs) and not _is_complete(before, slot_attrs)

        report["initial_pending_rows"] = [
            {
                "source_key": row["source_key"],
                "row_id": row["row_id"],
                "section": row["section"],
                "name": row["name"],
            }
            for row in pending_rows
        ]
        report["slots_pending"] = slots_pending

        if dry_run:
            report["status"] = "pass"
            report["verification"] = {
                "status": "not_run",
                "reason": "dry_run",
            }
            _save_json(output_path, report)
            return report, output_path

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = Path(CURRENT_RESULT_DIR) / (
            f"roll20-stage7-spells-backup-{actual_source_id}-{timestamp}.json"
        )
        _save_json(
            backup_path,
            {
                "version": STAGE7_VERSION,
                "source_character_id": actual_source_id,
                "character_name": plan["character_name"],
                "roll20_character_id": target["roll20_character_id"],
                "read_only_snapshot_before_apply": True,
                "managed_attributes": before["attributes"],
            },
        )
        report["backup_path"] = str(backup_path.resolve())
        _save_json(output_path, report)

        total = len(pending_rows)
        for index, row in enumerate(pending_rows, start=1):
            print(
                f"[시트 이동기] 주문 {index}/{total}: "
                f"{row['name']} ({row['section']})",
                flush=True,
            )
            attrs = _row_attributes(row)
            outcome, actual = _upsert_and_verify(
                driver,
                target,
                attrs,
                f"주문 '{row['name']}'",
            )
            report["mutated"] = True
            report["row_results"].append({
                "source_key": row["source_key"],
                "row_id": row["row_id"],
                "section": row["section"],
                "name": row["name"],
                "result": outcome,
                "verification": {
                    "status": "pass",
                    "actual": actual,
                    "mismatches": [],
                },
            })
            _save_json(output_path, report)

        if slots_pending:
            print("[시트 이동기] 주문 슬롯을 입력합니다.", flush=True)
            outcome, actual = _upsert_and_verify(
                driver,
                target,
                slot_attrs,
                "주문 슬롯",
            )
            report["mutated"] = True
            report["slot_result"] = {
                "result": outcome,
                "verification": {
                    "status": "pass",
                    "actual": actual,
                    "mismatches": [],
                },
            }
            _save_json(output_path, report)

        final_snapshot = _snapshot(driver, target, all_attrs.keys())
        actual, mismatches = _verify(final_snapshot, all_attrs)
        report["verification"] = {
            "status": "pass" if not mismatches else "fail",
            "actual": actual,
            "mismatches": mismatches,
            "server_fetch_status": final_snapshot.get("fetch_status"),
        }
        report["status"] = "pass" if not mismatches else "error"

        if mismatches:
            report["error"] = (
                "7단계 주문 최종 서버 재검증 실패: "
                + json.dumps(mismatches, ensure_ascii=False)
            )

        _save_json(output_path, report)

        if mismatches:
            raise RuntimeError(report["error"])

        return report, output_path

    except Exception as exc:
        if "error" not in report:
            report["status"] = "error"
            report["error"] = str(exc)
            _save_json(output_path, report)
        raise
    finally:
        _disconnect_driver(driver)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", help="사용할 sheet-result JSON")
    parser.add_argument("--source-id", default="170892133")
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="읽기 전용 계획만 수행. 기본값은 실제 입력입니다.",
    )
    args = parser.parse_args()

    print("[시트 이동기] 7단계: Roll20 주문 반복행 입력")
    print(f"[시트 이동기] 모드: {'읽기 전용' if args.dry_run else '실제 입력'}")
    print("[시트 이동기] 기존 Roll20 주문은 삭제하지 않습니다.")
    print("[시트 이동기] 주문의 SPELLCARD/ATTACK 분류와 전투 필드를 입력합니다.")
    print("[시트 이동기] 반복 공격행 생성/연결은 10B 단계에서 수행합니다.")

    report, output = apply_spells(
        result_path=args.result,
        source_id=args.source_id,
        cdp_url=args.cdp_url,
        dry_run=args.dry_run,
    )

    print(f"[시트 이동기] D&D Beyond 주문: {report['row_count']}개")
    print(
        f"[시트 이동기] 최초 미완료 주문: "
        f"{len(report.get('initial_pending_rows') or [])}개"
    )
    if report.get("slot_attributes"):
        level1_total = _dict(report["slot_attributes"].get("lvl1_slots_total")).get("current")
        level1_remaining = _dict(report["slot_attributes"].get("lvl1_slots_expended")).get("current")
        print(
            f"[시트 이동기] 1레벨 슬롯: "
            f"총 {level1_total} / 남음 {level1_remaining}"
        )
    else:
        print(
            "[시트 이동기] 주문 슬롯 총량을 안전하게 확정하지 못해 "
            "슬롯 값은 수정하지 않았습니다."
        )

    if not args.dry_run:
        print(f"[시트 이동기] 백업: {report.get('backup_path')}")
        print(
            "[시트 이동기] 최종 서버 재검증: "
            + str((report.get("verification") or {}).get("status"))
        )
    print("[시트 이동기] 공격/기술·내성/클래스 자원은 수정하지 않았습니다.")
    print(f"[시트 이동기] 결과 저장: {output.resolve()}")


if __name__ == "__main__":
    main()
