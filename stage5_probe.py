"""Stage 5 preflight: read-only Roll20 OGL5e attribute inventory.

Run from E:\sheet_mover while the dedicated Roll20 Chrome is open:
    python stage5_probe.py

This script does not create, update, or delete Roll20 attributes.
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

ATTRIBUTE_DISCOVERY_SCRIPT = r"""
const wantedId = String(arguments[0] || '').trim();
const wantedName = String(arguments[1] || '').trim();

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
      if ((wantedId && id === wantedId) || (!wantedId && wantedName && name === wantedName)) {
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
  return {
    ok: false,
    reason: 'character_not_found',
    wanted_id: wantedId,
    wanted_name: wantedName,
  };
}

const candidateCollections = [];
function addCandidate(label, value) {
  if (!value) return;
  candidateCollections.push([label, value]);
}

try { addCandidate('character.attribs', character.attribs); } catch (_) {}
try { addCandidate('character.attributes.attribs', character.attributes && character.attributes.attribs); } catch (_) {}
try { addCandidate('character.attributes.attributes', character.attributes && character.attributes.attributes); } catch (_) {}
try { addCandidate('character.attributes.characterattributes', character.attributes && character.attributes.characterattributes); } catch (_) {}
try { addCandidate('character.characterattributes', character.characterattributes); } catch (_) {}

const rows = [];
const seen = new Set();
const sources = [];

for (const [label, collection] of candidateCollections) {
  const models = modelsOf(collection);
  sources.push({
    label,
    count: models.length,
    type: Object.prototype.toString.call(collection),
  });

  for (const model of models) {
    const name = String(val(model, 'name') || '').trim();
    if (!name) continue;

    const currentRaw = val(model, 'current');
    const maxRaw = val(model, 'max');
    const id = modelId(model);
    const key = `${id}|${name}`;

    if (seen.has(key)) continue;
    seen.add(key);

    rows.push({
      id,
      name,
      current: currentRaw == null ? '' : String(currentRaw),
      max: maxRaw == null ? '' : String(maxRaw),
      source: label,
    });
  }
}

rows.sort((a, b) => a.name.localeCompare(b.name));

let modelKeys = [];
try {
  modelKeys = Object.keys(character || {}).sort();
} catch (_) {}

let attributeKeys = [];
try {
  attributeKeys = Object.keys((character && character.attributes) || {}).sort();
} catch (_) {}

return {
  ok: true,
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
  attribute_count: rows.length,
  collection_sources: sources,
  character_model_keys: modelKeys,
  character_attribute_keys: attributeKeys,
  attributes: rows,
};
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

    print("[시트 이동기] 5단계 사전 조사: Roll20 기본 attribute를 읽습니다.")
    print(f"[시트 이동기] 대상 캐릭터: {character_name}")
    print(f"[시트 이동기] Roll20 Character ID: {character_id}")
    print("[시트 이동기] 이 작업은 Roll20 데이터를 수정하지 않습니다.")

    _ensure_cdp(DEFAULT_CDP_URL)
    driver = _attach_driver(DEFAULT_CDP_URL)
    try:
        page_url = _select_roll20_tab(driver)
        result = driver.execute_script(
            ATTRIBUTE_DISCOVERY_SCRIPT,
            character_id,
            character_name,
        )
    finally:
        _disconnect_driver(driver)

    if not isinstance(result, dict) or not result.get("ok"):
        raise RuntimeError(
            "Roll20 캐릭터 attribute를 찾지 못했습니다: "
            + json.dumps(result, ensure_ascii=False)
        )

    result["page_url"] = page_url
    result["source_character_id"] = str(target.get("source_character_id") or "")
    result["read_only"] = True

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"[시트 이동기] 발견한 attribute: {result.get('attribute_count', 0)}개")
    print(f"[시트 이동기] 조사 결과를 저장했습니다: {OUTPUT_PATH.resolve()}")
    print("[시트 이동기] Roll20 시트 값 변경: 0건")


if __name__ == "__main__":
    main()
