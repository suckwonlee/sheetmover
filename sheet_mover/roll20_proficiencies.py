"""Stage 11 Roll20 Legacy OGL5e proficiency writer.

Scope:
- standard skill proficiencies
- Expertise
- saving throw proficiencies
- tool proficiencies
- languages
- weapon proficiencies
- armor/shield proficiencies
- other non-skill proficiencies when present

Roll20 placement:
- skills/saves: existing core-sheet attributes
- tools/custom skills: repeating_tool
- languages/weapons/armor/other: repeating_proficiencies

The writer preserves unrelated/manual repeating rows and only removes stale
Sheet Mover rows recorded by the previous Stage 11 report.
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
    _save_json,
    _snapshot,
    _text,
    _verify,
)


STAGE11_VERSION = "2026-10-06-stage11-roll20-proficiencies-v2.1-reporder"
PB_CHECKED = "(@{pb})"
ROW_PREFIX = "-SM"
ROW_HASH_LENGTH = 17

QUERY_ABILITY = (
    "?{Attribute?|"
    "Strength,@{strength_mod}|"
    "Dexterity,@{dexterity_mod}|"
    "Constitution,@{constitution_mod}|"
    "Intelligence,@{intelligence_mod}|"
    "Wisdom,@{wisdom_mod}|"
    "Charisma,@{charisma_mod}}"
)

SKILL_TO_ABILITY = {
    "acrobatics": "dexterity",
    "animal_handling": "wisdom",
    "arcana": "intelligence",
    "athletics": "strength",
    "deception": "charisma",
    "history": "intelligence",
    "insight": "wisdom",
    "intimidation": "charisma",
    "investigation": "intelligence",
    "medicine": "wisdom",
    "nature": "intelligence",
    "perception": "wisdom",
    "performance": "charisma",
    "persuasion": "charisma",
    "religion": "intelligence",
    "sleight_of_hand": "dexterity",
    "stealth": "dexterity",
    "survival": "wisdom",
}

ABILITY_ORDER = (
    "strength",
    "dexterity",
    "constitution",
    "intelligence",
    "wisdom",
    "charisma",
)

SKILL_CHECKED_VALUE = {
    skill: f"(@{{pb}}*@{{{skill}_type}})"
    for skill in SKILL_TO_ABILITY
}

SAVE_SUBTYPE_TO_ABILITY = {
    f"{ability}-saving-throws": ability
    for ability in ABILITY_ORDER
}

_EXPERTISE_TYPES = {
    "expertise",
    "double-proficiency",
    "double-proficiency-bonus",
    "double proficiency",
    "double proficiency bonus",
}

TOOL_FIELDS = (
    "options-flag",
    "toolname",
    "toolbonus_base",
    "toolattr_base",
    "tool_mod",
    "toolattr",
    "toolbonus",
    "toolbonus_display",
)

PROF_FIELDS = (
    "options-flag",
    "prof_type",
    "name",
)

ENTITY_TYPE_WEAPON = "660121713"
ENTITY_TYPE_ARMOR = "174869515"
ENTITY_TYPE_TOOL = "2103445194"


def _stable_row_id(seed: str) -> str:
    seed = _text(seed)
    if not seed:
        raise ValueError("반복행 seed가 비어 있습니다.")
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()
    row_id = ROW_PREFIX + digest[:ROW_HASH_LENGTH]
    if len(row_id) != 20 or "_" in row_id:
        raise AssertionError(f"잘못된 반복행 ID: {row_id}")
    return row_id


def tool_row_id(key: str) -> str:
    return _stable_row_id(f"stage11-tool:{key}")


def proficiency_row_id(prof_type: str, key: str) -> str:
    return _stable_row_id(f"stage11-prof:{prof_type}:{key}")


def _normalize_subtype(value: Any) -> str:
    value = _text(value).strip().casefold()
    return (
        value.replace("&", "and")
        .replace("'", "")
        .replace("-", "_")
        .replace(" ", "_")
    )


def _normalize_modifier_type(value: Any) -> str:
    return _text(value).strip().casefold().replace("_", "-").replace("  ", " ")


def _ability_modifier(score: Any) -> int:
    try:
        return (int(score) - 10) // 2
    except Exception:
        return 0


def _original_character(result_payload):
    return _dict(_dict(result_payload).get("original"))


def _translated_character(result_payload):
    return _dict(_dict(result_payload).get("translated"))


def _roll20_character(result_payload):
    return _dict(_dict(_dict(result_payload).get("roll20_payload")).get("character"))


def _translated_proficiency_map(result_payload):
    original = _original_character(result_payload)
    translated = _translated_character(result_payload)

    original_values = [_text(v) for v in _list(original.get("proficiencies"))]
    translated_values = [_text(v) for v in _list(translated.get("proficiencies"))]

    out = {}
    if len(original_values) == len(translated_values):
        for original_name, translated_name in zip(original_values, translated_values):
            if original_name:
                out[original_name.casefold()] = translated_name or original_name
    return out


def _translated_languages(result_payload):
    translated = _translated_character(result_payload)
    original = _original_character(result_payload)

    translated_values = [
        _text(v) for v in _list(translated.get("languages")) if _text(v)
    ]
    if translated_values:
        return translated_values

    return [
        _text(v) for v in _list(original.get("languages")) if _text(v)
    ]


def _skill_entries(result_payload):
    original = _original_character(result_payload)
    entries = []

    for entry in _list(original.get("skill_proficiencies")):
        entry = _dict(entry)
        subtype = _normalize_subtype(entry.get("subtype"))
        if subtype in SKILL_TO_ABILITY:
            entries.append({
                "skill": subtype,
                "type": _normalize_modifier_type(entry.get("type")),
                "source_group": _text(entry.get("source_group")),
                "component_id": _text(entry.get("component_id")),
            })

    for entry in _list(original.get("proficiency_entries")):
        entry = _dict(entry)
        subtype = _normalize_subtype(entry.get("subType"))
        if subtype not in SKILL_TO_ABILITY:
            continue
        entries.append({
            "skill": subtype,
            "type": _normalize_modifier_type(entry.get("type")),
            "source_group": _text(entry.get("source_group")),
            "component_id": _text(entry.get("componentId")),
        })

    return entries


def _save_entries(result_payload):
    original = _original_character(result_payload)
    result = set()

    for value in _list(original.get("saving_throw_proficiencies")):
        text = _text(value).strip().casefold()
        for ability in ABILITY_ORDER:
            if text == f"{ability} saving throws":
                result.add(ability)

    for entry in _list(original.get("proficiency_entries")):
        entry = _dict(entry)
        if _normalize_modifier_type(entry.get("type")) != "proficiency":
            continue
        raw_subtype = _text(entry.get("subType")).strip().casefold()
        ability = SAVE_SUBTYPE_TO_ABILITY.get(raw_subtype)
        if ability:
            result.add(ability)

    return result


def _skill_levels(result_payload):
    levels = {skill: 0 for skill in SKILL_TO_ABILITY}
    sources = {skill: [] for skill in SKILL_TO_ABILITY}

    for entry in _skill_entries(result_payload):
        skill = entry["skill"]
        modifier_type = entry["type"]

        if modifier_type in _EXPERTISE_TYPES or "expertise" in modifier_type:
            levels[skill] = max(levels[skill], 2)
        elif modifier_type == "proficiency":
            levels[skill] = max(levels[skill], 1)
        else:
            continue

        source = {
            "type": modifier_type,
            "source_group": entry["source_group"],
            "component_id": entry["component_id"],
        }
        if source not in sources[skill]:
            sources[skill].append(source)

    return levels, sources


def _entry_display_name(entry, translated_map):
    entry = _dict(entry)
    original_name = _text(entry.get("friendlySubtypeName"))
    if not original_name:
        original_name = _text(entry.get("subType"))
    return translated_map.get(original_name.casefold(), original_name)


def _entry_key(entry):
    entry = _dict(entry)
    subtype = _text(entry.get("subType"))
    friendly = _text(entry.get("friendlySubtypeName"))
    return _normalize_subtype(subtype or friendly)


def _classify_non_skill_entry(entry):
    entry = _dict(entry)
    modifier_type = _normalize_modifier_type(entry.get("type"))
    if modifier_type not in {"proficiency", *list(_EXPERTISE_TYPES)} and "expertise" not in modifier_type:
        return None

    subtype_raw = _text(entry.get("subType")).strip().casefold()
    subtype_norm = _normalize_subtype(entry.get("subType"))
    if subtype_norm in SKILL_TO_ABILITY:
        return None
    if subtype_raw in SAVE_SUBTYPE_TO_ABILITY:
        return None

    entity_type = _text(entry.get("entityTypeId"))
    friendly = _text(entry.get("friendlySubtypeName")).strip().casefold()

    if entity_type == ENTITY_TYPE_WEAPON:
        return "WEAPON"
    if entity_type == ENTITY_TYPE_ARMOR:
        return "ARMOR"
    if entity_type == ENTITY_TYPE_TOOL:
        return "TOOL"

    if "weapon" in friendly:
        return "WEAPON"
    if "armor" in friendly or "shield" in friendly:
        return "ARMOR"
    if any(token in friendly for token in (
        "tool", "kit", "instrument", "vehicle", "gaming set",
    )):
        return "TOOL"

    return "OTHER"


def _non_skill_proficiency_rows(result_payload, pb):
    original = _original_character(result_payload)
    translated_map = _translated_proficiency_map(result_payload)

    tool_by_key = {}
    other_rows = {}

    for entry in _list(original.get("proficiency_entries")):
        entry = _dict(entry)
        category = _classify_non_skill_entry(entry)
        if not category:
            continue

        key = _entry_key(entry)
        if not key:
            continue

        display_name = _entry_display_name(entry, translated_map)
        modifier_type = _normalize_modifier_type(entry.get("type"))
        is_expertise = (
            modifier_type in _EXPERTISE_TYPES
            or "expertise" in modifier_type
        )

        if category == "TOOL":
            current = tool_by_key.get(key)
            level = 2 if is_expertise else 1
            if current is None or level > current["proficiency_level"]:
                bonus_base = "(@{pb}*2)" if level == 2 else "(@{pb})"
                pb_value = pb * level
                tool_by_key[key] = {
                    "key": key,
                    "row_id": tool_row_id(key),
                    "name": display_name,
                    "original_name": _text(entry.get("friendlySubtypeName")),
                    "proficiency_level": level,
                    "expertise": level == 2,
                    "fields": {
                        "options-flag": "0",
                        "toolname": display_name,
                        "toolbonus_base": bonus_base,
                        "toolattr_base": QUERY_ABILITY,
                        "tool_mod": "0",
                        "toolattr": "QUERY",
                        "toolbonus": f"{QUERY_ABILITY}+{pb_value}",
                        "toolbonus_display": "?",
                    },
                }
            continue

        key2 = f"{category}:{key}"
        if key2 not in other_rows:
            other_rows[key2] = {
                "key": key,
                "row_id": proficiency_row_id(category, key),
                "prof_type": category,
                "name": display_name,
                "original_name": _text(entry.get("friendlySubtypeName")),
                "fields": {
                    "options-flag": "0",
                    "prof_type": category,
                    "name": display_name,
                },
            }

    # Languages are not represented as ordinary DDB proficiency modifiers.
    for index, language in enumerate(_translated_languages(result_payload)):
        key = _normalize_subtype(language) or f"language_{index}"
        row_key = f"LANGUAGE:{key}"
        if row_key in other_rows:
            continue
        other_rows[row_key] = {
            "key": key,
            "row_id": proficiency_row_id("LANGUAGE", key),
            "prof_type": "LANGUAGE",
            "name": language,
            "original_name": "",
            "fields": {
                "options-flag": "0",
                "prof_type": "LANGUAGE",
                "name": language,
            },
        }

    tools = sorted(tool_by_key.values(), key=lambda row: row["name"])
    order = {"LANGUAGE": 0, "WEAPON": 1, "ARMOR": 2, "OTHER": 3}
    profs = sorted(
        other_rows.values(),
        key=lambda row: (order.get(row["prof_type"], 9), row["name"]),
    )
    return tools, profs


def build_proficiency_plan(result_payload: dict[str, Any]) -> dict[str, Any]:
    roll20_payload = _dict(_dict(result_payload).get("roll20_payload"))
    character = _roll20_character(result_payload)

    source_character_id = _text(roll20_payload.get("source_character_id"))
    character_name = _text(character.get("name"))
    if not source_character_id:
        raise RuntimeError("roll20_payload.source_character_id가 없습니다.")
    if not character_name:
        raise RuntimeError("roll20_payload.character.name이 없습니다.")

    scores = _dict(character.get("ability_scores"))
    pb = int(character.get("proficiency_bonus") or 0)

    levels, skill_sources = _skill_levels(result_payload)
    save_proficiencies = _save_entries(result_payload)

    skills = []
    for skill, ability in SKILL_TO_ABILITY.items():
        level = levels[skill]
        ability_mod = _ability_modifier(scores.get(ability))
        bonus = ability_mod + (pb * level)

        skills.append({
            "skill": skill,
            "ability": ability,
            "proficiency_level": level,
            "proficient": level >= 1,
            "expertise": level >= 2,
            "bonus": bonus,
            "sources": skill_sources[skill],
            "attributes": {
                f"{skill}_prof": (
                    SKILL_CHECKED_VALUE[skill] if level >= 1 else "0"
                ),
                f"{skill}_type": "2" if level >= 2 else "1",
                f"{skill}_flat": "0",
                f"{skill}_bonus": str(bonus),
            },
        })

    saves = []
    for ability in ABILITY_ORDER:
        proficient = ability in save_proficiencies
        ability_mod = _ability_modifier(scores.get(ability))
        bonus = ability_mod + (pb if proficient else 0)

        saves.append({
            "ability": ability,
            "proficient": proficient,
            "bonus": bonus,
            "attributes": {
                f"{ability}_save_prof": PB_CHECKED if proficient else "0",
                f"{ability}_save_mod": "0",
                f"{ability}_save_bonus": str(bonus),
            },
        })

    tools, other_proficiencies = _non_skill_proficiency_rows(
        result_payload,
        pb,
    )

    return {
        "version": STAGE11_VERSION,
        "source_character_id": source_character_id,
        "character_name": character_name,
        "proficiency_bonus": pb,
        "skills": skills,
        "saves": saves,
        "tools": tools,
        "other_proficiencies": other_proficiencies,
        "skill_proficiency_count": sum(1 for row in skills if row["proficient"]),
        "expertise_count": sum(1 for row in skills if row["expertise"]),
        "save_proficiency_count": sum(1 for row in saves if row["proficient"]),
        "tool_proficiency_count": len(tools),
        "language_count": sum(
            1 for row in other_proficiencies
            if row["prof_type"] == "LANGUAGE"
        ),
        "weapon_proficiency_count": sum(
            1 for row in other_proficiencies
            if row["prof_type"] == "WEAPON"
        ),
        "armor_proficiency_count": sum(
            1 for row in other_proficiencies
            if row["prof_type"] == "ARMOR"
        ),
        "other_proficiency_count": sum(
            1 for row in other_proficiencies
            if row["prof_type"] == "OTHER"
        ),
        "desired_tool_order": [
            row["row_id"] for row in tools
        ],
        "desired_proficiency_order": [
            row["row_id"] for row in other_proficiencies
        ],
        "policy": {
            "skills_and_saves": "core_attributes",
            "tools": "repeating_tool",
            "languages_weapons_armor_other": "repeating_proficiencies",
            "proficiency_display_order": [
                "LANGUAGE",
                "WEAPON",
                "ARMOR",
                "OTHER",
            ],
            "preserve_manual_repeating_rows": True,
            "manual_rows_after_managed_rows": True,
            "remove_only_stale_previous_stage11_rows": True,
            "stable_row_ids": True,
            "tool_ability": "query_each_roll",
            "write_visible_skill_save_bonus": True,
        },
    }


def _tool_attr_name(row_id, field):
    if field not in TOOL_FIELDS:
        raise ValueError(f"허용되지 않은 tool 필드: {field}")
    return f"repeating_tool_{row_id}_{field}"


def _prof_attr_name(row_id, field):
    if field not in PROF_FIELDS:
        raise ValueError(f"허용되지 않은 proficiency 필드: {field}")
    return f"repeating_proficiencies_{row_id}_{field}"


def _tool_attributes(row):
    return {
        _tool_attr_name(row["row_id"], field): {
            "current": _text(_dict(row.get("fields")).get(field)),
            "max": "",
        }
        for field in TOOL_FIELDS
    }


def _prof_attributes(row):
    return {
        _prof_attr_name(row["row_id"], field): {
            "current": _text(_dict(row.get("fields")).get(field)),
            "max": "",
        }
        for field in PROF_FIELDS
    }


def plan_attributes(plan):
    attrs = {}

    for row in _list(plan.get("skills")):
        for name, current in _dict(row.get("attributes")).items():
            if name in attrs:
                raise RuntimeError(f"기술 attribute 중복: {name}")
            attrs[name] = {"current": _text(current), "max": ""}

    for row in _list(plan.get("saves")):
        for name, current in _dict(row.get("attributes")).items():
            if name in attrs:
                raise RuntimeError(f"내성 attribute 중복: {name}")
            attrs[name] = {"current": _text(current), "max": ""}

    for row in _list(plan.get("tools")):
        for name, spec in _tool_attributes(row).items():
            if name in attrs:
                raise RuntimeError(f"도구 attribute 중복: {name}")
            attrs[name] = spec

    for row in _list(plan.get("other_proficiencies")):
        for name, spec in _prof_attributes(row).items():
            if name in attrs:
                raise RuntimeError(f"기타 숙련 attribute 중복: {name}")
            attrs[name] = spec

    # Force the official complex/repeating UI instead of the legacy textarea.
    attrs["simpleproficencies"] = {"current": "complex", "max": ""}

    return attrs


def managed_repeating_rows(plan):
    return {
        "tool": [row["row_id"] for row in _list(plan.get("tools"))],
        "proficiencies": [
            row["row_id"]
            for row in _list(plan.get("other_proficiencies"))
        ],
    }


def _previous_managed_rows(output_path):
    if not output_path.is_file():
        return {"tool": [], "proficiencies": []}
    try:
        previous = json.loads(output_path.read_text(encoding="utf-8"))
    except Exception:
        return {"tool": [], "proficiencies": []}
    rows = _dict(previous.get("managed_repeating_rows"))
    return {
        "tool": [_text(v) for v in _list(rows.get("tool")) if _text(v)],
        "proficiencies": [
            _text(v)
            for v in _list(rows.get("proficiencies"))
            if _text(v)
        ],
    }


PROFICIENCY_STATE_SCRIPT = r"""
const wantedId = String(arguments[0] || '').trim();
const wantedName = String(arguments[1] || '').trim();
const done = arguments[arguments.length - 1];

function val(obj,key) {
  try {
    if (!obj) return null;
    if (obj.attributes && obj.attributes[key] != null) return obj.attributes[key];
    if (typeof obj.get === 'function') {
      const v=obj.get(key); if (v != null) return v;
    }
    if (obj[key] != null) return obj[key];
  } catch (_) {}
  return null;
}
function modelsOf(c) {
  try {
    if (!c) return [];
    if (Array.isArray(c.models)) return c.models;
    if (typeof c.toArray === 'function') return c.toArray();
    if (Array.isArray(c)) return c;
  } catch (_) {}
  return [];
}
function idOf(m) {
  return String(val(m,'id') || val(m,'_id') || val(m,'characterid') || (m && m.id) || '').trim();
}
function findCharacter() {
  const campaigns=[];
  try { if (window.d20 && window.d20.Campaign) campaigns.push(window.d20.Campaign); } catch (_) {}
  try { if (window.Campaign) campaigns.push(window.Campaign); } catch (_) {}
  for (const campaign of campaigns) {
    const collections=[campaign.characters, campaign.attributes && campaign.attributes.characters];
    for (const collection of collections) {
      for (const model of modelsOf(collection)) {
        const id=idOf(model);
        const name=String(val(model,'name') || '').trim();
        if ((wantedId && id===wantedId) || (!wantedId && wantedName && name===wantedName)) return model;
      }
    }
  }
  return null;
}
function finish(character,collection,status,error) {
  const toolIds=[];
  const profIds=[];
  const toolSeen=new Set();
  const profSeen=new Set();
  let toolReporder='';
  let profReporder='';

  for (const model of modelsOf(collection)) {
    const name=String(val(model,'name') || '').trim();

    if (name === '_reporder_repeating_tool') {
      toolReporder=String(val(model,'current') == null ? '' : val(model,'current'));
      continue;
    }
    if (name === '_reporder_repeating_proficiencies') {
      profReporder=String(val(model,'current') == null ? '' : val(model,'current'));
      continue;
    }

    let match=name.match(/^repeating_tool_([^_]+)_/);
    if (match && !toolSeen.has(match[1])) {
      toolSeen.add(match[1]);
      toolIds.push(match[1]);
      continue;
    }

    match=name.match(/^repeating_proficiencies_([^_]+)_/);
    if (match && !profSeen.has(match[1])) {
      profSeen.add(match[1]);
      profIds.push(match[1]);
    }
  }

  done({
    ok:status === 'success' || status === 'success_promise',
    fetch_status:status,
    fetch_error:error || '',
    character_id:idOf(character),
    tool_row_ids:toolIds,
    proficiency_row_ids:profIds,
    tool_reporder:toolReporder,
    proficiency_reporder:profReporder,
  });
}

const character=findCharacter();
if (!character) { done({ok:false,reason:'character_not_found'}); return; }
const collection=character.attribs;
if (!collection || typeof collection.fetch !== 'function') {
  done({ok:false,reason:'attribute_collection_unavailable'}); return;
}

let settled=false;
function final(status,error) {
  if(settled) return;
  settled=true;
  finish(character,collection,status,error);
}
try {
  const req=collection.fetch({
    reset:false,
    success:()=>final('success',''),
    error:(_c,xhr)=>final('error',`status=${xhr && xhr.status}; text=${xhr && xhr.statusText}`),
  });
  if(req && typeof req.then==='function') {
    req.then(()=>final('success_promise',''),e=>final('error_promise',String(e || '')));
  }
  setTimeout(()=>final('timeout','fetch callback timeout'),12000);
} catch(e) {
  final('exception',String(e && e.stack ? e.stack : e));
}
"""


def _proficiency_state(driver, target):
    from .roll20_read import read_persisted
    return read_persisted(
        driver,
        PROFICIENCY_STATE_SCRIPT,
        _text(target.get("roll20_character_id")),
        _text(target.get("character_name")),
    )


def _split_reporder(value):
    return [
        part.strip()
        for part in _text(value).split(",")
        if part.strip()
    ]


def _build_section_reporder(existing_ids, current_reporder, managed_ids, desired_managed):
    existing = [_text(v) for v in _list(existing_ids) if _text(v)]
    current = _split_reporder(current_reporder)
    managed = set(_text(v) for v in _list(managed_ids) if _text(v))
    desired = [_text(v) for v in _list(desired_managed) if _text(v)]

    manual = []
    seen = set()

    # Preserve manual/unmanaged order exactly as Roll20 currently knows it.
    # Rows omitted from the existing reporder are appended in collection order.
    for row_id in current + existing:
        if row_id in managed or row_id in seen:
            continue
        seen.add(row_id)
        manual.append(row_id)

    return desired + manual


def _build_reporders(state, plan):
    managed = managed_repeating_rows(plan)

    tool_order = _build_section_reporder(
        state.get("tool_row_ids"),
        state.get("tool_reporder"),
        managed.get("tool"),
        plan.get("desired_tool_order"),
    )
    proficiency_order = _build_section_reporder(
        state.get("proficiency_row_ids"),
        state.get("proficiency_reporder"),
        managed.get("proficiencies"),
        plan.get("desired_proficiency_order"),
    )

    return {
        "_reporder_repeating_tool": ",".join(tool_order),
        "_reporder_repeating_proficiencies": ",".join(proficiency_order),
    }


DELETE_ROWS_SCRIPT = r"""
const wantedId = String(arguments[0] || '').trim();
const wantedName = String(arguments[1] || '').trim();
const targets = arguments[2] || [];
const done = arguments[arguments.length - 1];

function val(obj,key) {
  try {
    if (!obj) return null;
    if (obj.attributes && obj.attributes[key] != null) return obj.attributes[key];
    if (typeof obj.get === 'function') {
      const v=obj.get(key); if (v != null) return v;
    }
    if (obj[key] != null) return obj[key];
  } catch (_) {}
  return null;
}
function modelsOf(c) {
  try {
    if (!c) return [];
    if (Array.isArray(c.models)) return c.models;
    if (typeof c.toArray === 'function') return c.toArray();
    if (Array.isArray(c)) return c;
  } catch (_) {}
  return [];
}
function idOf(m) {
  return String(val(m,'id') || val(m,'_id') || val(m,'characterid') || (m && m.id) || '').trim();
}
function findCharacter() {
  const campaigns=[];
  try { if (window.d20 && window.d20.Campaign) campaigns.push(window.d20.Campaign); } catch (_) {}
  try { if (window.Campaign) campaigns.push(window.Campaign); } catch (_) {}
  for (const campaign of campaigns) {
    const collections=[campaign.characters, campaign.attributes && campaign.attributes.characters];
    for (const collection of collections) {
      for (const model of modelsOf(collection)) {
        const id=idOf(model);
        const name=String(val(model,'name') || '').trim();
        if ((wantedId && id===wantedId) || (!wantedId && wantedName && name===wantedName)) return model;
      }
    }
  }
  return null;
}
function waitDestroy(model,name) {
  return new Promise((resolve,reject)=>{
    let settled=false;
    const timer=setTimeout(()=>{
      if(settled)return; settled=true; reject(new Error('destroy_timeout '+name));
    },12000);
    try {
      model.destroy({
        wait:true,
        success:()=>{
          if(settled)return; settled=true; clearTimeout(timer); resolve(name);
        },
        error:(_m,xhr)=>{
          if(settled)return; settled=true; clearTimeout(timer);
          reject(new Error('destroy_failed '+name+' status='+(xhr&&xhr.status)));
        },
      });
    } catch(e) {
      if(settled)return; settled=true; clearTimeout(timer); reject(e);
    }
  });
}

(async function(){
  const character=findCharacter();
  if(!character){done({ok:false,reason:'character_not_found'});return;}
  const collection=character.attribs;
  if(!collection){done({ok:false,reason:'attribute_collection_unavailable'});return;}

  const wanted = new Set(targets.map(x => `${x.section}:${x.row_id}`));
  const found=[];
  for(const model of modelsOf(collection)) {
    const name=String(val(model,'name') || '').trim();
    let match=name.match(/^repeating_tool_([^_]+)_/);
    if(match && wanted.has(`tool:${match[1]}`)) {
      found.push([model,name]);
      continue;
    }
    match=name.match(/^repeating_proficiencies_([^_]+)_/);
    if(match && wanted.has(`proficiencies:${match[1]}`)) {
      found.push([model,name]);
    }
  }

  try {
    const deleted=[];
    for(const [model,name] of found) {
      deleted.push(await waitDestroy(model,name));
    }
    done({ok:true,deleted_count:deleted.length,deleted});
  } catch(e) {
    done({ok:false,reason:'delete_failed',error:String(e && e.stack ? e.stack : e)});
  }
})();
"""


def _load_target(source_id: str):
    path = Path(CURRENT_RESULT_DIR) / f"roll20-target-{source_id}.json"
    if not path.is_file():
        raise RuntimeError(f"4단계 연결 파일이 없습니다: {path.resolve()}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if _text(payload.get("source_character_id")) != source_id:
        raise RuntimeError("Roll20 연결 파일 source ID가 다릅니다.")
    if _text(payload.get("sheet_type")) != "ogl5e":
        raise RuntimeError(
            f"11단계는 ogl5e만 지원합니다: "
            f"{_text(payload.get('sheet_type')) or '미확인'}"
        )
    if not _text(payload.get("roll20_character_id")):
        raise RuntimeError("Roll20 Character ID가 없습니다.")
    return payload, path


def _upsert_and_verify(driver, target, attrs):
    driver.set_script_timeout(120)
    outcome = driver.execute_async_script(
        UPSERT_ROW_SCRIPT,
        _text(target.get("roll20_character_id")),
        _text(target.get("character_name")),
        attrs,
    )
    if not isinstance(outcome, dict) or not outcome.get("ok"):
        raise RuntimeError(
            "11단계 숙련 저장 실패: "
            + json.dumps(outcome, ensure_ascii=False)
        )

    after = _snapshot(driver, target, attrs.keys())
    actual, mismatches = _verify(after, attrs)
    if mismatches:
        raise RuntimeError(
            "11단계 숙련 서버 재검증 실패: "
            + json.dumps(mismatches, ensure_ascii=False)
        )
    return outcome, actual


def _delete_stale_rows(driver, target, stale_targets):
    if not stale_targets:
        return {"ok": True, "deleted_count": 0, "deleted": []}
    driver.set_script_timeout(max(45, len(stale_targets) * 20))
    outcome = driver.execute_async_script(
        DELETE_ROWS_SCRIPT,
        _text(target.get("roll20_character_id")),
        _text(target.get("character_name")),
        stale_targets,
    )
    if not isinstance(outcome, dict) or not outcome.get("ok"):
        raise RuntimeError(
            "11단계 이전 관리 반복행 정리 실패: "
            + json.dumps(outcome, ensure_ascii=False)
        )
    return outcome


def apply_proficiencies(
    *,
    result_path: str | Path | None = None,
    source_id: str | None = None,
    cdp_url: str = DEFAULT_CDP_URL,
    dry_run: bool = False,
):
    source_id = _text(source_id)
    path = (
        Path(result_path)
        if result_path
        else latest_complete_result(source_id=source_id or None)
    )
    if path is None or not path.is_file():
        raise RuntimeError("사용할 정상 sheet-result JSON이 없습니다.")

    payload = load_result(path)
    plan = build_proficiency_plan(payload)
    source_id = plan["source_character_id"]
    target, target_path = _load_target(source_id)

    if _text(target.get("character_name")) != plan["character_name"]:
        raise RuntimeError("D&D Beyond 결과와 Roll20 대상 캐릭터 이름이 다릅니다.")

    attrs = plan_attributes(plan)
    output_path = (
        Path(CURRENT_RESULT_DIR)
        / f"roll20-proficiencies-{source_id}.json"
    )

    previous_rows = _previous_managed_rows(output_path)
    current_rows = managed_repeating_rows(plan)
    stale_targets = []

    for section in ("tool", "proficiencies"):
        current = set(current_rows[section])
        for row_id in previous_rows[section]:
            if row_id not in current:
                stale_targets.append({
                    "section": section,
                    "row_id": row_id,
                })

    report = {
        **plan,
        "mode": "dry-run" if dry_run else "apply",
        "roll20_character_id": target["roll20_character_id"],
        "result_path": str(path.resolve()),
        "target_path": str(Path(target_path).resolve()),
        "managed_attribute_count": len(attrs),
        "managed_repeating_rows": current_rows,
        "stale_previous_rows": stale_targets,
        "backup_path": None,
        "mutated": False,
        "cleanup_result": None,
        "verification": {},
        "status": "running",
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }

    _ensure_cdp(cdp_url)
    driver = _attach_driver(cdp_url)

    try:
        _select_roll20_tab(driver)

        state_before = _proficiency_state(driver, target)
        reporders = _build_reporders(state_before, plan)
        ordered_attrs = dict(attrs)
        for name, value in reporders.items():
            ordered_attrs[name] = {"current": value, "max": ""}

        before = _snapshot(driver, target, ordered_attrs.keys())
        report["before"] = {
            "managed_attributes": before["attributes"],
            "repeating_state": state_before,
        }
        report["desired_reporders"] = reporders

        _, initial_mismatches = _verify(before, ordered_attrs)
        report["initial_mismatch_count"] = len(initial_mismatches)

        if dry_run:
            report["status"] = "pass"
            report["verification"] = {
                "status": "not_run",
                "reason": "dry_run",
                "initial_mismatches": initial_mismatches,
            }
            _save_json(output_path, report)
            return report, output_path

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = (
            Path(CURRENT_RESULT_DIR)
            / f"roll20-stage11-proficiencies-backup-"
              f"{source_id}-{timestamp}.json"
        )
        _save_json(
            backup_path,
            {
                "version": STAGE11_VERSION,
                "source_character_id": source_id,
                "character_name": plan["character_name"],
                "roll20_character_id": target["roll20_character_id"],
                "read_only_snapshot_before_apply": True,
                "managed_attributes": before["attributes"],
                "repeating_state": state_before,
                "desired_reporders": reporders,
                "previous_managed_repeating_rows": previous_rows,
                "stale_previous_rows": stale_targets,
            },
        )
        report["backup_path"] = str(backup_path.resolve())
        _save_json(output_path, report)

        if stale_targets:
            report["cleanup_result"] = _delete_stale_rows(
                driver,
                target,
                stale_targets,
            )
            report["mutated"] = True

        # Re-read after stale-row cleanup so manual rows are preserved even if
        # cleanup changed the collection. Then write rows + explicit reporder.
        state_after_cleanup = _proficiency_state(driver, target)
        reporders = _build_reporders(state_after_cleanup, plan)
        ordered_attrs = dict(attrs)
        for name, value in reporders.items():
            ordered_attrs[name] = {"current": value, "max": ""}

        outcome, actual = _upsert_and_verify(
            driver,
            target,
            ordered_attrs,
        )
        report["mutated"] = report["mutated"] or bool(initial_mismatches)
        report["write_result"] = outcome
        final_state = _proficiency_state(driver, target)
        final_reporders = {
            "_reporder_repeating_tool": _text(final_state.get("tool_reporder")),
            "_reporder_repeating_proficiencies": _text(
                final_state.get("proficiency_reporder")
            ),
        }
        order_mismatches = [
            {
                "attribute": name,
                "expected": expected,
                "actual": final_reporders.get(name, ""),
            }
            for name, expected in reporders.items()
            if final_reporders.get(name, "") != expected
        ]
        if order_mismatches:
            raise RuntimeError(
                "11단계 반복행 정렬 서버 재검증 실패: "
                + json.dumps(order_mismatches, ensure_ascii=False)
            )

        report["desired_reporders"] = reporders
        report["verification"] = {
            "status": "pass",
            "actual": actual,
            "reporders": final_reporders,
            "mismatches": [],
        }
        report["status"] = "pass"
        _save_json(output_path, report)
        return report, output_path

    except Exception as exc:
        from .result_store import record_stage_failure
        record_stage_failure(exc, report, output_path)
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

    print("[시트 이동기] 11단계 v2: 전체 숙련 정보 입력")
    print(f"[시트 이동기] 모드: {'읽기 전용' if args.dry_run else '실제 입력'}")
    print("[시트 이동기] 기술/내성은 기존 체크박스에 반영합니다.")
    print("[시트 이동기] 도구 숙련은 TOOL PROFICIENCIES에 입력합니다.")
    print("[시트 이동기] 언어/무기/방어구 숙련은 OTHER PROFICIENCIES & LANGUAGES에 입력합니다.")
    print("[시트 이동기] 표시 순서: 언어 → 무기 → 방어구 → 기타입니다.")
    print("[시트 이동기] 기존 수동 반복행은 삭제하지 않고 관리행 뒤에 유지합니다.")

    report, output = apply_proficiencies(
        result_path=args.result,
        source_id=args.source_id,
        cdp_url=args.cdp_url,
        dry_run=args.dry_run,
    )

    print(
        f"[시트 이동기] 기술 숙련 {report['skill_proficiency_count']} / "
        f"Expertise {report['expertise_count']} / "
        f"내성 숙련 {report['save_proficiency_count']}"
    )
    print(
        f"[시트 이동기] 도구 {report['tool_proficiency_count']} / "
        f"언어 {report['language_count']} / "
        f"무기 {report['weapon_proficiency_count']} / "
        f"방어구·방패 {report['armor_proficiency_count']} / "
        f"기타 {report['other_proficiency_count']}"
    )

    if report["tools"]:
        print(
            "[시트 이동기] 도구: "
            + ", ".join(row["name"] for row in report["tools"])
        )
    if report["other_proficiencies"]:
        print("[시트 이동기] 기타 숙련/언어:")
        for row in report["other_proficiencies"]:
            print(f"  - {row['prof_type']}: {row['name']}")

    if not args.dry_run:
        print(f"[시트 이동기] 백업: {report.get('backup_path')}")
        print(
            "[시트 이동기] 최종 서버 재검증: "
            + str((report.get("verification") or {}).get("status"))
        )
    print(f"[시트 이동기] 결과 저장: {output.resolve()}")


if __name__ == "__main__":
    main()
