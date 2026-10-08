"""Stage 12 Roll20 Legacy OGL5e limited-use resource writer.

Placement policy:
1. Preserve manual fixed resource slots.
2. Reuse fixed slots previously owned by Sheet Mover.
3. Fill an available CLASS RESOURCE, then OTHER RESOURCE.
4. Put remaining resources in deterministic repeating_resource rows.
5. Preserve unrelated/manual repeating_resource rows.

D&D Beyond `numberUsed` is converted to Roll20 remaining uses:
    current = maxUses - numberUsed

This stage does not invent a separate resource for features that consume
another feature's resource (e.g. Tactical Mind consumes Second Wind).
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


STAGE12_VERSION = "2026-10-08-stage12-roll20-resources-v1.4-dynamic-uses"
ROW_PREFIX = "-SM"
ROW_HASH_LENGTH = 17

FIXED_SLOTS = ("class_resource", "other_resource")

REPEATING_FIELDS = (
    "resource_left",
    "resource_left_name",
    "resource_left_itemid",
    "resource_left_reset",
    "resource_right",
    "resource_right_name",
    "resource_right_itemid",
    "resource_right_reset",
)


def _stable_row_id(seed: str) -> str:
    seed = _text(seed)
    if not seed:
        raise ValueError("resource seed가 비어 있습니다.")
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()
    row_id = ROW_PREFIX + digest[:ROW_HASH_LENGTH]
    if len(row_id) != 20 or "_" in row_id:
        raise AssertionError(f"잘못된 반복행 ID: {row_id}")
    return row_id


def resource_row_id(source_key: str) -> str:
    return _stable_row_id(f"stage12-resource:{_text(source_key)}")


def _as_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


def _resource_values(result_payload):
    translated = _dict(_dict(result_payload).get("translated"))
    original = _dict(_dict(result_payload).get("original"))

    translated_values = _list(translated.get("resources"))
    original_values = _list(original.get("resources"))

    if not translated_values:
        return original_values
    if not original_values:
        return translated_values

    merged = list(translated_values)
    seen = set()

    for item in translated_values:
        item = _dict(item)
        seen.add(
            (
                _text(item.get("source_id")),
                _text(item.get("kind")),
                _text(item.get("original_name") or item.get("name")),
            )
        )

    for item in original_values:
        item = _dict(item)
        identity = (
            _text(item.get("source_id")),
            _text(item.get("kind")),
            _text(item.get("original_name") or item.get("name")),
        )
        if identity in seen:
            continue
        seen.add(identity)
        merged.append(item)

    return merged


def _resource_source_key(item, index):
    item = _dict(item)
    source_id = _text(item.get("source_id"))
    kind = _text(item.get("kind")) or "resource"
    original_name = _text(item.get("original_name") or item.get("name"))

    if source_id:
        return f"resource:{kind}:{source_id}"

    digest = hashlib.sha1(
        f"{kind}|{original_name}|{index}".encode("utf-8")
    ).hexdigest()[:12]
    return f"resource:{kind}:fallback:{digest}"


STAT_ID_TO_ABILITY = {
    1: "strength",
    2: "dexterity",
    3: "constitution",
    4: "intelligence",
    5: "wisdom",
    6: "charisma",
}


def _ability_modifier(score):
    if type(score) not in (int, float):
        return None
    return (int(score) - 10) // 2


def _max_uses(limited_use, proficiency_bonus, ability_scores=None):
    limited_use = _dict(limited_use)
    ability_scores = _dict(ability_scores)

    raw_max = limited_use.get("maxUses")
    base = (
        _as_int(raw_max, 0)
        if raw_max not in (None, "")
        else 0
    )

    dynamic = False

    stat_id = _as_int(limited_use.get("statModifierUsesId"), 0)
    ability = STAT_ID_TO_ABILITY.get(stat_id)
    if ability:
        dynamic = True
        modifier = _ability_modifier(ability_scores.get(ability))
        operator = limited_use.get("operator")
        if modifier is None:
            return 0
        if operator == 1:
            base += modifier
        elif operator in (None, "") and raw_max in (None, "", 0):
            # Backward-compatible DDB shape: a stat modifier with no explicit
            # operator and no fixed base meant "ability modifier uses".
            base += modifier
        else:
            return 0

    if limited_use.get("useProficiencyBonus") is True:
        dynamic = True
        pb_operator = limited_use.get("proficiencyBonusOperator")
        if pb_operator not in (None, "", 1):
            return 0
        pb = _as_int(proficiency_bonus, -1)
        if pb < 0:
            return 0
        base += pb

    if dynamic:
        return max(0, base)

    if raw_max not in (None, ""):
        return max(0, base)

    return 0


def _roll20_reset_type(limited_use):
    """Map D&D Beyond limited-use reset semantics to Legacy Roll20.

    Current DDB model values used by Sheet Mover:
    - resetType 1: short-rest full recharge
    - resetType 2: long-rest recharge

    Legacy Roll20 supports only full reset on a short/long rest. A resource
    such as 2024 Second Wind, which only regains one expended use on a short
    rest, cannot be represented exactly; the project policy is to treat that
    resource as long-rest recharge instead.
    """
    limited_use = _dict(limited_use)
    reset_type = limited_use.get("resetType")

    if reset_type == 1:
        return "short"
    if reset_type == 2:
        return "long"
    return ""


def build_resource_candidates(result_payload: dict[str, Any]):
    roll20_payload = _dict(_dict(result_payload).get("roll20_payload"))
    character = _dict(roll20_payload.get("character"))
    proficiency_bonus = _as_int(character.get("proficiency_bonus"), 0)
    ability_scores = _dict(character.get("ability_scores"))

    candidates = []
    seen = set()
    semantic_seen = set()

    for index, raw in enumerate(_resource_values(result_payload)):
        item = _dict(raw)
        limited = _dict(item.get("limited_use"))
        if not limited:
            continue

        maximum = _max_uses(limited, proficiency_bonus, ability_scores)
        if maximum <= 0:
            continue

        source_key = _resource_source_key(item, index)
        if source_key in seen:
            raise RuntimeError(f"자원 source_key 중복: {source_key}")
        seen.add(source_key)

        used = max(0, _as_int(limited.get("numberUsed"), 0))
        remaining = max(0, maximum - used)

        name = _text(item.get("name") or item.get("original_name"))
        if not name:
            raise RuntimeError(f"자원 이름이 비어 있습니다: {source_key}")

        semantic_key = (
            _text(item.get("kind")).casefold(),
            _text(item.get("original_name") or name).casefold(),
            maximum,
            used,
            limited.get("resetType"),
            limited.get("statModifierUsesId"),
            limited.get("useProficiencyBonus"),
            limited.get("proficiencyBonusOperator"),
            limited.get("operator"),
        )
        if semantic_key in semantic_seen:
            continue
        semantic_seen.add(semantic_key)

        candidates.append({
            "source_key": source_key,
            "source_id": _text(item.get("source_id")),
            "kind": _text(item.get("kind")),
            "name": name,
            "original_name": _text(item.get("original_name") or name),
            "current": remaining,
            "maximum": maximum,
            "number_used": used,
            "reset_type": limited.get("resetType"),
            "roll20_reset": _roll20_reset_type(limited),
            "limited_use": limited,
        })

    return candidates


def _snapshot_current(snapshot, name):
    rows = _dict(snapshot.get("attributes")).get(name) or []
    if len(rows) != 1:
        return ""
    return _text(_dict(rows[0]).get("current"))


def _snapshot_max(snapshot, name):
    rows = _dict(snapshot.get("attributes")).get(name) or []
    if len(rows) != 1:
        return ""
    return _text(_dict(rows[0]).get("max"))


def _fixed_slot_names(prefix):
    # In Roll20 sheet HTML, attr_<name>_max maps to the max property of the
    # same Attribute model. There is no separate "<name>_max" Attribute.
    return (
        prefix,
        f"{prefix}_name",
        f"{prefix}_reset",
    )


def _slot_blank(snapshot, prefix):
    current = _snapshot_current(snapshot, prefix)
    maximum = _snapshot_max(snapshot, prefix)
    name = _snapshot_current(snapshot, f"{prefix}_name")

    return (
        not name
        and current in {"", "0"}
        and maximum in {"", "0"}
    )


def _previous_report(output_path):
    if output_path.is_file():
        try:
            return json.loads(output_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    try:
        from .run_log import previous_stage_report
        return previous_stage_report(
            output_path.parent,
            "resources",
        )
    except Exception:
        return {}


def _previous_fixed_assignments(previous):
    raw = _dict(previous.get("fixed_slot_assignments"))
    return {
        slot: _text(raw.get(slot))
        for slot in FIXED_SLOTS
        if _text(raw.get(slot))
    }


def _previous_repeating_ids(previous):
    return [
        _text(v)
        for v in _list(previous.get("managed_repeating_row_ids"))
        if _text(v)
    ]


def place_resources(candidates, fixed_snapshot, previous=None):
    previous = _dict(previous)
    previous_fixed = _previous_fixed_assignments(previous)

    by_key = {
        row["source_key"]: row
        for row in candidates
    }
    assigned = {}
    used_keys = set()

    # First retain any still-valid previous Sheet Mover fixed ownership.
    for slot in FIXED_SLOTS:
        old_key = _text(previous_fixed.get(slot))
        if old_key and old_key in by_key:
            assigned[slot] = by_key[old_key]
            used_keys.add(old_key)

    # Then fill only slots that are genuinely blank or previously managed.
    for slot in FIXED_SLOTS:
        if slot in assigned:
            continue

        previously_owned = bool(_text(previous_fixed.get(slot)))
        if not previously_owned and not _slot_blank(fixed_snapshot, slot):
            continue

        next_resource = next(
            (
                row for row in candidates
                if row["source_key"] not in used_keys
            ),
            None,
        )
        if next_resource is not None:
            assigned[slot] = next_resource
            used_keys.add(next_resource["source_key"])

    repeating = [
        row for row in candidates
        if row["source_key"] not in used_keys
    ]

    # A previously owned fixed slot that no longer has a resource is cleared.
    clear_fixed = [
        slot for slot in FIXED_SLOTS
        if _text(previous_fixed.get(slot)) and slot not in assigned
    ]

    return {
        "fixed": assigned,
        "clear_fixed": clear_fixed,
        "repeating": repeating,
    }


def _fixed_attributes(placement):
    attrs = {}

    for slot, resource in _dict(placement.get("fixed")).items():
        # Legacy sheet attr_<slot>_max is the max property of attr_<slot>.
        attrs[slot] = {
            "current": str(resource["current"]),
            "max": str(resource["maximum"]),
        }
        attrs[f"{slot}_name"] = {
            "current": resource["name"],
            "max": "",
        }
        attrs[f"{slot}_reset"] = {
            "current": _text(resource.get("roll20_reset")),
            "max": "",
        }

    for slot in _list(placement.get("clear_fixed")):
        attrs[slot] = {"current": "", "max": ""}
        attrs[f"{slot}_name"] = {"current": "", "max": ""}
        attrs[f"{slot}_reset"] = {"current": "", "max": ""}

    return attrs


def _repeating_row(resource):
    row_id = resource_row_id(resource["source_key"])
    fields = {
        "resource_left": str(resource["current"]),
        "resource_left_name": resource["name"],
        "resource_left_itemid": "",
        "resource_left_reset": _text(resource.get("roll20_reset")),
        "resource_right": "",
        "resource_right_name": "",
        "resource_right_itemid": "",
        "resource_right_reset": "",
    }
    return {
        **resource,
        "row_id": row_id,
        "fields": fields,
    }


def _repeating_attribute_name(row_id, field):
    if field not in REPEATING_FIELDS:
        raise ValueError(f"허용되지 않은 resource 필드: {field}")
    return f"repeating_resource_{row_id}_{field}"


def _repeating_attributes(rows):
    attrs = {}
    for row in rows:
        row_id = row["row_id"]
        fields = _dict(row.get("fields"))
        for field in REPEATING_FIELDS:
            name = _repeating_attribute_name(row_id, field)
            if name in attrs:
                raise RuntimeError(f"resource attribute 중복: {name}")

            if field == "resource_left":
                attrs[name] = {
                    "current": _text(fields.get(field)),
                    "max": str(row["maximum"]),
                }
            elif field == "resource_right":
                attrs[name] = {
                    "current": _text(fields.get(field)),
                    "max": "",
                }
            else:
                attrs[name] = {
                    "current": _text(fields.get(field)),
                    "max": "",
                }
    return attrs


RESOURCE_STATE_SCRIPT = r"""
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

  const rowIds=[];
  const seen=new Set();
  let reporder='';

  for(const model of modelsOf(collection)) {
    const name=String(val(model,'name') || '').trim();
    if(name === '_reporder_repeating_resource') {
      reporder=String(val(model,'current') == null ? '' : val(model,'current'));
      continue;
    }
    const match=name.match(/^repeating_resource_([^_]+)_/);
    if(match && !seen.has(match[1])) {
      seen.add(match[1]);
      rowIds.push(match[1]);
    }
  }

  done({
    ok:status === 'success' || status === 'success_promise',
    fetch_status:status,
    fetch_error:error || '',
    row_ids:rowIds,
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


DELETE_RESOURCE_ROWS_SCRIPT = r"""
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
    const match=name.match(/^repeating_resource_([^_]+)_/);
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


def _resource_state(driver, target):
    from .roll20_read import read_persisted
    return read_persisted(
        driver,
        RESOURCE_STATE_SCRIPT,
        _text(target.get("roll20_character_id")),
        _text(target.get("character_name")),
    )


def _split_reporder(value):
    return [
        part.strip()
        for part in _text(value).split(",")
        if part.strip()
    ]


def _resource_reporder(state, managed_row_ids):
    managed = [_text(v) for v in managed_row_ids if _text(v)]
    managed_set = set(managed)

    current = _split_reporder(state.get("reporder"))
    existing = [
        _text(v)
        for v in _list(state.get("row_ids"))
        if _text(v)
    ]

    manual = []
    seen = set()
    for row_id in current + existing:
        if row_id in managed_set or row_id in seen:
            continue
        seen.add(row_id)
        manual.append(row_id)

    return ",".join(managed + manual)


def _load_target(source_id: str):
    path = Path(CURRENT_RESULT_DIR) / f"roll20-target-{source_id}.json"
    if not path.is_file():
        raise RuntimeError(f"4단계 연결 파일이 없습니다: {path.resolve()}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if _text(payload.get("source_character_id")) != source_id:
        raise RuntimeError("Roll20 연결 파일 source ID가 다릅니다.")
    if _text(payload.get("sheet_type")) != "ogl5e":
        raise RuntimeError(
            f"12단계는 ogl5e만 지원합니다: "
            f"{_text(payload.get('sheet_type')) or '미확인'}"
        )
    if not _text(payload.get("roll20_character_id")):
        raise RuntimeError("Roll20 Character ID가 없습니다.")
    return payload, path


def _upsert_and_verify(driver, target, attrs, label):
    if not attrs:
        return {"ok": True, "log": []}, {}

    driver.set_script_timeout(120)
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


def _delete_rows(driver, target, row_ids):
    row_ids = [rid for rid in row_ids if rid]
    if not row_ids:
        return {"ok": True, "deleted_count": 0, "deleted": []}

    driver.set_script_timeout(max(45, len(row_ids) * 20))
    result = driver.execute_async_script(
        DELETE_RESOURCE_ROWS_SCRIPT,
        _text(target.get("roll20_character_id")),
        _text(target.get("character_name")),
        row_ids,
    )
    if not isinstance(result, dict) or not result.get("ok"):
        raise RuntimeError(
            "이전 12단계 자원 반복행 삭제 실패: "
            + json.dumps(result, ensure_ascii=False)
        )
    return result


def apply_resources(
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
    roll20_payload = _dict(_dict(payload).get("roll20_payload"))
    character = _dict(roll20_payload.get("character"))

    actual_source_id = _text(roll20_payload.get("source_character_id"))
    character_name = _text(character.get("name"))
    if not actual_source_id or not character_name:
        raise RuntimeError("Roll20 payload 캐릭터 정보가 없습니다.")

    target, target_path = _load_target(actual_source_id)
    if _text(target.get("character_name")) != character_name:
        raise RuntimeError("D&D Beyond 결과와 Roll20 대상 캐릭터 이름이 다릅니다.")

    candidates = build_resource_candidates(payload)
    if candidates:
        summary = ", ".join(
            f"{row['name']} {row['current']}/{row['maximum']} "
            f"({row.get('roll20_reset') or '휴식 없음'})"
            for row in candidates
        )
        print(f"[시트 이동기] 자원 후보 {len(candidates)}개: {summary}")
    else:
        print("[시트 이동기] 자원 후보 0개")

    output_path = (
        Path(CURRENT_RESULT_DIR)
        / f"roll20-resources-{actual_source_id}.json"
    )
    previous = _previous_report(output_path)

    fixed_names = []
    for slot in FIXED_SLOTS:
        fixed_names.extend(_fixed_slot_names(slot))

    _ensure_cdp(cdp_url)
    driver = _attach_driver(cdp_url)

    report = {
        "version": STAGE12_VERSION,
        "mode": "dry-run" if dry_run else "apply",
        "source_character_id": actual_source_id,
        "character_name": character_name,
        "roll20_character_id": target["roll20_character_id"],
        "result_path": str(path.resolve()),
        "target_path": str(Path(target_path).resolve()),
        "resource_count": len(candidates),
        "resources": candidates,
        "fixed_slot_assignments": {},
        "managed_repeating_row_ids": [],
        "stale_repeating_row_ids": [],
        "backup_path": None,
        "mutated": False,
        "verification": {},
        "status": "running",
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "policy": {
            "prefer_fixed_slots": True,
            "preserve_manual_fixed_slots": True,
            "preserve_manual_repeating_rows": True,
            "delete_only_previous_sheet_mover_rows": True,
            "remaining_uses_from_number_used": True,
            "shared_resource_consumers_not_duplicated": True,
            "rest_reset_fields_written": True,
            "partial_short_rest_recharge_falls_back_to_long": True,
            "spell_slots_untouched": True,
        },
    }

    try:
        _select_roll20_tab(driver)

        fixed_before = _snapshot(driver, target, fixed_names)
        state_before = _resource_state(driver, target)

        placement = place_resources(
            candidates,
            fixed_before,
            previous,
        )

        fixed_assignments = {
            slot: resource["source_key"]
            for slot, resource in _dict(placement.get("fixed")).items()
        }
        repeating_rows = [
            _repeating_row(resource)
            for resource in _list(placement.get("repeating"))
        ]
        repeating_ids = [row["row_id"] for row in repeating_rows]

        previous_ids = set(_previous_repeating_ids(previous))
        current_ids = set(repeating_ids)
        stale_ids = sorted(previous_ids - current_ids)

        fixed_attrs = _fixed_attributes(placement)
        repeating_attrs = _repeating_attributes(repeating_rows)

        desired_reporder = _resource_reporder(
            state_before,
            repeating_ids,
        )
        repeating_attrs["_reporder_repeating_resource"] = {
            "current": desired_reporder,
            "max": "",
        }

        all_attrs = {**fixed_attrs, **repeating_attrs}

        report["fixed_slot_assignments"] = fixed_assignments
        report["fixed_slot_resources"] = {
            slot: resource
            for slot, resource in _dict(placement.get("fixed")).items()
        }
        report["clear_fixed_slots"] = list(placement.get("clear_fixed") or [])
        report["repeating_rows"] = repeating_rows
        report["managed_repeating_row_ids"] = repeating_ids
        report["stale_repeating_row_ids"] = stale_ids
        report["desired_reporder"] = desired_reporder
        report["before"] = {
            "fixed_attributes": fixed_before["attributes"],
            "resource_state": state_before,
        }

        before_managed = _snapshot(driver, target, all_attrs.keys())
        _, initial_mismatches = _verify(before_managed, all_attrs)
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
            / f"roll20-stage12-resources-backup-"
              f"{actual_source_id}-{timestamp}.json"
        )
        _save_json(
            backup_path,
            {
                "version": STAGE12_VERSION,
                "source_character_id": actual_source_id,
                "character_name": character_name,
                "roll20_character_id": target["roll20_character_id"],
                "read_only_snapshot_before_apply": True,
                "fixed_attributes": fixed_before["attributes"],
                "resource_state": state_before,
                "previous_fixed_assignments": _previous_fixed_assignments(previous),
                "previous_repeating_row_ids": _previous_repeating_ids(previous),
            },
        )
        report["backup_path"] = str(backup_path.resolve())
        _save_json(output_path, report)

        if stale_ids:
            report["cleanup_result"] = _delete_rows(
                driver,
                target,
                stale_ids,
            )
            report["mutated"] = True

        # Re-read after cleanup so manual row ordering is kept accurately.
        state_after_cleanup = _resource_state(driver, target)
        desired_reporder = _resource_reporder(
            state_after_cleanup,
            repeating_ids,
        )
        repeating_attrs["_reporder_repeating_resource"] = {
            "current": desired_reporder,
            "max": "",
        }
        all_attrs = {**fixed_attrs, **repeating_attrs}
        report["desired_reporder"] = desired_reporder

        outcome, actual = _upsert_and_verify(
            driver,
            target,
            all_attrs,
            "12단계 자원",
        )
        report["write_result"] = outcome
        report["mutated"] = report["mutated"] or bool(initial_mismatches)

        final_state = _resource_state(driver, target)
        actual_reporder = _text(final_state.get("reporder"))
        if actual_reporder != desired_reporder:
            raise RuntimeError(
                "12단계 resource 정렬 서버 재검증 실패: "
                + json.dumps(
                    {
                        "expected": desired_reporder,
                        "actual": actual_reporder,
                    },
                    ensure_ascii=False,
                )
            )

        report["verification"] = {
            "status": "pass",
            "actual": actual,
            "reporder": actual_reporder,
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

    print("[시트 이동기] 12단계: 클래스 자원 / 사용 횟수 입력")
    print(f"[시트 이동기] 모드: {'읽기 전용' if args.dry_run else '실제 입력'}")
    print("[시트 이동기] CLASS RESOURCE → OTHER RESOURCE → 추가 Resource 순으로 배치합니다.")
    print("[시트 이동기] 기존 수동 자원 슬롯과 수동 반복행은 덮어쓰지 않습니다.")
    print("[시트 이동기] 현재값/최대값은 Roll20 Legacy Attribute의 current/max에 저장합니다.")
    print("[시트 이동기] 숏레 완전 회복 자원은 short, 롱레 회복 자원은 long으로 설정합니다.")
    print("[시트 이동기] 숏레에 일부만 회복되는 자원은 Legacy 한계상 long으로 처리합니다.")
    print("[시트 이동기] 주문 슬롯은 이번 단계에서 건드리지 않습니다.")

    report, output = apply_resources(
        result_path=args.result,
        source_id=args.source_id,
        cdp_url=args.cdp_url,
        dry_run=args.dry_run,
    )

    print(f"[시트 이동기] 독립 자원: {report['resource_count']}개")
    for resource in report["resources"]:
        where = next(
            (
                slot
                for slot, source_key
                in report["fixed_slot_assignments"].items()
                if source_key == resource["source_key"]
            ),
            None,
        )
        if not where:
            row = next(
                (
                    row for row in report.get("repeating_rows", [])
                    if row["source_key"] == resource["source_key"]
                ),
                None,
            )
            where = (
                f"repeating_resource:{row['row_id']}"
                if row else "미배치"
            )

        print(
            f"  - {resource['name']}: "
            f"{resource['current']}/{resource['maximum']} / "
            f"휴식={resource.get('roll20_reset') or '없음'} → {where}"
        )

    if not args.dry_run:
        print(f"[시트 이동기] 백업: {report.get('backup_path')}")
        print(
            "[시트 이동기] 최종 서버 재검증: "
            + str((report.get("verification") or {}).get("status"))
        )
    print(f"[시트 이동기] 결과 저장: {output.resolve()}")


# runtime-integrity-v2.6 resource-cleanup hook
from .runtime_integrity_v26 import install_resource_integrity as _install_resource_integrity_v26
apply_resources = _install_resource_integrity_v26(apply_resources, globals())

if __name__ == "__main__":
    main()
