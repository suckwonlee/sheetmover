"""Conservative D&D Beyond response normalization.

Stage 1 expands the platform-neutral model and preserves all source inputs
needed by later calculation/mapping stages.  It intentionally does not try to
fully calculate final ability scores, HP, AC, skills, attacks or resources.
"""
from copy import deepcopy
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .models import CharacterSheet

ABILITIES = {
    1: "strength",
    2: "dexterity",
    3: "constitution",
    4: "intelligence",
    5: "wisdom",
    6: "charisma",
}


def source_character_id(url):
    parsed = urlparse(url)
    match = re.fullmatch(r"/characters/(\d+)/?", parsed.path)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"www.dndbeyond.com", "dndbeyond.com"}
        or not match
    ):
        raise ValueError(
            "https://www.dndbeyond.com/characters/숫자 형식의 원본 URL을 입력하세요."
        )
    return match.group(1)


def _number(value):
    return value if type(value) in (int, float) else None


def _dict(value):
    return value if isinstance(value, dict) else {}


def _list(value):
    return value if isinstance(value, list) else []


def _definition(entry, kind):
    if not isinstance(entry, dict):
        return None
    definition = entry.get("definition") or entry
    if not isinstance(definition, dict) or not definition.get("name"):
        return None
    name = str(definition["name"])
    return {
        "source_id": str(entry.get("id") or definition.get("id") or ""),
        "definition_id": str(definition.get("id") or ""),
        "kind": kind,
        "name": name,
        "original_name": name,
        "description": definition.get("description")
        or definition.get("snippet")
        or "",
    }


def _normalize_race(data):
    race = _dict(data.get("race"))
    if not race:
        return {}

    name = (
        race.get("fullName")
        or race.get("subRaceShortName")
        or race.get("baseRaceName")
        or race.get("name")
        or ""
    )
    return {
        "source_id": str(race.get("id") or ""),
        "name": str(name),
        "original_name": str(name),
        "base_name": str(race.get("baseRaceName") or ""),
        "subrace_name": str(race.get("subRaceShortName") or ""),
        "description": race.get("description") or "",
        "is_homebrew": bool(race.get("isHomebrew", False)),
        "size_id": race.get("sizeId"),
        "movement_source": deepcopy(race.get("weightSpeeds") or {}),
    }


def _normalize_background(data):
    background = _dict(_dict(data.get("background")).get("definition"))
    if not background:
        return {}

    name = str(background.get("name") or "")
    feature_name = str(background.get("featureName") or "")
    return {
        "source_id": str(background.get("id") or ""),
        "name": name,
        "original_name": name,
        "description": background.get("description") or "",
        "feature_name": feature_name,
        "original_feature_name": feature_name,
        "feature_description": background.get("featureDescription") or "",
    }


def _normalize_class(entry):
    if not isinstance(entry, dict):
        return None

    definition = _dict(entry.get("definition"))
    subclass = _dict(entry.get("subclassDefinition"))
    if not definition and not entry.get("level"):
        return None

    name = str(definition.get("name") or "")
    subclass_name = str(subclass.get("name") or "")
    return {
        "source_id": str(entry.get("id") or definition.get("id") or ""),
        "definition_id": str(definition.get("id") or ""),
        "name": name,
        "original_name": name,
        "level": _number(entry.get("level")),
        "hit_die": _number(definition.get("hitDice")),
        "spellcasting_ability_id": definition.get("spellCastingAbilityId"),
        "subclass_id": str(subclass.get("id") or ""),
        "subclass_name": subclass_name,
        "original_subclass_name": subclass_name,
        "is_homebrew": bool(
            definition.get("isHomebrew", False) or subclass.get("isHomebrew", False)
        ),
    }


def _normalize_inventory_item(entry, kind="equipment"):
    item = _definition(entry, kind)
    if not item:
        return None

    definition = _dict(entry.get("definition"))
    item.update(
        quantity=entry.get("quantity"),
        equipped=entry.get("equipped"),
        attuned=entry.get("isAttuned"),
        weight=definition.get("weight"),
        item_type=definition.get("filterType") or definition.get("type"),
        rarity=definition.get("rarity"),
        magic=definition.get("magic"),
        armor_class=definition.get("armorClass"),
        damage=deepcopy(definition.get("damage")),
        damage_type=definition.get("damageType"),
        range=deepcopy(definition.get("range")),
        properties=deepcopy(definition.get("properties") or []),
    )
    return item


def _normalize_spell(entry, source_kind="spell"):
    item = _definition(entry, source_kind)
    if not item:
        return None

    definition = _dict(entry.get("definition"))
    item.update(
        level=definition.get("level"),
        prepared=entry.get("prepared"),
        always_prepared=entry.get("alwaysPrepared"),
        uses_spell_slot=entry.get("usesSpellSlot"),
        casting_time=deepcopy(definition.get("activation")),
        range=deepcopy(definition.get("range")),
        duration=deepcopy(definition.get("duration")),
        components=deepcopy(definition.get("components")),
        components_description=definition.get("componentsDescription") or "",
        school=definition.get("school"),
        ritual=definition.get("ritual"),
        concentration=definition.get("concentration"),
        save_dc_ability_id=definition.get("saveDcAbilityId"),
        attack_type=definition.get("attackType"),
        damage_effect=deepcopy(definition.get("damageEffect")),
    )
    return item


def _normalize_feature(entry, kind):
    item = _definition(entry, kind)
    if not item:
        return None
    definition = _dict(entry.get("definition") or entry)
    item.update(
        required_level=definition.get("requiredLevel"),
        limited_use=deepcopy(entry.get("limitedUse") or definition.get("limitedUse")),
    )
    return item


def _normalize_action(entry, kind):
    item = _definition(entry, kind)
    if not item:
        return None

    definition = _dict(entry.get("definition") or entry)
    item.update(
        activation=deepcopy(definition.get("activation")),
        range=deepcopy(definition.get("range")),
        attack_type=definition.get("attackType"),
        ability_modifier_stat_id=definition.get("abilityModifierStatId"),
        dice=deepcopy(definition.get("dice")),
        damage_type_id=definition.get("damageTypeId"),
        limited_use=deepcopy(entry.get("limitedUse") or definition.get("limitedUse")),
    )
    return item


def _modifier_entries(data):
    result = []
    for group_name, group in _dict(data.get("modifiers")).items():
        for modifier in _list(group):
            if not isinstance(modifier, dict):
                continue
            item = deepcopy(modifier)
            item["source_group"] = group_name
            result.append(item)
    return result


def _proficiency_label(modifier):
    label = modifier.get("friendlySubtypeName") or modifier.get("subType") or ""
    if not label:
        return ""
    if modifier.get("type") == "expertise":
        return f"{label} (expertise)"
    return str(label)


def _collect_resources(*collections):
    resources = []
    seen = set()
    for collection in collections:
        for item in collection:
            limited = item.get("limited_use")
            if not isinstance(limited, dict):
                continue
            identity = (
                item.get("source_id"),
                item.get("kind"),
                item.get("original_name"),
            )
            if identity in seen:
                continue
            seen.add(identity)
            resources.append(
                {
                    "source_id": item.get("source_id", ""),
                    "kind": item.get("kind", ""),
                    "name": item.get("name", ""),
                    "original_name": item.get("original_name", ""),
                    "limited_use": deepcopy(limited),
                }
            )
    return resources



DDB_CHARACTER_API = (
    "https://character-service.dndbeyond.com/"
    "character/v5/character/{character_id}?includeCustomItems=true"
)


def fetch_character(source_url, timeout=20, opener=None):
    """Fetch a D&D Beyond character using only its character URL."""
    character_id = source_character_id(source_url)
    endpoint = DDB_CHARACTER_API.format(character_id=character_id)
    request = Request(
        endpoint,
        headers={
            "Accept": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/152.0.0.0 Safari/537.36"
            ),
            "Referer": source_url,
        },
        method="GET",
    )

    open_request = opener or urlopen
    try:
        with open_request(request, timeout=timeout) as response:
            status = getattr(response, "status", 200)
            body = response.read()
    except HTTPError as exc:
        if exc.code in {401, 403, 404}:
            raise RuntimeError(
                "D&D Beyond 캐릭터 데이터를 가져올 수 없습니다. "
                "캐릭터가 링크로 조회 가능한 상태인지 확인하고 다시 시도하세요. "
                f"(HTTP {exc.code})"
            ) from exc
        raise RuntimeError(
            f"D&D Beyond 캐릭터 서비스가 HTTP {exc.code} 오류를 반환했습니다."
        ) from exc
    except URLError as exc:
        raise RuntimeError(
            "D&D Beyond에 연결하지 못했습니다. 인터넷 연결을 확인하세요."
        ) from exc
    except TimeoutError as exc:
        raise RuntimeError(
            "D&D Beyond 응답 시간이 초과되었습니다."
        ) from exc

    if status != 200:
        raise RuntimeError(
            f"D&D Beyond 캐릭터 서비스가 HTTP {status} 오류를 반환했습니다."
        )

    try:
        import json
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "D&D Beyond 응답을 JSON으로 읽지 못했습니다."
        ) from exc

    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise RuntimeError(
            "D&D Beyond 응답에 캐릭터 데이터가 없습니다."
        )
    if str(data.get("id")) != character_id:
        raise RuntimeError(
            "요청한 D&D Beyond 캐릭터 ID와 응답 ID가 일치하지 않습니다."
        )
    if not isinstance(data.get("name"), str) or not data["name"].strip():
        raise RuntimeError(
            "D&D Beyond 응답에 캐릭터 이름이 없습니다."
        )
    return data

def normalize_character(data):
    if not isinstance(data, dict):
        raise ValueError("D&D Beyond 응답 형식이 올바르지 않습니다.")
    if not isinstance(data.get("name"), str) or not data["name"].strip():
        raise ValueError("원본 응답에 캐릭터 이름이 없습니다.")

    sheet = CharacterSheet(
        source_id=str(data.get("id") or ""),
        name=data["name"],
        race=_normalize_race(data),
        background=_normalize_background(data),
        experience=_number(data.get("currentXp")),
        alignment=str(data.get("alignment") or ""),
        temp_hp=_number(data.get("temporaryHitPoints")),
        death_saves=deepcopy(_dict(data.get("deathSaves"))),
        inspiration=data.get("inspiration")
        if isinstance(data.get("inspiration"), (bool, int))
        else None,
        currencies=deepcopy(_dict(data.get("currencies"))),
        character_traits=deepcopy(_dict(data.get("traits"))),
        notes=deepcopy(_dict(data.get("notes"))),
    )

    # Classes are source facts. total_level/proficiency_bonus remain for stage 2.
    for entry in _list(data.get("classes")):
        normalized = _normalize_class(entry)
        if normalized:
            sheet.classes.append(normalized)
            if normalized.get("level") is not None and normalized.get("hit_die"):
                sheet.hit_dice.append(
                    {
                        "class_name": normalized.get("name", ""),
                        "die": normalized.get("hit_die"),
                        "count_source": normalized.get("level"),
                    }
                )

    modifiers = _modifier_entries(data)
    sheet.proficiency_entries = [
        deepcopy(m)
        for m in modifiers
        if m.get("type") in {"proficiency", "expertise"}
    ]
    sheet.proficiencies = sorted(
        {
            label
            for label in (_proficiency_label(m) for m in sheet.proficiency_entries)
            if label
        }
    )

    # These are only source hints in stage 1. Accurate skill/save interpretation
    # and final bonuses are implemented in stage 11.
    for modifier in sheet.proficiency_entries:
        subtype = str(modifier.get("subType") or "")
        label = str(
            modifier.get("friendlySubtypeName")
            or modifier.get("subType")
            or ""
        )
        if subtype.endswith("-saving-throws") and label:
            sheet.saving_throw_proficiencies.append(label)
        elif subtype.endswith("-skill") and label:
            sheet.skill_proficiencies.append(
                {
                    "name": label,
                    "subtype": subtype,
                    "type": modifier.get("type"),
                }
            )

    for modifier in modifiers:
        modifier_type = str(modifier.get("type") or "")
        label = str(
            modifier.get("friendlySubtypeName")
            or modifier.get("subType")
            or ""
        )
        if modifier_type == "language" and label:
            sheet.languages.append(label)
        if modifier_type in {"resistance", "immunity", "vulnerability"}:
            sheet.defenses.append(
                {
                    "type": modifier_type,
                    "name": label,
                    "subtype": modifier.get("subType"),
                }
            )
        if modifier_type == "sense" and label:
            sheet.senses.append(
                {
                    "name": label,
                    "subtype": modifier.get("subType"),
                    "value": modifier.get("value"),
                }
            )

    sheet.languages = sorted(set(sheet.languages))

    # Stage 2 will turn these source values into final scores.
    base_stats = {
        s.get("id"): _number(s.get("value"))
        for s in _list(data.get("stats"))
        if isinstance(s, dict)
    }
    bonus_stats = {
        s.get("id"): _number(s.get("value"))
        for s in _list(data.get("bonusStats"))
        if isinstance(s, dict)
    }
    override_stats = {
        s.get("id"): _number(s.get("value"))
        for s in _list(data.get("overrideStats"))
        if isinstance(s, dict)
    }

    has_score_modifiers = bool(modifiers or data.get("characterValues"))
    for key, name in ABILITIES.items():
        if override_stats.get(key) is not None:
            sheet.ability_scores[name] = override_stats[key]
        elif base_stats.get(key) is not None and not has_score_modifiers:
            sheet.ability_scores[name] = (
                base_stats[key] + (bonus_stats.get(key) or 0)
            )
        else:
            sheet.ability_scores[name] = None

    sheet.calculation_inputs = {
        "stats": deepcopy(_list(data.get("stats"))),
        "bonus_stats": deepcopy(_list(data.get("bonusStats"))),
        "override_stats": deepcopy(_list(data.get("overrideStats"))),
        "modifiers": deepcopy(_dict(data.get("modifiers"))),
        "character_values": deepcopy(_list(data.get("characterValues"))),
        "base_hit_points": _number(data.get("baseHitPoints")),
        "bonus_hit_points": _number(data.get("bonusHitPoints")),
        "override_hit_points": _number(data.get("overrideHitPoints")),
        "removed_hit_points": _number(data.get("removedHitPoints")),
        "temporary_hit_points": _number(data.get("temporaryHitPoints")),
        "class_levels": [
            {
                "name": cls.get("name", ""),
                "level": cls.get("level"),
                "hit_die": cls.get("hit_die"),
            }
            for cls in sheet.classes
        ],
        "race_movement": deepcopy(sheet.race.get("movement_source", {})),
        "pact_magic": deepcopy(data.get("pactMagic")),
        "spell_slots": deepcopy(data.get("spellSlots")),
    }

    if any(value is None for value in sheet.ability_scores.values()):
        sheet.warnings.append(
            "일부 최종 능력치는 2단계 계산기가 필요하여 미확인(null)으로 표시했습니다."
        )

    # Preserve the old explicit override behavior until the stage 2 HP calculator
    # is implemented. Never pretend baseHitPoints alone is a final max HP value.
    sheet.max_hp = _number(data.get("overrideHitPoints"))
    if sheet.max_hp is not None:
        removed = _number(data.get("removedHitPoints"))
        if removed is not None:
            sheet.hp = max(0, sheet.max_hp - removed)

    if sheet.hp is None or sheet.max_hp is None:
        sheet.warnings.append(
            "최종 HP는 2단계 계산기가 필요하여, 명시적인 최대 HP 재정의 값이 없으면 "
            "미확인(null)으로 표시합니다."
        )

    # Movement is preserved but not flattened/calculated yet.
    sheet.speed = deepcopy(sheet.race.get("movement_source", {}))

    for entry in _list(data.get("inventory")):
        item = _normalize_inventory_item(entry)
        if item:
            sheet.equipment.append(item)

    for entry in _list(data.get("customItems")):
        item = _normalize_inventory_item(entry, "custom_equipment")
        if item:
            sheet.equipment.append(item)

    spell_groups = list(_dict(data.get("spells")).values())
    spell_groups += [
        _list(character_class.get("spells"))
        for character_class in _list(data.get("classSpells"))
        if isinstance(character_class, dict)
    ]
    for group in spell_groups:
        for entry in _list(group):
            item = _normalize_spell(entry)
            if item:
                sheet.spells.append(item)

    feature_groups = [
        (_list(_dict(data.get("race")).get("racialTraits")), "racial_trait"),
        (_list(data.get("feats")), "feat"),
    ]
    for character_class in _list(data.get("classes")):
        if isinstance(character_class, dict):
            class_level = _number(character_class.get("level"))
            active_class_features = []

            for entry in _list(character_class.get("classFeatures")):
                definition = _dict(
                    _dict(entry).get("definition") or entry
                )
                required_level = _number(
                    definition.get("requiredLevel")
                )

                # D&D Beyond returns future class progression too. Only reached
                # features belong to the current character sheet.
                if (
                    class_level is None
                    or required_level is None
                    or required_level <= class_level
                ):
                    active_class_features.append(entry)

            feature_groups.append(
                (active_class_features, "class_feature")
            )

    background = sheet.background
    if background.get("feature_name"):
        sheet.features.append(
            {
                "source_id": background.get("source_id", ""),
                "definition_id": background.get("source_id", ""),
                "kind": "background",
                "name": background["feature_name"],
                "original_name": background["feature_name"],
                "description": background.get("feature_description") or "",
                "required_level": None,
                "limited_use": None,
            }
        )

    for entries, kind in feature_groups:
        for entry in entries:
            item = _normalize_feature(entry, kind)
            if item:
                sheet.features.append(item)

    # D&D Beyond can return stale/orphan actions whose componentId no
    # longer belongs to any active feature on the character.  Do not expose
    # those actions to later translation/Roll20 stages.
    active_action_components = {
        "class": {
            str(item.get("definition_id"))
            for item in sheet.features
            if item.get("kind") == "class_feature"
            and item.get("definition_id") not in (None, "")
        },
        "feat": {
            str(item.get("definition_id"))
            for item in sheet.features
            if item.get("kind") == "feat"
            and item.get("definition_id") not in (None, "")
        },
        "race": {
            str(item.get("definition_id"))
            for item in sheet.features
            if item.get("kind") == "racial_trait"
            and item.get("definition_id") not in (None, "")
        },
        "background": {
            str(item.get("definition_id"))
            for item in sheet.features
            if item.get("kind") == "background"
            and item.get("definition_id") not in (None, "")
        },
        "item": {
            str(identity)
            for item in sheet.equipment
            for identity in (
                item.get("source_id"),
                item.get("definition_id"),
            )
            if identity not in (None, "")
        },
    }

    skipped_orphan_actions = []
    for group_name, group in _dict(data.get("actions")).items():
        allowed_components = active_action_components.get(group_name)

        for entry in _list(group):
            component_id = _dict(entry).get("componentId")

            if (
                allowed_components is not None
                and component_id not in (None, "")
                and str(component_id) not in allowed_components
            ):
                skipped_orphan_actions.append(
                    str(_dict(entry).get("name") or component_id)
                )
                continue

            item = _normalize_action(entry, f"action:{group_name}")
            if item:
                sheet.actions.append(item)

    if skipped_orphan_actions:
        sheet.warnings.append(
            "D&D Beyond 원본에 있었지만 현재 활성 특성과 연결되지 않은 "
            f"행동 {len(skipped_orphan_actions)}개를 제외했습니다: "
            + ", ".join(skipped_orphan_actions[:8])
        )

    for entry in _list(data.get("customActions")):
        item = _normalize_action(entry, "custom_action")
        if item:
            sheet.actions.append(item)

    sheet.resources = _collect_resources(
        sheet.features,
        sheet.actions,
        sheet.spells,
    )

    # Spellcasting facts are preserved, while DC/attack bonus/slots are stage 2.
    sheet.spellcasting = {
        "class_abilities": [
            {
                "class_name": cls.get("name", ""),
                "ability_id": cls.get("spellcasting_ability_id"),
            }
            for cls in sheet.classes
            if cls.get("spellcasting_ability_id") is not None
        ],
        "spell_slots_source": deepcopy(data.get("spellSlots")),
        "pact_magic_source": deepcopy(data.get("pactMagic")),
        "save_dc": None,
        "attack_bonus": None,
    }

    sheet.warnings.append(
        "1단계 데이터 모델 확장 상태입니다. 능력치·HP·레벨·AC·숙련 보너스 등 "
        "최종 계산값은 2단계에서 확정합니다."
    )
    sheet.warnings.append(
        "공격, 스킬/내성/전문화, 클래스 자원의 Roll20 변환은 각각 10·11·12단계에서 "
        "구현합니다."
    )
    return sheet
