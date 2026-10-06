"""Stage 5 basic Roll20 writer for the Legacy 5e OGL sheet.

Default mode is READ-ONLY planning:
    python stage5_basic_writer.py

Actual Roll20 mutation requires the explicit flag:
    python stage5_basic_writer.py --apply

The writer only touches a conservative Stage 5 whitelist:
- class / subclass / base level
- race / subrace / background / alignment / experience
- six base ability scores
- HP / max HP / temp HP / AC / speed
- initiative custom delta
- spellcasting ability and the two custom spell deltas
- Eldritch Knight / Arcane Trickster caster flags when applicable

It does NOT write equipment, spells, features, actions, attacks, skill/save
proficiencies, expertise, or class resources.
"""
from __future__ import annotations

import argparse
import json
import math
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from sheet_mover.result_store import CURRENT_RESULT_DIR, latest_complete_result, load_result
from sheet_mover.roll20_connection import (
    DEFAULT_CDP_URL,
    _attach_driver,
    _disconnect_driver,
    _ensure_cdp,
)

STAGE5_VERSION = "2026-10-06-stage5-basic-fields-v1"
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

# Fields that Stage 5 is allowed to change directly.
STAGE5_WHITELIST = {
    "class",
    "subclass",
    "base_level",
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
    "hp",
    "hp_max",
    "hp_temp",
    "ac",
    "speed",
    "initmod",
    "spellcasting_ability",
    "spell_dc_mod",
    "globalmagicmod",
    "arcane_fighter",
    "arcane_rogue",
}

# These are derived by the OGL5e sheet workers and are verification-only.
DERIVED_VERIFY_FIELDS = {
    "level",
    "pb",
    "strength",
    "dexterity",
    "constitution",
    "intelligence",
    "wisdom",
    "charisma",
    "initiative_bonus",
    "spell_save_dc",
    "spell_attack_bonus",
}


def _dict(value):
    return value if isinstance(value, dict) else {}


def _list(value):
    return value if isinstance(value, list) else []


def _text(value):
    return str(value or "").strip()


def _number(value):
    return value if type(value) in (int, float) else None


def ability_modifier(score):
    if _number(score) is None:
        return None
    return math.floor((score - 10) / 2)


def _sheet_character(payload):
    payload = _dict(payload)
    roll20_payload = _dict(payload.get("roll20_payload"))
    character = _dict(roll20_payload.get("character"))
    if not character:
        raise RuntimeError("결과 JSON에 roll20_payload.character가 없습니다.")
    return character


def _single_class(character):
    classes = [x for x in _list(character.get("classes")) if isinstance(x, dict)]
    if len(classes) != 1:
        raise RuntimeError(
            "5단계 기본 입력은 현재 단일 클래스만 지원합니다. "
            f"클래스 수: {len(classes)}"
        )
    return classes[0]


def _display_name(obj, translated_key="name", original_key="original_name"):
    obj = _dict(obj)
    return _text(obj.get(translated_key) or obj.get(original_key))


def _original_name(obj, original_key="original_name", fallback_key="name"):
    obj = _dict(obj)
    return _text(obj.get(original_key) or obj.get(fallback_key))


def _walk_speed(speed):
    speed = _dict(speed)
    candidates = []

    normal = speed.get("normal")
    if isinstance(normal, dict):
        candidates.append(normal.get("walk"))

    candidates.append(speed.get("walk"))

    for value in candidates:
        if _number(value) is not None:
            return value
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _spellcasting_ability(character, cls):
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

    ability = _text(cls.get("spellcasting_ability_name")).lower()
    return ability if ability in ABILITY_NAMES else None


def build_basic_plan(payload: dict[str, Any]):
    """Build the conservative Stage 5 Roll20 field plan.

    The returned values are sheet INPUT fields only. Derived Roll20 fields such
    as pb, level, ability modifiers, initiative_bonus, spell_save_dc, and
    spell_attack_bonus are intentionally not written.
    """
    character = _sheet_character(payload)
    cls = _single_class(character)
    values: dict[str, str] = {}

    # Roll20's class selector uses English class option values.
    class_name = _original_name(cls)
    if not class_name:
        raise RuntimeError("클래스 원문 이름을 확인할 수 없습니다.")
    values["class"] = class_name

    level = cls.get("level")
    if type(level) is not int or level < 1:
        raise RuntimeError("기본 클래스 레벨을 확인할 수 없습니다.")
    values["base_level"] = str(level)

    subclass_name = _text(
        cls.get("subclass_name")
        or cls.get("original_subclass_name")
    )
    if subclass_name:
        values["subclass"] = subclass_name

    original_subclass = _text(
        cls.get("original_subclass_name") or cls.get("subclass_name")
    ).casefold()
    if original_subclass == "eldritch knight":
        values["arcane_fighter"] = "1"
    elif original_subclass == "arcane trickster":
        values["arcane_rogue"] = "1"

    race = _dict(character.get("race"))
    race_name = _display_name(race)
    if race_name:
        values["race"] = race_name
    subrace_name = _text(race.get("subrace_name"))
    if subrace_name:
        values["subrace"] = subrace_name

    background = _dict(character.get("background"))
    background_name = _display_name(background)
    if background_name:
        values["background"] = background_name

    alignment = character.get("alignment")
    if alignment not in (None, ""):
        values["alignment"] = _text(alignment)

    experience = character.get("experience")
    if _number(experience) is not None:
        values["experience"] = str(experience)

    scores = _dict(character.get("ability_scores"))
    for ability in ABILITY_NAMES:
        score = scores.get(ability)
        if _number(score) is None:
            raise RuntimeError(f"{ability} 최종 능력치가 확정되지 않았습니다.")
        values[f"{ability}_base"] = str(score)

    hp = character.get("hp")
    max_hp = character.get("max_hp")
    temp_hp = character.get("temp_hp")
    ac = character.get("armor_class")

    if _number(hp) is None or _number(max_hp) is None:
        raise RuntimeError("HP/최대 HP가 확정되지 않았습니다.")
    if _number(ac) is None:
        raise RuntimeError("AC가 확정되지 않았습니다.")

    values["hp"] = str(hp)
    values["hp_max"] = str(max_hp)
    values["hp_temp"] = str(temp_hp if _number(temp_hp) is not None else 0)
    values["ac"] = str(ac)

    walk = _walk_speed(character.get("speed"))
    if walk is not None:
        values["speed"] = f"{walk} ft." if _number(walk) is not None else str(walk)

    # Roll20 computes initiative_bonus = dexterity_mod + initmod.
    final_initiative = character.get("initiative")
    dex_mod = ability_modifier(scores.get("dexterity"))
    if _number(final_initiative) is not None and dex_mod is not None:
        values["initmod"] = str(final_initiative - dex_mod)

    # Let Roll20 compute spell_save_dc / spell_attack_bonus from these input
    # fields instead of overwriting the derived outputs.
    spellcasting = _dict(character.get("spellcasting"))
    spell_ability = _spellcasting_ability(character, cls)
    pb = character.get("proficiency_bonus")
    if spell_ability:
        values["spellcasting_ability"] = SPELL_ABILITY_VALUE[spell_ability]

        ability_mod = ability_modifier(scores.get(spell_ability))
        final_dc = spellcasting.get("save_dc")
        final_attack = spellcasting.get("attack_bonus")

        if ability_mod is not None and _number(pb) is not None:
            if _number(final_dc) is not None:
                dc_delta = final_dc - (8 + pb + ability_mod)
                values["spell_dc_mod"] = str(dc_delta)
            if _number(final_attack) is not None:
                attack_delta = final_attack - (pb + ability_mod)
                values["globalmagicmod"] = str(attack_delta)

    unknown = set(values) - STAGE5_WHITELIST
    if unknown:
        raise RuntimeError(f"5단계 화이트리스트 밖 필드가 생성되었습니다: {sorted(unknown)}")

    expected = {
        "level": character.get("total_level"),
        "pb": character.get("proficiency_bonus"),
        "initiative_bonus": character.get("initiative"),
        "spell_save_dc": spellcasting.get("save_dc"),
        "spell_attack_bonus": spellcasting.get("attack_bonus"),
    }
    for ability in ABILITY_NAMES:
        expected[ability] = scores.get(ability)

    # Direct fields are also verified after write.
    for key in (
        "class", "subclass", "base_level", "race", "subrace", "background",
        "alignment", "experience", "hp", "hp_max", "hp_temp", "ac", "speed",
        "initmod", "spellcasting_ability", "spell_dc_mod", "globalmagicmod",
        "arcane_fighter", "arcane_rogue",
        "strength_base", "dexterity_base", "constitution_base",
        "intelligence_base", "wisdom_base", "charisma_base",
    ):
        if key in values:
            expected[key] = values[key]

    return {
        "version": STAGE5_VERSION,
        "source_character_id": _text(character.get("source_id")),
        "character_name": _text(character.get("name")),
        "write_fields": values,
        "expected_fields": {
            key: value for key, value in expected.items()
            if value is not None
        },
        "deferred": [
            "equipment",
            "spells",
            "features",
            "actions",
            "attacks",
            "skill_save_expertise_rows",
            "class_resources",
        ],
    }


FIND_SHEET_SCRIPT = r"""
const targetName = String(arguments[0] || '').trim();

function collectDocs(doc, where, out) {
  if (!doc) return;
  out.push([doc, where]);
  let frames = [];
  try { frames = Array.from(doc.querySelectorAll('iframe')); } catch (_) {}
  for (let i = 0; i < frames.length; i++) {
    try {
      if (frames[i].contentDocument) {
        collectDocs(frames[i].contentDocument, `${where}/iframe[${i}]`, out);
      }
    } catch (_) {}
  }
}

const docs = [];
collectDocs(document, 'document', docs);

for (const [doc, where] of docs) {
  let names = [];
  try {
    names = Array.from(doc.querySelectorAll('[name="attr_character_name"]'));
  } catch (_) {}

  const matched = names.some(el => String(el.value || '').trim() === targetName);
  if (!matched) continue;

  let count = 0;
  try { count = doc.querySelectorAll('[name^="attr_"]').length; } catch (_) {}

  return {ok: true, where, field_count: count};
}
return {ok: false, reason: 'target_sheet_iframe_not_found'};
"""


INSPECT_FIELDS_SCRIPT = r"""
const targetName = String(arguments[0] || '').trim();
const fieldNames = arguments[1] || [];

function collectDocs(doc, out) {
  if (!doc) return;
  out.push(doc);
  let frames = [];
  try { frames = Array.from(doc.querySelectorAll('iframe')); } catch (_) {}
  for (const frame of frames) {
    try {
      if (frame.contentDocument) collectDocs(frame.contentDocument, out);
    } catch (_) {}
  }
}

function targetDoc() {
  const docs = [];
  collectDocs(document, docs);
  for (const doc of docs) {
    let names = [];
    try { names = Array.from(doc.querySelectorAll('[name="attr_character_name"]')); } catch (_) {}
    if (names.some(el => String(el.value || '').trim() === targetName)) return doc;
  }
  return null;
}

function rowFor(doc, name) {
  const selector = `[name="attr_${CSS.escape(name)}"]`;
  let els = [];
  try { els = Array.from(doc.querySelectorAll(selector)); } catch (_) {}

  const options = [];
  for (const el of els) {
    let value = '';
    let checked = null;
    try {
      value = String(el.value == null ? '' : el.value);
      if (el.type === 'checkbox' || el.type === 'radio') checked = !!el.checked;
    } catch (_) {}
    let selectOptions = [];
    if (String(el.tagName || '').toLowerCase() === 'select') {
      try {
        selectOptions = Array.from(el.options || []).map(o => ({
          value: String(o.value || ''),
          text: String(o.textContent || '').trim(),
        }));
      } catch (_) {}
    }
    options.push({
      tag: String(el.tagName || '').toLowerCase(),
      type: String(el.getAttribute('type') || '').toLowerCase(),
      value,
      checked,
      select_options: selectOptions,
    });
  }
  return options;
}

const doc = targetDoc();
if (!doc) return {ok:false, reason:'target_sheet_iframe_not_found'};

const fields = {};
for (const name of fieldNames) fields[name] = rowFor(doc, name);
return {ok:true, fields};
"""


APPLY_FIELDS_SCRIPT = r"""
const targetName = String(arguments[0] || '').trim();
const changes = arguments[1] || {};

function collectDocs(doc, out) {
  if (!doc) return;
  out.push(doc);
  let frames = [];
  try { frames = Array.from(doc.querySelectorAll('iframe')); } catch (_) {}
  for (const frame of frames) {
    try {
      if (frame.contentDocument) collectDocs(frame.contentDocument, out);
    } catch (_) {}
  }
}

function targetDoc() {
  const docs = [];
  collectDocs(document, docs);
  for (const doc of docs) {
    let names = [];
    try { names = Array.from(doc.querySelectorAll('[name="attr_character_name"]')); } catch (_) {}
    if (names.some(el => String(el.value || '').trim() === targetName)) return doc;
  }
  return null;
}

function isVisible(el) {
  try {
    const r = el.getBoundingClientRect();
    const style = el.ownerDocument.defaultView.getComputedStyle(el);
    return r.width > 0 && r.height > 0 &&
      style.display !== 'none' && style.visibility !== 'hidden';
  } catch (_) {
    return false;
  }
}

function chooseElement(doc, name) {
  const selector = `[name="attr_${CSS.escape(name)}"]`;
  let els = Array.from(doc.querySelectorAll(selector));
  if (!els.length) return null;

  const visible = els.filter(isVisible);
  if (visible.length) return visible[0];

  const nonHidden = els.filter(el =>
    String(el.getAttribute('type') || '').toLowerCase() !== 'hidden'
  );
  if (nonHidden.length) return nonHidden[0];

  return els[0];
}

function setOne(doc, name, value) {
  const el = chooseElement(doc, name);
  if (!el) return {name, ok:false, reason:'field_not_found'};

  const tag = String(el.tagName || '').toLowerCase();
  const type = String(el.getAttribute('type') || '').toLowerCase();
  const oldValue = String(el.value == null ? '' : el.value);

  if (type === 'checkbox' || type === 'radio') {
    const wanted = String(value) === '1' || value === true || String(value).toLowerCase() === 'on';
    el.checked = wanted;
  } else if (tag === 'select') {
    const wanted = String(value);
    const exists = Array.from(el.options || []).some(o => String(o.value) === wanted);
    if (!exists) {
      return {
        name, ok:false, reason:'select_value_not_available',
        wanted,
        options:Array.from(el.options || []).map(o => String(o.value))
      };
    }
    el.value = wanted;
  } else {
    el.value = String(value);
  }

  try { el.dispatchEvent(new Event('input', {bubbles:true})); } catch (_) {}
  try { el.dispatchEvent(new Event('change', {bubbles:true})); } catch (_) {}

  return {
    name,
    ok:true,
    old_value:oldValue,
    new_value:(type === 'checkbox' || type === 'radio')
      ? (el.checked ? String(el.value || 'on') : '')
      : String(el.value == null ? '' : el.value),
  };
}

const doc = targetDoc();
if (!doc) return {ok:false, reason:'target_sheet_iframe_not_found', changed:[]};

const changed = [];
for (const [name, value] of Object.entries(changes)) {
  changed.push(setOne(doc, name, value));
}
return {ok: changed.every(x => x.ok), changed};
"""


def _target_path(source_id):
    return Path(CURRENT_RESULT_DIR) / f"roll20-target-{source_id}.json"


def _load_target(source_id):
    path = _target_path(source_id)
    if not path.is_file():
        raise RuntimeError(
            f"4단계 Roll20 연결 파일이 없습니다: {path.resolve()}"
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    if _text(payload.get("source_character_id")) != source_id:
        raise RuntimeError("Roll20 연결 파일의 source_character_id가 일치하지 않습니다.")
    if _text(payload.get("sheet_type")) != "ogl5e":
        raise RuntimeError(
            f"5단계는 ogl5e 시트만 지원합니다. 현재: {_text(payload.get('sheet_type')) or '미확인'}"
        )
    return payload, path


def _phase_plan(values):
    """Return ordered write phases to reduce OGL worker races."""
    phase1 = {}
    for key in ("class", "subclass", "arcane_fighter", "arcane_rogue"):
        if key in values:
            phase1[key] = values[key]

    phase2 = {}
    if "base_level" in values:
        phase2["base_level"] = values["base_level"]

    phase3 = {
        key: values[key]
        for key in (
            "strength_base", "dexterity_base", "constitution_base",
            "intelligence_base", "wisdom_base", "charisma_base",
        )
        if key in values
    }

    phase4 = {}
    for key in ("initmod", "spell_dc_mod", "globalmagicmod", "spellcasting_ability"):
        if key in values:
            phase4[key] = values[key]

    phase5 = {}
    for key in (
        "race", "subrace", "background", "alignment", "experience",
        "hp_max", "hp", "hp_temp", "speed", "ac",
    ):
        if key in values:
            phase5[key] = values[key]

    return [x for x in (phase1, phase2, phase3, phase4, phase5) if x]


def _normalize_dom_value(value):
    if value is None:
        return ""
    return str(value).strip()


def _current_value(rows):
    if not rows:
        return None
    # Prefer checked checkbox, then visible, then non-hidden, then first.
    for row in rows:
        if row.get("checked") is True:
            return _normalize_dom_value(row.get("value") or "on")
    for row in rows:
        if row.get("value") not in (None, ""):
            return _normalize_dom_value(row.get("value"))
    return _normalize_dom_value(rows[0].get("value"))


def _wait_for_class_option(driver, character_name, class_value, timeout=15.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = driver.execute_script(
            INSPECT_FIELDS_SCRIPT,
            character_name,
            ["class"],
        )
        rows = _dict(_dict(last).get("fields")).get("class") or []
        for row in rows:
            if row.get("tag") != "select":
                continue
            values = {str(x.get("value") or "") for x in row.get("select_options") or []}
            if class_value in values:
                return last
        time.sleep(0.5)
    return last


def _inspect(driver, character_name, names):
    result = driver.execute_script(
        INSPECT_FIELDS_SCRIPT,
        character_name,
        list(names),
    )
    if not isinstance(result, dict) or not result.get("ok"):
        raise RuntimeError(
            "Roll20에서 대상 캐릭터 시트 iframe을 찾지 못했습니다. "
            "견본 캐릭터 시트를 화면에 열어 두세요."
        )
    return _dict(result.get("fields"))


def _verify(fields, expected):
    mismatches = []
    actual = {}
    for name, wanted in expected.items():
        rows = fields.get(name) or []
        got = _current_value(rows)
        actual[name] = got
        wanted_text = _normalize_dom_value(wanted)

        # Checkbox values are represented as "1"/"on" when checked.
        if name in {"arcane_fighter", "arcane_rogue"}:
            checked = any(row.get("checked") is True for row in rows)
            okay = checked == (wanted_text == "1")
        else:
            okay = got == wanted_text

        if not okay:
            mismatches.append({
                "field": name,
                "expected": wanted_text,
                "actual": got,
            })
    return actual, mismatches


def run(source_id="170892133", cdp_url=DEFAULT_CDP_URL, apply=False):
    result_path = latest_complete_result(source_id=source_id)
    if result_path is None:
        raise RuntimeError(
            f"source_id={source_id}의 정상 결과 JSON을 찾지 못했습니다."
        )

    result_payload = load_result(result_path)
    plan = build_basic_plan(result_payload)
    target, target_path = _load_target(source_id)

    if _text(target.get("character_name")) != _text(plan.get("character_name")):
        raise RuntimeError(
            "D&D Beyond 결과와 Roll20 연결 파일의 캐릭터 이름이 다릅니다."
        )

    character_name = _text(plan["character_name"])
    write_fields = _dict(plan["write_fields"])
    expected_fields = _dict(plan["expected_fields"])

    _ensure_cdp(cdp_url)
    driver = _attach_driver(cdp_url)

    report = {
        "version": STAGE5_VERSION,
        "mode": "apply" if apply else "plan",
        "source_character_id": source_id,
        "character_name": character_name,
        "roll20_character_id": _text(target.get("roll20_character_id")),
        "result_path": str(Path(result_path).resolve()),
        "target_path": str(Path(target_path).resolve()),
        "write_fields": write_fields,
        "expected_fields": expected_fields,
        "mutated": False,
        "phases": [],
        "verification": {},
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }

    try:
        found = driver.execute_script(FIND_SHEET_SCRIPT, character_name)
        if not isinstance(found, dict) or not found.get("ok"):
            raise RuntimeError(
                "Roll20에서 대상 캐릭터 시트 iframe을 찾지 못했습니다. "
                f"'{character_name}' 시트를 화면에 열어 두세요."
            )

        # Fail closed before any mutation if the standard class selector has not
        # finished loading the expected English class option.
        class_value = write_fields["class"]
        class_state = _wait_for_class_option(
            driver,
            character_name,
            class_value,
            timeout=15.0,
        )
        rows = _dict(_dict(class_state).get("fields")).get("class") or []
        class_options = {
            str(opt.get("value") or "")
            for row in rows
            for opt in row.get("select_options") or []
        }
        if class_value not in class_options:
            raise RuntimeError(
                f"Roll20 class 선택지에 '{class_value}'가 아직 없습니다. "
                "캐릭터 시트를 잠시 열어 둔 뒤 다시 실행하세요."
            )

        # Ensure every planned field exists in the target sheet before writing.
        pre = _inspect(driver, character_name, write_fields.keys())
        missing = [name for name in write_fields if not (pre.get(name) or [])]
        if missing:
            raise RuntimeError(
                "Roll20 시트에서 5단계 필드를 찾지 못했습니다: "
                + ", ".join(sorted(missing))
            )

        # Verify select values before mutation.
        for name in ("class", "spellcasting_ability"):
            if name not in write_fields:
                continue
            rows = pre.get(name) or []
            wanted = write_fields[name]
            select_rows = [row for row in rows if row.get("tag") == "select"]
            if not select_rows:
                raise RuntimeError(f"Roll20 select 필드를 찾지 못했습니다: {name}")
            option_values = {
                str(opt.get("value") or "")
                for row in select_rows
                for opt in row.get("select_options") or []
            }
            if wanted not in option_values:
                raise RuntimeError(
                    f"Roll20 {name} 선택지에 값이 없습니다: {wanted}"
                )

        report["preflight"] = {
            "sheet_where": found.get("where"),
            "field_count": found.get("field_count"),
            "planned_write_count": len(write_fields),
            "status": "pass",
        }

        if not apply:
            return report

        for index, phase in enumerate(_phase_plan(write_fields), start=1):
            outcome = driver.execute_script(
                APPLY_FIELDS_SCRIPT,
                character_name,
                phase,
            )
            if not isinstance(outcome, dict) or not outcome.get("ok"):
                raise RuntimeError(
                    f"Roll20 5단계 {index}차 입력 실패: "
                    + json.dumps(outcome, ensure_ascii=False)
                )
            report["phases"].append({
                "phase": index,
                "fields": phase,
                "result": outcome,
            })
            report["mutated"] = True

            # Class/level/ability/spell changes can cascade through sheet workers.
            time.sleep(1.5 if index < 5 else 1.0)

        # Give OGL workers a final chance to settle.
        time.sleep(2.0)
        post = _inspect(driver, character_name, expected_fields.keys())
        actual, mismatches = _verify(post, expected_fields)

        report["verification"] = {
            "actual_fields": actual,
            "mismatches": mismatches,
            "status": "pass" if not mismatches else "fail",
        }
        if mismatches:
            raise RuntimeError(
                "Roll20 입력 후 검증이 일치하지 않습니다: "
                + json.dumps(mismatches, ensure_ascii=False)
            )

        return report
    finally:
        _disconnect_driver(driver)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-id",
        default="170892133",
        help="D&D Beyond source_character_id",
    )
    parser.add_argument(
        "--cdp-url",
        default=DEFAULT_CDP_URL,
        help="Roll20 전용 Chrome CDP 주소",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="실제로 5단계 기본 필드를 Roll20에 입력",
    )
    args = parser.parse_args()

    print("[시트 이동기] 5단계 기본 필드 매핑을 준비합니다.")
    print(f"[시트 이동기] 모드: {'실제 입력' if args.apply else '계획 확인(읽기 전용)'}")

    report = run(
        source_id=str(args.source_id),
        cdp_url=args.cdp_url,
        apply=args.apply,
    )

    output = Path(CURRENT_RESULT_DIR) / (
        f"roll20-basic-write-{args.source_id}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("[시트 이동기] 5단계 화이트리스트:")
    for name, value in report["write_fields"].items():
        print(f"  - {name} = {value}")

    if args.apply:
        print(
            "[시트 이동기] 입력 후 검증: "
            + str(report.get("verification", {}).get("status"))
        )
        print("[시트 이동기] 장비/주문/특성/행동/공격/기술·내성/자원은 수정하지 않았습니다.")
    else:
        print("[시트 이동기] Roll20 시트 값 변경: 0건")
        print("[시트 이동기] 실제 입력은 --apply를 붙였을 때만 수행합니다.")

    print(f"[시트 이동기] 결과 저장: {output.resolve()}")


if __name__ == "__main__":
    main()
