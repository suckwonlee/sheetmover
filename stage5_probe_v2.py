"""Stage 5 preflight v2: fetch Roll20 OGL5e attributes read-only.

Run from E:\sheet_mover while the dedicated Roll20 Chrome is open:
    python stage5_probe_v2.py

This script performs a read-only Backbone collection fetch (HTTP GET through
the already logged-in Roll20 page). It does not create, update, or delete
Roll20 attributes.
"""
from __future__ import annotations

import json
from pathlib import Path

from sheet_mover.roll20_connection import (
    DEFAULT_CDP_URL,
    _attach_driver,
    _disconnect_driver,
    _ensure_cdp,
    _select_roll20_tab,
)

ROOT = Path.cwd()
TARGET_PATH = ROOT / "results" / "current" / "roll20-target-170892133.json"
OUTPUT_PATH = ROOT / "results" / "current" / "roll20-attributes-170892133.json"

FETCH_ATTRIBUTES_SCRIPT = r"""
const wantedId = String(arguments[0] || '').trim();
const wantedName = String(arguments[1] || '').trim();
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

function safeKeys(obj) {
  try { return Object.keys(obj || {}).sort(); }
  catch (_) { return []; }
}

function collectionUrl(collection) {
  try {
    if (!collection) return '';
    if (typeof collection.url === 'function') return String(collection.url() || '');
    return String(collection.url || '');
  } catch (_) {
    return '';
  }
}

function snapshot(collection, source) {
  const rows = [];
  const seen = new Set();

  for (const model of modelsOf(collection)) {
    const name = String(val(model, 'name') || '').trim();
    if (!name) continue;

    const id = modelId(model);
    const currentRaw = val(model, 'current');
    const maxRaw = val(model, 'max');
    const key = `${id}|${name}`;

    if (seen.has(key)) continue;
    seen.add(key);

    rows.push({
      id,
      name,
      current: currentRaw == null ? '' : String(currentRaw),
      max: maxRaw == null ? '' : String(maxRaw),
      source,
    });
  }

  rows.sort((a, b) => a.name.localeCompare(b.name));
  return rows;
}

const campaigns = [];
try {
  if (window.d20 && window.d20.Campaign) {
    campaigns.push(['d20.Campaign', window.d20.Campaign]);
  }
} catch (_) {}
try {
  if (window.Campaign) {
    campaigns.push(['Campaign', window.Campaign]);
  }
} catch (_) {}

let character = null;
let characterSource = '';

for (const [label, campaign] of campaigns) {
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
        characterSource = label + '.characters';
        break;
      }
    }
    if (character) break;
  }
  if (character) break;
}

if (!character) {
  done({
    ok: false,
    stage: 'find_character',
    reason: 'character_not_found',
    wanted_id: wantedId,
    wanted_name: wantedName,
  });
  return;
}

let collection = null;
let collectionSource = '';

const candidates = [
  ['character.attribs', (() => { try { return character.attribs; } catch (_) { return null; } })()],
  ['character.attributes.attribs', (() => { try { return character.attributes && character.attributes.attribs; } catch (_) { return null; } })()],
  ['character.characterattributes', (() => { try { return character.characterattributes; } catch (_) { return null; } })()],
];

for (const [label, value] of candidates) {
  if (value) {
    collection = value;
    collectionSource = label;
    break;
  }
}

if (!collection) {
  done({
    ok: false,
    stage: 'find_attribute_collection',
    reason: 'attribute_collection_not_found',
    character_id: modelId(character),
    character_name: String(val(character, 'name') || '').trim(),
    character_model_keys: safeKeys(character),
    character_attribute_keys: safeKeys(character.attributes),
  });
  return;
}

const before = snapshot(collection, collectionSource);
const diagnostic = {
  character_source: characterSource,
  character_id: modelId(character),
  character_name: String(val(character, 'name') || '').trim(),
  sheet_type: String(
    val(character, 'charactersheetname') ||
    val(character, 'sheet_type') ||
    val(character, 'sheettype') ||
    val(character, 'character_sheet') ||
    ''
  ).trim(),
  collection_source: collectionSource,
  collection_url: collectionUrl(collection),
  collection_keys: safeKeys(collection),
  before_count: before.length,
  has_fetch: typeof collection.fetch === 'function',
};

function finish(fetchStatus, fetchError) {
  const rows = snapshot(collection, collectionSource);
  done({
    ok: true,
    fetch_status: fetchStatus,
    fetch_error: fetchError || '',
    ...diagnostic,
    attribute_count: rows.length,
    attributes: rows,
  });
}

if (before.length > 0) {
  finish('already_loaded', '');
  return;
}

if (typeof collection.fetch !== 'function') {
  finish('fetch_unavailable', '');
  return;
}

let settled = false;
const once = (status, error) => {
  if (settled) return;
  settled = true;
  setTimeout(() => finish(status, error), 300);
};

try {
  const request = collection.fetch({
    reset: false,
    success: function() {
      once('success', '');
    },
    error: function(_collection, xhr) {
      let detail = '';
      try {
        detail = `status=${xhr && xhr.status}; text=${xhr && xhr.statusText}`;
      } catch (_) {}
      once('error', detail);
    },
  });

  if (request && typeof request.then === 'function') {
    request.then(
      () => once('success_promise', ''),
      (err) => once('error_promise', String(err || ''))
    );
  }

  setTimeout(() => once('timeout', 'fetch callback timeout'), 12000);
} catch (err) {
  once('exception', String(err && err.stack ? err.stack : err));
}
"""


def main():
    if not TARGET_PATH.is_file():
        raise RuntimeError(
            f"4단계 연결 파일이 없습니다: {TARGET_PATH}\n"
            "먼저 --roll20-check를 성공시켜 주세요."
        )

    target = json.loads(TARGET_PATH.read_text(encoding="utf-8"))
    character_id = str(target.get("roll20_character_id") or "").strip()
    character_name = str(target.get("character_name") or "").strip()

    if not character_id or not character_name:
        raise RuntimeError("4단계 연결 파일에 Roll20 캐릭터 ID/이름이 없습니다.")

    print("[시트 이동기] 5단계 사전 조사 v2: Roll20 attribute를 서버에서 읽습니다.")
    print(f"[시트 이동기] 대상 캐릭터: {character_name}")
    print(f"[시트 이동기] Roll20 Character ID: {character_id}")
    print("[시트 이동기] HTTP GET만 사용하며 Roll20 값을 수정하지 않습니다.")

    _ensure_cdp(DEFAULT_CDP_URL)
    driver = _attach_driver(DEFAULT_CDP_URL)

    try:
        page_url = _select_roll20_tab(driver)
        driver.set_script_timeout(20)
        result = driver.execute_async_script(
            FETCH_ATTRIBUTES_SCRIPT,
            character_id,
            character_name,
        )
    finally:
        _disconnect_driver(driver)

    if not isinstance(result, dict):
        raise RuntimeError(f"Roll20 attribute 조사 응답이 올바르지 않습니다: {result!r}")

    result["page_url"] = page_url
    result["source_character_id"] = str(target.get("source_character_id") or "")
    result["read_only"] = True

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if not result.get("ok"):
        print(
            "[시트 이동기] attribute 조사 실패: "
            f"{result.get('stage')} / {result.get('reason')}"
        )
    else:
        print(f"[시트 이동기] fetch 상태: {result.get('fetch_status')}")
        if result.get("fetch_error"):
            print(f"[시트 이동기] fetch 상세: {result.get('fetch_error')}")
        print(f"[시트 이동기] 발견한 attribute: {result.get('attribute_count', 0)}개")

    print(f"[시트 이동기] 조사 결과를 저장했습니다: {OUTPUT_PATH.resolve()}")
    print("[시트 이동기] Roll20 시트 값 변경: 0건")


if __name__ == "__main__":
    main()
