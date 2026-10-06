"""Stage 6 Roll20 Legacy OGL5e inventory writer.

Scope:
- imports Stage 3 ``roll20_payload.equipment`` into ``repeating_inventory``
- preserves unrelated/manual Roll20 inventory rows
- deterministically reuses Sheet Mover row IDs on rerun
- does not create attacks or class resources
- backs up all managed repeating attributes before mutation
- verifies every imported row from Roll20's persisted attribute collection

Default behavior is APPLY.  Use ``--dry-run`` only when a read-only plan is
explicitly wanted.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
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
from .translation_render import strip_dnd_display_tags


STAGE6_VERSION = "2026-10-06-stage6-roll20-inventory-v1.1-weight-hotfix"
ROW_PREFIX = "-SM"
ROW_HASH_LENGTH = 17

INVENTORY_FIELDS = (
    "itemcount",
    "itemname",
    "itemweight",
    "inventorysubflag",
    "equipped",
    "useasresource",
    "hasattack",
    "itemproperties",
    "itemmodifiers",
    "itemcontent",
    "itemattackid",
    "itemresourceid",
)

_HTML_BREAK_RE = re.compile(r"(?i)<(?:br\s*/?|/p|/div|/li|/tr|/h[1-6])\s*>")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"[ \t\f\v]+")
_BLANK_RE = re.compile(r"\n{3,}")

# D&D Beyond catalog entries whose definition.weight describes one fixed
# bundle rather than one piece. Roll20 multiplies itemweight by itemcount,
# so these must be converted to per-piece weight before import.
#
# Confirmed against the current sample character:
# - Caltrops: 20 pieces together weigh 2 lb -> 0.1 lb each
# - Oil: 2 units together weigh 1 lb -> 0.5 lb each
#
# Ordinary stacks such as Javelin, Rations and Torch already expose
# per-item weight and must not be divided.
_BUNDLED_DEFINITION_COUNTS = {
    "caltrops": 20,
    "caltrops (bag of 20)": 20,
    "oil": 2,
    "oil (flask)": 2,
}


def _dict(value):
    return value if isinstance(value, dict) else {}


def _list(value):
    return value if isinstance(value, list) else []


def _text(value):
    return str(value or "").strip()


def _plain_text(value):
    """Render translated text safely for Roll20 textarea/text fields."""
    if value is None:
        return ""
    text = strip_dnd_display_tags(str(value))
    text = _HTML_BREAK_RE.sub("\n", text)
    text = _HTML_TAG_RE.sub("", text)
    text = html.unescape(text)
    lines = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = _SPACE_RE.sub(" ", line).strip()
        if line:
            lines.append(line)
    return _BLANK_RE.sub("\n\n", "\n".join(lines)).strip()


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


def _truthy(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return _text(value).casefold() in {"1", "true", "yes", "on", "equipped"}


def _display_piece(value):
    """Compact a property-like value without dumping arbitrary nested JSON."""
    if value is None:
        return ""
    if isinstance(value, str):
        return _plain_text(value)
    if isinstance(value, (int, float, bool)):
        return _scalar(value)
    if isinstance(value, list):
        return ", ".join(
            piece for piece in (_display_piece(item) for item in value) if piece
        )
    if isinstance(value, dict):
        for key in (
            "name",
            "label",
            "value",
            "text",
            "display",
            "description",
            "dice_string",
            "diceString",
        ):
            piece = _display_piece(value.get(key))
            if piece:
                return piece
        return ""
    return ""


def inventory_row_id(source_key: str) -> str:
    """Return a Roll20-safe deterministic 20-character repeating-row ID."""
    source_key = _text(source_key)
    if not source_key:
        raise ValueError("equipment source_key가 비어 있습니다.")
    digest = hashlib.sha1(source_key.encode("utf-8")).hexdigest()
    row_id = ROW_PREFIX + digest[:ROW_HASH_LENGTH]
    if len(row_id) != 20 or "_" in row_id:
        raise AssertionError(f"잘못된 반복행 ID: {row_id}")
    return row_id


def _roll20_unit_weight(item):
    """Convert D&D Beyond bundle weight to Roll20 per-piece weight when needed."""
    item = _dict(item)
    raw_weight = item.get("weight")

    if not isinstance(raw_weight, (int, float)) or isinstance(raw_weight, bool):
        return _scalar(raw_weight, default="")

    original_name = _text(
        item.get("original_name") or item.get("name")
    ).casefold()

    bundle_count = _BUNDLED_DEFINITION_COUNTS.get(original_name)
    if not bundle_count:
        return _scalar(raw_weight, default="")

    unit_weight = raw_weight / bundle_count
    unit_weight = round(unit_weight, 6)
    return _scalar(unit_weight, default="")


def _properties_text(item):
    parts = []

    properties = _display_piece(item.get("properties"))
    if properties:
        parts.append(properties)

    rarity = _display_piece(item.get("rarity"))
    if rarity:
        parts.append(f"희귀도: {rarity}")

    if _truthy(item.get("magic")):
        parts.append("마법 아이템")
    if _truthy(item.get("attuned")):
        parts.append("조율됨")

    # item_type is display-only. It does not feed itemmodifiers, so it cannot
    # change AC, attacks, saves, or other sheet calculations.
    item_type = _display_piece(item.get("item_type"))
    if item_type:
        parts.append(f"종류: {item_type}")

    # Preserve order while removing exact duplicates.
    out = []
    seen = set()
    for part in parts:
        key = part.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(part)
    return " | ".join(out)


def map_equipment_row(item: dict[str, Any]) -> dict[str, Any]:
    item = _dict(item)
    source_key = _text(item.get("source_key"))
    row_id = inventory_row_id(source_key)

    name = _plain_text(item.get("name") or item.get("original_name"))
    if not name:
        raise ValueError(f"장비 이름이 비어 있습니다: {source_key}")

    quantity = _scalar(item.get("quantity"), default="1") or "1"
    weight = _roll20_unit_weight(item)

    fields = {
        "itemcount": quantity,
        "itemname": name,
        "itemweight": weight,
        "inventorysubflag": "0",
        "equipped": "1" if _truthy(item.get("equipped")) else "0",

        # Stage 10/12 are intentionally deferred.
        "useasresource": "0",
        "hasattack": "0",

        # Display-only fields. Never place mechanical modifiers here because
        # Legacy OGL5e parses itemmodifiers and can alter AC/roll calculations.
        "itemproperties": _properties_text(item),
        "itemmodifiers": "",
        "itemcontent": _plain_text(item.get("description")),
        "itemattackid": "",
        "itemresourceid": "",
    }

    return {
        "source_key": source_key,
        "source_id": _text(item.get("source_id")),
        "definition_id": _text(item.get("definition_id")),
        "row_id": row_id,
        "name": name,
        "fields": fields,
    }


def build_inventory_plan(result_payload: dict[str, Any]) -> dict[str, Any]:
    roll20_payload = _dict(_dict(result_payload).get("roll20_payload"))
    equipment = _list(roll20_payload.get("equipment"))
    source_character_id = _text(roll20_payload.get("source_character_id"))
    character = _dict(roll20_payload.get("character"))
    character_name = _text(character.get("name"))

    if not source_character_id:
        raise RuntimeError("roll20_payload.source_character_id가 없습니다.")
    if not character_name:
        raise RuntimeError("roll20_payload.character.name이 없습니다.")

    rows = []
    seen_source_keys = set()
    seen_row_ids = set()

    for item in equipment:
        row = map_equipment_row(item)
        source_key = row["source_key"]
        row_id = row["row_id"]
        if source_key in seen_source_keys:
            raise RuntimeError(f"장비 source_key 중복: {source_key}")
        if row_id in seen_row_ids:
            raise RuntimeError(f"장비 row_id 충돌: {row_id}")
        seen_source_keys.add(source_key)
        seen_row_ids.add(row_id)
        rows.append(row)

    return {
        "version": STAGE6_VERSION,
        "source_character_id": source_character_id,
        "character_name": character_name,
        "section": "repeating_inventory",
        "row_count": len(rows),
        "rows": rows,
        "policy": {
            "preserve_unmanaged_rows": True,
            "delete_existing_rows": False,
            "stable_row_ids": True,
            "hasattack": False,
            "useasresource": False,
            "itemmodifiers": False,
        },
        "deferred": [
            "spells",
            "features",
            "actions",
            "attacks",
            "skill_save_proficiency_expertise",
            "class_resources",
        ],
    }


def row_attribute_name(row_id: str, field: str) -> str:
    if field not in INVENTORY_FIELDS:
        raise ValueError(f"허용되지 않은 inventory 필드: {field}")
    if not row_id or "_" in row_id:
        raise ValueError(f"잘못된 반복행 ID: {row_id}")
    return f"repeating_inventory_{row_id}_{field}"


def plan_attributes(plan: dict[str, Any]) -> dict[str, dict[str, str]]:
    attrs: dict[str, dict[str, str]] = {}
    for row in _list(plan.get("rows")):
        row_id = _text(row.get("row_id"))
        fields = _dict(row.get("fields"))
        for field in INVENTORY_FIELDS:
            name = row_attribute_name(row_id, field)
            if name in attrs:
                raise RuntimeError(f"Roll20 attribute 이름 중복: {name}")
            attrs[name] = {
                "current": _text(fields.get(field)),
                "max": "",
            }
    return attrs


def _load_target(source_id: str):
    path = Path(CURRENT_RESULT_DIR) / f"roll20-target-{source_id}.json"
    if not path.is_file():
        raise RuntimeError(f"4단계 연결 파일이 없습니다: {path.resolve()}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if _text(payload.get("source_character_id")) != source_id:
        raise RuntimeError("Roll20 연결 파일 source ID가 다릅니다.")
    if _text(payload.get("sheet_type")) != "ogl5e":
        raise RuntimeError(
            f"6단계는 ogl5e만 지원합니다: {_text(payload.get('sheet_type')) or '미확인'}"
        )
    if not _text(payload.get("roll20_character_id")):
        raise RuntimeError("Roll20 Character ID가 없습니다.")
    return payload, path


FETCH_ATTRS_SCRIPT = r"""
const wantedId = String(arguments[0] || '').trim();
const wantedName = String(arguments[1] || '').trim();
const names = arguments[2] || [];
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
function snapshot(collection) {
  const out={};
  for (const name of names) out[name]=[];
  for (const model of modelsOf(collection)) {
    const name=String(val(model,'name') || '').trim();
    if (!names.includes(name)) continue;
    out[name].push({
      id:idOf(model),
      current:String(val(model,'current') == null ? '' : val(model,'current')),
      max:String(val(model,'max') == null ? '' : val(model,'max')),
    });
  }
  return out;
}

const character=findCharacter();
if (!character) { done({ok:false,reason:'character_not_found'}); return; }
const collection=character.attribs;
if (!collection || typeof collection.fetch !== 'function') {
  done({ok:false,reason:'attribute_collection_unavailable'}); return;
}

let settled=false;
function finish(status,error) {
  if (settled) return;
  settled=true;
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
  const req=collection.fetch({
    reset:false,
    success:()=>finish('success',''),
    error:(_c,xhr)=>finish('error',`status=${xhr && xhr.status}; text=${xhr && xhr.statusText}`),
  });
  if (req && typeof req.then === 'function') {
    req.then(()=>finish('success_promise',''),e=>finish('error_promise',String(e || '')));
  }
  setTimeout(()=>finish('timeout','fetch callback timeout'),12000);
} catch (e) {
  finish('exception',String(e && e.stack ? e.stack : e));
}
"""


UPSERT_ROW_SCRIPT = r"""
const wantedId = String(arguments[0] || '').trim();
const wantedName = String(arguments[1] || '').trim();
const changes = arguments[2] || {};
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
function modelsNamed(collection,name) {
  return modelsOf(collection).filter(m=>String(val(m,'name') || '').trim()===name);
}
function timeoutPromise(executor,ms,label) {
  return new Promise((resolve,reject)=>{
    let settled=false;
    const timer=setTimeout(()=>{
      if (settled) return;
      settled=true;
      reject(new Error(label+'_timeout'));
    },ms);
    executor(
      value=>{if(settled)return;settled=true;clearTimeout(timer);resolve(value);},
      error=>{if(settled)return;settled=true;clearTimeout(timer);reject(error);}
    );
  });
}
function fetchCollection(collection) {
  return timeoutPromise((resolve,reject)=>{
    try {
      collection.fetch({
        reset:false,
        success:()=>resolve(true),
        error:(_c,xhr)=>reject(new Error(
          'fetch_failed status='+(xhr && xhr.status)+' text='+(xhr && xhr.statusText)
        )),
      });
    } catch(e) { reject(e); }
  },12000,'fetch');
}
function same(model,spec) {
  const current=String(val(model,'current') == null ? '' : val(model,'current'));
  const max=String(val(model,'max') == null ? '' : val(model,'max'));
  return current===String(spec.current == null ? '' : spec.current)
      && max===String(spec.max == null ? '' : spec.max);
}
function saveExisting(model,name,spec) {
  return timeoutPromise((resolve,reject)=>{
    const before={
      current:String(val(model,'current') == null ? '' : val(model,'current')),
      max:String(val(model,'max') == null ? '' : val(model,'max')),
    };
    try {
      model.save(
        {current:String(spec.current || ''),max:String(spec.max || '')},
        {
          wait:true,
          success:m=>resolve({
            name,action:'update',id:idOf(m || model),before,
            after:{
              current:String(val(m || model,'current') == null ? '' : val(m || model,'current')),
              max:String(val(m || model,'max') == null ? '' : val(m || model,'max')),
            }
          }),
          error:(_m,xhr)=>reject(new Error(
            'update_failed '+name+' status='+(xhr && xhr.status)+' text='+(xhr && xhr.statusText)
          )),
        }
      );
    } catch(e) { reject(e); }
  },15000,'save_'+name);
}
function createNew(collection,name,spec) {
  return timeoutPromise((resolve,reject)=>{
    try {
      collection.create(
        {
          name,
          current:String(spec.current || ''),
          max:String(spec.max || ''),
          characterid:wantedId,
        },
        {
          wait:true,
          success:model=>resolve({
            name,action:'create',id:idOf(model),before:null,
            after:{
              current:String(val(model,'current') == null ? '' : val(model,'current')),
              max:String(val(model,'max') == null ? '' : val(model,'max')),
            }
          }),
          error:(_m,xhr)=>reject(new Error(
            'create_failed '+name+' status='+(xhr && xhr.status)+' text='+(xhr && xhr.statusText)
          )),
        }
      );
    } catch(e) { reject(e); }
  },15000,'create_'+name);
}

(async function(){
  const character=findCharacter();
  if (!character) { done({ok:false,reason:'character_not_found',log:[]}); return; }
  const collection=character.attribs;
  if (!collection || typeof collection.fetch !== 'function') {
    done({ok:false,reason:'attribute_collection_unavailable',log:[]}); return;
  }

  const log=[];
  try {
    await fetchCollection(collection);
    const jobs=[];

    for (const [name,spec] of Object.entries(changes)) {
      const existing=modelsNamed(collection,name);
      if (existing.length>1) throw new Error('duplicate_attribute '+name+' count='+existing.length);
      if (existing.length===1 && same(existing[0],spec)) {
        log.push({
          name,action:'skip',id:idOf(existing[0]),
          current:String(val(existing[0],'current') == null ? '' : val(existing[0],'current')),
          max:String(val(existing[0],'max') == null ? '' : val(existing[0],'max')),
        });
        continue;
      }
      jobs.push(
        existing.length===1
          ? saveExisting(existing[0],name,spec)
          : createNew(collection,name,spec)
      );
    }

    const completed=await Promise.all(jobs);
    log.push(...completed);
    done({ok:true,log});
  } catch(e) {
    done({ok:false,reason:'row_upsert_failed',error:String(e && e.stack ? e.stack : e),log});
  }
})();
"""


def _snapshot(driver, target, names):
    from .roll20_read import read_persisted
    return read_persisted(
        driver,
        FETCH_ATTRS_SCRIPT,
        _text(target.get("roll20_character_id")),
        _text(target.get("character_name")),
        list(names),
    )


def _verify(snapshot, expected_attrs):
    actual = {}
    mismatches = []
    stored = _dict(snapshot.get("attributes"))

    for name, spec in expected_attrs.items():
        rows = stored.get(name) or []
        if len(rows) != 1:
            mismatches.append({
                "attribute": name,
                "reason": "missing_or_duplicate",
                "count": len(rows),
            })
            actual[name] = rows
            continue
        row = rows[0]
        got_current = _text(row.get("current"))
        got_max = _text(row.get("max"))
        want_current = _text(spec.get("current"))
        want_max = _text(spec.get("max"))
        actual[name] = {
            "id": row.get("id"),
            "current": got_current,
            "max": got_max,
        }
        if got_current != want_current or got_max != want_max:
            mismatches.append({
                "attribute": name,
                "expected": {"current": want_current, "max": want_max},
                "actual": {"current": got_current, "max": got_max},
            })

    return actual, mismatches


def _row_attributes(row):
    row_id = _text(row.get("row_id"))
    fields = _dict(row.get("fields"))
    return {
        row_attribute_name(row_id, field): {
            "current": _text(fields.get(field)),
            "max": "",
        }
        for field in INVENTORY_FIELDS
    }


def _row_is_complete(snapshot, row_attrs):
    _, mismatches = _verify(snapshot, row_attrs)
    return not mismatches


def _save_json(path: Path, payload):
    from .result_store import write_json_atomic
    write_json_atomic(path, payload)


def apply_inventory(
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
    plan = build_inventory_plan(payload)
    actual_source_id = plan["source_character_id"]
    target, target_path = _load_target(actual_source_id)

    if _text(target.get("character_name")) != plan["character_name"]:
        raise RuntimeError("D&D Beyond 결과와 Roll20 대상 캐릭터 이름이 다릅니다.")

    all_attrs = plan_attributes(plan)
    output_path = Path(CURRENT_RESULT_DIR) / f"roll20-inventory-{actual_source_id}.json"

    report = {
        "version": STAGE6_VERSION,
        "mode": "dry-run" if dry_run else "apply",
        "source_character_id": actual_source_id,
        "character_name": plan["character_name"],
        "roll20_character_id": target["roll20_character_id"],
        "result_path": str(path.resolve()),
        "target_path": str(Path(target_path).resolve()),
        "row_count": plan["row_count"],
        "managed_attribute_count": len(all_attrs),
        "policy": plan["policy"],
        "rows": [
            {
                "source_key": row["source_key"],
                "row_id": row["row_id"],
                "name": row["name"],
                "fields": row["fields"],
            }
            for row in plan["rows"]
        ],
        "backup_path": None,
        "mutated": False,
        "row_results": [],
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
            row_attrs = _row_attributes(row)
            if not _row_is_complete(before, row_attrs):
                pending_rows.append(row)

        report["initial_pending_rows"] = [
            {
                "source_key": row["source_key"],
                "row_id": row["row_id"],
                "name": row["name"],
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
        backup_path = Path(CURRENT_RESULT_DIR) / (
            f"roll20-stage6-inventory-backup-{actual_source_id}-{timestamp}.json"
        )
        _save_json(
            backup_path,
            {
                "version": STAGE6_VERSION,
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
            row_attrs = _row_attributes(row)
            print(
                f"[시트 이동기] 장비 {index}/{total}: {row['name']}",
                flush=True,
            )

            driver.set_script_timeout(45)
            outcome = driver.execute_async_script(
                UPSERT_ROW_SCRIPT,
                _text(target.get("roll20_character_id")),
                _text(target.get("character_name")),
                row_attrs,
            )

            row_report = {
                "source_key": row["source_key"],
                "row_id": row["row_id"],
                "name": row["name"],
                "result": outcome,
                "verification": {},
            }
            report["row_results"].append(row_report)

            if not isinstance(outcome, dict) or not outcome.get("ok"):
                report["status"] = "error"
                report["error"] = (
                    f"장비 '{row['name']}' 저장 실패: "
                    + json.dumps(outcome, ensure_ascii=False)
                )
                _save_json(output_path, report)
                raise RuntimeError(report["error"])

            report["mutated"] = True

            after_row = _snapshot(driver, target, row_attrs.keys())
            actual, mismatches = _verify(after_row, row_attrs)
            row_report["verification"] = {
                "status": "pass" if not mismatches else "fail",
                "actual": actual,
                "mismatches": mismatches,
            }
            _save_json(output_path, report)

            if mismatches:
                report["status"] = "error"
                report["error"] = (
                    f"장비 '{row['name']}' 서버 재검증 실패: "
                    + json.dumps(mismatches, ensure_ascii=False)
                )
                _save_json(output_path, report)
                raise RuntimeError(report["error"])

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
                "6단계 장비 최종 서버 재검증 실패: "
                + json.dumps(mismatches, ensure_ascii=False)
            )

        _save_json(output_path, report)

        if mismatches:
            raise RuntimeError(report["error"])

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

    print("[시트 이동기] 6단계: Roll20 장비 반복행 입력")
    print(f"[시트 이동기] 모드: {'읽기 전용' if args.dry_run else '실제 입력'}")
    print("[시트 이동기] 기존 Roll20 장비는 삭제하지 않습니다.")
    print("[시트 이동기] 공격/자원 자동생성은 비활성화합니다.")

    report, output = apply_inventory(
        result_path=args.result,
        source_id=args.source_id,
        cdp_url=args.cdp_url,
        dry_run=args.dry_run,
    )

    print(f"[시트 이동기] D&D Beyond 장비: {report['row_count']}개")
    print(
        f"[시트 이동기] 최초 미완료 장비: "
        f"{len(report.get('initial_pending_rows') or [])}개"
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
