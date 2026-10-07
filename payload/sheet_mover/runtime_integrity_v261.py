# -*- coding: utf-8 -*-
"""v2.6.1: resolve DDB ASI-vs-feat choices before Stage 2 ability math.

D&D Beyond can leave the generic +1/+1 "choose-an-ability-score" modifiers
attached to an Ability Score Improvement feature even when the character
selected a feat instead.  The selected feat is represented in raw choices.
If those stale generic modifiers are allowed into Stage 2, the conservative
calculator intentionally nulls every ability score.

This module removes only those stale generic modifiers for components that
have an explicit selected feat child choice.  Actual ASI choices and item
set-score effects (for example Belt of Fire Giant Strength) are preserved.
"""
from __future__ import annotations

from copy import deepcopy


RUNTIME_INTEGRITY_V261 = (
    "2026-10-07-runtime-integrity-v2.6.1-asi-feat-choice"
)


def _dict(value):
    return value if isinstance(value, dict) else {}


def _list(value):
    return value if isinstance(value, list) else []


def _text(value):
    return str(value or "").strip()


def _feat_ids(raw_source):
    result = set()
    for entry in _list(_dict(raw_source).get("feats")):
        entry = _dict(entry)
        definition = _dict(entry.get("definition") or entry)
        value = definition.get("id")
        if value not in (None, ""):
            result.add(_text(value))
    return result


def _choice_rows(raw_source):
    rows = []
    for group_name, entries in _dict(
        _dict(raw_source).get("choices")
    ).items():
        for entry in _list(entries):
            if not isinstance(entry, dict):
                continue
            row = deepcopy(entry)
            row["_choice_group"] = _text(group_name)
            rows.append(row)
    return rows


def selected_feat_replacement_components(raw_source):
    """Return feature component IDs whose ASI branch was replaced by a feat."""
    feat_ids = _feat_ids(raw_source)
    if not feat_ids:
        return set(), []

    choices = _choice_rows(raw_source)
    by_id = {
        _text(row.get("id")): row
        for row in choices
        if _text(row.get("id"))
    }

    components = set()
    selections = []

    for row in choices:
        option_value = _text(row.get("optionValue"))
        if option_value not in feat_ids:
            continue

        label = _text(row.get("label")).casefold()
        choice_type = row.get("type")
        if "choose a feat" not in label and choice_type != 6:
            continue

        parent = by_id.get(_text(row.get("parentChoiceId")))
        component = _text(
            _dict(parent).get("componentId")
            or row.get("componentId")
        )
        if not component:
            continue

        components.add(component)
        selections.append(
            {
                "component_id": component,
                "feat_id": option_value,
                "choice_id": _text(row.get("id")),
                "parent_choice_id": _text(row.get("parentChoiceId")),
            }
        )

    return components, selections


def sanitize_raw_ability_choices(raw_source):
    """Drop stale generic ASI modifiers only when a feat was selected instead."""
    raw = deepcopy(_dict(raw_source))
    components, selections = selected_feat_replacement_components(raw)

    removed = []
    modifiers = _dict(raw.get("modifiers"))

    if components:
        for group_name, entries in list(modifiers.items()):
            kept = []
            for modifier in _list(entries):
                if not isinstance(modifier, dict):
                    kept.append(modifier)
                    continue

                component = _text(modifier.get("componentId"))
                subtype = _text(modifier.get("subType")).casefold()
                modifier_type = _text(
                    modifier.get("type")
                ).casefold()
                stat_id = modifier.get("statId")
                entity_id = modifier.get("entityId")

                stale_generic = (
                    component in components
                    and modifier_type in {"bonus", "set"}
                    and subtype in {
                        "ability-score",
                        "choose-an-ability-score",
                    }
                    and stat_id in (None, "")
                    and entity_id in (None, "")
                )

                if stale_generic:
                    removed.append(
                        {
                            "group": _text(group_name),
                            "id": _text(modifier.get("id")),
                            "component_id": component,
                            "type": modifier_type,
                            "subtype": subtype,
                            "value": (
                                modifier.get("value")
                                if modifier.get("value") is not None
                                else modifier.get("fixedValue")
                            ),
                        }
                    )
                    continue

                kept.append(modifier)

            modifiers[group_name] = kept

    raw["modifiers"] = modifiers
    report = {
        "version": RUNTIME_INTEGRITY_V261,
        "feat_replacement_components": sorted(components),
        "selected_feats": selections,
        "removed_stale_generic_asi_modifiers": removed,
        "removed_count": len(removed),
    }
    return raw, report


def install_source_integrity(original_normalize_character):
    def normalize_character_v261(data):
        sanitized, report = sanitize_raw_ability_choices(data)
        sheet = original_normalize_character(sanitized)

        calculation_inputs = getattr(
            sheet,
            "calculation_inputs",
            None,
        )
        if isinstance(calculation_inputs, dict):
            calculation_inputs[
                "ability_choice_resolution"
            ] = deepcopy(report)

        return sheet

    normalize_character_v261.__name__ = (
        getattr(
            original_normalize_character,
            "__name__",
            "normalize_character",
        )
    )
    return normalize_character_v261


_DERIVED_TRANSLATED_KEYS = (
    "total_level",
    "ability_scores",
    "proficiency_bonus",
    "armor_class",
    "initiative",
    "hp",
    "max_hp",
    "temp_hp",
)


def _refresh_translated_numbers(translated, fresh_original):
    out = deepcopy(_dict(translated))
    fresh = _dict(fresh_original)

    for key in _DERIVED_TRANSLATED_KEYS:
        if key in fresh:
            out[key] = deepcopy(fresh.get(key))

    fresh_spellcasting = _dict(fresh.get("spellcasting"))
    if fresh_spellcasting:
        spellcasting = deepcopy(_dict(out.get("spellcasting")))
        for key in (
            "save_dc",
            "attack_bonus",
            "class_calculations",
            "class_abilities",
            "class_rules_source",
            "spell_slots_source",
            "pact_magic_source",
        ):
            if key in fresh_spellcasting:
                spellcasting[key] = deepcopy(
                    fresh_spellcasting.get(key)
                )
        out["spellcasting"] = spellcasting

    return out


def refresh_cached_result_payload(payload):
    """Refresh calculated fields without re-running translation."""
    current = deepcopy(_dict(payload))
    raw_source = _dict(current.get("raw_source"))
    if not raw_source:
        return current, {
            "version": RUNTIME_INTEGRITY_V261,
            "refreshed": False,
            "reason": "raw_source_missing",
        }

    sanitized, choice_report = sanitize_raw_ability_choices(
        raw_source
    )

    # Import lazily to avoid source <-> runtime import cycles at module load.
    from .source import normalize_character
    from .roll20_payload import build_roll20_payload

    fresh_original = normalize_character(sanitized).to_dict()
    old_scores = deepcopy(
        _dict(current.get("original")).get("ability_scores")
    )
    new_scores = deepcopy(
        _dict(fresh_original).get("ability_scores")
    )

    current["original"] = fresh_original

    translated = _refresh_translated_numbers(
        current.get("translated"),
        fresh_original,
    )
    current["translated"] = translated
    current["roll20_payload"] = build_roll20_payload(
        translated,
        fresh_original,
    )

    refresh_report = {
        "version": RUNTIME_INTEGRITY_V261,
        "refreshed": True,
        "old_ability_scores": old_scores,
        "new_ability_scores": new_scores,
        "ability_choice_resolution": choice_report,
        "translation_reused": True,
    }
    current["derived_refresh"] = refresh_report
    return current, refresh_report


def install_reusable_result_refresh(original_find_reusable_result):
    def find_reusable_result_v261(source_id, raw_source):
        payload, path = original_find_reusable_result(
            source_id,
            raw_source,
        )
        if payload is None or path is None:
            return payload, path

        refreshed, report = refresh_cached_result_payload(
            payload
        )

        from .result_store import write_json_atomic
        write_json_atomic(path, refreshed)

        choice_report = _dict(
            report.get("ability_choice_resolution")
        )
        removed = int(
            choice_report.get("removed_count") or 0
        )
        scores = _dict(report.get("new_ability_scores"))
        if removed:
            print(
                "[시트 이동기] 기존 번역 재사용 · "
                f"ASI/특기 선택 수치 재계산: "
                f"stale modifier {removed}개 제외 · "
                f"STR {scores.get('strength')}, "
                f"DEX {scores.get('dexterity')}, "
                f"CON {scores.get('constitution')}, "
                f"INT {scores.get('intelligence')}, "
                f"WIS {scores.get('wisdom')}, "
                f"CHA {scores.get('charisma')}"
            )

        return refreshed, path

    return find_reusable_result_v261
