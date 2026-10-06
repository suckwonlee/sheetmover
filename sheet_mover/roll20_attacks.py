"""Stage 10A Roll20 Legacy OGL5e weapon attacks.

User-requested scope:
- ATTACKS & SPELLCASTING should contain actual attacks, not generic action cards.
- Remove only Stage 9 Sheet Mover action-card rows (War Bond, GWM Attack, etc.).
- Create actual weapon attacks from Stage 3 equipment.
- Preserve unrelated/manual Roll20 attack rows.
- Do not create feat/action cards here.
- Spell ATTACK/SPELLCARD conversion remains separate for the next Stage 10 pass.

Current sample produces: Javelin, Sickle, Flail, Greatsword.
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


STAGE10_VERSION = "2026-10-06-stage10a-roll20-weapon-attacks-v1"
ROW_PREFIX = "-SM"
ROW_HASH_LENGTH = 17

ATTACK_FLAG = "{{attack=1}}"
DMG_FLAG = "{{damage=1}} {{dmg1flag=1}}"
PB_FLAG = "(@{pb})"

ATTACK_FIELDS = (
    "options-flag",
    "atkname",
    "atkflag",
    "atkattr_base",
    "atkmod",
    "atkprofflag",
    "atkmagic",
    "atkcritrange",
    "atkrange",
    "dmgflag",
    "dmgbase",
    "dmgattr",
    "dmgmod",
    "dmgtype",
    "dmgcustcrit",
    "dmg2flag",
    "dmg2base",
    "dmg2attr",
    "dmg2mod",
    "dmg2type",
    "dmg2custcrit",
    "saveflag",
    "saveattr",
    "savedc",
    "saveflat",
    "saveeffect",
    "ammo",
    "atk_desc",
    "hldmg",
    "spelllevel",
    "itemid",
    "spellid",
    "spell_innate",
    "atkbonus",
    "atkdmgtype",
    "rollbase",
    "rollbase_dmg",
    "rollbase_crit",
)

ABILITY_ATTR = {
    "strength": "@{strength_mod}",
    "dexterity": "@{dexterity_mod}",
}

ABILITY_LABEL = {
    "strength": "STR",
    "dexterity": "DEX",
}


def _stable_row_id(seed: str) -> str:
    seed = _text(seed)
    if not seed:
        raise ValueError("반복행 seed가 비어 있습니다.")
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()
    row_id = ROW_PREFIX + digest[:ROW_HASH_LENGTH]
    if len(row_id) != 20 or "_" in row_id:
        raise AssertionError(f"잘못된 반복행 ID: {row_id}")
    return row_id


def weapon_attack_row_id(source_key: str) -> str:
    return _stable_row_id(f"weapon-attack:{_text(source_key)}")


def old_stage9_action_row_id(source_key: str) -> str:
    # Stage 9 v1 used sha1(source_key) directly.
    return _stable_row_id(_text(source_key))


def attack_attribute_name(row_id: str, field: str) -> str:
    if field not in ATTACK_FIELDS:
        raise ValueError(f"허용되지 않은 attack 필드: {field}")
    if not row_id or "_" in row_id:
        raise ValueError(f"잘못된 반복행 ID: {row_id}")
    return f"repeating_attack_{row_id}_{field}"


def _ability_modifier(score: Any) -> int:
    try:
        return (int(score) - 10) // 2
    except Exception:
        return 0


def _property_names(item: dict[str, Any]) -> set[str]:
    out = set()
    for prop in _list(item.get("properties")):
        prop = _dict(prop)
        name = _text(prop.get("original_name") or prop.get("name"))
        if name:
            out.add(name.casefold())
    return out


def _raw_inventory_map(result_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw_source = _dict(_dict(result_payload).get("raw_source"))
    out = {}
    for raw_item in _list(raw_source.get("inventory")):
        raw_item = _dict(raw_item)
        source_id = _text(raw_item.get("id"))
        if source_id:
            out[source_id] = _dict(raw_item.get("definition"))
    return out


def _original_proficiencies(result_payload: dict[str, Any]) -> set[str]:
    original = _dict(_dict(result_payload).get("original"))
    return {
        _text(value).casefold()
        for value in _list(original.get("proficiencies"))
        if _text(value)
    }


def _is_weapon_proficient(
    item: dict[str, Any],
    raw_definition: dict[str, Any],
    result_payload: dict[str, Any],
) -> bool:
    profs = _original_proficiencies(result_payload)
    original_name = _text(item.get("original_name")).casefold()
    category_id = raw_definition.get("categoryId")

    if original_name and original_name in profs:
        return True
    if category_id == 1 and "simple weapons" in profs:
        return True
    if category_id == 2 and "martial weapons" in profs:
        return True
    return False


def _attack_ability(
    item: dict[str, Any],
    raw_definition: dict[str, Any],
    character: dict[str, Any],
) -> tuple[str, int]:
    scores = _dict(character.get("ability_scores"))
    str_mod = _ability_modifier(scores.get("strength"))
    dex_mod = _ability_modifier(scores.get("dexterity"))

    raw_props = {
        _text(_dict(prop).get("name")).casefold()
        for prop in _list(raw_definition.get("properties"))
        if _text(_dict(prop).get("name"))
    }
    props = _property_names(item) | raw_props

    attack_type = raw_definition.get("attackType")

    # DDB attackType 2 is a ranged weapon attack. A thrown melee weapon keeps
    # the same ability it uses in melee, so Javelin (attackType 1 + Thrown)
    # remains Strength.
    if attack_type == 2:
        return "dexterity", dex_mod

    if "finesse" in props and dex_mod > str_mod:
        return "dexterity", dex_mod

    return "strength", str_mod


def _format_range(item, raw_definition) -> str:
    attack_type = raw_definition.get("attackType")
    normal = raw_definition.get("range")
    long_range = raw_definition.get("longRange")

    props = {
        _text(_dict(prop).get("name")).casefold()
        for prop in _list(raw_definition.get("properties"))
        if _text(_dict(prop).get("name"))
    } | _property_names(item)

    def num(value):
        if value is None or value == "":
            return ""
        try:
            f = float(value)
            return str(int(f)) if f.is_integer() else str(f)
        except Exception:
            return _text(value)

    if attack_type == 1 and "thrown" in props and normal not in (None, ""):
        ranged = num(normal)
        if long_range not in (None, "") and num(long_range) != ranged:
            ranged = f"{ranged}/{num(long_range)}"
        return f"5 ft. / {ranged} ft."

    if attack_type == 2 and normal not in (None, ""):
        ranged = num(normal)
        if long_range not in (None, "") and num(long_range) != ranged:
            ranged = f"{ranged}/{num(long_range)}"
        return f"{ranged} ft."

    normal = item.get("range")
    if normal not in (None, ""):
        return f"{num(normal)} ft."
    return "5 ft."


def _dice_string(item, raw_definition) -> str:
    damage = _dict(item.get("damage"))
    value = _text(damage.get("diceString"))
    if value:
        return value
    raw_damage = _dict(raw_definition.get("damage"))
    return _text(raw_damage.get("diceString"))


def _damage_type(item, raw_definition) -> str:
    return _text(item.get("damage_type") or raw_definition.get("damageType"))


def _format_signed(value: int) -> str:
    return f"+{value}" if value >= 0 else str(value)


def _rollbase(
    *,
    ability_mod: int,
    ability_label: str,
    proficient: bool,
) -> str:
    hbonus = f" + {ability_mod}[{ability_label}]"
    if proficient:
        hbonus += " + @{pb}[PROF]"

    return (
        "@{wtype}&{template:atkdmg} "
        "{{mod=@{atkbonus}}} "
        "{{rname=@{atkname}}} "
        f"{{{{r1=[[@{{d20}}cs>@{{atkcritrange}}{hbonus}]]}}}} "
        f"@{{rtype}}cs>@{{atkcritrange}}{hbonus}]]}} "
        "@{atkflag} "
        "{{range=@{atkrange}}} "
        "@{dmgflag} "
        "{{dmg1=[[@{dmgbase} + "
        f"{ability_mod}[{ability_label}]]]}} "
        "{{dmg1type=@{dmgtype}}} "
        "{{crit1=[[@{dmgcustcrit}]]}} "
        "{{desc=@{atk_desc}}} "
        "@{charname_output} "
        "{{licensedsheet=@{licensedsheet}}}"
    )


def _rollbase_damage(*, ability_mod: int, ability_label: str) -> str:
    return (
        "@{wtype}&{template:dmg} "
        "{{rname=@{atkname}}} "
        "{{range=@{atkrange}}} "
        "@{dmgflag} "
        "{{dmg1=[[@{dmgbase} + "
        f"{ability_mod}[{ability_label}]]]}} "
        "{{dmg1type=@{dmgtype}}} "
        "{{desc=@{atk_desc}}} "
        "@{charname_output} "
        "{{licensedsheet=@{licensedsheet}}}"
    )


def map_weapon_attack(
    item: dict[str, Any],
    character: dict[str, Any],
    raw_definition: dict[str, Any],
    result_payload: dict[str, Any],
) -> dict[str, Any]:
    item = _dict(item)
    source_key = _text(item.get("source_key"))
    row_id = weapon_attack_row_id(source_key)

    name = _plain_text(item.get("name") or item.get("original_name"))
    if not name:
        raise ValueError(f"무기 이름이 비어 있습니다: {source_key}")

    dice = _dice_string(item, raw_definition)
    damage_type = _damage_type(item, raw_definition)
    if not dice or not damage_type:
        raise ValueError(f"무기 피해 정보가 없습니다: {name}")

    ability_name, ability_mod = _attack_ability(
        item,
        raw_definition,
        character,
    )
    ability_attr = ABILITY_ATTR[ability_name]
    ability_label = ABILITY_LABEL[ability_name]

    proficient = _is_weapon_proficient(
        item,
        raw_definition,
        result_payload,
    )
    pb = int(character.get("proficiency_bonus") or 0)
    attack_bonus = ability_mod + (pb if proficient else 0)
    damage_total = f"{dice}{_format_signed(ability_mod)}"

    description = _plain_text(item.get("description"))
    attack_range = _format_range(item, raw_definition)

    fields = {
        "options-flag": "0",
        "atkname": name,
        "atkflag": ATTACK_FLAG,
        "atkattr_base": ability_attr,
        "atkmod": "",
        "atkprofflag": PB_FLAG if proficient else "0",
        "atkmagic": "",
        "atkcritrange": "20",
        "atkrange": attack_range,

        "dmgflag": DMG_FLAG,
        "dmgbase": dice,
        "dmgattr": ability_attr,
        "dmgmod": "",
        "dmgtype": damage_type,
        # Roll20 defaults crit1 to base damage if empty, but our direct
        # rollbase references this field, so store the base dice explicitly.
        "dmgcustcrit": dice,

        "dmg2flag": "0",
        "dmg2base": "",
        "dmg2attr": "0",
        "dmg2mod": "",
        "dmg2type": "",
        "dmg2custcrit": "",

        "saveflag": "0",
        "saveattr": "",
        "savedc": "",
        "saveflat": "",
        "saveeffect": "",

        "ammo": "",
        "atk_desc": description,
        "hldmg": "",
        "spelllevel": "",
        "itemid": "",
        "spellid": "",
        "spell_innate": "",

        "atkbonus": _format_signed(attack_bonus),
        "atkdmgtype": f"{damage_total} {damage_type}",
        "rollbase": _rollbase(
            ability_mod=ability_mod,
            ability_label=ability_label,
            proficient=proficient,
        ),
        "rollbase_dmg": _rollbase_damage(
            ability_mod=ability_mod,
            ability_label=ability_label,
        ),
        "rollbase_crit": "",
    }

    return {
        "source_key": source_key,
        "source_id": _text(item.get("source_id")),
        "definition_id": _text(item.get("definition_id")),
        "row_id": row_id,
        "name": name,
        "ability": ability_name,
        "ability_modifier": ability_mod,
        "proficient": proficient,
        "proficiency_bonus": pb if proficient else 0,
        "attack_bonus": attack_bonus,
        "damage_display": f"{damage_total} {damage_type}",
        "range": attack_range,
        "fields": fields,
    }


def _row_attributes(row):
    row_id = _text(row.get("row_id"))
    fields = _dict(row.get("fields"))
    return {
        attack_attribute_name(row_id, field): {
            "current": _text(fields.get(field)),
            "max": "",
        }
        for field in ATTACK_FIELDS
    }


def plan_attack_attributes(plan):
    attrs = {}
    for row in _list(plan.get("rows")):
        for name, spec in _row_attributes(row).items():
            if name in attrs:
                raise RuntimeError(f"Roll20 attack attribute 이름 중복: {name}")
            attrs[name] = spec
    return attrs


def build_attack_plan(result_payload: dict[str, Any]) -> dict[str, Any]:
    roll20_payload = _dict(_dict(result_payload).get("roll20_payload"))
    character = _dict(roll20_payload.get("character"))
    equipment = _list(roll20_payload.get("equipment"))
    actions = _list(roll20_payload.get("actions"))

    source_character_id = _text(roll20_payload.get("source_character_id"))
    character_name = _text(character.get("name"))

    if not source_character_id:
        raise RuntimeError("roll20_payload.source_character_id가 없습니다.")
    if not character_name:
        raise RuntimeError("roll20_payload.character.name이 없습니다.")

    raw_map = _raw_inventory_map(result_payload)

    rows = []
    for item in equipment:
        item = _dict(item)
        if _text(item.get("item_type")).casefold() != "weapon":
            continue
        if not _dict(item.get("damage")):
            continue

        raw_definition = raw_map.get(_text(item.get("source_id")), {})
        rows.append(
            map_weapon_attack(
                item,
                character,
                raw_definition,
                result_payload,
            )
        )

    cleanup_stage9_row_ids = [
        old_stage9_action_row_id(_text(action.get("source_key")))
        for action in actions
        if _text(_dict(action).get("source_key"))
    ]

    return {
        "version": STAGE10_VERSION,
        "source_character_id": source_character_id,
        "character_name": character_name,
        "row_count": len(rows),
        "rows": rows,
        "cleanup_stage9_row_ids": cleanup_stage9_row_ids,
        "policy": {
            "attack_section_contains_actual_attacks_only": True,
            "cleanup_stage9_action_cards": True,
            "preserve_unmanaged_rows": True,
            "delete_manual_rows": False,
            "include_weapon_items": True,
            "include_nonweapon_actions": False,
            "include_spell_attacks": False,
            "resource_links_enabled": False,
            "collapse_rows": True,
        },
        "deferred": [
            "spell_attack_output",
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
            f"10단계는 ogl5e만 지원합니다: "
            f"{_text(payload.get('sheet_type')) or '미확인'}"
        )
    if not _text(payload.get("roll20_character_id")):
        raise RuntimeError("Roll20 Character ID가 없습니다.")
    return payload, path


ATTACK_STATE_SCRIPT = r"""
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

const character=findCharacter();
if (!character) { done({ok:false,reason:'character_not_found'}); return; }
const collection=character.attribs;
if (!collection || typeof collection.fetch !== 'function') {
  done({ok:false,reason:'attribute_collection_unavailable'}); return;
}

let settled=false;
function finish(status,error) {
  if(settled)return;
  settled=true;
  const rows={};
  let reporder='';
  for(const model of modelsOf(collection)) {
    const name=String(val(model,'name') || '').trim();
    if(name === '_reporder_repeating_attack') {
      reporder=String(val(model,'current') == null ? '' : val(model,'current'));
      continue;
    }
    const match=name.match(/^repeating_attack_([^_]+)_(.+)$/);
    if(!match) continue;
    const rowid=match[1];
    if(!rows[rowid]) rows[rowid]={};
    rows[rowid][name]={
      id:idOf(model),
      current:String(val(model,'current') == null ? '' : val(model,'current')),
      max:String(val(model,'max') == null ? '' : val(model,'max')),
    };
  }
  done({
    ok:true,
    fetch_status:status,
    fetch_error:error || '',
    rows,
    reporder,
  });
}

try {
  const req=collection.fetch({
    reset:false,
    success:()=>finish('success',''),
    error:(_c,xhr)=>finish('error',`status=${xhr&&xhr.status}; text=${xhr&&xhr.statusText}`),
  });
  if(req && typeof req.then==='function') {
    req.then(()=>finish('success_promise',''),e=>finish('error_promise',String(e||'')));
  }
  setTimeout(()=>finish('timeout','fetch callback timeout'),12000);
} catch(e) {
  finish('exception',String(e && e.stack ? e.stack : e));
}
"""


DELETE_ATTACK_ROWS_SCRIPT = r"""
const wantedId = String(arguments[0] || '').trim();
const wantedName = String(arguments[1] || '').trim();
const rowIds = new Set(arguments[2] || []);
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

  const targets=[];
  for(const model of modelsOf(collection)) {
    const name=String(val(model,'name') || '').trim();
    const match=name.match(/^repeating_attack_([^_]+)_/);
    if(match && rowIds.has(match[1])) targets.push([model,name]);
  }

  try {
    const deleted=[];
    for(const [model,name] of targets) {
      deleted.push(await waitDestroy(model,name));
    }
    done({ok:true,deleted_count:deleted.length,deleted});
  } catch(e) {
    done({ok:false,reason:'delete_failed',error:String(e && e.stack ? e.stack : e)});
  }
})();
"""


def _attack_state(driver, target):
    driver.set_script_timeout(25)
    result = driver.execute_async_script(
        ATTACK_STATE_SCRIPT,
        _text(target.get("roll20_character_id")),
        _text(target.get("character_name")),
    )
    if not isinstance(result, dict) or not result.get("ok"):
        raise RuntimeError(
            "Roll20 attack 상태 읽기 실패: "
            + json.dumps(result, ensure_ascii=False)
        )
    return result


def _delete_rows(driver, target, row_ids):
    row_ids = [rid for rid in row_ids if rid]
    if not row_ids:
        return {"ok": True, "deleted_count": 0, "deleted": []}
    driver.set_script_timeout(max(45, len(row_ids) * 20))
    result = driver.execute_async_script(
        DELETE_ATTACK_ROWS_SCRIPT,
        _text(target.get("roll20_character_id")),
        _text(target.get("character_name")),
        row_ids,
    )
    if not isinstance(result, dict) or not result.get("ok"):
        raise RuntimeError(
            "기존 9단계 행동 카드 삭제 실패: "
            + json.dumps(result, ensure_ascii=False)
        )
    return result


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


def apply_attacks(
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
    plan = build_attack_plan(payload)
    actual_source_id = plan["source_character_id"]
    target, target_path = _load_target(actual_source_id)

    if _text(target.get("character_name")) != plan["character_name"]:
        raise RuntimeError("D&D Beyond 결과와 Roll20 대상 캐릭터 이름이 다릅니다.")

    all_attrs = plan_attack_attributes(plan)
    output_path = (
        Path(CURRENT_RESULT_DIR)
        / f"roll20-attacks-{actual_source_id}.json"
    )

    report = {
        "version": STAGE10_VERSION,
        "mode": "dry-run" if dry_run else "apply",
        "source_character_id": actual_source_id,
        "character_name": plan["character_name"],
        "roll20_character_id": target["roll20_character_id"],
        "result_path": str(path.resolve()),
        "target_path": str(Path(target_path).resolve()),
        "row_count": plan["row_count"],
        "managed_attribute_count": len(all_attrs),
        "policy": plan["policy"],
        "rows": plan["rows"],
        "cleanup_stage9_row_ids": plan["cleanup_stage9_row_ids"],
        "backup_path": None,
        "mutated": False,
        "cleanup_result": None,
        "row_results": [],
        "verification": {},
        "status": "running",
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }

    _ensure_cdp(cdp_url)
    driver = _attach_driver(cdp_url)

    try:
        _select_roll20_tab(driver)

        attack_state_before = _attack_state(driver, target)
        before = _snapshot(driver, target, all_attrs.keys())
        report["before"] = {
            "managed_attributes": before["attributes"],
            "attack_state": attack_state_before,
        }

        existing_rows = set(_dict(attack_state_before.get("rows")).keys())
        cleanup_present = [
            rid for rid in plan["cleanup_stage9_row_ids"]
            if rid in existing_rows
        ]
        report["cleanup_present_before"] = cleanup_present

        pending_rows = []
        for row in plan["rows"]:
            attrs = _row_attributes(row)
            if not _is_complete(before, attrs):
                pending_rows.append(row)

        report["initial_pending_rows"] = [
            {
                "source_key": row["source_key"],
                "row_id": row["row_id"],
                "name": row["name"],
                "attack_bonus": row["attack_bonus"],
                "damage_display": row["damage_display"],
            }
            for row in pending_rows
        ]

        if dry_run:
            report["status"] = "pass"
            report["verification"] = {
                "status": "not_run",
                "reason": "dry_run",
            }
            _save_json(output_path, report)
            return report, output_path

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = (
            Path(CURRENT_RESULT_DIR)
            / f"roll20-stage10a-attacks-backup-"
              f"{actual_source_id}-{timestamp}.json"
        )
        _save_json(
            backup_path,
            {
                "version": STAGE10_VERSION,
                "source_character_id": actual_source_id,
                "character_name": plan["character_name"],
                "roll20_character_id": target["roll20_character_id"],
                "read_only_snapshot_before_apply": True,
                "attack_state": attack_state_before,
                "managed_attributes": before["attributes"],
            },
        )
        report["backup_path"] = str(backup_path.resolve())
        _save_json(output_path, report)

        if cleanup_present:
            print(
                f"[시트 이동기] 9단계 행동 카드 {len(cleanup_present)}개를 "
                "ATTACKS & SPELLCASTING에서 제거합니다.",
                flush=True,
            )
            report["cleanup_result"] = _delete_rows(
                driver,
                target,
                cleanup_present,
            )
            report["mutated"] = True
            _save_json(output_path, report)

        total = len(pending_rows)
        for index, row in enumerate(pending_rows, start=1):
            print(
                f"[시트 이동기] 실제 무기 공격 {index}/{total}: "
                f"{row['name']} "
                f"(명중 {_format_signed(row['attack_bonus'])}, "
                f"{row['damage_display']})",
                flush=True,
            )
            attrs = _row_attributes(row)
            outcome, actual = _upsert_and_verify(
                driver,
                target,
                attrs,
                f"무기 공격 '{row['name']}'",
            )
            report["mutated"] = True
            report["row_results"].append({
                "source_key": row["source_key"],
                "row_id": row["row_id"],
                "name": row["name"],
                "result": outcome,
                "verification": {
                    "status": "pass",
                    "actual": actual,
                    "mismatches": [],
                },
            })
            _save_json(output_path, report)

        final_snapshot = _snapshot(driver, target, all_attrs.keys())
        actual, mismatches = _verify(final_snapshot, all_attrs)

        final_state = _attack_state(driver, target)
        final_existing = set(_dict(final_state.get("rows")).keys())
        stale_stage9 = [
            rid for rid in plan["cleanup_stage9_row_ids"]
            if rid in final_existing
        ]
        if stale_stage9:
            mismatches.append({
                "reason": "stage9_action_rows_still_present",
                "row_ids": stale_stage9,
            })

        report["verification"] = {
            "status": "pass" if not mismatches else "fail",
            "actual": actual,
            "stage9_action_rows_absent": not stale_stage9,
            "mismatches": mismatches,
            "server_fetch_status": final_snapshot.get("fetch_status"),
        }
        report["status"] = "pass" if not mismatches else "error"

        if mismatches:
            report["error"] = (
                "10A단계 실제 무기 공격 최종 서버 재검증 실패: "
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

    print("[시트 이동기] 10A단계: 실제 무기 공격만 입력")
    print(f"[시트 이동기] 모드: {'읽기 전용' if args.dry_run else '실제 입력'}")
    print("[시트 이동기] 전쟁의 유대/GWM 행동/재기의 바람 같은 행동 카드는 제거합니다.")
    print("[시트 이동기] ATTACKS & SPELLCASTING에는 실제 무기 공격만 남깁니다.")
    print("[시트 이동기] 주문 ATTACK 출력 연동은 다음 10단계 패스에서 처리합니다.")
    print("[시트 이동기] 기존 수동 공격행은 삭제하지 않습니다.")

    report, output = apply_attacks(
        result_path=args.result,
        source_id=args.source_id,
        cdp_url=args.cdp_url,
        dry_run=args.dry_run,
    )

    print(f"[시트 이동기] 실제 무기 공격: {report['row_count']}개")
    for row in report["rows"]:
        print(
            f"  - {row['name']}: "
            f"명중 {_format_signed(row['attack_bonus'])} / "
            f"{row['damage_display']} / {row['range']}"
        )

    if not args.dry_run:
        print(f"[시트 이동기] 백업: {report.get('backup_path')}")
        print(
            "[시트 이동기] 최종 서버 재검증: "
            + str((report.get("verification") or {}).get("status"))
        )
    print(f"[시트 이동기] 결과 저장: {output.resolve()}")


if __name__ == "__main__":
    main()
