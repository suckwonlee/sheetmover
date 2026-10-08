# -*- coding: utf-8 -*-
"""v2.6.2: safely resolve DDB ASI-vs-feat choices before Stage 2 ability math.

D&D Beyond can leave generic +1/+1 ``choose-an-ability-score`` modifiers on
an Ability Score Improvement feature even when the character selected a feat.

v2.6.2 removes those stale generic modifiers only when all of the following
source facts agree:

1. the top-level choice explicitly selected the ``Feat`` branch,
2. a child choice selected an actual feat that exists in ``raw_source.feats``,
3. the modifier belongs to the same choice group,
4. the modifier has the same componentId,
5. when the selected choice has componentTypeId, the modifier must match it.

If the choice metadata is missing or ambiguous, the modifier is left alone so
the existing Stage 2 calculator can fail closed instead of guessing.
"""
from __future__ import annotations

from copy import deepcopy


RUNTIME_INTEGRITY_V262 = (
    "2026-10-08-runtime-integrity-v2.6.2.1-choiceDefinitions-nested"
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
    """Return only actual character choice rows."""
    rows = []
    choices = _dict(_dict(raw_source).get("choices"))

    for group_name, entries in choices.items():
        if group_name in {"choiceDefinitions", "definitionKeyNameMap"}:
            continue

        group = _text(group_name).casefold()
        for entry in _list(entries):
            if not isinstance(entry, dict):
                continue
            row = deepcopy(entry)
            row["_choice_group"] = group
            rows.append(row)

    return rows


def _choice_definition_labels(raw_source):
    """Map (choice-definition-id, option-id) -> source option label.

    Current DDB payloads keep choiceDefinitions inside raw_source.choices.
    The old top-level shape remains as a compatibility fallback.
    """
    raw = _dict(raw_source)
    choices = _dict(raw.get("choices"))

    definitions = _list(choices.get("choiceDefinitions"))
    if not definitions:
        definitions = _list(raw.get("choiceDefinitions"))

    result = {}
    for definition in definitions:
        definition = _dict(definition)
        definition_id = _text(definition.get("id"))
        if not definition_id:
            continue

        for option in _list(definition.get("options")):
            option = _dict(option)
            option_id = _text(option.get("id"))
            label = _text(option.get("label"))
            if option_id and label:
                result[(definition_id, option_id)] = label

    return result


def _selected_option_label(row, labels):
    component_type = _text(row.get("componentTypeId"))
    choice_type = _text(row.get("type"))
    option_value = _text(row.get("optionValue"))
    if not component_type or not choice_type or not option_value:
        return ""
    definition_id = f"{component_type}-{choice_type}"
    return _text(labels.get((definition_id, option_value)))


def selected_feat_replacement_components(raw_source):
    """Return exact source component keys whose ASI branch became a feat.

    Component identity is:
        (choice_group, component_type_id, component_id)

    No DDB numeric option/component IDs are hardcoded.
    """
    raw = _dict(raw_source)
    feat_ids = _feat_ids(raw)
    if not feat_ids:
        return set(), []

    choices = _choice_rows(raw)
    labels = _choice_definition_labels(raw)

    by_parent = {}
    for row in choices:
        parent_id = _text(row.get("parentChoiceId"))
        group = _text(row.get("_choice_group")).casefold()
        if parent_id:
            by_parent.setdefault((group, parent_id), []).append(row)

    components = set()
    selections = []

    for parent in choices:
        if _text(parent.get("parentChoiceId")):
            continue

        group = _text(parent.get("_choice_group")).casefold()
        parent_id = _text(parent.get("id"))
        component_id = _text(parent.get("componentId"))
        component_type_id = _text(parent.get("componentTypeId"))

        if not group or not parent_id or not component_id:
            continue

        # Resolve the parent option through DDB's own choiceDefinitions.
        # Missing definitions are intentionally not guessed.
        branch_label = _selected_option_label(parent, labels)
        if branch_label.casefold() != "feat":
            continue

        selected_feat = None
        for child in by_parent.get((group, parent_id), []):
            option_value = _text(child.get("optionValue"))
            if option_value in feat_ids:
                selected_feat = child
                break

        # A parent saying "Feat" without a concrete selected feat is incomplete.
        # Keep fail-closed behavior in that case.
        if selected_feat is None:
            continue

        key = (group, component_type_id, component_id)
        components.add(key)
        selections.append(
            {
                "group": group,
                "component_type_id": component_type_id,
                "component_id": component_id,
                "feat_id": _text(selected_feat.get("optionValue")),
                "choice_id": _text(selected_feat.get("id")),
                "parent_choice_id": parent_id,
                "branch_label": branch_label,
            }
        )

    return components, selections


def _modifier_matches_selected_component(*, group_name, modifier, selected_components):
    group = _text(group_name).casefold()
    component_id = _text(modifier.get("componentId"))
    component_type_id = _text(modifier.get("componentTypeId"))

    if not group or not component_id:
        return False

    for selected_group, selected_component_type, selected_component_id in selected_components:
        if selected_group != group:
            continue
        if selected_component_id != component_id:
            continue

        # If DDB gave us a component type for the selected choice, require an
        # exact type match. Missing/mismatched modifier metadata is not enough
        # evidence to delete anything.
        if selected_component_type and component_type_id != selected_component_type:
            continue

        return True

    return False


def sanitize_raw_ability_choices(raw_source):
    """Remove only proven stale generic ASI modifiers."""
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

                subtype = _text(modifier.get("subType")).casefold()
                modifier_type = _text(modifier.get("type")).casefold()
                stat_id = modifier.get("statId")
                entity_id = modifier.get("entityId")

                stale_generic = (
                    _modifier_matches_selected_component(
                        group_name=group_name,
                        modifier=modifier,
                        selected_components=components,
                    )
                    and modifier_type in {"bonus", "set"}
                    and subtype in {"ability-score", "choose-an-ability-score"}
                    and stat_id in (None, "")
                    and entity_id in (None, "")
                )

                if stale_generic:
                    removed.append(
                        {
                            "group": _text(group_name).casefold(),
                            "id": _text(modifier.get("id")),
                            "component_id": _text(modifier.get("componentId")),
                            "component_type_id": _text(modifier.get("componentTypeId")),
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

    component_rows = [
        {
            "group": group,
            "component_type_id": component_type_id,
            "component_id": component_id,
        }
        for group, component_type_id, component_id in sorted(components)
    ]

    report = {
        "version": RUNTIME_INTEGRITY_V262,
        "feat_replacement_components": component_rows,
        "selected_feats": selections,
        "removed_stale_generic_asi_modifiers": removed,
        "removed_count": len(removed),
    }
    return raw, report


def install_source_integrity(original_normalize_character):
    """Wrap normalize_character without changing Stage 2 fail-closed rules."""
    if getattr(original_normalize_character, "_sheetmover_ability_choice_v262", False):
        return original_normalize_character

    def normalize_character_v262(data):
        sanitized, report = sanitize_raw_ability_choices(data)
        sheet = original_normalize_character(sanitized)

        calculation_inputs = getattr(sheet, "calculation_inputs", None)
        if isinstance(calculation_inputs, dict):
            calculation_inputs["ability_choice_resolution"] = deepcopy(report)

        return sheet

    normalize_character_v262.__name__ = getattr(
        original_normalize_character,
        "__name__",
        "normalize_character",
    )
    normalize_character_v262._sheetmover_ability_choice_v262 = True
    return normalize_character_v262


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
    """Replace only calculated numeric fields; keep translated text untouched."""
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
                spellcasting[key] = deepcopy(fresh_spellcasting.get(key))
        out["spellcasting"] = spellcasting

    return out


def refresh_cached_result_payload(payload):
    """Recalculate derived values while reusing the existing translation."""
    current = deepcopy(_dict(payload))
    raw_source = _dict(current.get("raw_source"))
    if not raw_source:
        return current, {
            "version": RUNTIME_INTEGRITY_V262,
            "refreshed": False,
            "reason": "raw_source_missing",
        }

    # Import lazily to avoid source <-> runtime import cycles. normalize_character
    # is already wrapped by install_source_integrity, so pass original raw_source.
    from .source import normalize_character
    from .roll20_payload import build_roll20_payload

    fresh_original = normalize_character(raw_source).to_dict()

    choice_report = deepcopy(
        _dict(
            _dict(fresh_original.get("calculation_inputs")).get(
                "ability_choice_resolution"
            )
        )
    )
    if not choice_report:
        _unused, choice_report = sanitize_raw_ability_choices(raw_source)

    old_scores = deepcopy(_dict(current.get("original")).get("ability_scores"))
    new_scores = deepcopy(_dict(fresh_original).get("ability_scores"))

    current["original"] = fresh_original

    translated = _refresh_translated_numbers(current.get("translated"), fresh_original)
    current["translated"] = translated
    current["roll20_payload"] = build_roll20_payload(translated, fresh_original)

    refresh_report = {
        "version": RUNTIME_INTEGRITY_V262,
        "refreshed": True,
        "old_ability_scores": old_scores,
        "new_ability_scores": new_scores,
        "ability_choice_resolution": choice_report,
        "translation_reused": True,
    }
    current["derived_refresh"] = refresh_report
    return current, refresh_report


def install_reusable_result_refresh(original_find_reusable_result):
    """Refresh an exact-raw cache hit without calling translation again."""
    if getattr(original_find_reusable_result, "_sheetmover_ability_choice_v262", False):
        return original_find_reusable_result

    def find_reusable_result_v262(source_id, raw_source):
        payload, path = original_find_reusable_result(source_id, raw_source)
        if payload is None or path is None:
            return payload, path

        refreshed, report = refresh_cached_result_payload(payload)

        from .result_store import write_json_atomic
        write_json_atomic(path, refreshed)

        choice_report = _dict(report.get("ability_choice_resolution"))
        removed = int(choice_report.get("removed_count") or 0)
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

    find_reusable_result_v262._sheetmover_ability_choice_v262 = True
    return find_reusable_result_v262
