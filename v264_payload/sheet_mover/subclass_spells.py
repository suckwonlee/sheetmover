# -*- coding: utf-8 -*-
"""Generic D&D Beyond subclass-granted spell recovery.

This module supplements *missing* always-prepared subclass spells when D&D
Beyond exposes only a feature table instead of structured spell objects.

Safety rules:
- structured character spell objects are always authoritative;
- expanded-list features are never auto-added;
- table rows above the current class level are ignored;
- spell mechanics come only from D&D Beyond's public spell catalog;
- legacy/revised edition selection is inferred from source metadata, never
  from character/subclass IDs or hard-coded spell lists;
- ambiguous/missing catalog matches fail closed and are reported.
"""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
from html import unescape
from html.parser import HTMLParser
import json
import re
from typing import Any
from urllib.request import Request, urlopen


SUBCLASS_SPELL_VERSION = "2026-10-08-subclass-spells-v2.6.4"

# D&D Beyond's public always-known spell endpoint is partitioned by the eight
# core caster-list buckets.  We sweep the partitions only as a catalog; no
# class behavior is keyed from these numeric values.
_CATALOG_PARTITIONS = tuple(range(1, 9))
_CATALOG_URL = (
    "https://character-service.dndbeyond.com/character/v5/"
    "game-data/always-known-spells"
    "?classId={partition}&classLevel=20&sharingSetting=2"
)


def _dict(value):
    return value if isinstance(value, dict) else {}


def _list(value):
    return value if isinstance(value, list) else []


def _text(value):
    return str(value or "").strip()


def _as_int(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        value = value.strip()
        if re.fullmatch(r"[+-]?\d+", value):
            try:
                return int(value)
            except ValueError:
                return None
    return None


def _name_key(value):
    text = unescape(_text(value)).replace("’", "'").replace("‘", "'")
    text = re.sub(r"\s+", " ", text)
    return text.casefold().strip()


def _plain_text(value):
    text = unescape(_text(value))
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("’", "'").replace("‘", "'")
    return re.sub(r"\s+", " ", text).strip()


class _TableHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self._table_depth = 0
        self._rows = None
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        tag = tag.casefold()
        if tag == "table":
            self._table_depth += 1
            if self._table_depth == 1:
                self._rows = []
        elif self._table_depth == 1 and tag == "tr":
            self._row = []
        elif self._table_depth == 1 and tag in {"td", "th"} and self._row is not None:
            self._cell = []
        elif self._table_depth == 1 and tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag):
        tag = tag.casefold()
        if self._table_depth == 1 and tag in {"td", "th"} and self._cell is not None:
            value = re.sub(r"\s+", " ", "".join(self._cell)).strip()
            self._row.append(value)
            self._cell = None
        elif self._table_depth == 1 and tag == "tr" and self._row is not None:
            if any(_text(cell) for cell in self._row):
                self._rows.append(self._row)
            self._row = None
        elif tag == "table" and self._table_depth:
            if self._table_depth == 1:
                self.tables.append(self._rows or [])
                self._rows = None
            self._table_depth -= 1

    def handle_data(self, data):
        if self._table_depth == 1 and self._cell is not None:
            self._cell.append(data)


def _spell_table_rows(html_text):
    parser = _TableHTMLParser()
    try:
        parser.feed(_text(html_text))
        parser.close()
    except Exception:
        return []

    out = []
    for table in parser.tables:
        header_index = None
        for index, row in enumerate(table):
            if len(row) < 2:
                continue
            first = _plain_text(row[0]).casefold()
            second = _plain_text(row[1]).casefold()
            if "level" in first and "spell" in second:
                header_index = index
                break
        if header_index is None:
            continue

        for row in table[header_index + 1 :]:
            if len(row) < 2:
                continue
            level_text = _plain_text(row[0])
            spell_text = _plain_text(row[1])
            match = re.search(r"(?<!\d)(\d{1,2})(?:st|nd|rd|th)?(?!\d)", level_text, re.I)
            if not match:
                continue
            required_level = int(match.group(1))
            names = [
                re.sub(r"\s+", " ", part).strip()
                for part in re.split(r"[,;]", spell_text)
                if re.search(r"[A-Za-z]", part)
            ]
            if names:
                out.append((required_level, names))
    return out


def _feature_definition(entry):
    entry = _dict(entry)
    return _dict(entry.get("definition") or entry)


def _feature_text(definition):
    definition = _dict(definition)
    return _plain_text(
        " ".join(
            value
            for value in (
                _text(definition.get("name")),
                _text(definition.get("description")),
                _text(definition.get("snippet")),
            )
            if value
        )
    )


def _direct_grant_type(text):
    value = _name_key(text)
    if not value:
        return "unknown"

    if "expanded spell list" in value or "choose from an expanded list" in value:
        return "expanded_list"

    if (
        re.search(r"\b(?:add|added)\b.{0,80}\bspell list\b", value)
        and "always" not in value
        and "prepared" not in value
    ):
        return "expanded_list"

    always_prepared = (
        "always prepared" in value
        or bool(re.search(r"\balways\b.{0,90}\bprepared\b", value))
        or bool(re.search(r"\bthereafter always\b.{0,90}\bprepared\b", value))
        or (
            "doesn't count against" in value
            and "prepare" in value
        )
        or (
            "does not count against" in value
            and "prepare" in value
        )
    )
    if always_prepared:
        return "always_prepared"

    return "unknown"


def _feature_grant_type(definition, active_definitions):
    text = _feature_text(definition)
    direct = _direct_grant_type(text)
    if direct != "unknown":
        return direct

    # Some legacy subclass tables explicitly defer the spell semantics to a
    # base-class feature, e.g. "See the Divine Domain class feature for how
    # domain spells work." Follow only that explicit source reference.
    reference = re.search(
        r"see the\s+(.+?)\s+class feature\s+for how\s+.+?\s+work",
        text,
        re.I,
    )
    if not reference:
        return "unknown"

    wanted = _name_key(reference.group(1))
    for other in active_definitions:
        if _name_key(_dict(other).get("name")) != wanted:
            continue
        inherited = _direct_grant_type(_feature_text(other))
        if inherited != "unknown":
            return inherited
    return "unknown"


def _active_feature_definitions(character_class):
    character_class = _dict(character_class)
    class_level = _as_int(character_class.get("level"))
    out = []
    for entry in _list(character_class.get("classFeatures")):
        definition = _feature_definition(entry)
        if not definition:
            continue
        if definition.get("hideInSheet") is True:
            continue
        required = _as_int(definition.get("requiredLevel"))
        if class_level is not None and required is not None and required > class_level:
            continue
        out.append(definition)
    return out


def _extract_definition(row):
    row = _dict(row)
    definition = _dict(row.get("definition"))
    return definition or row


@lru_cache(maxsize=1)
def _cached_catalog():
    definitions = {}
    for partition in _CATALOG_PARTITIONS:
        req = Request(
            _CATALOG_URL.format(partition=partition),
            headers={"User-Agent": "SheetMover/2.6.4"},
        )
        with urlopen(req, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        for row in _list(_dict(payload).get("data")):
            definition = _extract_definition(row)
            definition_id = _text(definition.get("id"))
            name = _text(definition.get("name"))
            if not definition_id or not name:
                continue
            definitions[definition_id] = deepcopy(definition)
    return tuple(definitions.values())


def _catalog_indexes(catalog):
    by_name = {}
    source_editions = {}
    unique = {}

    for raw in catalog:
        definition = _extract_definition(raw)
        definition_id = _text(definition.get("id"))
        name = _text(definition.get("name"))
        if not definition_id or not name:
            continue
        unique[definition_id] = definition

    for definition in unique.values():
        by_name.setdefault(_name_key(definition.get("name")), []).append(definition)
        legacy = definition.get("isLegacy")
        if isinstance(legacy, bool):
            for source in _list(definition.get("sources")):
                source_id = _text(_dict(source).get("sourceId"))
                if source_id:
                    source_editions.setdefault(source_id, set()).add(legacy)

    return by_name, source_editions


def _class_source_ids(character_class):
    definition = _dict(_dict(character_class).get("definition"))
    result = set()
    for source in _list(definition.get("sources")):
        source_id = _text(_dict(source).get("sourceId"))
        if source_id:
            result.add(source_id)
    source_id = _text(definition.get("sourceId"))
    if source_id:
        result.add(source_id)
    return result


def _class_legacy_value(character_class, source_editions):
    votes = set()
    for source_id in _class_source_ids(character_class):
        values = source_editions.get(source_id, set())
        if len(values) == 1:
            votes.update(values)
    if len(votes) == 1:
        return next(iter(votes))
    return None


def _definition_source_ids(definition):
    return {
        _text(_dict(source).get("sourceId"))
        for source in _list(_dict(definition).get("sources"))
        if _text(_dict(source).get("sourceId"))
    }


def _resolve_catalog_definition(name, *, by_name, legacy_value, class_source_ids):
    candidates = list(by_name.get(_name_key(name), []))
    if not candidates:
        return None, "not_found"

    if isinstance(legacy_value, bool):
        candidates = [
            row for row in candidates
            if row.get("isLegacy") is legacy_value
        ]
        if not candidates:
            return None, "edition_not_found"

    unique = {_text(row.get("id")): row for row in candidates if _text(row.get("id"))}
    candidates = list(unique.values())
    if len(candidates) == 1:
        return candidates[0], "exact"

    if class_source_ids:
        intersecting = [
            row for row in candidates
            if _definition_source_ids(row) & class_source_ids
        ]
        unique_intersecting = {
            _text(row.get("id")): row
            for row in intersecting
            if _text(row.get("id"))
        }
        if len(unique_intersecting) == 1:
            return next(iter(unique_intersecting.values())), "source_match"

    return None, "ambiguous"


def _class_spellcasting_ability(character_class):
    character_class = _dict(character_class)
    subclass = _dict(character_class.get("subclassDefinition"))
    definition = _dict(character_class.get("definition"))
    value = subclass.get("spellCastingAbilityId")
    if value is None:
        value = definition.get("spellCastingAbilityId")
    return value


def _synthetic_spell(definition, character_class, feature):
    definition = _dict(definition)
    character_class = _dict(character_class)
    feature = _dict(feature)
    feature_id = _text(feature.get("id"))
    definition_id = _text(definition.get("id"))
    class_instance_id = _text(character_class.get("id"))
    source_id = (
        f"subclass-grant:{class_instance_id or 'class'}:"
        f"{feature_id or 'feature'}:{definition_id}"
    )
    level = definition.get("level")

    return {
        "source_id": source_id,
        "definition_id": definition_id,
        "kind": "spell",
        "name": _text(definition.get("name")),
        "original_name": _text(definition.get("name")),
        "description": definition.get("description") or definition.get("snippet") or "",
        "level": level,
        "prepared": True,
        "always_prepared": True,
        "uses_spell_slot": bool(isinstance(level, int) and level > 0),
        "casting_time": deepcopy(definition.get("activation")),
        "range": deepcopy(definition.get("range")),
        "duration": deepcopy(definition.get("duration")),
        "components": deepcopy(definition.get("components")),
        "components_description": definition.get("componentsDescription") or "",
        "school": definition.get("school"),
        "ritual": definition.get("ritual"),
        "concentration": definition.get("concentration"),
        "save_dc_ability_id": definition.get("saveDcAbilityId"),
        "attack_type": definition.get("attackType"),
        "damage_effect": deepcopy(definition.get("damageEffect")),
        "counts_as_known_spell": False,
        "spellcasting_ability_id": _class_spellcasting_ability(character_class),
        "cast_only_as_ritual": False,
        "ritual_casting_type": None,
        "restriction": "",
        "display_as_attack": None,
        "source_group": "subclass_feature_fallback",
        "character_class_id": class_instance_id,
        "component_id": feature_id,
        "component_type_id": _text(feature.get("entityTypeId")),
        "definition_is_legacy": definition.get("isLegacy"),
        "grant_type": "always_prepared",
        "grant_feature_id": feature_id,
        "grant_feature_name": _text(feature.get("name")),
    }


def _existing_definition_matches(spells, definition_id):
    return [
        item for item in _list(spells)
        if isinstance(item, dict)
        and _text(item.get("definition_id")) == _text(definition_id)
    ]


def apply_subclass_spell_resolution(raw_source, sheet, *, catalog=None):
    """Mutate ``sheet.spells`` only for source-proven always-prepared grants."""
    raw_source = _dict(raw_source)
    spells = getattr(sheet, "spells", None)
    if not isinstance(spells, list):
        return {
            "version": SUBCLASS_SPELL_VERSION,
            "status": "skipped",
            "reason": "sheet_spells_unavailable",
            "added_count": 0,
            "upgraded_count": 0,
            "unresolved_count": 0,
            "resolved_definitions": {},
            "features": [],
        }

    requests = []
    feature_reports = []

    for character_class in _list(raw_source.get("classes")):
        character_class = _dict(character_class)
        class_level = _as_int(character_class.get("level"))
        if class_level is None:
            continue
        active = _active_feature_definitions(character_class)

        for feature in active:
            rows = _spell_table_rows(feature.get("description"))
            if not rows:
                continue
            grant_type = _feature_grant_type(feature, active)
            report = {
                "class_name": _text(_dict(character_class.get("definition")).get("name")),
                "class_level": class_level,
                "feature_id": _text(feature.get("id")),
                "feature_name": _text(feature.get("name")),
                "grant_type": grant_type,
                "table_row_count": len(rows),
                "eligible_spell_names": [],
            }

            for required_level, names in rows:
                if required_level <= class_level:
                    report["eligible_spell_names"].extend(names)
                    if grant_type == "always_prepared":
                        for name in names:
                            requests.append((character_class, feature, required_level, name))
            feature_reports.append(report)

    report = {
        "version": SUBCLASS_SPELL_VERSION,
        "status": "pass",
        "added_count": 0,
        "upgraded_count": 0,
        "unresolved_count": 0,
        "expanded_list_count": sum(
            1 for row in feature_reports if row.get("grant_type") == "expanded_list"
        ),
        "features": feature_reports,
        "resolved": [],
        "unresolved": [],
        "resolved_definitions": {},
    }

    if not requests:
        return report

    try:
        catalog_values = tuple(catalog) if catalog is not None else _cached_catalog()
    except Exception as exc:
        report["status"] = "partial"
        report["catalog_error"] = f"{type(exc).__name__}: {exc}"
        report["unresolved_count"] = len(requests)
        report["unresolved"] = [
            {
                "feature_id": _text(feature.get("id")),
                "feature_name": _text(feature.get("name")),
                "name": name,
                "reason": "catalog_unavailable",
            }
            for _character_class, feature, _required_level, name in requests
        ]
        return report

    by_name, source_editions = _catalog_indexes(catalog_values)

    for character_class, feature, required_level, name in requests:
        class_source_ids = _class_source_ids(character_class)
        legacy_value = _class_legacy_value(character_class, source_editions)
        definition, reason = _resolve_catalog_definition(
            name,
            by_name=by_name,
            legacy_value=legacy_value,
            class_source_ids=class_source_ids,
        )
        if definition is None:
            report["unresolved_count"] += 1
            report["unresolved"].append(
                {
                    "feature_id": _text(feature.get("id")),
                    "feature_name": _text(feature.get("name")),
                    "name": name,
                    "required_level": required_level,
                    "reason": reason,
                    "class_legacy": legacy_value,
                }
            )
            continue

        definition_id = _text(definition.get("id"))
        existing = _existing_definition_matches(spells, definition_id)
        slot_entry = next(
            (item for item in existing if item.get("uses_spell_slot") is True),
            None,
        )
        already = next(
            (item for item in existing if item.get("always_prepared") is True),
            None,
        )

        if already is not None:
            already.setdefault("grant_type", "always_prepared")
            already.setdefault("grant_feature_id", _text(feature.get("id")))
            already.setdefault("grant_feature_name", _text(feature.get("name")))
            source_id = _text(already.get("source_id"))
            outcome = "structured_existing"
        elif slot_entry is not None:
            slot_entry["prepared"] = True
            slot_entry["always_prepared"] = True
            slot_entry["grant_type"] = "always_prepared"
            slot_entry["grant_feature_id"] = _text(feature.get("id"))
            slot_entry["grant_feature_name"] = _text(feature.get("name"))
            source_id = _text(slot_entry.get("source_id"))
            report["upgraded_count"] += 1
            outcome = "upgraded_existing"
        else:
            item = _synthetic_spell(definition, character_class, feature)
            spells.append(item)
            source_id = _text(item.get("source_id"))
            report["added_count"] += 1
            outcome = "added_fallback"

        if source_id:
            report["resolved_definitions"][source_id] = deepcopy(definition)
        report["resolved"].append(
            {
                "feature_id": _text(feature.get("id")),
                "feature_name": _text(feature.get("name")),
                "name": _text(definition.get("name")),
                "definition_id": definition_id,
                "required_level": required_level,
                "is_legacy": definition.get("isLegacy"),
                "catalog_match": reason,
                "outcome": outcome,
                "source_id": source_id,
            }
        )

    if report["unresolved_count"]:
        report["status"] = "partial"
    return report


def install_source_integrity(original_normalize_character):
    if getattr(original_normalize_character, "_sheetmover_subclass_spells_v264", False):
        return original_normalize_character

    def normalize_character_v264(data):
        sheet = original_normalize_character(data)
        report = apply_subclass_spell_resolution(data, sheet)

        calculation_inputs = getattr(sheet, "calculation_inputs", None)
        if isinstance(calculation_inputs, dict):
            calculation_inputs["subclass_spell_resolution"] = deepcopy(report)

        warnings = getattr(sheet, "warnings", None)
        if isinstance(warnings, list):
            if report.get("catalog_error"):
                warnings.append(
                    "서브클래스 자동 준비 주문의 D&D Beyond 주문 원본을 조회하지 못해 "
                    "추가하지 않았습니다."
                )
            elif report.get("unresolved_count"):
                warnings.append(
                    "서브클래스 자동 준비 주문 중 원본 주문을 하나로 확정하지 못한 항목 "
                    f"{report['unresolved_count']}개를 추가하지 않았습니다."
                )

        return sheet

    normalize_character_v264.__name__ = getattr(
        original_normalize_character,
        "__name__",
        "normalize_character",
    )
    normalize_character_v264._sheetmover_subclass_spells_v264 = True
    return normalize_character_v264
