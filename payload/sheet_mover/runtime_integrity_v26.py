# -*- coding: utf-8 -*-
"""Runtime integrity v2.6: DDB selected features + combat math parity.

This module is intentionally isolated from the translation pipeline.
It consumes the already-saved raw_source so reruns do not need paid translation.
"""
from __future__ import annotations

import re
from copy import deepcopy


RUNTIME_INTEGRITY_VERSION = "2026-10-07-runtime-integrity-v2.6-combat-features"


def _dict(value):
    return value if isinstance(value, dict) else {}


def _list(value):
    return value if isinstance(value, list) else []


def _text(value):
    return str(value or "").strip()


def _number(value):
    return value if type(value) in (int, float) else None


def _plain(value, plain_text=None):
    if plain_text is not None:
        try:
            return plain_text(value)
        except Exception:
            pass
    text = str(value or "")
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</p\s*>", "\n", text)
    text = re.sub(r"(?is)<[^>]+>", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _raw_modifier_groups(result_payload):
    raw = _dict(_dict(result_payload).get("raw_source"))
    modifiers = _dict(raw.get("modifiers"))
    for group, values in modifiers.items():
        for modifier in _list(values):
            if isinstance(modifier, dict):
                yield str(group), modifier


def _modifier_value(modifier):
    for key in ("value", "fixedValue"):
        value = modifier.get(key)
        if type(value) in (int, float):
            return value
        if isinstance(value, str):
            try:
                return float(value) if "." in value else int(value)
            except ValueError:
                pass
    return None


def _unconditional(modifier):
    return _text(modifier.get("restriction")) == ""


# ---------------------------------------------------------------------------
# Stage 8: selected Weapon Mastery + Eldritch Invocation options
# ---------------------------------------------------------------------------

def _mastery_display(label):
    label = _text(label)
    match = re.fullmatch(r"(.+?)\s*\((.+)\)", label)
    if not match:
        return label
    mastery = match.group(1).strip()
    weapon = match.group(2).strip()
    return f"{weapon} ({mastery})"


def _weapon_mastery_details(result_payload, row, plain_text=None):
    identities = {
        _text(row.get("source_id")),
        _text(row.get("definition_id")),
    }
    identities.discard("")

    labels = []
    for _group, modifier in _raw_modifier_groups(result_payload):
        if _text(modifier.get("type")).casefold() != "weapon-mastery":
            continue
        if modifier.get("isGranted") is False:
            continue
        component = _text(modifier.get("componentId"))
        if identities and component not in identities:
            continue
        label = _text(
            modifier.get("friendlySubtypeName")
            or modifier.get("subType")
        )
        if label and label not in labels:
            labels.append(label)

    # 2024 DDB can anchor the selected mastery modifiers to an internal
    # feat/choice component rather than the visible Fighter feature id.
    # If the exact component yielded nothing, use the concrete granted
    # weapon-mastery modifiers on this character.
    if not labels:
        for _group, modifier in _raw_modifier_groups(result_payload):
            if _text(modifier.get("type")).casefold() != "weapon-mastery":
                continue
            if modifier.get("isGranted") is False:
                continue
            label = _text(
                modifier.get("friendlySubtypeName")
                or modifier.get("subType")
            )
            if label and label not in labels:
                labels.append(label)

    if not labels:
        return []

    raw = _dict(_dict(result_payload).get("raw_source"))
    raw_actions = []
    for group in ("feat", "class"):
        raw_actions.extend(_list(_dict(raw.get("actions")).get(group)))

    action_by_name = {}
    for action in raw_actions:
        action = _dict(action)
        name = _text(action.get("name"))
        if not name:
            continue
        description = (
            action.get("snippet")
            or action.get("description")
            or _dict(action.get("definition")).get("snippet")
            or _dict(action.get("definition")).get("description")
            or ""
        )
        action_by_name[name.casefold()] = _plain(description, plain_text)

    details = []
    for label in labels:
        display = _mastery_display(label)
        action_text = action_by_name.get(label.casefold(), "")
        details.append(
            f"{display}: {action_text}" if action_text else display
        )
    return details


def _eldritch_invocation_rows(result_payload, plan, module_globals):
    roll20_payload = _dict(_dict(result_payload).get("roll20_payload"))
    features = [
        _dict(item)
        for item in _list(roll20_payload.get("features"))
    ]
    plain_text = module_globals.get("_plain_text")
    feature_row_id = module_globals["feature_row_id"]

    invocation_parents = []
    for feature in features:
        if _text(feature.get("original_name")).casefold() != "eldritch invocations":
            continue
        identities = {
            _text(feature.get("source_id")),
            _text(feature.get("definition_id")),
        }
        identities.discard("")
        if identities:
            invocation_parents.append((feature, identities))

    if not invocation_parents:
        return {}

    raw = _dict(_dict(result_payload).get("raw_source"))
    class_options = _list(_dict(raw.get("options")).get("class"))
    class_actions = _list(_dict(raw.get("actions")).get("class"))

    existing_names = {
        _text(_dict(row).get("original_name")).casefold()
        for row in _list(plan.get("rows"))
        if _text(_dict(row).get("original_name"))
    }
    existing_names.update(
        _text(_dict(row).get("name")).casefold()
        for row in _list(plan.get("rows"))
        if _text(_dict(row).get("name"))
    )

    rows_by_parent = {}
    for parent, parent_ids in invocation_parents:
        parent_key = _text(parent.get("source_key"))
        if not parent_key:
            continue

        for option in class_options:
            option = _dict(option)
            if _text(option.get("componentId")) not in parent_ids:
                continue

            definition = _dict(option.get("definition"))
            name = _text(definition.get("name"))
            definition_id = _text(
                definition.get("id")
                or option.get("definitionId")
                or option.get("id")
            )
            if not name or not definition_id:
                continue
            if name.casefold() in existing_names:
                continue

            description = _plain(
                definition.get("description")
                or definition.get("snippet")
                or "",
                plain_text,
            )

            linked_actions = []
            for action in class_actions:
                action = _dict(action)
                action_name = _text(action.get("name"))
                if not action_name:
                    continue
                linked = (
                    _text(action.get("componentId")) == definition_id
                    or action_name.casefold().startswith(
                        name.casefold() + ":"
                    )
                )
                if not linked:
                    continue
                action_desc = _plain(
                    action.get("snippet")
                    or action.get("description")
                    or _dict(action.get("definition")).get("snippet")
                    or _dict(action.get("definition")).get("description")
                    or "",
                    plain_text,
                )
                linked_actions.append(
                    f"Action — {action_name}: {action_desc}"
                    if action_desc
                    else f"Action — {action_name}"
                )

            if linked_actions:
                description = (
                    description
                    + ("\n\n" if description else "")
                    + "\n".join(linked_actions)
                )

            source_key = (
                f"feature-option:{next(iter(sorted(parent_ids)))}:"
                f"{definition_id}"
            )
            row_id = feature_row_id(source_key)

            parent_plan_row = next(
                (
                    _dict(row)
                    for row in _list(plan.get("rows"))
                    if _text(_dict(row).get("source_key")) == parent_key
                ),
                {},
            )
            source_type = _text(
                _dict(parent_plan_row.get("fields")).get("source_type")
            )

            row = {
                "source_key": source_key,
                "source_id": definition_id,
                "definition_id": definition_id,
                "kind": "class_feature_option",
                "original_name": name,
                "required_level": None,
                "row_id": row_id,
                "name": name,
                "fields": {
                    "name": name,
                    "source": "Class",
                    "source_type": source_type,
                    "description": description,
                    "options-flag": "0",
                },
                "limited_use": None,
                "synthetic": True,
                "parent_feature_source_key": parent_key,
            }
            rows_by_parent.setdefault(parent_key, []).append(row)
            existing_names.add(name.casefold())

    return rows_by_parent


def install_feature_integrity(original_build_feature_plan, module_globals):
    def build_feature_plan_v26(result_payload):
        plan = original_build_feature_plan(result_payload)
        plan = deepcopy(plan)
        rows = [
            deepcopy(_dict(row))
            for row in _list(plan.get("rows"))
        ]
        plain_text = module_globals.get("_plain_text")

        # Enrich one visible Weapon Mastery row with the actual DDB choices.
        mastery_rows = [
            row for row in rows
            if _text(row.get("original_name")).casefold() == "weapon mastery"
        ]
        mastery_rows.sort(
            key=lambda row: 0
            if _text(row.get("kind")) == "class_feature"
            else 1
        )
        for row in mastery_rows[:1]:
            details = _weapon_mastery_details(
                result_payload,
                row,
                plain_text,
            )
            if not details:
                continue
            fields = _dict(row.get("fields"))
            description = _text(fields.get("description"))
            selection_text = "선택한 무기 숙달\n" + "\n".join(
                f"- {detail}" for detail in details
            )
            fields["description"] = (
                description
                + ("\n\n" if description else "")
                + selection_text
            )
            row["fields"] = fields
            row["weapon_mastery_selections"] = details

        plan["rows"] = rows

        # DDB stores selected Eldritch Invocations in raw_source.options.class,
        # not as ordinary class-feature rows. Surface those selections.
        option_rows = _eldritch_invocation_rows(
            result_payload,
            plan,
            module_globals,
        )
        if option_rows:
            expanded = []
            added_ids = []
            for row in rows:
                expanded.append(row)
                parent_key = _text(row.get("source_key"))
                for extra in option_rows.get(parent_key, []):
                    expanded.append(extra)
                    added_ids.append(_text(extra.get("row_id")))

            rows = expanded
            plan["rows"] = rows
            managed = list(plan.get("all_managed_row_ids") or [])
            for row_id in added_ids:
                if row_id and row_id not in managed:
                    managed.append(row_id)
            plan["all_managed_row_ids"] = managed
            plan["desired_managed_order"] = [
                _text(row.get("row_id"))
                for row in rows
                if _text(row.get("row_id"))
            ]
            plan["selected_class_option_count"] = len(added_ids)

        plan["row_count"] = len(rows)
        plan["version"] = (
            str(plan.get("version") or "")
            + "+runtime-integrity-v2.6"
        )
        return plan

    return build_feature_plan_v26


# ---------------------------------------------------------------------------
# Stage 10A: magic weapon, Fighting Style: Archery, Unarmed Strike
# ---------------------------------------------------------------------------

def _magic_weapon_bonus(item, raw_definition):
    candidates = []

    for key in (
        "magicWeaponBonus",
        "attackBonus",
        "bonus",
    ):
        value = raw_definition.get(key)
        if type(value) in (int, float) and value > 0:
            candidates.append(int(value))

    for modifier in _list(raw_definition.get("grantedModifiers")):
        modifier = _dict(modifier)
        if modifier.get("isGranted") is False:
            continue
        if not _unconditional(modifier):
            continue
        if _text(modifier.get("type")).casefold() != "bonus":
            continue
        subtype = _text(modifier.get("subType")).casefold()
        friendly = _text(modifier.get("friendlySubtypeName")).casefold()
        if (
            subtype in {
                "magic",
                "magic-weapon",
                "weapon-attacks",
                "weapon-attack-rolls",
                "weapon-damage",
            }
            or friendly in {
                "magic",
                "weapon attacks",
                "weapon attack rolls",
            }
        ):
            value = _modifier_value(modifier)
            if value is not None and value > 0:
                candidates.append(int(value))

    # Some DDB magic items expose the bonus only in the item rules text.
    description = " ".join(
        str(raw_definition.get(key) or "")
        for key in ("description", "snippet")
    )
    for match in re.finditer(
        r"\+(\d+)\s+bonus\s+to\s+attack\s+and\s+damage\s+rolls",
        description,
        flags=re.I,
    ):
        candidates.append(int(match.group(1)))

    # +1/+2/+3 weapons usually carry the bonus in the item name as a final
    # fallback. Require the explicit comma form so ordinary names with numbers
    # are not mistaken for weapon bonuses.
    original_name = _text(
        _dict(item).get("original_name")
        or raw_definition.get("name")
    )
    match = re.search(r",\s*\+(\d+)\s*$", original_name)
    if match:
        candidates.append(int(match.group(1)))

    return max(candidates) if candidates else 0


def _has_archery_feature(result_payload):
    roll20_payload = _dict(_dict(result_payload).get("roll20_payload"))
    for feature in _list(roll20_payload.get("features")):
        feature = _dict(feature)
        if _text(feature.get("original_name")).casefold() == "archery":
            return True
        if (
            _text(feature.get("original_name")).casefold() == "fighting style"
            and "archery" in _text(feature.get("name")).casefold()
        ):
            return True
    return False


def _ranged_weapon_attack_bonus(result_payload, raw_definition):
    if raw_definition.get("attackType") != 2:
        return 0

    bonus = 0
    for group, modifier in _raw_modifier_groups(result_payload):
        if group == "item":
            continue
        if modifier.get("isGranted") is False:
            continue
        if not _unconditional(modifier):
            continue
        if _text(modifier.get("type")).casefold() != "bonus":
            continue
        subtype = _text(modifier.get("subType")).casefold()
        friendly = _text(modifier.get("friendlySubtypeName")).casefold()
        if (
            subtype in {
                "ranged-weapon-attacks",
                "ranged-weapon-attack-rolls",
                "ranged-attacks",
            }
            or "ranged weapon attack" in friendly
        ):
            value = _modifier_value(modifier)
            if value is not None:
                bonus += int(value)

    # DDB's Archery Fighting Style is exactly +2 to attacks with Ranged
    # weapons. This fallback handles payloads where the selected feat is
    # present but its modifier entry is not materialized.
    if bonus == 0 and _has_archery_feature(result_payload):
        bonus = 2
    return bonus


def _attack_rollbase(
    ability_mod,
    ability_label,
    proficient,
    magic_bonus,
    extra_attack_bonus,
):
    hbonus = f" + {ability_mod}[{ability_label}]"
    if proficient:
        hbonus += " + @{pb}[PROF]"
    if magic_bonus:
        hbonus += f" + {magic_bonus}[MAGIC]"
    if extra_attack_bonus:
        hbonus += f" + {extra_attack_bonus}[ATTACK]"

    damage_bonus = ability_mod + magic_bonus
    dmg_expr = f" + {damage_bonus}[{ability_label}+MAGIC]"

    return (
        "@{wtype}&{template:atkdmg} "
        "{{mod=@{atkbonus}}} "
        "{{rname=@{atkname}}} "
        f"{{{{r1=[[@{{d20}}cs>@{{atkcritrange}}{hbonus}]]}}}} "
        f"@{{rtype}}cs>@{{atkcritrange}}{hbonus}]]}} "
        "@{atkflag} "
        "{{range=@{atkrange}}} "
        "@{dmgflag} "
        "{{dmg1=[[@{dmgbase}"
        f"{dmg_expr}]]}} "
        "{{dmg1type=@{dmgtype}}} "
        "{{crit1=[[@{dmgcustcrit}]]}} "
        "{{desc=@{atk_desc}}} "
        "@{charname_output} "
        "{{licensedsheet=@{licensedsheet}}}"
    )


def _damage_rollbase(
    ability_mod,
    ability_label,
    magic_bonus,
):
    damage_bonus = ability_mod + magic_bonus
    return (
        "@{wtype}&{template:dmg} "
        "{{rname=@{atkname}}} "
        "{{range=@{atkrange}}} "
        "@{dmgflag} "
        "{{dmg1=[[@{dmgbase} + "
        f"{damage_bonus}[{ability_label}+MAGIC]]]}} "
        "{{dmg1type=@{dmgtype}}} "
        "{{desc=@{atk_desc}}} "
        "@{charname_output} "
        "{{licensedsheet=@{licensedsheet}}}"
    )


def install_attack_integrity(
    original_map_weapon_attack,
    original_build_attack_plan,
    module_globals,
):
    fmt = module_globals["_format_signed"]
    raw_inventory_map = module_globals["_raw_inventory_map"]
    weapon_attack_row_id = module_globals["weapon_attack_row_id"]
    ATTACK_FLAG = module_globals["ATTACK_FLAG"]
    DMG_FLAG = module_globals["DMG_FLAG"]
    PB_FLAG = module_globals["PB_FLAG"]

    def map_weapon_attack_v26(
        item,
        character,
        raw_definition,
        result_payload,
    ):
        row = original_map_weapon_attack(
            item,
            character,
            raw_definition,
            result_payload,
        )
        row = deepcopy(row)

        magic_bonus = _magic_weapon_bonus(item, raw_definition)
        extra_attack_bonus = _ranged_weapon_attack_bonus(
            result_payload,
            raw_definition,
        )
        base_attack = int(row.get("attack_bonus") or 0)
        attack_bonus = base_attack + magic_bonus + extra_attack_bonus
        ability_mod = int(row.get("ability_modifier") or 0)

        fields = _dict(row.get("fields"))
        dice = _text(fields.get("dmgbase"))
        damage_type = _text(fields.get("dmgtype"))
        damage_modifier = ability_mod + magic_bonus
        damage_display = (
            f"{dice}{fmt(damage_modifier)} {damage_type}"
            if dice
            else row.get("damage_display")
        )

        fields["atkmod"] = (
            fmt(extra_attack_bonus)
            if extra_attack_bonus
            else ""
        )
        fields["atkmagic"] = (
            fmt(magic_bonus)
            if magic_bonus
            else ""
        )
        fields["dmgmod"] = (
            fmt(magic_bonus)
            if magic_bonus
            else ""
        )
        fields["atkbonus"] = fmt(attack_bonus)
        fields["atkdmgtype"] = damage_display
        fields["rollbase"] = _attack_rollbase(
            ability_mod=ability_mod,
            ability_label=(
                "DEX"
                if row.get("ability") == "dexterity"
                else "STR"
            ),
            proficient=bool(row.get("proficient")),
            magic_bonus=magic_bonus,
            extra_attack_bonus=extra_attack_bonus,
        )
        fields["rollbase_dmg"] = _damage_rollbase(
            ability_mod=ability_mod,
            ability_label=(
                "DEX"
                if row.get("ability") == "dexterity"
                else "STR"
            ),
            magic_bonus=magic_bonus,
        )

        row["fields"] = fields
        row["attack_bonus"] = attack_bonus
        row["damage_display"] = damage_display
        row["magic_weapon_bonus"] = magic_bonus
        row["additional_attack_bonus"] = extra_attack_bonus
        return row

    def _unarmed_row(result_payload):
        roll20_payload = _dict(_dict(result_payload).get("roll20_payload"))
        character = _dict(roll20_payload.get("character"))
        raw_source = _dict(_dict(result_payload).get("raw_source"))
        if not raw_source.get("id"):
            return None

        scores = _dict(character.get("ability_scores"))
        try:
            strength_mod = (int(scores.get("strength")) - 10) // 2
        except Exception:
            return None

        pb = int(character.get("proficiency_bonus") or 0)
        attack_bonus = strength_mod + pb
        damage_total = max(0, 1 + strength_mod)
        source_key = "attack:synthetic:unarmed-strike"
        row_id = weapon_attack_row_id(source_key)

        fields = {
            "options-flag": "0",
            "atkname": "비무장 타격 (Unarmed Strike)",
            "atkflag": ATTACK_FLAG,
            "atkattr_base": "@{strength_mod}",
            "atkmod": "",
            "atkprofflag": PB_FLAG,
            "atkmagic": "",
            "atkcritrange": "20",
            "atkrange": "5 ft.",
            "dmgflag": DMG_FLAG,
            "dmgbase": "1",
            "dmgattr": "@{strength_mod}",
            "dmgmod": "",
            "dmgtype": "Bludgeoning",
            "dmgcustcrit": "0",
            "dmg2flag": "0",
            "dmg2base": "",
            "dmg2attr": "0",
            "dmg2mod": "",
            "dmg2type": "",
            "dmg2custcrit": "",
            "saveflag": "0",
            "saveattr": "",
            "savedc": "",
            "saveflat": "",
            "saveeffect": "",
            "ammo": "",
            "atk_desc": "",
            "hldmg": "",
            "spelllevel": "",
            "itemid": "",
            "spellid": "",
            "spell_innate": "",
            "atkbonus": fmt(attack_bonus),
            "atkdmgtype": f"{damage_total} Bludgeoning",
            "rollbase": _attack_rollbase(
                ability_mod=strength_mod,
                ability_label="STR",
                proficient=True,
                magic_bonus=0,
                extra_attack_bonus=0,
            ),
            "rollbase_dmg": _damage_rollbase(
                ability_mod=strength_mod,
                ability_label="STR",
                magic_bonus=0,
            ),
            "rollbase_crit": "",
        }
        return {
            "source_key": source_key,
            "source_id": "",
            "definition_id": "",
            "row_id": row_id,
            "name": "비무장 타격 (Unarmed Strike)",
            "ability": "strength",
            "ability_modifier": strength_mod,
            "proficient": True,
            "proficiency_bonus": pb,
            "attack_bonus": attack_bonus,
            "damage_display": f"{damage_total} Bludgeoning",
            "range": "5 ft.",
            "synthetic": True,
            "fields": fields,
        }

    def build_attack_plan_v26(result_payload):
        plan = deepcopy(original_build_attack_plan(result_payload))
        rows = list(plan.get("rows") or [])

        if not any(
            "unarmed strike" in _text(row.get("name")).casefold()
            for row in rows
        ):
            unarmed = _unarmed_row(result_payload)
            if unarmed is not None:
                rows.append(unarmed)

        plan["rows"] = rows
        plan["row_count"] = len(rows)
        plan["version"] = (
            str(plan.get("version") or "")
            + "+runtime-integrity-v2.6"
        )
        plan.setdefault("policy", {})[
            "include_unarmed_strike"
        ] = True
        return plan

    return map_weapon_attack_v26, build_attack_plan_v26


# ---------------------------------------------------------------------------
# Stage 2 / Stage 5: AC parity for explicit custom/unarmored DDB formulas
# ---------------------------------------------------------------------------

def _item_active(item):
    equipped = item.get("equipped") is True
    attuned = item.get("attuned") is True
    can_equip = item.get("can_equip") is True
    can_attune = item.get("can_attune") is True
    consumable = item.get("is_consumable") is True
    return (
        (not can_equip and not can_attune and not consumable)
        or (attuned and equipped)
        or (attuned and not can_equip)
        or (not can_attune and equipped)
    )


def _ability_mod(scores, stat_id):
    ability_by_id = {
        1: "strength",
        2: "dexterity",
        3: "constitution",
        4: "intelligence",
        5: "wisdom",
        6: "charisma",
    }
    ability = ability_by_id.get(stat_id)
    if not ability:
        return None
    try:
        return (int(scores.get(ability)) - 10) // 2
    except Exception:
        return None


def _resolved_ac_bonus(modifier, scores):
    value = _modifier_value(modifier)
    if value is not None:
        return value
    stat_id = modifier.get("statId")
    if type(stat_id) is int and stat_id:
        return _ability_mod(scores, stat_id)
    return None


def _explicit_ac_candidates(sheet, scores):
    equipment = [
        item
        for item in (getattr(sheet, "equipment", []) or [])
        if isinstance(item, dict)
    ]
    dex_mod = _ability_mod(scores, 2)
    if dex_mod is None:
        return []

    candidates = []

    # Homebrew/custom armor entries can carry an explicit final AC without a
    # normal light/medium/heavy armorTypeId. DDB itself uses these for display
    # helpers; don't silently discard that explicit AC.
    for item in equipment:
        if not _item_active(item):
            continue
        armor_type = item.get("armor_type_id")
        armor_class = _number(item.get("armor_class"))
        if (
            armor_class is not None
            and armor_type not in {1, 2, 3, 4}
        ):
            candidates.append(armor_class)

    # Resolve explicit unarmored formulas from active item grantedModifiers.
    # This handles combinations such as Robe of the Archmagi plus a
    # stat-based unarmored AC bonus.
    set_candidates = []
    unarmored_bonus = 0

    for item in equipment:
        if not _item_active(item):
            continue
        for modifier in _list(item.get("granted_modifiers")):
            modifier = _dict(modifier)
            if modifier.get("isGranted") is False:
                continue
            if not _unconditional(modifier):
                continue
            modifier_type = _text(modifier.get("type")).casefold()
            subtype = _text(modifier.get("subType")).casefold()

            if subtype != "unarmored-armor-class":
                continue

            value = _resolved_ac_bonus(modifier, scores)
            if modifier_type == "set":
                extra = value or 0
                stat_id = modifier.get("statId")
                stat_bonus = 0
                if stat_id not in (None, 0) and _modifier_value(modifier) is not None:
                    stat_bonus = _ability_mod(scores, stat_id) or 0
                set_candidates.append(
                    10 + dex_mod + stat_bonus + extra
                )
            elif modifier_type == "bonus" and value is not None:
                unarmored_bonus += value

    for base in set_candidates:
        candidates.append(base + unarmored_bonus)

    return candidates


def install_ac_integrity(original_calculate_ac, module_globals):
    def calculate_ac_v26(sheet, ability_scores):
        original_value, unresolved = original_calculate_ac(
            sheet,
            ability_scores,
        )
        explicit = _explicit_ac_candidates(
            sheet,
            _dict(ability_scores),
        )
        if not explicit:
            return original_value, unresolved

        best = max(explicit)
        if original_value is None or best > original_value:
            return best, unresolved
        return original_value, unresolved

    return calculate_ac_v26


def install_basic_plan_integrity(original_build_plan, module_globals):
    def build_plan_v26(payload):
        payload_for_plan = payload
        raw_source = _dict(_dict(payload).get("raw_source"))
        if raw_source:
            try:
                from sheet_mover.source import normalize_character
                fresh = normalize_character(raw_source).to_dict()
                fresh_ac = fresh.get("armor_class")
                roll20_payload = _dict(_dict(payload).get("roll20_payload"))
                old_character = _dict(roll20_payload.get("character"))
                old_ac = old_character.get("armor_class")
                if type(fresh_ac) in (int, float):
                    payload_for_plan = deepcopy(payload)
                    patched_roll20 = _dict(
                        payload_for_plan.get("roll20_payload")
                    )
                    patched_character = _dict(
                        patched_roll20.get("character")
                    )
                    patched_character["armor_class"] = fresh_ac
                    patched_roll20["character"] = patched_character
                    payload_for_plan["roll20_payload"] = patched_roll20
                    if old_ac != fresh_ac:
                        print(
                            "[시트 이동기] AC 원본 재계산 보정: "
                            f"{old_ac} -> {fresh_ac}"
                        )
            except Exception as exc:
                # Do not make Stage 5 less reliable if a legacy raw payload
                # cannot be re-normalized. The original plan remains usable.
                print(
                    "[시트 이동기] AC 원본 재계산 생략: "
                    + str(exc)
                )

        plan = original_build_plan(payload_for_plan)
        plan["version"] = (
            str(plan.get("version") or "")
            + "+runtime-integrity-v2.6"
        )
        return plan

    return build_plan_v26

# ---------------------------------------------------------------------------
# Stage 12: remove stale duplicate Sheet Mover resource rows
# ---------------------------------------------------------------------------

RESOURCE_DETAIL_STATE_SCRIPT = r"""
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

const character=findCharacter();
if(!character){done({ok:false,reason:'character_not_found'});return;}
const collection=character.attribs;
if(!collection || typeof collection.fetch!=='function'){
  done({ok:false,reason:'attribute_collection_unavailable'});return;
}

let settled=false;
function finish(status,error){
  if(settled)return; settled=true;
  const rows={};
  for(const model of modelsOf(collection)){
    const name=String(val(model,'name') || '').trim();
    const match=name.match(/^repeating_resource_([^_]+)_(.+)$/);
    if(!match) continue;
    const rowId=match[1];
    const field=match[2];
    if(!rows[rowId]) rows[rowId]={};
    rows[rowId][field]={
      current:String(val(model,'current') == null ? '' : val(model,'current')),
      max:String(val(model,'max') == null ? '' : val(model,'max')),
    };
  }
  done({
    ok:status === 'success' || status === 'success_promise',
    fetch_status:status,
    fetch_error:error || '',
    rows,
  });
}

try{
  const req=collection.fetch({
    reset:false,
    success:()=>finish('success',''),
    error:(_c,xhr)=>finish('error',`status=${xhr&&xhr.status}; text=${xhr&&xhr.statusText}`),
  });
  if(req && typeof req.then==='function'){
    req.then(()=>finish('success_promise',''),e=>finish('error_promise',String(e||'')));
  }
  setTimeout(()=>finish('timeout','fetch callback timeout'),12000);
}catch(e){
  finish('exception',String(e && e.stack ? e.stack : e));
}
"""


def _resource_row_field(row, field, *, max_value=False):
    cell = _dict(_dict(row).get(field))
    key = "max" if max_value else "current"
    return _text(cell.get(key))


def stale_duplicate_resource_row_ids(
    state,
    desired_resources,
    current_managed_ids,
):
    desired_names = {
        _text(_dict(resource).get("name")).casefold()
        for resource in _list(desired_resources)
        if _text(_dict(resource).get("name"))
    }
    current_ids = {
        _text(value)
        for value in _list(current_managed_ids)
        if _text(value)
    }

    stale = []
    for row_id, row in _dict(_dict(state).get("rows")).items():
        row_id = _text(row_id)
        if not row_id.startswith("-SM"):
            continue
        if row_id in current_ids:
            continue

        left_name = _resource_row_field(row, "resource_left_name")
        right_name = _resource_row_field(row, "resource_right_name")
        names = {
            value.casefold()
            for value in (left_name, right_name)
            if value
        }
        if names & desired_names:
            stale.append(row_id)

    return sorted(set(stale))


def install_resource_integrity(original_apply_resources, module_globals):
    def apply_resources_v26(*args, **kwargs):
        report, report_path = original_apply_resources(*args, **kwargs)
        if kwargs.get("dry_run") is True:
            return report, report_path

        desired = _list(_dict(report).get("resources"))
        current_ids = _list(
            _dict(report).get("managed_repeating_row_ids")
        )
        if not desired:
            return report, report_path

        ensure_cdp = module_globals["_ensure_cdp"]
        attach = module_globals["_attach_driver"]
        disconnect = module_globals["_disconnect_driver"]
        select_tab = module_globals["_select_roll20_tab"]
        delete_rows = module_globals["_delete_rows"]
        save_json = module_globals["_save_json"]

        cdp_url = kwargs.get("cdp_url")
        if cdp_url is None:
            cdp_url = module_globals.get("DEFAULT_CDP_URL")

        target = {
            "roll20_character_id": _text(
                _dict(report).get("roll20_character_id")
            ),
            "character_name": _text(
                _dict(report).get("character_name")
            ),
        }

        driver = None
        stale = []
        try:
            ensure_cdp(cdp_url)
            driver = attach(cdp_url)
            select_tab(driver)

            from sheet_mover.roll20_read import read_persisted
            state = read_persisted(
                driver,
                RESOURCE_DETAIL_STATE_SCRIPT,
                target["roll20_character_id"],
                target["character_name"],
            )
            stale = stale_duplicate_resource_row_ids(
                state,
                desired,
                current_ids,
            )
            if stale:
                delete_rows(driver, target, stale)

            report = deepcopy(report)
            report["stale_duplicate_resource_row_ids"] = stale
            report["duplicate_resource_cleanup_count"] = len(stale)
            save_json(report_path, report)

            if stale:
                print(
                    "[시트 이동기] 중복 자원 반복행 정리: "
                    + str(len(stale))
                    + "개"
                )
            return report, report_path
        finally:
            if driver is not None:
                disconnect(driver)

    return apply_resources_v26

