# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sys

ROOT = Path.cwd()
SPELLS = ROOT / "sheet_mover" / "roll20_spells.py"
PROFS = ROOT / "sheet_mover" / "roll20_proficiencies.py"
FULL_RUN = ROOT / "sheet_mover" / "full_run.py"
MOVER = ROOT / "sheet_mover" / "mover.py"
UI = ROOT / "sheet_mover" / "ui.py"
TEST_FULL_RUN = ROOT / "tests" / "test_full_run.py"
BASE_COMMIT = "29c50d9c5850495667a02666e08a1acb25ab1591"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: 예상 코드 1개가 필요한데 {count}개를 찾았습니다.")
    return text.replace(old, new, 1)


def replace_region(text: str, start_marker: str, end_marker: str, new: str, label: str) -> str:
    start = text.find(start_marker)
    if start < 0:
        raise RuntimeError(f"{label}: 시작 위치를 찾지 못했습니다.")
    end = text.find(end_marker, start)
    if end < 0:
        raise RuntimeError(f"{label}: 끝 위치를 찾지 못했습니다.")
    return text[:start] + new + text[end:]


def patch_spells(text: str) -> str:
    if "stage7-roll20-spells-v2.1-grouped-source-damage" in text:
        return text

    text = replace_once(
        text,
        'STAGE7_VERSION = "2026-10-06-stage7-roll20-spells-v2-combat-fields"',
        'STAGE7_VERSION = "2026-10-07-stage7-roll20-spells-v2.1-grouped-source-damage"',
        "Stage 7 version",
    )

    old = '''def _raw_spell_definitions(result_payload):
    """Return D&D Beyond raw spell definitions keyed by character spell id."""
    raw_source = _dict(_dict(result_payload).get("raw_source"))
    out = {}

    for class_spell_group in _list(raw_source.get("classSpells")):
        class_spell_group = _dict(class_spell_group)
        for spell in _list(class_spell_group.get("spells")):
            spell = _dict(spell)
            source_id = _text(spell.get("id"))
            definition = _dict(spell.get("definition"))
            if source_id and definition:
                out[source_id] = definition

    # Compatibility fallback if a future result exposes top-level spells.
    for spell in _list(raw_source.get("spells")):
        spell = _dict(spell)
        source_id = _text(spell.get("id"))
        definition = _dict(spell.get("definition"))
        if source_id and definition:
            out[source_id] = definition

    return out
'''
    new = '''def _raw_spell_definitions(result_payload):
    """Return raw DDB spell definitions keyed by character spell id.

    D&D Beyond exposes ordinary class-list spells under classSpells[*].spells,
    while race/class/background/item/feat granted spells can live inside a
    grouped top-level spells object. Moonbeam in the current multiclass sample
    is one of those grouped spells.
    """
    raw_source = _dict(_dict(result_payload).get("raw_source"))
    out = {}

    def add_spells(entries, *, overwrite=False):
        for spell in _list(entries):
            spell = _dict(spell)
            source_id = _text(spell.get("id"))
            definition = _dict(spell.get("definition"))
            if not source_id or not definition:
                continue
            if overwrite or source_id not in out:
                out[source_id] = definition

    for class_spell_group in _list(raw_source.get("classSpells")):
        class_spell_group = _dict(class_spell_group)
        add_spells(class_spell_group.get("spells"), overwrite=True)

    grouped = raw_source.get("spells")
    if isinstance(grouped, dict):
        for entries in grouped.values():
            add_spells(entries)
    else:
        add_spells(grouped)

    return out
'''
    text = replace_once(text, old, new, "grouped raw spell definitions")

    old_delta = '''        delta = next(
            (
                row for row in higher
                if row.get("level") == level
                and _dict(row.get("dice")).get("diceString")
            ),
            None,
        )
'''
    new_delta = '''        # Leveled-spell modifiers normally encode the per-slot-above-base
        # increment as level=1, even for a base 2nd-level spell such as
        # Moonbeam or Heat Metal. Keep the previous interpretation as a
        # compatibility fallback for older payload shapes.
        delta = next(
            (
                row for row in higher
                if row.get("level") == 1
                and _dict(row.get("dice")).get("diceString")
            ),
            None,
        )
        if delta is None:
            delta = next(
                (
                    row for row in higher
                    if row.get("level") == level
                    and _dict(row.get("dice")).get("diceString")
                ),
                None,
            )
        if delta is None:
            dice_rows = [
                row for row in higher
                if _dict(row.get("dice")).get("diceString")
            ]
            if len(dice_rows) == 1:
                delta = dice_rows[0]
'''
    text = replace_once(text, old_delta, new_delta, "leveled spell scaling")

    text = replace_once(
        text,
        '''            "combat_fields_prepared": True,
            "create_attacks": False,
''',
        '''            "combat_fields_prepared": True,
            "grouped_raw_spell_sources": True,
            "leveled_spell_scaling_delta": True,
            "create_attacks": False,
''',
        "Stage 7 policy",
    )
    return text


def patch_proficiencies(text: str) -> str:
    if "stage11-roll20-proficiencies-v2.2-resilient-batches" in text:
        return text

    text = replace_once(
        text,
        'STAGE11_VERSION = "2026-10-06-stage11-roll20-proficiencies-v2.1-reporder"',
        'STAGE11_VERSION = "2026-10-07-stage11-roll20-proficiencies-v2.2-resilient-batches"',
        "Stage 11 version",
    )
    if "\nimport time\n" not in text:
        text = replace_once(text, "import json\n", "import json\nimport time\n", "Stage 11 time import")
    text = replace_once(text, "ROW_HASH_LENGTH = 17\n", "ROW_HASH_LENGTH = 17\nWRITE_BATCH_SIZE = 16\n", "Stage 11 batch constant")

    old_save = '''def _save_entries(result_payload):
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
'''
    new_save = '''def _starting_class_context(original):
    classes = [
        _dict(row)
        for row in _list(_dict(original).get("classes"))
        if isinstance(row, dict)
    ]
    starting = next(
        (
            _text(row.get("original_name") or row.get("name"))
            for row in classes
            if row.get("is_starting_class") is True
        ),
        "",
    )
    if not starting and classes:
        starting = _text(classes[0].get("original_name") or classes[0].get("name"))

    feature_owners = {}
    calculation_inputs = _dict(_dict(original).get("calculation_inputs"))
    for row in _list(calculation_inputs.get("class_feature_levels")):
        row = _dict(row)
        feature_id = _text(row.get("feature_id"))
        class_name = _text(row.get("class_name"))
        if feature_id and class_name:
            feature_owners[feature_id] = class_name

    core_feature_ids = set()
    for feature in _list(_dict(original).get("features")):
        feature = _dict(feature)
        if _text(feature.get("kind")) != "class_feature":
            continue
        original_name = _text(feature.get("original_name") or feature.get("name"))
        if original_name.startswith("Core ") and original_name.endswith(" Traits"):
            feature_id = _text(feature.get("source_id") or feature.get("definition_id"))
            if feature_id:
                core_feature_ids.add(feature_id)

    return starting, feature_owners, core_feature_ids


def _save_entries(result_payload):
    original = _original_character(result_payload)
    result = set()
    detailed_save_found = False
    starting_class, feature_owners, core_feature_ids = _starting_class_context(original)

    for entry in _list(original.get("proficiency_entries")):
        entry = _dict(entry)
        if _normalize_modifier_type(entry.get("type")) != "proficiency":
            continue
        raw_subtype = _text(entry.get("subType")).strip().casefold()
        ability = SAVE_SUBTYPE_TO_ABILITY.get(raw_subtype)
        if not ability:
            continue
        detailed_save_found = True

        component_id = _text(entry.get("componentId"))
        source_group = _text(entry.get("source_group")).casefold()
        owner = feature_owners.get(component_id, "")
        if (
            source_group == "class"
            and starting_class
            and owner
            and owner != starting_class
            and component_id in core_feature_ids
        ):
            continue
        result.add(ability)

    if not detailed_save_found:
        for value in _list(original.get("saving_throw_proficiencies")):
            value_text = _text(value).strip().casefold()
            for ability in ABILITY_ORDER:
                if value_text == f"{ability} saving throws":
                    result.add(ability)

    return result
'''
    text = replace_once(text, old_save, new_save, "multiclass save filtering")

    text = replace_once(
        text,
        '''        key = _entry_key(entry)
        if not key:
            continue

        display_name = _entry_display_name(entry, translated_map)
''',
        '''        key = _entry_key(entry)
        if not key or key.startswith("choose_a_"):
            continue

        display_name = _entry_display_name(entry, translated_map)
''',
        "generic proficiency placeholder filter",
    )

    text = replace_once(
        text,
        '''            "write_visible_skill_save_bonus": True,
''',
        '''            "write_visible_skill_save_bonus": True,
            "write_batch_size": WRITE_BATCH_SIZE,
            "persisted_state_after_callback_timeout": True,
            "no_wait_repair": True,
            "starting_class_saves_only": True,
            "omit_generic_choice_placeholders": True,
''',
        "Stage 11 policy",
    )

    marker = "\ndef _upsert_and_verify(driver, target, attrs):\n"
    if marker not in text:
        raise RuntimeError("Stage 11 _upsert_and_verify 위치를 찾지 못했습니다.")

    helper = r'''\nSTAGE11_NO_WAIT_ATTR_SCRIPT = r"""
const wantedId = String(arguments[0] || '').trim();
const wantedName = String(arguments[1] || '').trim();
const attrName = String(arguments[2] || '').trim();
const spec = arguments[3] || {};
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
function named(collection,name) {
  return modelsOf(collection).filter(m=>String(val(m,'name') || '').trim()===name);
}
function finish(payload) { try { done(payload); } catch (_) {} }

const character=findCharacter();
if (!character) { finish({ok:false,reason:'character_not_found'}); return; }
const collection=character.attribs;
if (!collection) { finish({ok:false,reason:'attribute_collection_unavailable'}); return; }
const current=String(spec.current == null ? '' : spec.current);
const max=String(spec.max == null ? '' : spec.max);

try {
  const existing=named(collection,attrName);
  if (existing.length > 1) { finish({ok:false,reason:'duplicate_attribute',count:existing.length}); return; }
  if (existing.length === 1) {
    const model=existing[0];
    const before=String(val(model,'current') == null ? '' : val(model,'current'));
    const beforeMax=String(val(model,'max') == null ? '' : val(model,'max'));
    if (before===current && beforeMax===max) { finish({ok:true,action:'skip',id:idOf(model)}); return; }
    model.save({current:current,max:max},{wait:false});
    finish({ok:true,action:'update_dispatched',id:idOf(model)});
    return;
  }
  const model=collection.create({name:attrName,current:current,max:max,characterid:wantedId},{wait:false});
  finish({ok:true,action:'create_dispatched',id:idOf(model)});
} catch(e) {
  finish({ok:false,reason:'dispatch_exception',error:String(e && e.stack ? e.stack : e)});
}
"""


def _attribute_batches(attrs, size=WRITE_BATCH_SIZE):
    items = list(attrs.items())
    return [dict(items[index:index + size]) for index in range(0, len(items), size)]


def _poll_attribute(driver, target, name, spec, attempts=8, delay=0.5):
    expected = {name: spec}
    last_mismatches = []
    for attempt in range(1, attempts + 1):
        snapshot = _snapshot(driver, target, [name])
        actual, mismatches = _verify(snapshot, expected)
        last_mismatches = mismatches
        if not mismatches:
            return actual[name], attempt
        if attempt < attempts:
            time.sleep(delay)
    raise RuntimeError(
        "11단계 숙련 서버 반영 확인 실패: "
        + json.dumps(last_mismatches, ensure_ascii=False)
    )


def _repair_attribute(driver, target, name, spec):
    driver.set_script_timeout(30)
    outcome = driver.execute_async_script(
        STAGE11_NO_WAIT_ATTR_SCRIPT,
        _text(target.get("roll20_character_id")),
        _text(target.get("character_name")),
        name,
        spec,
    )
    if not isinstance(outcome, dict) or not outcome.get("ok"):
        raise RuntimeError(
            "11단계 숙련 복구 저장 실패: "
            + json.dumps({"attribute": name, "outcome": outcome}, ensure_ascii=False)
        )
    actual, attempts = _poll_attribute(driver, target, name, spec)
    return {
        "attribute": name,
        "outcome": outcome,
        "persisted_verify_attempts": attempts,
        "actual": actual,
    }


'''
    text = text.replace(marker, "\n" + helper + "def _upsert_and_verify(driver, target, attrs):\n", 1)

    old_upsert = '''def _upsert_and_verify(driver, target, attrs):
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
'''
    new_upsert = '''def _upsert_and_verify(driver, target, attrs):
    batch_results = []

    for batch_index, batch in enumerate(_attribute_batches(attrs), start=1):
        driver.set_script_timeout(60)
        outcome = driver.execute_async_script(
            UPSERT_ROW_SCRIPT,
            _text(target.get("roll20_character_id")),
            _text(target.get("character_name")),
            batch,
        )

        after = _snapshot(driver, target, batch.keys())
        _, mismatches = _verify(after, batch)
        repairs = []
        if mismatches:
            for mismatch in mismatches:
                name = _text(_dict(mismatch).get("attribute"))
                if name and name in batch:
                    repairs.append(_repair_attribute(driver, target, name, batch[name]))
            after = _snapshot(driver, target, batch.keys())
            _, mismatches = _verify(after, batch)

        if mismatches:
            raise RuntimeError(
                "11단계 숙련 서버 재검증 실패: "
                + json.dumps(
                    {"batch": batch_index, "writer_outcome": outcome, "mismatches": mismatches},
                    ensure_ascii=False,
                )
            )

        batch_results.append({
            "batch": batch_index,
            "attribute_count": len(batch),
            "writer_outcome": outcome,
            "repairs": repairs,
        })

    final_snapshot = _snapshot(driver, target, attrs.keys())
    actual, mismatches = _verify(final_snapshot, attrs)
    if mismatches:
        repairs = []
        for mismatch in mismatches:
            name = _text(_dict(mismatch).get("attribute"))
            if name and name in attrs:
                repairs.append(_repair_attribute(driver, target, name, attrs[name]))
        final_snapshot = _snapshot(driver, target, attrs.keys())
        actual, mismatches = _verify(final_snapshot, attrs)
        batch_results.append({
            "batch": "final_repair",
            "attribute_count": len(repairs),
            "writer_outcome": {"ok": True, "action": "persisted_repair"},
            "repairs": repairs,
        })

    if mismatches:
        raise RuntimeError(
            "11단계 숙련 최종 서버 재검증 실패: "
            + json.dumps(mismatches, ensure_ascii=False)
        )

    return {"ok": True, "batch_size": WRITE_BATCH_SIZE, "batches": batch_results}, actual
'''
    text = replace_once(text, old_upsert, new_upsert, "Stage 11 resilient writer")
    return text


def patch_mover(text: str) -> str:
    if "async def prepare(self, raw_source=None):" not in text:
        text = replace_once(
            text,
            '''    async def prepare(self):
        self.report(5, "D&D Beyond 링크를 확인합니다.")
        self.report(15, "D&D Beyond 원본 데이터를 수집합니다.")

        raw = await asyncio.to_thread(
            fetch_character,
            self.source_url,
        )
        original = normalize_character(raw)
''',
            '''    async def prepare(self, raw_source=None):
        self.report(5, "D&D Beyond 링크를 확인합니다.")
        if raw_source is None:
            self.report(15, "D&D Beyond 원본 데이터를 수집합니다.")
            raw = await asyncio.to_thread(
                fetch_character,
                self.source_url,
            )
        else:
            self.report(15, "사전 점검에서 확인한 D&D Beyond 원본을 사용합니다.")
            raw = raw_source
        original = normalize_character(raw)
''',
            "mover preloaded raw source",
        )

    old_run = '''def run(source_url=SOURCE_URL, cdp_url=None, on_progress=None):
    return asyncio.run(
        SheetMover(
            source_url,
            cdp_url,
            on_progress,
        ).prepare()
    )
'''
    new_run = '''def run(source_url=SOURCE_URL, cdp_url=None, on_progress=None, raw_source=None):
    return asyncio.run(
        SheetMover(
            source_url,
            cdp_url,
            on_progress,
        ).prepare(raw_source=raw_source)
    )
'''
    if new_run not in text:
        text = replace_once(text, old_run, new_run, "mover run raw_source")
    return text


def patch_full_run(text: str) -> str:
    if "stage13-full-run-v2.2-roll20-preflight" in text:
        return text

    text = replace_once(
        text,
        'FULL_RUN_VERSION = "2026-10-06-stage13-full-run-v2.1-soft-fallback"',
        'FULL_RUN_VERSION = "2026-10-07-stage13-full-run-v2.2-roll20-preflight"',
        "full-run version",
    )

    start_marker = '        for name, check in (("Google", check_google), ("Ollama", check_ollama)):\n'
    end_marker = '        run_stage(3, "basic", apply_basic, source_id=source_id,\n'
    new_region = '''        # Import stages only after applying the worker's settings snapshot.
        from .mover import run as prepare
        from .result_store import default_result_path
        from .roll20_connection import check_roll20_target
        from .source import fetch_character
        from .roll20_inventory import apply_inventory
        from .roll20_spells import apply_spells
        from .roll20_features import apply_features
        from .roll20_attacks import apply_attacks
        from .roll20_spell_attacks import apply_spell_attacks
        from .roll20_proficiencies import apply_proficiencies
        from .roll20_resources import apply_resources
        from stage5_basic_writer_v3 import run as apply_basic

        # Roll20 readiness is checked before any Google/Ollama translation work.
        # Only the D&D Beyond character ID/name is needed for this preflight.
        stage(2, "running", "번역 전에 Roll20 대상과 시트 상태를 확인합니다.")
        overall(1, "D&D Beyond 캐릭터 ID와 이름만 먼저 확인합니다.")
        identity_raw = fetch_character(source_url)
        source_id = str(identity_raw.get("id") or "").strip()
        character_name = str(identity_raw.get("name") or "").strip()
        if not source_id or not character_name:
            raise RuntimeError("D&D Beyond 원본에서 캐릭터 ID/이름을 확인하지 못했습니다.")

        state["source_character_id"] = source_id
        state["character_name"] = character_name

        preflight_path = data_dir() / "workers" / run_id / "roll20-preflight.json"
        _write_json(
            preflight_path,
            {
                "original": {"source_id": source_id, "name": character_name},
                "roll20_payload": {
                    "source_character_id": source_id,
                    "character": {"name": character_name},
                },
            },
        )
        try:
            target, target_path = check_roll20_target(
                result_path=preflight_path,
                source_id=source_id,
                cdp_url=settings.roll20_cdp_url,
                save=True,
            )
        finally:
            preflight_path.unlink(missing_ok=True)

        sheet_type = str(getattr(target, "sheet_type", "") or "").strip()
        if sheet_type != "ogl5e":
            raise RuntimeError(
                "Roll20 대상 캐릭터가 Legacy OGL5e 시트가 아닙니다. "
                f"확인된 시트 유형: {sheet_type or '미확인'}"
            )

        state["reports"]["target"] = (
            target.__dict__ if hasattr(target, "__dict__") else str(target)
        )
        state["paths"]["target"] = str(Path(target_path).resolve())
        stage(2, "pass", "Roll20 게임 탭 · 대상 캐릭터 · Legacy OGL5e 시트 확인 완료")
        overall(5, "Roll20 준비 완료. 번역 서비스 상태를 확인합니다.")

        for name, check in (("Google", check_google), ("Ollama", check_ollama)):
            result = check(settings)
            if not result.get("ok"):
                raise RuntimeError(
                    f"{name}: {result.get('message', '설정 오류')} "
                    f"{result.get('detail') or ''}".strip()
                )

        stage(1, "running")
        prepared = prepare(
            source_url,
            cdp_url=settings.roll20_cdp_url,
            on_progress=lambda p, m: overall(5 + float(p) * .25, m),
            raw_source=identity_raw,
        )
        payload = prepared.to_dict()
        original = payload.get("original") or {}
        prepared_source_id = str(original.get("source_id") or "").strip()
        prepared_name = str(original.get("name") or "").strip()
        if prepared_source_id != source_id or prepared_name != character_name:
            raise RuntimeError(
                "사전 확인한 D&D Beyond 캐릭터와 번역 대상이 달라졌습니다. "
                "Roll20 입력을 중단합니다."
            )

        summary = payload.get("translation_summary") or {}
        translation_status = str(summary.get("status") or "").strip()
        preserved_count = int(summary.get("original_preserved_count") or 0)
        state["translation_summary"] = summary
        if translation_status not in {"complete", "partial"}:
            raise RuntimeError(
                "번역 결과 상태를 확인할 수 없어 Roll20 입력을 중단합니다. "
                f"상태={translation_status or '없음'}"
            )

        result_path = default_result_path(source_id, root=data_dir())
        _write_json(result_path, payload)
        state["paths"]["sheet_result"] = str(result_path.resolve())

        if translation_status == "partial":
            stage(
                1,
                "pass",
                f"원문 {preserved_count}개를 안전하게 유지하고 계속 진행합니다. "
                f"결과 저장: {result_path.name}",
            )
            overall(30, f"D&D Beyond 준비 완료 · 원문 유지 {preserved_count}개")
        else:
            stage(1, "pass", f"결과 저장: {result_path.name}")
            overall(30, "D&D Beyond 준비 완료")

'''
    return replace_region(text, start_marker, end_marker, new_region, "Roll20-before-translation flow")


def patch_ui(text: str) -> str:
    if 'log_path = run_folder / "run.log"' not in text:
        text = replace_once(text, '        log_path = run_folder / "worker.log"\n', '        log_path = run_folder / "run.log"\n', "single run log name")

    if 'self.log(f"실행 로그: {log_path}")' not in text:
        text = replace_once(
            text,
            '''        self.log("통합 실행 시작")
        self.log(f"작업 기록: {run_folder}")
''',
            '''        self.log("통합 실행 시작")
        self.log(f"작업 기록: {run_folder}")
        self.log(f"실행 로그: {log_path}")
''',
            "UI run log display",
        )

    old_worker_tail = '''                self._post(self._worker_done, code, error)
            except Exception as exc:
                self._post(self._worker_failure, str(exc))

        threading.Thread(target=worker, daemon=True).start()
'''
    new_worker_tail = '''                self._post(self._worker_done, code, error)
            except Exception as exc:
                self._post(self._worker_failure, str(exc))
            finally:
                # events/settings are transient IPC files. Keep one retained
                # execution log (run.log) in the worker folder.
                for transient in (event_path, snapshot_path):
                    try:
                        Path(transient).unlink(missing_ok=True)
                    except OSError:
                        pass

        threading.Thread(target=worker, daemon=True).start()
'''
    if "for transient in (event_path, snapshot_path):" not in text:
        text = replace_once(text, old_worker_tail, new_worker_tail, "UI transient cleanup")

    text = replace_once(
        text,
        '''                (error or "시트 이동이 중단되었습니다.")
                + "\\n이미 입력된 내용이 있을 수 있습니다. 로그와 결과 기록을 확인하세요.",
''',
        '''                (error or "시트 이동이 중단되었습니다.")
                + "\\n완료된 단계는 유지됩니다. 다시 실행하면 같은 값은 건너뜁니다."
                + "\\n세부 원인은 이번 실행의 run.log를 확인하세요.",
''',
        "UI failure popup",
    )
    return text


def patch_full_run_tests(text: str) -> str:
    if "self.fetch_character" not in text:
        text = replace_once(
            text,
            '''        self.settings = AppSettings(google_project_id="fixture")
        self.events = []
''',
            '''        self.settings = AppSettings(google_project_id="fixture")
        self.events = []
        self.fetch_character = self.stack.enter_context(
            patch("sheet_mover.source.fetch_character")
        )
        self.fetch_character.return_value = {"id": 1, "name": "fixture"}
''',
            "full-run preflight source mock",
        )

    text = replace_once(
        text,
        '''        self.target.return_value = (SimpleNamespace(character_name="fixture"), self.root / "target.json")
''',
        '''        self.target.return_value = (
            SimpleNamespace(character_name="fixture", sheet_type="ogl5e"),
            self.root / "target.json",
        )
''',
        "full-run target sheet type",
    )

    text = replace_once(
        text,
        '''        self.assertFalse(partial["applied"])
        self.target.assert_not_called()
        self.assertIsNone(latest_complete_result(root=self.root))
''',
        '''        self.assertFalse(partial["applied"])
        self.target.assert_called_once()
        self.assertIsNone(latest_complete_result(root=self.root))
''',
        "translation exception preflight expectation",
    )

    text = replace_once(
        text,
        '''        self.assertTrue(Path(self.report()["paths"]["partial_result"]).exists())
        self.target.assert_not_called()
''',
        '''        self.assertTrue(Path(self.report()["paths"]["partial_result"]).exists())
        self.target.assert_called_once()
''',
        "result save failure preflight expectation",
    )

    if "test_roll20_preflight_happens_before_translation" not in text:
        marker = '''    def test_partial_return_continues_with_exact_original_fallback(self):
'''
        addition = '''    def test_roll20_preflight_happens_before_translation(self):
        order = []

        self.fetch_character.side_effect = lambda _url: (
            order.append("source") or {"id": 1, "name": "fixture"}
        )
        self.target.side_effect = lambda **_kwargs: (
            order.append("roll20")
            or (
                SimpleNamespace(character_name="fixture", sheet_type="ogl5e"),
                self.root / "target.json",
            )
        )
        original_prepare = self.prepare.return_value

        def prepared(*_args, **_kwargs):
            order.append("translate")
            return original_prepare

        self.prepare.side_effect = prepared
        self.run_move()
        self.assertLess(order.index("roll20"), order.index("translate"))

    def test_roll20_preflight_failure_stops_before_translation(self):
        self.target.side_effect = RuntimeError("Roll20 target unavailable")
        with self.assertRaises(RuntimeError):
            self.run_move()
        self.prepare.assert_not_called()
        report = self.report()
        self.assertEqual(report["stage_statuses"]["2"], "error")
        self.assertEqual(report["stage_statuses"]["1"], "pending")

'''
        if marker not in text:
            raise RuntimeError("full-run test insert marker not found")
        text = text.replace(marker, addition + marker, 1)
    return text


def backup(path: Path):
    dst = path.with_suffix(path.suffix + ".pre-runtime-v2.2.bak")
    if not dst.exists():
        shutil.copy2(path, dst)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    targets = (SPELLS, PROFS, FULL_RUN, MOVER, UI, TEST_FULL_RUN)
    missing = [str(path) for path in targets if not path.is_file()]
    if missing:
        raise RuntimeError("필수 파일이 없습니다: " + ", ".join(missing))

    patched = {
        SPELLS: patch_spells(SPELLS.read_text(encoding="utf-8")),
        PROFS: patch_proficiencies(PROFS.read_text(encoding="utf-8")),
        FULL_RUN: patch_full_run(FULL_RUN.read_text(encoding="utf-8")),
        MOVER: patch_mover(MOVER.read_text(encoding="utf-8")),
        UI: patch_ui(UI.read_text(encoding="utf-8")),
        TEST_FULL_RUN: patch_full_run_tests(TEST_FULL_RUN.read_text(encoding="utf-8")),
    }

    if args.check:
        print("[시트 이동기] runtime-integrity v2.2 사전 점검 통과")
        print(f"- 기준 커밋: {BASE_COMMIT}")
        print("- Moonbeam grouped spell damage 보정 가능")
        print("- Stage 11 숙련 callback timeout 복구 가능")
        print("- Roll20 준비 확인을 번역보다 먼저 실행 가능")
        print("- 실행 폴더에 run.log 하나만 남기도록 정리 가능")
        print("[시트 이동기] 아직 파일은 수정하지 않았습니다.")
        return 0

    for path in targets:
        backup(path)
    for path, content in patched.items():
        path.write_text(content, encoding="utf-8")

    print("[시트 이동기] runtime-integrity v2.2 적용 완료")
    print("- Moonbeam: 2d10 Radiant + 상위 슬롯당 1d10")
    print("- Stage 11: 16개 단위 저장 + persisted 검증 + no-wait 복구")
    print("- 멀티클래스: 비시작 클래스 Core Traits의 내성 숙련 제외")
    print("- Roll20 대상/Legacy OGL5e 확인 뒤에만 번역 시작")
    print("- workers/<run_id>/에는 종료 후 run.log만 유지")
    print("python -m unittest discover -s tests -v")
    print("python main.py")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[시트 이동기] runtime-integrity v2.2 패치 실패: {exc}", file=sys.stderr)
        raise
