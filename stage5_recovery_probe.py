"""Stage 5 recovery probe after a failed write.

READ-ONLY:
    python stage5_recovery_probe.py

It reads the selected Stage 5 fields from:
1) Roll20's persisted character.attribs collection (after fetch)
2) the currently open OGL5e character-sheet DOM

No create/update/delete operation is performed.
"""
from __future__ import annotations

import json
from pathlib import Path

from sheet_mover.roll20_connection import (
    DEFAULT_CDP_URL,
    _attach_driver,
    _disconnect_driver,
    _ensure_cdp,
)

ROOT = Path.cwd()
SOURCE_ID = "170892133"
TARGET_PATH = ROOT / "results" / "current" / f"roll20-target-{SOURCE_ID}.json"
OUTPUT_PATH = ROOT / "results" / "current" / f"roll20-stage5-state-{SOURCE_ID}.json"

FIELDS = [
    # Identity / class inputs
    "class",
    "subclass",
    "base_level",
    "arcane_fighter",
    "arcane_rogue",
    "race",
    "subrace",
    "background",
    "alignment",
    "experience",

    # Ability inputs
    "strength_base",
    "dexterity_base",
    "constitution_base",
    "intelligence_base",
    "wisdom_base",
    "charisma_base",

    # Direct basic values
    "hp",
    "hp_max",
    "hp_temp",
    "ac",
    "speed",
    "initmod",

    # Spell input fields
    "spellcasting_ability",
    "spell_dc_mod",
    "globalmagicmod",

    # Derived verification fields
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
]

READ_STATE_SCRIPT = r"""
const wantedId = String(arguments[0] || '').trim();
const wantedName = String(arguments[1] || '').trim();
const fieldNames = arguments[2] || [];
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

function modelsOf(collection) {
  try {
    if (!collection) return [];
    if (Array.isArray(collection.models)) return collection.models;
    if (typeof collection.toArray === 'function') return collection.toArray();
    if (Array.isArray(collection)) return collection;
  } catch (_) {}
  return [];
}

function modelId(model) {
  return String(
    val(model, 'id') ||
    val(model, '_id') ||
    val(model, 'characterid') ||
    (model && model.id) ||
    ''
  ).trim();
}

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

function findTargetDoc() {
  const docs = [];
  collectDocs(document, docs);

  for (const doc of docs) {
    let names = [];
    try {
      names = Array.from(doc.querySelectorAll('[name="attr_character_name"]'));
    } catch (_) {}
    if (names.some(el => String(el.value || '').trim() === wantedName)) {
      return doc;
    }
  }
  return null;
}

function domRows(doc, name) {
  if (!doc) return [];
  let els = [];
  try {
    els = Array.from(doc.querySelectorAll(`[name="attr_${CSS.escape(name)}"]`));
  } catch (_) {
    return [];
  }

  return els.map(el => {
    let value = '';
    let checked = null;
    let visible = false;
    try {
      value = String(el.value == null ? '' : el.value);
      if (el.type === 'checkbox' || el.type === 'radio') {
        checked = !!el.checked;
      }
      const r = el.getBoundingClientRect();
      const style = doc.defaultView.getComputedStyle(el);
      visible =
        r.width > 0 &&
        r.height > 0 &&
        style.display !== 'none' &&
        style.visibility !== 'hidden';
    } catch (_) {}

    return {
      tag: String(el.tagName || '').toLowerCase(),
      type: String(el.getAttribute('type') || '').toLowerCase(),
      value,
      checked,
      visible,
    };
  });
}

const campaigns = [];
try {
  if (window.d20 && window.d20.Campaign) {
    campaigns.push(window.d20.Campaign);
  }
} catch (_) {}
try {
  if (window.Campaign) {
    campaigns.push(window.Campaign);
  }
} catch (_) {}

let character = null;
for (const campaign of campaigns) {
  const collections = [
    campaign.characters,
    campaign.attributes && campaign.attributes.characters,
  ];
  for (const collection of collections) {
    for (const model of modelsOf(collection)) {
      const id = modelId(model);
      const name = String(val(model, 'name') || '').trim();
      if ((wantedId && id === wantedId) ||
          (!wantedId && wantedName && name === wantedName)) {
        character = model;
        break;
      }
    }
    if (character) break;
  }
  if (character) break;
}

if (!character) {
  done({ok:false, reason:'character_not_found'});
  return;
}

const attribs = character.attribs;
if (!attribs) {
  done({ok:false, reason:'attribute_collection_not_found'});
  return;
}

function finish(fetchStatus, fetchError) {
  const persisted = {};
  const byName = {};

  for (const model of modelsOf(attribs)) {
    const name = String(val(model, 'name') || '').trim();
    if (!fieldNames.includes(name)) continue;

    if (!byName[name]) byName[name] = [];
    byName[name].push({
      id: modelId(model),
      current: String(val(model, 'current') == null ? '' : val(model, 'current')),
      max: String(val(model, 'max') == null ? '' : val(model, 'max')),
    });
  }

  for (const name of fieldNames) {
    persisted[name] = byName[name] || [];
  }

  const doc = findTargetDoc();
  const dom = {};
  for (const name of fieldNames) {
    dom[name] = domRows(doc, name);
  }

  done({
    ok:true,
    fetch_status:fetchStatus,
    fetch_error:fetchError || '',
    character_id:modelId(character),
    character_name:String(val(character, 'name') || '').trim(),
    persisted,
    dom,
  });
}

let settled = false;
function once(status, error) {
  if (settled) return;
  settled = true;
  setTimeout(() => finish(status, error), 300);
}

if (typeof attribs.fetch !== 'function') {
  once('fetch_unavailable', '');
  return;
}

try {
  const req = attribs.fetch({
    reset:false,
    success:function(){ once('success', ''); },
    error:function(_c, xhr){
      let detail = '';
      try { detail = `status=${xhr && xhr.status}; text=${xhr && xhr.statusText}`; }
      catch (_) {}
      once('error', detail);
    },
  });

  if (req && typeof req.then === 'function') {
    req.then(
      () => once('success_promise', ''),
      err => once('error_promise', String(err || ''))
    );
  }
  setTimeout(() => once('timeout', 'fetch callback timeout'), 12000);
} catch (err) {
  once('exception', String(err && err.stack ? err.stack : err));
}
"""


def _preferred_dom_value(rows):
    if not isinstance(rows, list) or not rows:
        return None

    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("checked") is True:
            return str(row.get("value") or "on")

    visible = [r for r in rows if isinstance(r, dict) and r.get("visible")]
    for row in visible:
        if str(row.get("value") or "") != "":
            return str(row.get("value") or "")

    non_hidden = [
        r for r in rows
        if isinstance(r, dict) and str(r.get("type") or "") != "hidden"
    ]
    for row in non_hidden:
        if str(row.get("value") or "") != "":
            return str(row.get("value") or "")

    for row in rows:
        if isinstance(row, dict) and str(row.get("value") or "") != "":
            return str(row.get("value") or "")

    return ""


def main():
    if not TARGET_PATH.is_file():
        raise RuntimeError(f"4단계 연결 파일이 없습니다: {TARGET_PATH}")

    target = json.loads(TARGET_PATH.read_text(encoding="utf-8"))
    character_id = str(target.get("roll20_character_id") or "").strip()
    character_name = str(target.get("character_name") or "").strip()

    print("[시트 이동기] 5단계 실패 후 현재 Roll20 상태를 읽습니다.")
    print(f"[시트 이동기] 대상 캐릭터: {character_name}")
    print("[시트 이동기] 서버 attribute + 현재 시트 DOM을 읽기만 합니다.")
    print("[시트 이동기] Roll20 값 변경: 0건")

    _ensure_cdp(DEFAULT_CDP_URL)
    driver = _attach_driver(DEFAULT_CDP_URL)

    try:
        driver.set_script_timeout(20)
        result = driver.execute_async_script(
            READ_STATE_SCRIPT,
            character_id,
            character_name,
            FIELDS,
        )
    finally:
        _disconnect_driver(driver)

    if not isinstance(result, dict) or not result.get("ok"):
        raise RuntimeError(
            "Roll20 상태 조사 실패: "
            + json.dumps(result, ensure_ascii=False)
        )

    compact = {}
    persisted = result.get("persisted") or {}
    dom = result.get("dom") or {}

    for name in FIELDS:
        server_rows = persisted.get(name) or []
        compact[name] = {
            "persisted": [
                {
                    "id": row.get("id"),
                    "current": row.get("current"),
                    "max": row.get("max"),
                }
                for row in server_rows
                if isinstance(row, dict)
            ],
            "dom_value": _preferred_dom_value(dom.get(name) or []),
        }

    output = {
        "source_character_id": SOURCE_ID,
        "roll20_character_id": character_id,
        "character_name": character_name,
        "fetch_status": result.get("fetch_status"),
        "fetch_error": result.get("fetch_error"),
        "read_only": True,
        "fields": compact,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"[시트 이동기] fetch 상태: {result.get('fetch_status')}")
    print("[시트 이동기] 현재 핵심값:")
    for name in FIELDS:
        row = compact[name]
        persisted_values = [
            x.get("current") for x in row["persisted"]
        ]
        print(
            f"  - {name}: "
            f"server={persisted_values if persisted_values else '없음'} / "
            f"dom={row['dom_value']!r}"
        )

    print(f"[시트 이동기] 결과 저장: {OUTPUT_PATH.resolve()}")
    print("[시트 이동기] Roll20 값 변경: 0건")


if __name__ == "__main__":
    main()
