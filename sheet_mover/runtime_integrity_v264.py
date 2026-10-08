# -*- coding: utf-8 -*-
"""v2.6.4 cache refresh for newly recovered subclass spells.

The existing v2.6.2 cache hook recalculates ``original`` from the exact same raw
D&D Beyond payload while preserving translated text.  v2.6.4 then copies only
fresh mechanical spell metadata into matching translated rows and appends only
new source-backed rows.  No translation API call is made.
"""
from __future__ import annotations

from copy import deepcopy


RUNTIME_INTEGRITY_V264 = "2026-10-08-runtime-integrity-v2.6.4-subclass-spells"


def _dict(value):
    return value if isinstance(value, dict) else {}


def _list(value):
    return value if isinstance(value, list) else []


def _text(value):
    return str(value or "").strip()


# Text fields are deliberately excluded so an exact-raw cache hit keeps the
# user's already completed translation.  A newly appended spell has no prior
# translated counterpart, so its DDB English source text is preserved as-is.
_SPELL_MECHANICAL_FIELDS = (
    "level",
    "prepared",
    "always_prepared",
    "uses_spell_slot",
    "casting_time",
    "range",
    "duration",
    "components",
    "school",
    "ritual",
    "concentration",
    "save_dc_ability_id",
    "attack_type",
    "damage_effect",
    "counts_as_known_spell",
    "spellcasting_ability_id",
    "cast_only_as_ritual",
    "ritual_casting_type",
    "restriction",
    "display_as_attack",
    "source_group",
    "character_class_id",
    "component_id",
    "component_type_id",
    "definition_is_legacy",
    "grant_type",
    "grant_feature_id",
    "grant_feature_name",
)


def _spell_identity(item):
    item = _dict(item)
    source_id = _text(item.get("source_id"))
    definition_id = _text(item.get("definition_id"))
    if not source_id or not definition_id:
        return None
    return source_id, definition_id


def refresh_cached_spell_metadata(payload):
    current = deepcopy(_dict(payload))
    original = _dict(current.get("original"))
    translated = deepcopy(_dict(current.get("translated")))

    original_spells = [
        row for row in _list(original.get("spells"))
        if isinstance(row, dict)
    ]
    translated_spells = [
        row for row in _list(translated.get("spells"))
        if isinstance(row, dict)
    ]

    by_identity = {}
    for row in translated_spells:
        identity = _spell_identity(row)
        if identity is not None:
            by_identity.setdefault(identity, []).append(row)

    added = []
    refreshed = []

    for original_row in original_spells:
        identity = _spell_identity(original_row)
        matches = by_identity.get(identity, []) if identity is not None else []

        if matches:
            for translated_row in matches:
                changed = False
                for field in _SPELL_MECHANICAL_FIELDS:
                    if field not in original_row:
                        continue
                    value = deepcopy(original_row.get(field))
                    if translated_row.get(field) != value:
                        translated_row[field] = value
                        changed = True
                if changed:
                    refreshed.append(
                        {
                            "source_id": _text(original_row.get("source_id")),
                            "definition_id": _text(original_row.get("definition_id")),
                        }
                    )
            continue

        new_row = deepcopy(original_row)
        translated_spells.append(new_row)
        if identity is not None:
            by_identity.setdefault(identity, []).append(new_row)
        added.append(
            {
                "source_id": _text(original_row.get("source_id")),
                "definition_id": _text(original_row.get("definition_id")),
                "name": _text(original_row.get("original_name") or original_row.get("name")),
                "grant_type": _text(original_row.get("grant_type")),
            }
        )

    translated["spells"] = translated_spells
    current["translated"] = translated

    if original:
        from .roll20_payload import build_roll20_payload
        current["roll20_payload"] = build_roll20_payload(translated, original)

    report = {
        "version": RUNTIME_INTEGRITY_V264,
        "translation_reused": True,
        "added_count": len(added),
        "refreshed_count": len(refreshed),
        "added": added,
        "refreshed": refreshed,
    }
    current["subclass_spell_cache_refresh"] = report
    return current, report


def install_reusable_result_refresh(original_find_reusable_result):
    if getattr(original_find_reusable_result, "_sheetmover_subclass_spells_v264", False):
        return original_find_reusable_result

    def find_reusable_result_v264(source_id, raw_source):
        payload, path = original_find_reusable_result(source_id, raw_source)
        if payload is None or path is None:
            return payload, path

        refreshed, report = refresh_cached_spell_metadata(payload)

        from .result_store import write_json_atomic
        write_json_atomic(path, refreshed)

        if report.get("added_count") or report.get("refreshed_count"):
            print(
                "[시트 이동기] 기존 번역 재사용 · 서브클래스 주문 메타데이터 갱신: "
                f"신규 {report.get('added_count', 0)}개 / "
                f"기존 {report.get('refreshed_count', 0)}개"
            )

        return refreshed, path

    find_reusable_result_v264._sheetmover_subclass_spells_v264 = True
    return find_reusable_result_v264
