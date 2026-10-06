"""Stage 8 Roll20 Legacy OGL5e features & traits writer — v1.1 cleanup.

User-facing policy:
- omit Languages, Skills, and Core Fighter Traits from FEATURES & TRAITS
- keep Spellcasting but remove embedded <table> blocks from its description
- order rows as racial -> background -> class (level order) -> feats (payload order)
- set each row's options-flag to 0 so rows are collapsed by default
- preserve unrelated/manual Roll20 trait rows
- remove only previously-created Sheet Mover rows that are now explicitly excluded
- do not create class resources / limited-use counters (Stage 12)

Default behavior is APPLY. Use ``--dry-run`` only when explicitly wanted.
"""
from __future__ import annotations

import argparse
import hashlib
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


STAGE8_VERSION = "2026-10-06-stage8-roll20-features-v1.6-fighting-style-description"
ROW_PREFIX = "-SM"
ROW_HASH_LENGTH = 17

TRAIT_FIELDS = (
    "name",
    "source",
    "source_type",
    "description",
    "options-flag",
)

EXCLUDED_ORIGINAL_NAMES = {
    "Languages",
    "Skills",
    "Core Fighter Traits",
    "Farmer Ability Score Improvements",
}

_SOURCE_BY_KIND = {
    "racial_trait": "Racial",
    "class_feature": "Class",
    "feat": "Feat",
    "background_feature": "Background",
    "background": "Background",
}

_TABLE_RE = re.compile(r"(?is)<table\b[^>]*>.*?</table>")


def feature_row_id(source_key: str) -> str:
    source_key = _text(source_key)
    if not source_key:
        raise ValueError("feature source_key가 비어 있습니다.")
    digest = hashlib.sha1(source_key.encode("utf-8")).hexdigest()
    row_id = ROW_PREFIX + digest[:ROW_HASH_LENGTH]
    if len(row_id) != 20 or "_" in row_id:
        raise AssertionError(f"잘못된 반복행 ID: {row_id}")
    return row_id


def _ordinal_level(level) -> str:
    if type(level) is not int or level < 1:
        return ""
    if 10 <= level % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(level % 10, "th")
    return f"{level}{suffix} Level"


def _single_class_name(character: dict[str, Any]) -> str:
    classes = [
        row for row in _list(character.get("classes"))
        if isinstance(row, dict)
    ]
    if len(classes) != 1:
        return ""
    cls = classes[0]
    return _text(cls.get("original_name") or cls.get("name"))


def _race_name(character: dict[str, Any]) -> str:
    race = _dict(character.get("race"))
    return _text(race.get("original_name") or race.get("name"))


def _background_name(character: dict[str, Any]) -> str:
    background = _dict(character.get("background"))
    return _text(background.get("original_name") or background.get("name"))


def _is_background_metadata_feat(item, character) -> bool:
    """Treat the generated '<Background> Ability Score Improvements' row as background."""
    if _text(item.get("kind")) != "feat":
        return False
    original_name = _text(item.get("original_name"))
    background_name = _background_name(character)
    if not original_name or not background_name:
        return False
    return original_name.casefold() == (
        f"{background_name} Ability Score Improvements".casefold()
    )


def source_for_feature(item, character) -> str:
    if _is_background_metadata_feat(item, character):
        return "Background"
    return _SOURCE_BY_KIND.get(_text(item.get("kind")), "Other")


def source_type_for_feature(item, character) -> str:
    kind = _text(item.get("kind"))
    required_level = item.get("required_level")
    level_text = _ordinal_level(required_level)

    if _is_background_metadata_feat(item, character):
        return _background_name(character)

    if kind == "racial_trait":
        return _race_name(character)

    if kind == "class_feature":
        class_name = _single_class_name(character)
        parts = [part for part in (class_name, level_text) if part]
        return " / ".join(parts)

    if kind in {"background_feature", "background"}:
        return _background_name(character)

    # Actual feats remain Feat rows. Their acquisition source is not reliably
    # carried in Stage 3 for every feat, so don't invent one.
    if kind == "feat":
        return level_text

    return level_text


def _feature_description(item):
    raw = str(item.get("description") or "")
    if _text(item.get("original_name")) == "Spellcasting":
        raw = _TABLE_RE.sub("", raw)
    return _plain_text(raw)


def map_feature_row(
    item: dict[str, Any],
    character: dict[str, Any],
) -> dict[str, Any]:
    item = _dict(item)
    source_key = _text(item.get("source_key"))
    row_id = feature_row_id(source_key)

    name = _plain_text(item.get("name") or item.get("original_name"))
    if not name:
        raise ValueError(f"특성 이름이 비어 있습니다: {source_key}")

    kind = _text(item.get("kind"))
    fields = {
        "name": name,
        "source": source_for_feature(item, character),
        "source_type": source_type_for_feature(item, character),
        "description": _feature_description(item),
        # Roll20 Legacy OGL5e: checked options-flag means the gear/editor is open.
        "options-flag": "0",
    }

    return {
        "source_key": source_key,
        "source_id": _text(item.get("source_id")),
        "definition_id": _text(item.get("definition_id")),
        "kind": kind,
        "original_name": _text(item.get("original_name")),
        "required_level": item.get("required_level"),
        "row_id": row_id,
        "name": name,
        "fields": fields,
        "limited_use": item.get("limited_use"),
    }


def trait_attribute_name(row_id: str, field: str) -> str:
    if field not in TRAIT_FIELDS:
        raise ValueError(f"허용되지 않은 trait 필드: {field}")
    if not row_id or "_" in row_id:
        raise ValueError(f"잘못된 반복행 ID: {row_id}")
    return f"repeating_traits_{row_id}_{field}"


def _row_attributes(row):
    row_id = _text(row.get("row_id"))
    fields = _dict(row.get("fields"))
    return {
        trait_attribute_name(row_id, field): {
            "current": _text(fields.get(field)),
            "max": "",
        }
        for field in TRAIT_FIELDS
    }


def plan_feature_attributes(plan):
    attrs = {}
    for row in _list(plan.get("rows")):
        for name, spec in _row_attributes(row).items():
            if name in attrs:
                raise RuntimeError(f"Roll20 trait attribute 이름 중복: {name}")
            attrs[name] = spec
    return attrs


def _group_key(item, character, original_index):
    kind = _text(item.get("kind"))

    # 1) racial traits
    if kind == "racial_trait":
        return (0, 0, original_index)

    # 2) background metadata/features
    if kind in {"background_feature", "background"} or _is_background_metadata_feat(item, character):
        return (1, 0, original_index)

    # 3) class features, acquisition level first
    if kind == "class_feature":
        level = item.get("required_level")
        level_key = level if type(level) is int and level > 0 else 999
        return (2, level_key, original_index)

    # 4) actual feats: preserve D&D Beyond payload order (= acquisition order source)
    if kind == "feat":
        return (3, 0, original_index)

    return (4, 0, original_index)


def _raw_feat_links(result_payload):
    """Map granting component IDs to actual feat definition IDs.

    Full D&D Beyond provenance lives in ``raw_source.feats`` in the current
    result schema.  ``original.feats`` is kept only as a compatibility
    fallback for older result formats.
    """
    raw_source = _dict(_dict(result_payload).get("raw_source"))
    original = _dict(_dict(result_payload).get("original"))

    raw_feats = _list(raw_source.get("feats"))
    if not raw_feats:
        raw_feats = _list(original.get("feats"))

    out = {}
    for raw in raw_feats:
        raw = _dict(raw)
        component_id = _text(raw.get("componentId"))
        definition = _dict(raw.get("definition"))
        definition_id = _text(definition.get("id"))
        if not component_id or not definition_id:
            continue
        out.setdefault(component_id, []).append(definition_id)
    return out


def _resolve_feature_grants(result_payload, features):
    """Resolve feature -> selected feat/style using exact DDB component links.

    Examples in the sample character:
    - Variant Human Feat (component 103) -> Great Weapon Master
    - Fighter Fighting Style (component 10292232) -> Great Weapon Fighting

    Only exact one-to-one links are accepted.  No name guessing is used here.
    """
    links = _raw_feat_links(result_payload)

    actual_by_definition = {}
    for item in features:
        item = _dict(item)
        if _text(item.get("kind")) != "feat":
            continue
        definition_id = _text(item.get("definition_id"))
        if definition_id:
            actual_by_definition.setdefault(definition_id, []).append(item)

    resolved = {}
    for item in features:
        item = _dict(item)
        source_key = _text(item.get("source_key"))
        source_id = _text(item.get("source_id") or item.get("definition_id"))
        if not source_key or not source_id:
            continue

        candidates = []
        for definition_id in list(dict.fromkeys(links.get(source_id, []))):
            candidates.extend(actual_by_definition.get(definition_id, []))

        unique = {
            _text(candidate.get("source_key")): candidate
            for candidate in candidates
            if _text(candidate.get("source_key"))
        }
        if len(unique) == 1:
            resolved[source_key] = next(iter(unique.values()))

    return resolved


def _map_resolved_grant(wrapper, actual_feat, character):
    """Show the acquired feat/style name only on the granting feature row.

    The actual feat remains a separate Feat row with its full description.
    """
    row = map_feature_row(wrapper, character)
    actual_name = _plain_text(
        actual_feat.get("name") or actual_feat.get("original_name")
    )
    if not actual_name:
        raise ValueError(
            f"연결된 실제 feat 이름이 비어 있습니다: {row['source_key']}"
        )

    original_wrapper = _text(wrapper.get("original_name"))
    if original_wrapper == "Feat":
        row["name"] = f"특기: {actual_name}"
    elif original_wrapper == "Fighting Style":
        row["name"] = f"전투 스타일: {actual_name}"
    else:
        row["name"] = f"{row['name']}: {actual_name}"

    row["fields"]["name"] = row["name"]

    # Generic racial/background feat grants only need to show what was chosen.
    # Fighting Style is different: the user wants the selected style's actual
    # effect readable directly from the class feature row.
    if original_wrapper == "Fighting Style":
        row["fields"]["description"] = _feature_description(actual_feat)
    else:
        row["fields"]["description"] = actual_name

    row["resolved_feat"] = {
        "source_key": _text(actual_feat.get("source_key")),
        "source_id": _text(actual_feat.get("source_id")),
        "definition_id": _text(actual_feat.get("definition_id")),
        "name": actual_name,
        "original_name": _text(actual_feat.get("original_name")),
    }
    return row


def _background_feat_grant(result_payload, character, features):
    """Build a name-only Background grant row when the background grants a feat.

    The actual feat row remains in the Feat group with its full description.
    """
    background = _dict(character.get("background"))
    translated_name = _plain_text(background.get("feature_name"))
    original_name = _text(background.get("original_feature_name"))
    background_source_id = _text(background.get("source_id"))

    if not translated_name and not original_name:
        return None

    actual = None
    for item in features:
        item = _dict(item)
        if _text(item.get("kind")) != "feat":
            continue
        candidate_original = _text(item.get("original_name"))
        candidate_name = _text(item.get("name"))
        if original_name and candidate_original.casefold() == original_name.casefold():
            actual = item
            break
        if translated_name and candidate_name.casefold() == translated_name.casefold():
            actual = item
            break

    # Only show a feat grant if an actual selected feat exists in the character.
    if actual is None:
        return None

    actual_name = _plain_text(actual.get("name") or actual.get("original_name"))
    if not actual_name:
        return None

    source_key = (
        f"background-feat:{background_source_id or _text(background.get('original_name'))}:"
        f"{_text(actual.get('definition_id') or actual.get('source_id'))}"
    )
    row_id = feature_row_id(source_key)

    return {
        "source_key": source_key,
        "source_id": background_source_id,
        "definition_id": _text(actual.get("definition_id")),
        "kind": "background_feature",
        "original_name": original_name or "Feat",
        "required_level": None,
        "row_id": row_id,
        "name": f"특기: {actual_name}",
        "fields": {
            "name": f"배경 특기: {actual_name}",
            "source": "Background",
            "source_type": _background_name(character),
            "description": actual_name,
            "options-flag": "0",
        },
        "limited_use": None,
        "resolved_feat": {
            "source_key": _text(actual.get("source_key")),
            "source_id": _text(actual.get("source_id")),
            "definition_id": _text(actual.get("definition_id")),
            "name": actual_name,
            "original_name": _text(actual.get("original_name")),
        },
        "synthetic": True,
    }


def build_feature_plan(result_payload: dict[str, Any]) -> dict[str, Any]:
    roll20_payload = _dict(_dict(result_payload).get("roll20_payload"))
    character = _dict(roll20_payload.get("character"))
    features = _list(roll20_payload.get("features"))
    source_character_id = _text(roll20_payload.get("source_character_id"))
    character_name = _text(character.get("name"))

    if not source_character_id:
        raise RuntimeError("roll20_payload.source_character_id가 없습니다.")
    if not character_name:
        raise RuntimeError("roll20_payload.character.name이 없습니다.")

    resolved_feature_grants = _resolve_feature_grants(
        result_payload,
        features,
    )

    active_items = []
    excluded_items = []
    all_managed_row_ids = []

    for index, item in enumerate(features):
        item = _dict(item)
        source_key = _text(item.get("source_key"))
        original_name = _text(item.get("original_name"))
        row_id = feature_row_id(source_key)
        all_managed_row_ids.append(row_id)

        if original_name in EXCLUDED_ORIGINAL_NAMES:
            excluded_items.append({
                "source_key": source_key,
                "row_id": row_id,
                "name": _text(item.get("name")),
                "original_name": original_name,
                "reason": "dedicated_roll20_field",
            })
            continue

        # Generic Feat grants are useful only when the actual selected feat
        # is known.  Fighting Style stays even if unresolved, but when linked it
        # displays the chosen style name.
        if original_name == "Feat":
            actual = resolved_feature_grants.get(source_key)
            if actual is None:
                excluded_items.append({
                    "source_key": source_key,
                    "row_id": row_id,
                    "name": _text(item.get("name")),
                    "original_name": original_name,
                    "reason": "unresolved_generic_feat",
                })
                continue
            active_items.append((index, item, actual))
            continue

        active_items.append(
            (index, item, resolved_feature_grants.get(source_key))
        )

    background_grant = _background_feat_grant(
        result_payload,
        character,
        features,
    )

    active_items.sort(key=lambda entry: _group_key(entry[1], character, entry[0]))

    rows = []
    seen_source_keys = set()
    seen_row_ids = set()
    for _, item, resolved_actual in active_items:
        if resolved_actual is not None:
            row = _map_resolved_grant(
                item,
                resolved_actual,
                character,
            )
        else:
            row = map_feature_row(item, character)

        if row["source_key"] in seen_source_keys:
            raise RuntimeError(f"특성 source_key 중복: {row['source_key']}")
        if row["row_id"] in seen_row_ids:
            raise RuntimeError(f"특성 row_id 충돌: {row['row_id']}")
        seen_source_keys.add(row["source_key"])
        seen_row_ids.add(row["row_id"])
        rows.append(row)

    if background_grant is not None:
        if background_grant["source_key"] in seen_source_keys:
            raise RuntimeError(
                f"배경 특성 source_key 중복: {background_grant['source_key']}"
            )
        if background_grant["row_id"] in seen_row_ids:
            raise RuntimeError(
                f"배경 특성 row_id 충돌: {background_grant['row_id']}"
            )

        # Background rows must sit after racial rows and before class rows.
        insert_at = 0
        for idx, row in enumerate(rows):
            if row["fields"]["source"] in {"Racial", "Background"}:
                insert_at = idx + 1
            else:
                break
        rows.insert(insert_at, background_grant)
        seen_source_keys.add(background_grant["source_key"])
        seen_row_ids.add(background_grant["row_id"])
        all_managed_row_ids.append(background_grant["row_id"])

    return {
        "version": STAGE8_VERSION,
        "source_character_id": source_character_id,
        "character_name": character_name,
        "row_count": len(rows),
        "rows": rows,
        "excluded_rows": excluded_items,
        "all_managed_row_ids": all_managed_row_ids,
        "desired_managed_order": [row["row_id"] for row in rows],
        "resolved_feature_grants": {
            source_key: {
                "source_key": _text(actual.get("source_key")),
                "definition_id": _text(actual.get("definition_id")),
                "name": _text(actual.get("name")),
            }
            for source_key, actual in resolved_feature_grants.items()
        },
        "policy": {
            "preserve_unmanaged_rows": True,
            "delete_existing_rows": False,
            "delete_only_explicitly_excluded_managed_rows": True,
            "stable_row_ids": True,
            "write_limited_use": False,
            "create_resources": False,
            "collapse_rows": True,
            "order": "racial-background-class-feat",
            "class_order": "required_level_then_payload",
            "feat_order": "payload_order",
            "generic_feat_policy": (
                "resolve_exact_choice_name_only_keep_actual_feat_else_omit"
            ),
            "background_feat_policy": (
                "show_name_only_grant_keep_actual_feat"
            ),
            "fighting_style_policy": (
                "show_selected_style_name_and_full_description_keep_actual_feat"
            ),
        },
        "deferred": [
            "actions",
            "attacks",
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
            f"8단계는 ogl5e만 지원합니다: "
            f"{_text(payload.get('sheet_type')) or '미확인'}"
        )
    if not _text(payload.get("roll20_character_id")):
        raise RuntimeError("Roll20 Character ID가 없습니다.")
    return payload, path


TRAIT_STATE_SCRIPT = r"""
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
  const rowIds=[];
  const seen=new Set();
  let reporder='';
  for (const model of modelsOf(collection)) {
    const name=String(val(model,'name') || '').trim();
    if (name === '_reporder_repeating_traits') {
      reporder=String(val(model,'current') == null ? '' : val(model,'current'));
    }
    const match=name.match(/^repeating_traits_([^_]+)_/);
    if (match && !seen.has(match[1])) {
      seen.add(match[1]);
      rowIds.push(match[1]);
    }
  }
  done({
    ok:status === 'success' || status === 'success_promise',
    fetch_status:status,
    fetch_error:error || '',
    character_id:idOf(character),
    row_ids:rowIds,
    reporder,
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


DELETE_TRAIT_ROWS_SCRIPT = r"""
const wantedId = String(arguments[0] || '').trim();
const wantedName = String(arguments[1] || '').trim();
const rowIds = arguments[2] || [];
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
  for(const model of modelsOf(collection)){
    const name=String(val(model,'name') || '').trim();
    for(const rowId of rowIds){
      if(name.startsWith(`repeating_traits_${rowId}_`)){
        targets.push([model,name]);
        break;
      }
    }
  }

  try {
    const deleted=[];
    // Sequential destruction is slower but avoids overwhelming Roll20.
    for(const [model,name] of targets){
      deleted.push(await waitDestroy(model,name));
    }
    done({ok:true,deleted_count:deleted.length,deleted});
  } catch(e) {
    done({ok:false,reason:'delete_failed',error:String(e && e.stack ? e.stack : e)});
  }
})();
"""


def _trait_state(driver, target):
    from .roll20_read import read_persisted
    return read_persisted(
        driver,
        TRAIT_STATE_SCRIPT,
        _text(target.get("roll20_character_id")),
        _text(target.get("character_name")),
    )


def _build_reporder(state, plan):
    existing = [str(v) for v in _list(state.get("row_ids")) if v]
    current_order = [
        part.strip()
        for part in _text(state.get("reporder")).split(",")
        if part.strip()
    ]

    managed_all = set(_list(plan.get("all_managed_row_ids")))
    desired = list(_list(plan.get("desired_managed_order")))

    # Preserve manual/unmanaged order. Prefer explicit Roll20 reporder, then
    # append rows that currently exist but weren't named in reporder.
    manual = []
    seen = set()
    for row_id in current_order + existing:
        if row_id in managed_all or row_id in seen:
            continue
        seen.add(row_id)
        manual.append(row_id)

    return desired + manual


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


def _delete_excluded_rows(driver, target, plan):
    row_ids = [
        _text(row.get("row_id"))
        for row in _list(plan.get("excluded_rows"))
        if _text(row.get("row_id"))
    ]
    if not row_ids:
        return {"ok": True, "deleted_count": 0, "deleted": []}

    driver.set_script_timeout(max(45, len(row_ids) * 20))
    result = driver.execute_async_script(
        DELETE_TRAIT_ROWS_SCRIPT,
        _text(target.get("roll20_character_id")),
        _text(target.get("character_name")),
        row_ids,
    )
    if not isinstance(result, dict) or not result.get("ok"):
        raise RuntimeError(
            "제외 특성행 삭제 실패: "
            + json.dumps(result, ensure_ascii=False)
        )
    return result


def _verify_excluded_absent(driver, target, plan):
    state = _trait_state(driver, target)
    existing = set(_list(state.get("row_ids")))
    unwanted = [
        row for row in _list(plan.get("excluded_rows"))
        if _text(row.get("row_id")) in existing
    ]
    return state, unwanted


def apply_features(
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
    plan = build_feature_plan(payload)
    actual_source_id = plan["source_character_id"]
    target, target_path = _load_target(actual_source_id)

    if _text(target.get("character_name")) != plan["character_name"]:
        raise RuntimeError("D&D Beyond 결과와 Roll20 대상 캐릭터 이름이 다릅니다.")

    active_attrs = plan_feature_attributes(plan)
    order_attr_name = "_reporder_repeating_traits"
    output_path = (
        Path(CURRENT_RESULT_DIR)
        / f"roll20-features-{actual_source_id}.json"
    )

    report = {
        "version": STAGE8_VERSION,
        "mode": "dry-run" if dry_run else "apply",
        "source_character_id": actual_source_id,
        "character_name": plan["character_name"],
        "roll20_character_id": target["roll20_character_id"],
        "result_path": str(path.resolve()),
        "target_path": str(Path(target_path).resolve()),
        "row_count": plan["row_count"],
        "managed_attribute_count": len(active_attrs),
        "excluded_rows": plan["excluded_rows"],
        "desired_managed_order": plan["desired_managed_order"],
        "policy": plan["policy"],
        "rows": plan["rows"],
        "backup_path": None,
        "mutated": False,
        "delete_result": None,
        "row_results": [],
        "order_result": None,
        "verification": {},
        "status": "running",
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }

    _ensure_cdp(cdp_url)
    driver = _attach_driver(cdp_url)

    try:
        _select_roll20_tab(driver)

        state_before = _trait_state(driver, target)
        before = _snapshot(
            driver,
            target,
            list(active_attrs.keys()) + [order_attr_name],
        )
        report["before"] = {
            "managed_attributes": before["attributes"],
            "trait_state": state_before,
        }

        pending_rows = []
        for row in plan["rows"]:
            attrs = _row_attributes(row)
            if not _is_complete(before, attrs):
                pending_rows.append(row)

        existing_ids = set(_list(state_before.get("row_ids")))
        excluded_present = [
            row for row in plan["excluded_rows"]
            if row["row_id"] in existing_ids
        ]
        desired_reporder = _build_reporder(state_before, plan)
        desired_reporder_value = ",".join(desired_reporder)
        current_reporder_value = _text(state_before.get("reporder"))
        order_pending = current_reporder_value != desired_reporder_value

        report["initial_pending_rows"] = [
            {
                "source_key": row["source_key"],
                "row_id": row["row_id"],
                "kind": row["kind"],
                "name": row["name"],
            }
            for row in pending_rows
        ]
        report["excluded_present_before"] = excluded_present
        report["desired_reporder"] = desired_reporder
        report["order_pending"] = order_pending

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
            / f"roll20-stage8-features-backup-"
              f"{actual_source_id}-{timestamp}.json"
        )
        _save_json(
            backup_path,
            {
                "version": STAGE8_VERSION,
                "source_character_id": actual_source_id,
                "character_name": plan["character_name"],
                "roll20_character_id": target["roll20_character_id"],
                "read_only_snapshot_before_apply": True,
                "managed_attributes": before["attributes"],
                "trait_state": state_before,
            },
        )
        report["backup_path"] = str(backup_path.resolve())
        _save_json(output_path, report)

        if excluded_present:
            print(
                "[시트 이동기] 제외 특성행을 정리합니다: "
                + ", ".join(row["name"] for row in excluded_present),
                flush=True,
            )
            report["delete_result"] = _delete_excluded_rows(driver, target, plan)
            report["mutated"] = True
            _save_json(output_path, report)

        total = len(pending_rows)
        for index, row in enumerate(pending_rows, start=1):
            print(
                f"[시트 이동기] 특성 {index}/{total}: {row['name']}",
                flush=True,
            )
            attrs = _row_attributes(row)
            outcome, actual = _upsert_and_verify(
                driver,
                target,
                attrs,
                f"특성 '{row['name']}'",
            )
            report["mutated"] = True
            report["row_results"].append({
                "source_key": row["source_key"],
                "row_id": row["row_id"],
                "kind": row["kind"],
                "name": row["name"],
                "result": outcome,
                "verification": {
                    "status": "pass",
                    "actual": actual,
                    "mismatches": [],
                },
            })
            _save_json(output_path, report)

        # Re-read after deletions/creates, then keep unmanaged rows after our
        # desired managed order.
        state_mid = _trait_state(driver, target)
        final_reporder = _build_reporder(state_mid, plan)
        reporder_attr = {
            order_attr_name: {
                "current": ",".join(final_reporder),
                "max": "",
            }
        }
        current_mid = _text(state_mid.get("reporder"))
        if current_mid != reporder_attr[order_attr_name]["current"]:
            print("[시트 이동기] 특성 표시 순서를 정리합니다.", flush=True)
            outcome, actual = _upsert_and_verify(
                driver,
                target,
                reporder_attr,
                "특성 표시 순서",
            )
            report["mutated"] = True
            report["order_result"] = {
                "result": outcome,
                "verification": {
                    "status": "pass",
                    "actual": actual,
                    "mismatches": [],
                },
            }

        final_attrs = {**active_attrs, **reporder_attr}
        final_snapshot = _snapshot(driver, target, final_attrs.keys())
        actual, mismatches = _verify(final_snapshot, final_attrs)
        state_final, excluded_still_present = _verify_excluded_absent(
            driver, target, plan
        )

        if excluded_still_present:
            mismatches.append({
                "reason": "excluded_rows_still_present",
                "rows": excluded_still_present,
            })

        expected_order = final_reporder
        final_order = [
            part.strip()
            for part in _text(state_final.get("reporder")).split(",")
            if part.strip()
        ]
        if final_order != expected_order:
            mismatches.append({
                "reason": "reporder_mismatch",
                "expected": expected_order,
                "actual": final_order,
            })

        report["verification"] = {
            "status": "pass" if not mismatches else "fail",
            "actual": actual,
            "excluded_rows_absent": not excluded_still_present,
            "final_reporder": final_order,
            "mismatches": mismatches,
            "server_fetch_status": final_snapshot.get("fetch_status"),
        }
        report["status"] = "pass" if not mismatches else "error"

        if mismatches:
            report["error"] = (
                "8단계 특성 최종 서버 재검증 실패: "
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

    print("[시트 이동기] 8단계 v1.1: Roll20 특성 정리 및 입력")
    print(f"[시트 이동기] 모드: {'읽기 전용' if args.dry_run else '실제 입력'}")
    print("[시트 이동기] 제외: 언어 / 기술 / 전사 핵심 특성")
    print("[시트 이동기] 종족/배경/전투스타일 획득 경로에는 실제 선택 이름만 표시합니다.")
    print("[시트 이동기] 주문시전의 슬롯 표는 설명에서 제거합니다.")
    print("[시트 이동기] 순서: 종족 -> 배경 -> 클래스 -> 재주")
    print("[시트 이동기] 모든 특성행은 닫힌 상태로 입력합니다.")
    print("[시트 이동기] 사용 횟수/자원은 12단계까지 건드리지 않습니다.")

    report, output = apply_features(
        result_path=args.result,
        source_id=args.source_id,
        cdp_url=args.cdp_url,
        dry_run=args.dry_run,
    )

    print(f"[시트 이동기] 최종 특성: {report['row_count']}개")
    print(
        f"[시트 이동기] 제거 대상: "
        f"{len(report.get('excluded_rows') or [])}개"
    )
    if not args.dry_run:
        print(f"[시트 이동기] 백업: {report.get('backup_path')}")
        print(
            "[시트 이동기] 최종 서버 재검증: "
            + str((report.get("verification") or {}).get("status"))
        )
    print("[시트 이동기] 행동/공격/기술·내성/클래스 자원은 수정하지 않았습니다.")
    print(f"[시트 이동기] 결과 저장: {output.resolve()}")


if __name__ == "__main__":
    main()
