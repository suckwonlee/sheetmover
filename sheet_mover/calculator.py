"""Stage 2 final-value extraction for D&D Beyond character data.

The D&D Beyond character API exposes many already-resolved source facts
(base scores, selected modifiers, class levels, HP sources, equipped items),
but it does not expose every final sheet number as a single field.

This module deliberately performs only the small amount of deterministic
combination needed to produce final Roll20-ready values.  It is not intended
to become a second D&D rules engine.

No network access is performed here.
"""
from __future__ import annotations

from copy import deepcopy
from math import floor
import re


STAGE2_CALCULATOR_VERSION = "2026-10-06-stage2-final-values-v1"

ABILITY_IDS = {
    1: "strength",
    2: "dexterity",
    3: "constitution",
    4: "intelligence",
    5: "wisdom",
    6: "charisma",
}
ABILITY_NAMES = tuple(ABILITY_IDS.values())
ABILITY_SCORE_SUBTYPE = re.compile(
    r"^(strength|dexterity|constitution|intelligence|wisdom|charisma)-score$",
    re.I,
)

_STAGE1_WARNING_PREFIXES = (
    "일부 최종 능력치는 2단계 계산기가 필요하여",
    "최종 HP는 2단계 계산기가 필요하여",
    "1단계 데이터 모델 확장 상태입니다.",
)


def _number(value):
    return value if type(value) in (int, float) else None


def _list(value):
    return value if isinstance(value, list) else []


def _dict(value):
    return value if isinstance(value, dict) else {}


def _text(value):
    return str(value or "")


def _restriction_is_unconditional(modifier):
    return _text(modifier.get("restriction")).strip() == ""


def _warn_once(sheet, message):
    if message not in sheet.warnings:
        sheet.warnings.append(message)


def ability_modifier(score):
    """Return the standard D&D ability modifier, or None when unresolved."""
    if _number(score) is None:
        return None
    return floor((score - 10) / 2)


def proficiency_bonus_for_level(level):
    """Return the normal character-level proficiency bonus."""
    if type(level) is not int or level < 1:
        return None
    return 2 + (level - 1) // 4


def _component_id(value):
    if value in (None, ""):
        return None
    return str(value)


def _feature_ids(sheet, kind):
    result = set()
    for item in getattr(sheet, "features", []) or []:
        if not isinstance(item, dict) or item.get("kind") != kind:
            continue
        for key in ("definition_id", "source_id"):
            identity = _component_id(item.get(key))
            if identity:
                result.add(identity)
    return result


def _active_component_ids(sheet):
    background = getattr(sheet, "background", {}) or {}
    classes = getattr(sheet, "classes", []) or []

    active = {
        "race": _feature_ids(sheet, "racial_trait"),
        "feat": _feature_ids(sheet, "feat"),
        "class": _feature_ids(sheet, "class_feature"),
        "background": _feature_ids(sheet, "background"),
    }

    background_id = _component_id(background.get("source_id"))
    if background_id:
        active["background"].add(background_id)

    # Some class modifiers are anchored directly to the class/subclass rather
    # than a visible feature.
    for cls in classes:
        if not isinstance(cls, dict):
            continue
        for key in ("source_id", "definition_id", "subclass_id"):
            identity = _component_id(cls.get(key))
            if identity:
                active["class"].add(identity)

    return active


def _modifier_value(modifier):
    value = _number(modifier.get("value"))
    if value is None:
        value = _number(modifier.get("fixedValue"))
    return value


def _modifier_active(group, modifier, active_ids):
    component = _component_id(modifier.get("componentId"))

    # A component-less modifier is already a concrete source fact in many DDB
    # payloads.  isGranted=False is the only clear signal that it is inactive.
    if component is None:
        return modifier.get("isGranted") is not False

    known = active_ids.get(group)
    if known is None:
        return False
    return component in known


def _item_modifier_active(item):
    """Mirror DDB's practical active-item rules closely enough for final stats."""
    equipped = item.get("equipped") is True
    attuned = item.get("attuned") is True
    can_equip = item.get("can_equip") is True
    can_attune = item.get("can_attune") is True
    consumable = item.get("is_consumable") is True

    return (
        (not can_equip and not can_attune and not consumable)
        or (attuned and equipped)
        or (attuned and not can_equip)
        or (not can_attune and equipped)
    )


def _iter_active_modifiers(sheet):
    """Yield active non-item modifiers plus active inventory granted modifiers."""
    inputs = _dict(getattr(sheet, "calculation_inputs", {}) or {})
    modifiers = _dict(inputs.get("modifiers"))
    active_ids = _active_component_ids(sheet)

    # DDB's item effects are more reliably represented by each inventory
    # definition's grantedModifiers than by character.modifiers.item, so the
    # top-level item group is intentionally skipped to avoid double counting.
    for group, entries in modifiers.items():
        group = str(group)
        if group == "item":
            continue
        for modifier in _list(entries):
            if not isinstance(modifier, dict):
                continue
            if _modifier_active(group, modifier, active_ids):
                yield group, modifier

    for item in getattr(sheet, "equipment", []) or []:
        if not isinstance(item, dict) or not _item_modifier_active(item):
            continue
        component = (
            _component_id(item.get("definition_id"))
            or _component_id(item.get("source_id"))
        )
        for source_modifier in _list(item.get("granted_modifiers")):
            if not isinstance(source_modifier, dict):
                continue
            modifier = deepcopy(source_modifier)
            if modifier.get("componentId") in (None, "") and component:
                modifier["componentId"] = component
            yield "item", modifier


def _ability_from_modifier(modifier):
    subtype = _text(modifier.get("subType"))
    match = ABILITY_SCORE_SUBTYPE.fullmatch(subtype)
    if match:
        return match.group(1).casefold()

    if subtype in {"ability-score", "choose-an-ability-score"}:
        for key in ("statId", "entityId"):
            stat_id = modifier.get(key)
            if type(stat_id) is int and stat_id in ABILITY_IDS:
                return ABILITY_IDS[stat_id]

    return None


def _restriction_cap(restriction):
    text = _text(restriction)
    patterns = (
        r"maximum(?:\s+score)?\s+is\s+now\s+(\d+)",
        r"maximum\s+of\s+(\d+)",
        r"maximum\s+score\s+(\d+)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return int(match.group(1))
    return None


def _ability_cap_bonus(sheet, ability_id):
    total = 0
    for _group, modifier in _iter_active_modifiers(sheet):
        if modifier.get("type") != "bonus":
            continue
        if modifier.get("subType") != "ability-score-maximum":
            continue
        stat_id = modifier.get("statId")
        if stat_id not in (None, ability_id):
            continue
        value = _modifier_value(modifier)
        if value is not None:
            total += value
    return total


def _ability_restriction_supported(modifier_type, modifier):
    restriction = _text(modifier.get("restriction")).strip()
    if restriction == "":
        return True
    if _restriction_cap(restriction) is not None:
        return True
    if modifier_type == "set" and restriction.casefold() == "if not already higher":
        return True
    return False


def _calculate_ability_scores(sheet):
    inputs = _dict(sheet.calculation_inputs)
    base = {
        item.get("id"): _number(item.get("value"))
        for item in _list(inputs.get("stats"))
        if isinstance(item, dict)
    }
    bonus_stats = {
        item.get("id"): _number(item.get("value"))
        for item in _list(inputs.get("bonus_stats"))
        if isinstance(item, dict)
    }
    override = {
        item.get("id"): _number(item.get("value"))
        for item in _list(inputs.get("override_stats"))
        if isinstance(item, dict)
    }

    scores = {}
    unresolved_global = []
    unresolved_abilities = set()
    modifiers_by_ability = {name: [] for name in ABILITY_NAMES}

    for _group, modifier in _iter_active_modifiers(sheet):
        modifier_type = _text(modifier.get("type"))
        subtype = _text(modifier.get("subType"))
        ability = _ability_from_modifier(modifier)
        value = _modifier_value(modifier)

        if (
            modifier_type in {"bonus", "set"}
            and subtype in {"ability-score", "choose-an-ability-score"}
            and ability is None
            and value not in (None, 0)
        ):
            unresolved_global.append(deepcopy(modifier))
            continue

        if not ability or modifier_type not in {"bonus", "set"} or value is None:
            continue

        if not _ability_restriction_supported(modifier_type, modifier):
            unresolved_abilities.add(ability)
            continue

        modifiers_by_ability[ability].append(
            (
                modifier_type,
                value,
                _restriction_cap(modifier.get("restriction")),
            )
        )

    for ability_id, ability in ABILITY_IDS.items():
        # DDB treats 0 as the absence of an override. Actual D&D ability scores
        # cannot normally be 0, so following that convention avoids old payload
        # compatibility bugs.
        explicit_override = override.get(ability_id)
        if explicit_override not in (None, 0):
            scores[ability] = explicit_override
            continue

        base_value = base.get(ability_id)
        if base_value is None or ability in unresolved_abilities:
            scores[ability] = None
            continue

        ordinary_bonus = 0
        set_values = []
        cap = 20 + _ability_cap_bonus(sheet, ability_id)

        for modifier_type, modifier_value, restriction_cap in modifiers_by_ability[ability]:
            if modifier_type == "bonus":
                ordinary_bonus += modifier_value
                if restriction_cap is not None:
                    cap = max(cap, restriction_cap)
            elif modifier_type == "set":
                set_values.append(modifier_value)

        # Match DDB ordering: normal bonuses are capped, bonusStats are applied
        # after that cap, then a set-score effect may raise a lower result.
        value = min(cap, base_value + ordinary_bonus)
        value += bonus_stats.get(ability_id) or 0
        if set_values:
            value = max(value, max(set_values))

        scores[ability] = value

    if unresolved_global:
        scores = {name: None for name in ABILITY_NAMES}

    return scores, unresolved_global, unresolved_abilities


def _total_level(sheet):
    total = 0
    seen = False
    for item in _list(_dict(sheet.calculation_inputs).get("class_levels")):
        if not isinstance(item, dict):
            continue
        level = item.get("level")
        if type(level) is int and level >= 0:
            total += level
            seen = True
    return total if seen else None


def _class_feature_level_map(sheet):
    result = {}
    for item in _list(_dict(sheet.calculation_inputs).get("class_feature_levels")):
        if not isinstance(item, dict):
            continue
        feature_id = _component_id(
            item.get("feature_id")
            or item.get("component_id")
            or item.get("definition_id")
        )
        level = item.get("class_level")
        if feature_id and type(level) is int and level >= 0:
            result[feature_id] = level
    return result


def _hp_modifier_total(sheet, total_level):
    total = 0
    unresolved = []
    feature_levels = _class_feature_level_map(sheet)

    for _group, modifier in _iter_active_modifiers(sheet):
        if _text(modifier.get("type")) != "bonus":
            continue

        subtype = _text(modifier.get("subType"))
        if subtype not in {
            "hit-points-per-level",
            "hit-points",
            "hit-point-maximum",
            "maximum-hit-points",
        }:
            continue

        # Conditional max-HP effects cannot safely be collapsed to one final
        # number without evaluating the condition.
        if not _restriction_is_unconditional(modifier):
            unresolved.append(deepcopy(modifier))
            continue

        if subtype == "hit-points-per-level":
            value = _modifier_value(modifier)
            if value is None:
                unresolved.append(deepcopy(modifier))
                continue
            component = _component_id(modifier.get("componentId"))
            multiplier = feature_levels.get(component, total_level)
            if multiplier is None:
                unresolved.append(deepcopy(modifier))
            else:
                total += value * multiplier
            continue

        # DDB also uses hit-points modifiers for healing riders. A dice-only
        # healing rider must never inflate maximum HP.
        if modifier.get("dice") and _number(modifier.get("value")) is None:
            continue

        value = _modifier_value(modifier)
        if value is None:
            unresolved.append(deepcopy(modifier))
        else:
            total += value

    return total, unresolved


def _calculate_hp(sheet, total_level, ability_scores):
    inputs = _dict(sheet.calculation_inputs)
    override_hp = _number(inputs.get("override_hit_points"))
    bonus_hp = _number(inputs.get("bonus_hit_points")) or 0
    removed = _number(inputs.get("removed_hit_points"))

    if override_hp not in (None, 0):
        core_max_hp = override_hp
        unresolved = []
    else:
        base_hp = _number(inputs.get("base_hit_points"))
        constitution = ability_scores.get("constitution")
        con_mod = ability_modifier(constitution)
        if base_hp is None or total_level is None or con_mod is None:
            return None, None, []

        modifier_hp, unresolved = _hp_modifier_total(sheet, total_level)
        if unresolved:
            return None, None, unresolved

        core_max_hp = base_hp + con_mod * total_level + modifier_hp

    # DDB keeps bonusHitPoints separately from base/override HP, but the
    # effective character maximum used by Roll20 needs the final combined
    # number.
    max_hp = core_max_hp + bonus_hp
    current_hp = None if removed is None else max(0, max_hp - removed)
    return current_hp, max_hp, unresolved


def _character_value_number(entry):
    value = entry.get("value")
    if type(value) in (int, float):
        return value
    if isinstance(value, str):
        try:
            return float(value) if "." in value else int(value)
        except ValueError:
            return None
    return None


def _ac_character_values(sheet):
    override = None
    custom_bonus = 0
    dual_wield_ids = set()

    for item in _list(_dict(sheet.calculation_inputs).get("character_values")):
        if not isinstance(item, dict):
            continue
        type_id = item.get("typeId")
        value = _character_value_number(item)
        if type_id == 1 and value is not None:
            override = value
        elif type_id in {2, 3} and value is not None:
            custom_bonus += value
        elif type_id == 18 and item.get("valueId") not in (None, ""):
            dual_wield_ids.add(str(item.get("valueId")))

    return override, custom_bonus, dual_wield_ids


def _equipment_ac_bonus(item):
    if not _item_modifier_active(item):
        return 0

    total = 0
    for modifier in _list(item.get("granted_modifiers")):
        if not isinstance(modifier, dict):
            continue
        if not _restriction_is_unconditional(modifier):
            continue
        if modifier.get("type") == "bonus" and modifier.get("subType") == "armor-class":
            value = _modifier_value(modifier)
            if value is not None:
                total += value
    return total


def _ac_modifier_state(sheet, *, armored):
    generic = 0
    conditional = []
    medium_dex_cap = 2
    unarmored_dex_cap = 20
    ignore_unarmored_dex = False
    unarmored_sets = []
    minimum_base_armor = []

    for group, modifier in _iter_active_modifiers(sheet):
        modifier_type = _text(modifier.get("type"))
        subtype = _text(modifier.get("subType"))
        value = _modifier_value(modifier)

        # Item armor-class bonuses are already applied from inventory
        # grantedModifiers to the correct item/gear, so do not count them again.
        if group == "item" and subtype == "armor-class":
            continue

        if subtype in {
            "armor-class",
            "armored-armor-class",
            "unarmored-armor-class",
            "dual-wield-armor-class",
            "ac-max-dex-armored-modifier",
            "ac-max-dex-modifier",
            "minimum-base-armor",
            "unarmored-dex-ac-bonus",
        } and not _restriction_is_unconditional(modifier):
            conditional.append(deepcopy(modifier))
            continue

        if modifier_type == "bonus":
            if subtype == "armor-class" and value is not None:
                generic += value
            elif armored and subtype == "armored-armor-class" and value is not None:
                generic += value
            elif not armored and subtype == "unarmored-armor-class" and value is not None:
                generic += value
            # dual-wield is applied only after checking characterValues.
        elif modifier_type == "set":
            if armored and subtype == "ac-max-dex-armored-modifier" and value is not None:
                medium_dex_cap = max(medium_dex_cap, int(value))
            elif not armored and subtype == "ac-max-dex-modifier" and value is not None:
                unarmored_dex_cap = min(unarmored_dex_cap, int(value))
            elif not armored and subtype == "unarmored-armor-class":
                unarmored_sets.append(deepcopy(modifier))
            elif not armored and subtype == "minimum-base-armor":
                minimum_base_armor.append(deepcopy(modifier))
        elif (
            not armored
            and modifier_type == "ignore"
            and subtype == "unarmored-dex-ac-bonus"
        ):
            ignore_unarmored_dex = True

    return {
        "generic": generic,
        "conditional": conditional,
        "medium_dex_cap": medium_dex_cap,
        "unarmored_dex_cap": unarmored_dex_cap,
        "ignore_unarmored_dex": ignore_unarmored_dex,
        "unarmored_sets": unarmored_sets,
        "minimum_base_armor": minimum_base_armor,
    }


def _unarmored_special_candidates(state, ability_scores, dex_mod):
    candidates = []

    # minimum-base-armor is race-specific in DDB (some add Dexterity, some do
    # not). Without the race-specific formula, guessing would be unsafe.
    if state["minimum_base_armor"]:
        return [], ["minimum-base-armor-unresolved"]

    if state["ignore_unarmored_dex"] and not state["unarmored_sets"]:
        return [], ["unarmored-dex-rule-unresolved"]

    effective_dex = 0 if state["ignore_unarmored_dex"] else max(
        0,
        min(dex_mod, state["unarmored_dex_cap"]),
    )

    for modifier in state["unarmored_sets"]:
        extra = _modifier_value(modifier) or 0
        stat_id = modifier.get("statId")
        stat_mod = 0

        if stat_id not in (None, 0):
            ability = ABILITY_IDS.get(stat_id)
            if not ability:
                return [], ["unarmored-stat-unresolved"]
            stat_mod = ability_modifier(ability_scores.get(ability))
            if stat_mod is None:
                return [], ["unarmored-stat-unresolved"]

        candidates.append(
            10 + effective_dex + stat_mod + extra + state["generic"]
        )

    return candidates, []


def _dual_wield_ac_bonus(sheet, dual_wield_ids):
    if not dual_wield_ids:
        return 0

    equipped_ids = {
        str(item.get("source_id"))
        for item in getattr(sheet, "equipment", []) or []
        if isinstance(item, dict)
        and item.get("equipped")
        and item.get("source_id") not in (None, "")
    }
    if not (equipped_ids & dual_wield_ids):
        return 0

    total = 0
    for _group, modifier in _iter_active_modifiers(sheet):
        if (
            modifier.get("type") == "bonus"
            and modifier.get("subType") == "dual-wield-armor-class"
            and _restriction_is_unconditional(modifier)
        ):
            value = _modifier_value(modifier)
            if value is not None:
                total += value
    return total


def _calculate_armor_class(sheet, ability_scores):
    override, custom_bonus, dual_wield_ids = _ac_character_values(sheet)
    if override is not None:
        return override, []

    dex_mod = ability_modifier(ability_scores.get("dexterity"))
    if dex_mod is None:
        return None, ["dexterity-unresolved"]

    all_equipment = [
        item
        for item in (getattr(sheet, "equipment", []) or [])
        if isinstance(item, dict)
    ]
    equipped_armor_items = [
        item
        for item in all_equipment
        if item.get("equipped") and item.get("item_type") == "Armor"
    ]
    shields = [
        item for item in equipped_armor_items if item.get("armor_type_id") == 4
    ]
    armors = [
        item for item in equipped_armor_items if item.get("armor_type_id") != 4
    ]

    # Worn non-armor gear such as protection items can grant flat AC.
    gear_bonus = sum(
        _equipment_ac_bonus(item)
        for item in all_equipment
        if item.get("item_type") != "Armor"
    )

    candidates = []
    unresolved = []

    if not armors:
        state = _ac_modifier_state(sheet, armored=False)
        if state["conditional"]:
            return None, state["conditional"]

        # Standard unarmored AC remains a valid candidate unless a source rule
        # explicitly says Dexterity must be ignored in a way we cannot model.
        if not state["ignore_unarmored_dex"]:
            candidates.append(10 + dex_mod + state["generic"])

        special, special_unresolved = _unarmored_special_candidates(
            state,
            ability_scores,
            dex_mod,
        )
        candidates.extend(special)
        unresolved.extend(special_unresolved)

        if not candidates and unresolved:
            return None, unresolved
    else:
        state = _ac_modifier_state(sheet, armored=True)
        if state["conditional"]:
            return None, state["conditional"]

        for armor in armors:
            base_ac = _number(armor.get("armor_class"))
            armor_type = armor.get("armor_type_id")
            if base_ac is None or armor_type not in {1, 2, 3}:
                unresolved.append("equipped-armor-metadata-unresolved")
                continue

            base_ac += _equipment_ac_bonus(armor)

            if armor_type == 1:
                value = base_ac + dex_mod
            elif armor_type == 2:
                value = base_ac + min(dex_mod, state["medium_dex_cap"])
            else:
                value = base_ac

            candidates.append(value + state["generic"])

        if not candidates:
            return None, unresolved or ["equipped-armor-metadata-unresolved"]

    shield_bonus = 0
    for shield in shields:
        base = _number(shield.get("armor_class"))
        if base is None:
            return None, ["shield-ac-unresolved"]
        shield_bonus = max(
            shield_bonus,
            base + _equipment_ac_bonus(shield),
        )

    dual_wield_bonus = _dual_wield_ac_bonus(sheet, dual_wield_ids)
    return (
        max(candidates)
        + shield_bonus
        + gear_bonus
        + custom_bonus
        + dual_wield_bonus,
        unresolved,
    )


def _calculate_initiative(sheet, ability_scores):
    dex_mod = ability_modifier(ability_scores.get("dexterity"))
    if dex_mod is None:
        return None

    bonus = 0
    for _group, modifier in _iter_active_modifiers(sheet):
        if (
            modifier.get("type") == "bonus"
            and modifier.get("subType") in {"initiative", "initiative-score"}
            and _restriction_is_unconditional(modifier)
        ):
            value = _modifier_value(modifier)
            if value is not None:
                bonus += value
    return dex_mod + bonus


def _spell_bonus(sheet, class_name, *, dc):
    class_key = _text(class_name).strip().casefold().replace(" ", "-")
    if dc:
        accepted = {"spell-save-dc"}
        if class_key:
            accepted.add(f"{class_key}-spell-save-dc")
    else:
        accepted = {"spell-attacks"}
        if class_key:
            accepted.add(f"{class_key}-spell-attacks")

    total = 0
    for _group, modifier in _iter_active_modifiers(sheet):
        if modifier.get("type") != "bonus":
            continue
        if modifier.get("subType") not in accepted:
            continue
        if not _restriction_is_unconditional(modifier):
            continue
        value = _modifier_value(modifier)
        if value is not None:
            total += value
    return total


def _calculate_spellcasting(sheet, ability_scores, proficiency_bonus):
    spellcasting = deepcopy(getattr(sheet, "spellcasting", {}) or {})
    rows = []
    seen = set()

    for item in _list(spellcasting.get("class_abilities")):
        if not isinstance(item, dict):
            continue
        ability_name = item.get("ability_name")
        if ability_name not in ABILITY_NAMES:
            continue

        key = (
            item.get("class_name"),
            item.get("subclass_name"),
            ability_name,
        )
        if key in seen:
            continue
        seen.add(key)

        mod = ability_modifier(ability_scores.get(ability_name))
        save_dc = None
        attack_bonus = None

        if mod is not None and proficiency_bonus is not None:
            class_name = item.get("class_name", "")
            save_dc = (
                8
                + proficiency_bonus
                + mod
                + _spell_bonus(sheet, class_name, dc=True)
            )
            attack_bonus = (
                proficiency_bonus
                + mod
                + _spell_bonus(sheet, class_name, dc=False)
            )

        rows.append(
            {
                "class_name": item.get("class_name", ""),
                "subclass_name": item.get("subclass_name", ""),
                "ability_id": item.get("ability_id"),
                "ability_name": ability_name,
                "ability_modifier": mod,
                "save_dc": save_dc,
                "attack_bonus": attack_bonus,
            }
        )

    spellcasting["class_calculations"] = rows

    resolved_pairs = {
        (row["save_dc"], row["attack_bonus"])
        for row in rows
        if row["save_dc"] is not None and row["attack_bonus"] is not None
    }
    if len(resolved_pairs) == 1:
        save_dc, attack_bonus = next(iter(resolved_pairs))
        spellcasting["save_dc"] = save_dc
        spellcasting["attack_bonus"] = attack_bonus
    else:
        # Multiclass characters may have different casting abilities/bonuses.
        # Keep the per-class final numbers instead of inventing one global pair.
        spellcasting["save_dc"] = None
        spellcasting["attack_bonus"] = None

    return spellcasting


def _remove_stage1_placeholder_warnings(sheet):
    kept = []
    for warning in getattr(sheet, "warnings", []) or []:
        if any(_text(warning).startswith(prefix) for prefix in _STAGE1_WARNING_PREFIXES):
            continue
        kept.append(warning)
    sheet.warnings = kept


def apply_stage2_calculations(sheet):
    """Mutate and return a CharacterSheet using only preserved DDB source facts."""
    _remove_stage1_placeholder_warnings(sheet)

    sheet.total_level = _total_level(sheet)
    sheet.proficiency_bonus = proficiency_bonus_for_level(sheet.total_level)

    scores, unresolved_global, unresolved_abilities = _calculate_ability_scores(sheet)
    sheet.ability_scores = scores

    if (
        unresolved_global
        or unresolved_abilities
        or any(scores.get(name) is None for name in ABILITY_NAMES)
    ):
        _warn_once(
            sheet,
            "2단계 계산기가 일부 능력치 선택/보정을 확정하지 못했습니다. "
            "추정값을 넣지 않고 해당 능력치를 null로 유지합니다.",
        )

    sheet.hp, sheet.max_hp, unresolved_hp = _calculate_hp(
        sheet,
        sheet.total_level,
        sheet.ability_scores,
    )
    if unresolved_hp or sheet.hp is None or sheet.max_hp is None:
        _warn_once(
            sheet,
            "2단계 계산기가 최종 HP를 확정하지 못했습니다. "
            "원천값이 부족하거나 해석하지 않은 HP 보정이 있습니다.",
        )

    sheet.temp_hp = _number(
        _dict(sheet.calculation_inputs).get("temporary_hit_points")
    )
    sheet.initiative = _calculate_initiative(sheet, sheet.ability_scores)

    sheet.armor_class, unresolved_ac = _calculate_armor_class(
        sheet,
        sheet.ability_scores,
    )
    if unresolved_ac or sheet.armor_class is None:
        _warn_once(
            sheet,
            "2단계 계산기가 AC를 안전하게 확정하지 못했습니다. "
            "지원하지 않는 방어도 보정 또는 장비 메타데이터가 있습니다.",
        )

    sheet.spellcasting = _calculate_spellcasting(
        sheet,
        sheet.ability_scores,
        sheet.proficiency_bonus,
    )

    # Movement source is already concrete DDB data. More complicated movement
    # bonus/replacement rules are deliberately not guessed in Stage 2.
    if not getattr(sheet, "speed", None):
        sheet.speed = deepcopy(
            _dict(sheet.calculation_inputs).get("race_movement") or {}
        )

    return sheet

# runtime-integrity-v2.6 AC hook
from .runtime_integrity_v26 import install_ac_integrity as _install_ac_integrity_v26
_calculate_armor_class = _install_ac_integrity_v26(
    _calculate_armor_class,
    globals(),
)
