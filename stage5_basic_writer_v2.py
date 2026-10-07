"""Stage 5 basic Roll20 writer v2.

Why v2:
- v1 changed DOM controls. Many controls changed visually but were never
  persisted to Roll20's character attribute collection.
- v2 writes through character.attribs (Backbone collection) and verifies the
  persisted server state after a fresh fetch.
- No attributes outside the explicit Stage 5 whitelist are deleted or changed.

Plan only:
    python stage5_basic_writer_v2.py

Apply:
    python stage5_basic_writer_v2.py --apply
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

from sheet_mover.result_store import CURRENT_RESULT_DIR, latest_complete_result, load_result
from sheet_mover.roll20_connection import (
    DEFAULT_CDP_URL,
    _attach_driver,
    _disconnect_driver,
    _ensure_cdp,
    _select_roll20_tab,
)

VERSION = "2026-10-06-stage5-basic-fields-v2.1-multiclass"
ROOT = Path.cwd()

ABILITY_NAMES = (
    "strength",
    "dexterity",
    "constitution",
    "intelligence",
    "wisdom",
    "charisma",
)

SPELL_ABILITY_VALUE = {
    "strength": "@{strength_mod}+",
    "dexterity": "@{dexterity_mod}+",
    "constitution": "@{constitution_mod}+",
    "intelligence": "@{intelligence_mod}+",
    "wisdom": "@{wisdom_mod}+",
    "charisma": "@{charisma_mod}+",
}

# Stage 5 owns only these single-value attributes.
STAGE5_NAMES = {
    "class",
    "class_display",
    "subclass",
    "base_level",
    "multiclass1_flag",
    "multiclass1",
    "multiclass1_lvl",
    "multiclass1_subclass",
    "multiclass2_flag",
    "multiclass2",
    "multiclass2_lvl",
    "multiclass2_subclass",
    "multiclass3_flag",
    "multiclass3",
    "multiclass3_lvl",
    "multiclass3_subclass",
    "level",
    "pb_type",
    "pb",
    "arcane_fighter",
    "arcane_rogue",
    "race",
    "subrace",
    "background",
    "alignment",
    "experience",
    "strength_base",
    "dexterity_base",
    "constitution_base",
    "intelligence_base",
    "wisdom_base",
    "charisma_base",
    "strength",
    "dexterity",
    "constitution",
    "intelligence",
    "wisdom",
    "charisma",
    "strength_mod",
    "dexterity_mod",
    "constitution_mod",
    "intelligence_mod",
    "wisdom_mod",
    "charisma_mod",
    "hp",
    "hp_temp",
    "ac",
    "speed",
    "initmod",
    "initiative_bonus",
    "spellcasting_ability",
    "spell_dc_mod",
    "globalmagicmod",
    "spell_attack_mod",
    "spell_save_dc",
    "spell_attack_bonus",
    "caster_level",
}

# hp is special: Roll20 stores current/max on ONE attribute named "hp".
MAX_FIELDS = {"hp"}


def _dict(value):
    return value if isinstance(value, dict) else {}


def _list(value):
    return value if isinstance(value, list) else []


def _text(value):
    return str(value or "").strip()


def _number(value):
    return value if type(value) in (int, float) else None


def _string_number(value):
    if type(value) is float and value.is_integer():
        return str(int(value))
    return str(value)


def ability_modifier(score):
    if _number(score) is None:
        return None
    return math.floor((score - 10) / 2)


def _character(payload):
    character = _dict(_dict(payload).get("roll20_payload")).get("character")
    if not isinstance(character, dict):
        raise RuntimeError("결과 JSON에 roll20_payload.character가 없습니다.")
    return character


def _ordered_classes(character):
    rows = [
        row for row in _list(character.get("classes"))
        if isinstance(row, dict)
    ]
    if not rows:
        raise RuntimeError("클래스 정보가 없습니다.")
    if len(rows) > 4:
        raise RuntimeError(
            "Roll20 Legacy는 최대 4개 클래스(기본 1 + 멀티 3)까지만 "
            f"지원합니다. 클래스 수: {len(rows)}"
        )

    starting = [row for row in rows if row.get("is_starting_class") is True]
    if len(starting) > 1:
        raise RuntimeError("D&D Beyond 시작 클래스가 둘 이상입니다.")

    if starting:
        base = starting[0]
        return [base, *[row for row in rows if row is not base]]

    return rows


def _original_name(obj, original_key="original_name", fallback_key="name"):
    obj = _dict(obj)
    return _text(obj.get(original_key) or obj.get(fallback_key))


def _display_name(obj):
    obj = _dict(obj)
    return _text(obj.get("name") or obj.get("original_name"))


def _walk_speed(speed):
    speed = _dict(speed)
    normal = speed.get("normal")
    if isinstance(normal, dict) and normal.get("walk") is not None:
        return normal.get("walk")
    return speed.get("walk")


def _spell_ability(character, cls):
    spellcasting = _dict(character.get("spellcasting"))
    rows = [
        row for row in _list(spellcasting.get("class_calculations"))
        if isinstance(row, dict)
    ]
    abilities = {
        _text(row.get("ability_name")).lower()
        for row in rows
        if _text(row.get("ability_name")).lower() in ABILITY_NAMES
    }
    if len(abilities) == 1:
        return next(iter(abilities))

    value = _text(cls.get("spellcasting_ability_name")).lower()
    return value if value in ABILITY_NAMES else None


def _caster_type(cls):
    name = _original_name(cls).casefold()
    level = cls.get("level")
    if type(level) is not int or level < 1:
        return 0.0

    if name in {"bard", "cleric", "druid", "sorcerer", "wizard"}:
        return 1.0

    if name in {"artificer", "paladin", "ranger"}:
        if name == "artificer" and level == 1:
            return 1.0
        return 0.0 if level == 1 else 0.5

    subclass = _text(
        cls.get("original_subclass_name") or cls.get("subclass_name")
    ).casefold()
    if (
        (name == "fighter" and subclass == "eldritch knight")
        or (name == "rogue" and subclass == "arcane trickster")
    ):
        return 0.0 if level in {1, 2} else (1.0 / 3.0)

    return 0.0


def _caster_level_for_classes(classes):
    multicaster = sum(1 for cls in classes if _caster_type(cls) > 0) > 1
    total = 0
    for cls in classes:
        level = cls.get("level")
        if type(level) is not int or level < 1:
            continue
        value = level * _caster_type(cls)
        total += math.floor(value) if multicaster else math.ceil(value)
    return total


def _is_arcane_fighter(classes):
    return any(
        _original_name(cls).casefold() == "fighter"
        and _text(
            cls.get("original_subclass_name") or cls.get("subclass_name")
        ).casefold() == "eldritch knight"
        for cls in classes
    )


def _is_arcane_rogue(classes):
    return any(
        _original_name(cls).casefold() == "rogue"
        and _text(
            cls.get("original_subclass_name") or cls.get("subclass_name")
        ).casefold() == "arcane trickster"
        for cls in classes
    )


def build_plan(payload: dict[str, Any]):
    character = _character(payload)
    classes = _ordered_classes(character)
    cls = classes[0]

    values: dict[str, dict[str, str | None]] = {}

    def put(name, current, max_value=None):
        if name not in STAGE5_NAMES:
            raise RuntimeError(f"5단계 화이트리스트 밖 필드: {name}")
        if current is None:
            return
        values[name] = {
            "current": _string_number(current),
            "max": None if max_value is None else _string_number(max_value),
        }

    class_name = _original_name(cls)
    if not class_name:
        raise RuntimeError("클래스 원문 이름이 없습니다.")
    put("class", class_name)

    level = cls.get("level")
    total_level = character.get("total_level")
    pb = character.get("proficiency_bonus")
    if type(level) is not int or level < 1:
        raise RuntimeError("기본 클래스 레벨이 확정되지 않았습니다.")
    if type(total_level) is not int or total_level < 1:
        raise RuntimeError("총 레벨이 확정되지 않았습니다.")
    if _number(pb) is None:
        raise RuntimeError("숙련 보너스가 확정되지 않았습니다.")

    put("base_level", level)
    put("level", total_level)
    put("pb_type", "level")
    put("pb", pb)

    subclass = _text(
        cls.get("subclass_name") or cls.get("original_subclass_name")
    )
    put("subclass", subclass)

    display_parts = [
        f"{subclass} {class_name} {level}"
        if subclass else f"{class_name} {level}"
    ]

    secondary = classes[1:]
    for slot in range(1, 4):
        row = secondary[slot - 1] if slot <= len(secondary) else None
        prefix = f"multiclass{slot}"

        if row is None:
            put(f"{prefix}_flag", 0)
            put(prefix, "")
            put(f"{prefix}_lvl", "")
            put(f"{prefix}_subclass", "")
            continue

        multi_name = _original_name(row)
        multi_level = row.get("level")
        if not multi_name:
            raise RuntimeError(f"{slot + 1}번째 클래스 원문 이름이 없습니다.")
        if type(multi_level) is not int or multi_level < 1:
            raise RuntimeError(f"{multi_name} 클래스 레벨이 확정되지 않았습니다.")

        multi_subclass = _text(
            row.get("subclass_name") or row.get("original_subclass_name")
        )

        put(f"{prefix}_flag", 1)
        put(prefix, multi_name)
        put(f"{prefix}_lvl", multi_level)
        put(f"{prefix}_subclass", multi_subclass)

        display_parts.append(
            f"{multi_subclass} {multi_name} {multi_level}"
            if multi_subclass else f"{multi_name} {multi_level}"
        )

    put("class_display", ", ".join(display_parts))
    put("arcane_fighter", 1 if _is_arcane_fighter(classes) else 0)
    put("arcane_rogue", 1 if _is_arcane_rogue(classes) else 0)

    race = _dict(character.get("race"))
    race_name = _display_name(race)
    if race_name:
        put("race", race_name)
    if _text(race.get("subrace_name")):
        put("subrace", _text(race.get("subrace_name")))

    background = _dict(character.get("background"))
    background_name = _display_name(background)
    if background_name:
        put("background", background_name)

    if character.get("alignment") not in (None, ""):
        put("alignment", character.get("alignment"))

    if _number(character.get("experience")) is not None:
        put("experience", character.get("experience"))

    scores = _dict(character.get("ability_scores"))
    for ability in ABILITY_NAMES:
        score = scores.get(ability)
        if _number(score) is None:
            raise RuntimeError(f"{ability} 능력치가 확정되지 않았습니다.")
        mod = ability_modifier(score)
        put(f"{ability}_base", score)
        put(ability, score)
        put(f"{ability}_mod", mod)

    hp = character.get("hp")
    max_hp = character.get("max_hp")
    temp_hp = character.get("temp_hp")
    ac = character.get("armor_class")
    initiative = character.get("initiative")

    if _number(hp) is None or _number(max_hp) is None:
        raise RuntimeError("HP가 확정되지 않았습니다.")
    if _number(ac) is None:
        raise RuntimeError("AC가 확정되지 않았습니다.")
    if _number(initiative) is None:
        raise RuntimeError("우선권이 확정되지 않았습니다.")

    # Important: Roll20 API represents attr_hp + attr_hp_max as one attribute.
    put("hp", hp, max_hp)
    put("hp_temp", temp_hp if _number(temp_hp) is not None else 0)
    put("ac", ac)

    walk = _walk_speed(character.get("speed"))
    if walk is not None:
        speed = f"{_string_number(walk)} ft." if _number(walk) is not None else str(walk)
        put("speed", speed)

    dex_mod = ability_modifier(scores.get("dexterity"))
    if dex_mod is None:
        raise RuntimeError("DEX modifier를 계산할 수 없습니다.")
    put("initmod", initiative - dex_mod)
    put("initiative_bonus", initiative)

    spellcasting = _dict(character.get("spellcasting"))
    spell_ability = _spell_ability(character, cls)
    if spell_ability:
        ability_mod = ability_modifier(scores.get(spell_ability))
        final_dc = spellcasting.get("save_dc")
        final_attack = spellcasting.get("attack_bonus")

        put("spellcasting_ability", SPELL_ABILITY_VALUE[spell_ability])

        if ability_mod is not None:
            if _number(final_dc) is not None:
                put("spell_dc_mod", final_dc - (8 + pb + ability_mod))
                put("spell_save_dc", final_dc)
            if _number(final_attack) is not None:
                attack_delta = final_attack - (pb + ability_mod)
                put("globalmagicmod", attack_delta)
                put("spell_attack_mod", attack_delta)
                put("spell_attack_bonus", final_attack)

    put("caster_level", _caster_level_for_classes(classes))

    return {
        "version": VERSION,
        "source_character_id": _text(character.get("source_id")),
        "character_name": _text(character.get("name")),
        "attributes": values,
        "deferred": [
            "equipment",
            "spells",
            "features",
            "actions",
            "attacks",
            "skill_save_proficiency_expertise",
            "class_resources",
        ],
    }


FETCH_SCRIPT = r"""
const wantedId = String(arguments[0] || '').trim();
const wantedName = String(arguments[1] || '').trim();
const names = arguments[2] || [];
const done = arguments[arguments.length - 1];

function val(obj, key) {
  try {
    if (!obj) return null;
    if (obj.attributes && obj.attributes[key] != null) return obj.attributes[key];
    if (typeof obj.get === 'function') {
      const v = obj.get(key);
      if (v != null) return v;
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
  return String(
    val(m,'id') || val(m,'_id') || val(m,'characterid') || (m && m.id) || ''
  ).trim();
}

function findCharacter() {
  const campaigns = [];
  try { if (window.d20 && window.d20.Campaign) campaigns.push(window.d20.Campaign); } catch (_) {}
  try { if (window.Campaign) campaigns.push(window.Campaign); } catch (_) {}

  for (const campaign of campaigns) {
    const collections = [
      campaign.characters,
      campaign.attributes && campaign.attributes.characters,
    ];
    for (const collection of collections) {
      for (const model of modelsOf(collection)) {
        const id = idOf(model);
        const name = String(val(model,'name') || '').trim();
        if ((wantedId && id === wantedId) ||
            (!wantedId && wantedName && name === wantedName)) {
          return model;
        }
      }
    }
  }
  return null;
}

function snapshot(collection) {
  const result = {};
  for (const name of names) result[name] = [];
  for (const model of modelsOf(collection)) {
    const name = String(val(model,'name') || '').trim();
    if (!names.includes(name)) continue;
    result[name].push({
      id:idOf(model),
      current:String(val(model,'current') == null ? '' : val(model,'current')),
      max:String(val(model,'max') == null ? '' : val(model,'max')),
    });
  }
  return result;
}

const character = findCharacter();
if (!character) {
  done({ok:false, reason:'character_not_found'});
  return;
}
const collection = character.attribs;
if (!collection || typeof collection.fetch !== 'function') {
  done({ok:false, reason:'attribute_collection_unavailable'});
  return;
}

let settled = false;
function finish(status, error) {
  if (settled) return;
  settled = true;
  done({
    ok:status === 'success' || status === 'success_promise',
    fetch_status:status,
    fetch_error:error || '',
    character_id:idOf(character),
    character_name:String(val(character,'name') || '').trim(),
    attributes:snapshot(collection),
  });
}

try {
  const req = collection.fetch({
    reset:false,
    success:() => finish('success',''),
    error:(_c,xhr) => finish(
      'error',
      `status=${xhr && xhr.status}; text=${xhr && xhr.statusText}`
    ),
  });
  if (req && typeof req.then === 'function') {
    req.then(
      () => finish('success_promise',''),
      e => finish('error_promise',String(e || ''))
    );
  }
  setTimeout(() => finish('timeout','fetch callback timeout'),12000);
} catch (e) {
  finish('exception',String(e && e.stack ? e.stack : e));
}
"""


UPSERT_SCRIPT = r"""
const wantedId = String(arguments[0] || '').trim();
const wantedName = String(arguments[1] || '').trim();
const changes = arguments[2] || {};
const done = arguments[arguments.length - 1];

function val(obj, key) {
  try {
    if (!obj) return null;
    if (obj.attributes && obj.attributes[key] != null) return obj.attributes[key];
    if (typeof obj.get === 'function') {
      const v = obj.get(key);
      if (v != null) return v;
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
  return String(
    val(m,'id') || val(m,'_id') || val(m,'characterid') || (m && m.id) || ''
  ).trim();
}

function findCharacter() {
  const campaigns = [];
  try { if (window.d20 && window.d20.Campaign) campaigns.push(window.d20.Campaign); } catch (_) {}
  try { if (window.Campaign) campaigns.push(window.Campaign); } catch (_) {}

  for (const campaign of campaigns) {
    const collections = [
      campaign.characters,
      campaign.attributes && campaign.attributes.characters,
    ];
    for (const collection of collections) {
      for (const model of modelsOf(collection)) {
        const id = idOf(model);
        const name = String(val(model,'name') || '').trim();
        if ((wantedId && id === wantedId) ||
            (!wantedId && wantedName && name === wantedName)) {
          return model;
        }
      }
    }
  }
  return null;
}

function modelsNamed(collection, name) {
  return modelsOf(collection).filter(
    m => String(val(m,'name') || '').trim() === name
  );
}

const character = findCharacter();
if (!character) {
  done({ok:false, reason:'character_not_found'});
  return;
}
const collection = character.attribs;
if (!collection || typeof collection.fetch !== 'function') {
  done({ok:false, reason:'attribute_collection_unavailable'});
  return;
}

const log = [];
const entries = Object.entries(changes);
let finished = false;

function fail(reason, detail) {
  if (finished) return;
  finished = true;
  done({ok:false, reason, detail:detail || '', log});
}

function succeed() {
  if (finished) return;
  finished = true;
  done({ok:true, log});
}

function saveExisting(model, name, spec, next) {
  const update = {current:String(spec.current == null ? '' : spec.current)};
  if (spec.max !== null && spec.max !== undefined) {
    update.max = String(spec.max);
  }

  const before = {
    current:String(val(model,'current') == null ? '' : val(model,'current')),
    max:String(val(model,'max') == null ? '' : val(model,'max')),
  };

  try {
    model.save(update, {
      wait:true,
      success:function(m) {
        log.push({
          name,
          action:'update',
          id:idOf(m || model),
          before,
          after:{
            current:String(val(m || model,'current') == null ? '' : val(m || model,'current')),
            max:String(val(m || model,'max') == null ? '' : val(m || model,'max')),
          },
        });
        next();
      },
      error:function(_m,xhr) {
        fail('update_failed', {
          name,
          status:xhr && xhr.status,
          statusText:xhr && xhr.statusText,
        });
      },
    });
  } catch (e) {
    fail('update_exception',{name,error:String(e && e.stack ? e.stack : e)});
  }
}

function createNew(name, spec, next) {
  const attrs = {
    name,
    current:String(spec.current == null ? '' : spec.current),
    max:(spec.max !== null && spec.max !== undefined) ? String(spec.max) : '',
    characterid:wantedId,
  };

  try {
    collection.create(attrs, {
      wait:true,
      success:function(model) {
        log.push({
          name,
          action:'create',
          id:idOf(model),
          before:null,
          after:{
            current:String(val(model,'current') == null ? '' : val(model,'current')),
            max:String(val(model,'max') == null ? '' : val(model,'max')),
          },
        });
        next();
      },
      error:function(_m,xhr) {
        fail('create_failed', {
          name,
          status:xhr && xhr.status,
          statusText:xhr && xhr.statusText,
        });
      },
    });
  } catch (e) {
    fail('create_exception',{name,error:String(e && e.stack ? e.stack : e)});
  }
}

function process(index) {
  if (index >= entries.length) {
    succeed();
    return;
  }

  const [name,spec] = entries[index];
  const existing = modelsNamed(collection,name);

  if (existing.length > 1) {
    fail('duplicate_attribute',{name,count:existing.length});
    return;
  }

  const next = () => process(index + 1);
  if (existing.length === 1) {
    saveExisting(existing[0],name,spec,next);
  } else {
    createNew(name,spec,next);
  }
}

let fetchSettled = false;
function begin() {
  if (fetchSettled) return;
  fetchSettled = true;
  process(0);
}

try {
  const req = collection.fetch({
    reset:false,
    success:begin,
    error:function(_c,xhr) {
      fail('initial_fetch_failed',{
        status:xhr && xhr.status,
        statusText:xhr && xhr.statusText,
      });
    },
  });
  if (req && typeof req.then === 'function') {
    req.then(begin, e => fail('initial_fetch_promise_failed',String(e || '')));
  }
  setTimeout(() => {
    if (!fetchSettled && !finished) fail('initial_fetch_timeout','');
  },12000);
} catch (e) {
  fail('initial_fetch_exception',String(e && e.stack ? e.stack : e));
}
"""


def _load_target(source_id):
    path = Path(CURRENT_RESULT_DIR) / f"roll20-target-{source_id}.json"
    if not path.is_file():
        raise RuntimeError(f"4단계 연결 파일이 없습니다: {path.resolve()}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if _text(data.get("source_character_id")) != source_id:
        raise RuntimeError("Roll20 연결 파일 source ID가 다릅니다.")
    if _text(data.get("sheet_type")) != "ogl5e":
        raise RuntimeError("5단계 v2는 ogl5e만 지원합니다.")
    return data, path


def _snapshot(driver, target, names):
    from sheet_mover.roll20_read import read_persisted
    return read_persisted(
        driver,
        FETCH_SCRIPT,
        _text(target.get("roll20_character_id")),
        _text(target.get("character_name")),
        sorted(names),
    )


def _persisted_single(snapshot, name):
    rows = _dict(snapshot.get("attributes")).get(name) or []
    if len(rows) != 1:
        return None, rows
    return rows[0], rows


def _verify(snapshot, plan_attrs):
    mismatches = []
    actual = {}

    for name, spec in plan_attrs.items():
        row, rows = _persisted_single(snapshot, name)
        if row is None:
            mismatches.append({
                "field": name,
                "reason": "missing_or_duplicate",
                "count": len(rows),
            })
            actual[name] = rows
            continue

        got_current = _text(row.get("current"))
        want_current = _text(spec.get("current"))
        got_max = _text(row.get("max"))
        want_max = spec.get("max")

        actual[name] = {
            "id": row.get("id"),
            "current": got_current,
            "max": got_max,
        }

        if got_current != want_current:
            mismatches.append({
                "field": name,
                "part": "current",
                "expected": want_current,
                "actual": got_current,
            })

        if name in MAX_FIELDS and want_max is not None:
            want_max_text = _text(want_max)
            if got_max != want_max_text:
                mismatches.append({
                    "field": name,
                    "part": "max",
                    "expected": want_max_text,
                    "actual": got_max,
                })

    return actual, mismatches


def run(source_id="170892133", cdp_url=DEFAULT_CDP_URL, apply=False):
    result_path = latest_complete_result(source_id=source_id)
    if result_path is None:
        raise RuntimeError(f"source_id={source_id} 정상 결과를 찾지 못했습니다.")

    result_payload = load_result(result_path)
    plan = build_plan(result_payload)
    target, target_path = _load_target(source_id)

    if _text(plan.get("character_name")) != _text(target.get("character_name")):
        raise RuntimeError("D&D Beyond 결과와 Roll20 대상 이름이 다릅니다.")

    report = {
        "version": VERSION,
        "mode": "apply" if apply else "plan",
        "source_character_id": source_id,
        "character_name": plan["character_name"],
        "roll20_character_id": target["roll20_character_id"],
        "result_path": str(Path(result_path).resolve()),
        "target_path": str(Path(target_path).resolve()),
        "attributes": plan["attributes"],
        "deferred": plan["deferred"],
        "mutated": False,
        "backup_path": None,
        "upsert_log": [],
        "verification": {},
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }

    _ensure_cdp(cdp_url)
    driver = _attach_driver(cdp_url)
    try:
        _select_roll20_tab(driver)

        before = _snapshot(driver, target, plan["attributes"].keys())
        report["before"] = before["attributes"]

        if not apply:
            report["preflight"] = {
                "status": "pass",
                "attribute_count": len(plan["attributes"]),
                "server_fetch_status": before.get("fetch_status"),
            }
            return report

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = Path(CURRENT_RESULT_DIR) / (
            f"roll20-stage5-backup-{source_id}-{timestamp}.json"
        )
        backup_payload = {
            "version": VERSION,
            "source_character_id": source_id,
            "character_name": plan["character_name"],
            "roll20_character_id": target["roll20_character_id"],
            "read_only_snapshot_before_apply": True,
            "attributes": before["attributes"],
        }
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_text(
            json.dumps(backup_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        report["backup_path"] = str(backup_path.resolve())

        driver.set_script_timeout(60)
        outcome = driver.execute_async_script(
            UPSERT_SCRIPT,
            _text(target.get("roll20_character_id")),
            _text(target.get("character_name")),
            plan["attributes"],
        )
        if not isinstance(outcome, dict) or not outcome.get("ok"):
            raise RuntimeError(
                "Roll20 서버 attribute 입력 실패: "
                + json.dumps(outcome, ensure_ascii=False)
            )

        report["mutated"] = True
        report["upsert_log"] = outcome.get("log") or []

        after = _snapshot(driver, target, plan["attributes"].keys())
        actual, mismatches = _verify(after, plan["attributes"])
        report["verification"] = {
            "status": "pass" if not mismatches else "fail",
            "actual": actual,
            "mismatches": mismatches,
            "server_fetch_status": after.get("fetch_status"),
        }

        if mismatches:
            raise RuntimeError(
                "Roll20 서버 저장 후 검증 불일치: "
                + json.dumps(mismatches, ensure_ascii=False)
            )

        return report
    finally:
        _disconnect_driver(driver)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-id", default="170892133")
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Roll20 character.attribs에 5단계 값을 실제 저장",
    )
    args = parser.parse_args()

    print("[시트 이동기] 5단계 v2: Roll20 서버 attribute 방식")
    print(f"[시트 이동기] 모드: {'실제 입력' if args.apply else '계획 확인(읽기 전용)'}")

    report = None
    output = Path(CURRENT_RESULT_DIR) / f"roll20-basic-write-v2-{args.source_id}.json"

    try:
        report = run(
            source_id=str(args.source_id),
            cdp_url=args.cdp_url,
            apply=args.apply,
        )
    except Exception as exc:
        # Unlike v1, preserve a failure report when possible.
        failure = {
            "version": VERSION,
            "mode": "apply" if args.apply else "plan",
            "source_character_id": str(args.source_id),
            "status": "error",
            "error": str(exc),
            "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(failure, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[시트 이동기] 실패 기록 저장: {output.resolve()}")
        raise

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"[시트 이동기] 대상 attribute: {len(report['attributes'])}개")
    if args.apply:
        print(f"[시트 이동기] 서버 upsert: {len(report['upsert_log'])}건")
        print(f"[시트 이동기] 백업: {report['backup_path']}")
        print(
            "[시트 이동기] 서버 재검증: "
            + str(report.get("verification", {}).get("status"))
        )
    else:
        print("[시트 이동기] Roll20 값 변경: 0건")
        print("[시트 이동기] 실제 입력은 --apply에서만 수행합니다.")

    print("[시트 이동기] 장비/주문목록/특성/행동/공격/기술·내성/자원은 수정하지 않습니다.")
    print(f"[시트 이동기] 결과 저장: {output.resolve()}")


# runtime-integrity-v2.6 Stage 5 cache-refresh hook
from sheet_mover.runtime_integrity_v26 import install_basic_plan_integrity as _install_basic_plan_integrity_v26
build_plan = _install_basic_plan_integrity_v26(build_plan, globals())

if __name__ == "__main__":
    main()
