# -*- coding: utf-8 -*-
"""Resolve DDB selected-option component IDs without guessing."""
from __future__ import annotations

from copy import deepcopy

RUNTIME_INTEGRITY_V263 = (
    "2026-10-08-runtime-integrity-v2.6.3-selected-option-components"
)


def _dict(value):
    return value if isinstance(value, dict) else {}


def _list(value):
    return value if isinstance(value, list) else []


def _text(value):
    return str(value or "").strip()


def selected_option_component_map(raw_source):
    raw = _dict(raw_source)
    options = _dict(raw.get("options"))
    result = {}

    for group_name, entries in options.items():
        if not isinstance(entries, list):
            continue

        group = _text(group_name).casefold()
        candidates = {}

        for entry in entries:
            entry = _dict(entry)
            definition = _dict(entry.get("definition"))
            option_id = _text(definition.get("id"))
            component_id = _text(entry.get("componentId"))

            if not group or not option_id or not component_id:
                continue

            candidates.setdefault(option_id, set()).add(component_id)

        resolved = {
            option_id: next(iter(component_ids))
            for option_id, component_ids in candidates.items()
            if len(component_ids) == 1
        }
        if resolved:
            result[group] = resolved

    return result


def normalize_selected_option_components(raw_source):
    raw = deepcopy(_dict(raw_source))
    mapping = selected_option_component_map(raw)
    changed = []

    for collection_name in ("modifiers", "actions"):
        collection = _dict(raw.get(collection_name))

        for group_name, entries in list(collection.items()):
            if not isinstance(entries, list):
                continue

            group = _text(group_name).casefold()
            group_map = _dict(mapping.get(group))
            if not group_map:
                continue

            for entry in entries:
                if not isinstance(entry, dict):
                    continue

                original_component = _text(entry.get("componentId"))
                parent_component = _text(group_map.get(original_component))

                if (
                    not original_component
                    or not parent_component
                    or original_component == parent_component
                ):
                    continue

                entry["componentId"] = parent_component
                changed.append(
                    {
                        "collection": collection_name,
                        "group": group,
                        "entry_id": _text(entry.get("id")),
                        "entry_name": _text(entry.get("name")),
                        "original_component_id": original_component,
                        "active_component_id": parent_component,
                        "subtype": _text(entry.get("subType")),
                    }
                )

    return raw, {
        "version": RUNTIME_INTEGRITY_V263,
        "selected_option_component_map": deepcopy(mapping),
        "remapped": changed,
        "remapped_count": len(changed),
    }


def _copy_hit_dice_used(raw_source, sheet):
    by_source_id = {}

    for entry in _list(_dict(raw_source).get("classes")):
        entry = _dict(entry)
        source_id = _text(entry.get("id"))
        used = entry.get("hitDiceUsed")
        if source_id and type(used) is int and used >= 0:
            by_source_id[source_id] = used

    for cls in getattr(sheet, "classes", []) or []:
        if not isinstance(cls, dict):
            continue
        source_id = _text(cls.get("source_id"))
        if source_id in by_source_id:
            cls["hit_dice_used"] = by_source_id[source_id]


def install_source_integrity(original_normalize_character):
    if getattr(
        original_normalize_character,
        "_sheetmover_selected_option_v263",
        False,
    ):
        return original_normalize_character

    def normalize_character_v263(data):
        normalized_raw, report = normalize_selected_option_components(data)
        sheet = original_normalize_character(normalized_raw)
        _copy_hit_dice_used(normalized_raw, sheet)

        calculation_inputs = getattr(sheet, "calculation_inputs", None)
        if isinstance(calculation_inputs, dict):
            calculation_inputs[
                "selected_option_component_resolution"
            ] = deepcopy(report)

        return sheet

    normalize_character_v263.__name__ = getattr(
        original_normalize_character,
        "__name__",
        "normalize_character",
    )
    normalize_character_v263._sheetmover_selected_option_v263 = True
    return normalize_character_v263
