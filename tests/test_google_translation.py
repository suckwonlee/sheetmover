import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sheet_mover.google_glossary import _tsv_bytes
from sheet_mover.translator import (
    TranslationError,
    Translator,
    cloud_glossary_entries,
    glossary_id_for,
    mechanics_compatible,
    mechanics_mismatch_text,
    structure_tokens,
)


class _Translation:
    def __init__(self, text):
        self.translated_text = text


class _Response:
    def __init__(self, values, glossary=True):
        if glossary:
            self.glossary_translations = [_Translation(value) for value in values]
            self.translations = []
        else:
            self.translations = [_Translation(value) for value in values]
            self.glossary_translations = []


class FakeGoogleClient:
    def __init__(self, transform=None):
        self.transform = transform or (lambda value, request: value)
        self.requests = []

    def translate_text(self, request=None, timeout=None):
        self.requests.append((request, timeout))
        return _Response([
            self.transform(value, request)
            for value in request["contents"]
        ])


class GoogleTranslationTests(unittest.TestCase):
    def _translator(self, client, glossary=None):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        path = Path(td.name) / "glossary.json"
        path.write_text(
            json.dumps(
                glossary
                if glossary is not None
                else {
                    "thunder damage": "천둥 피해",
                    "origin feat": "기원 재주",
                    "tough": "강인함",
                    "familiar": "사역마",
                    "proficiency with": "숙련",
                    "common": "공용어",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return Translator(
            glossary_path=path,
            client=client,
            project_id="sheet-mover-test",
        )

    def test_google_request_uses_official_glossary_and_us_central1(self):
        client = FakeGoogleClient(
            lambda value, _request: value.replace("Thunder damage", "천둥 피해")
        )
        translator = self._translator(client)
        result = translator.translate("1d8 Thunder damage")

        self.assertEqual(result, "1d8 천둥 피해")
        request, timeout = client.requests[0]
        self.assertEqual(
            request["parent"],
            "projects/sheet-mover-test/locations/us-central1",
        )
        self.assertEqual(request["source_language_code"], "en")
        self.assertEqual(request["target_language_code"], "ko")
        self.assertEqual(
            request["glossary_config"]["glossary"],
            translator.glossary_name,
        )
        self.assertTrue(request["glossary_config"]["ignore_case"])
        self.assertGreaterEqual(timeout, 10)

    def test_missing_glossary_term_does_not_trigger_marker_retry(self):
        client = FakeGoogleClient()
        translator = self._translator(client)
        result = translator._google_retry_missing_terms(
            "Deal Thunder damage.",
            [("Thunder damage", "천둥 피해")],
        )
        self.assertIsNone(result)
        self.assertEqual(client.requests, [])

    def test_exact_entity_prefixed_glossary_value_is_local(self):
        client = FakeGoogleClient()
        translator = self._translator(client)
        self.assertEqual(
            translator._translate_plain_segment("&nbsp;Tough"),
            " 강인함",
        )
        self.assertEqual(client.requests, [])

    def test_mechanics_accepts_number_word_and_dice_count_echoes(self):
        self.assertTrue(
            mechanics_compatible(
                "A Healer's Kit has ten uses and stabilizes a creature at 0 Hit Points.",
                "치유사의 도구는 10회 사용할 수 있고 HP 0인 생물을 안정화합니다.",
            )
        )
        self.assertTrue(
            mechanics_compatible(
                "Roll 1d10 and add it to the check.",
                "1d10을 굴려 주사위 1개의 결과를 판정에 더합니다.",
            )
        )
        self.assertFalse(
            mechanics_compatible(
                "A Waterskin holds 4 pints.",
                "물주머니에는 4파인트(약 1.9리터)가 들어갑니다.",
            )
        )

    def test_cloud_glossary_filters_syntactic_and_ambiguous_single_terms(self):
        filtered = cloud_glossary_entries({
            "thunder damage": "천둥 피해",
            "familiar": "사역마",
            "proficiency with": "숙련",
            "common": "공용어",
        })
        self.assertEqual(filtered["thunder damage"], "천둥 피해")
        self.assertEqual(filtered["familiar"], "사역마")
        self.assertNotIn("proficiency with", filtered)
        self.assertNotIn("common", filtered)

    def test_glossary_tsv_has_no_header_and_only_safe_terms(self):
        payload = _tsv_bytes({
            "thunder damage": "천둥 피해",
            "familiar": "사역마",
            "proficiency with": "숙련",
        }).decode("utf-8")
        self.assertIn("thunder damage\t천둥 피해", payload)
        self.assertIn("familiar\t사역마", payload)
        self.assertNotIn("proficiency with", payload)
        self.assertFalse(payload.startswith("en\tko"))

    def test_exact_dnd_term_stays_local_without_api_call(self):
        client = FakeGoogleClient()
        translator = self._translator(client)
        self.assertEqual(translator.translate("Origin Feat"), "기원 재주")
        self.assertEqual(client.requests, [])

    def test_meta_prefix_is_removed(self):
        client = FakeGoogleClient(lambda _value, _request: "직역: 정상 번역")
        translator = self._translator(client, {})
        self.assertEqual(translator.translate("Normal translation"), "정상 번역")


    def test_structured_google_request_keeps_whole_sentence_context(self):
        client = FakeGoogleClient()
        translator = self._translator(
            client,
            {
                "attack": "공격",
                "light": "빛",
            },
        )
        source = (
            "<p>When you take the [action]Attack[/action] action with "
            "a [wprop]Light[/wprop] weapon.</p>"
        )
        result = translator.translate(source)

        self.assertEqual(structure_tokens(source), structure_tokens(result))
        self.assertIn("[action]공격[/action]", result)
        self.assertIn("[wprop]경량[/wprop]", result)
        request, _timeout = client.requests[0]
        self.assertEqual(request["mime_type"], "text/html")
        payload = request["contents"][0]
        self.assertIn("When you take the", payload)
        self.assertIn("weapon.", payload)
        self.assertIn("<p>", payload)
        self.assertIn('data-sm-dnd="0"', payload)
        self.assertIn('data-sm-dnd="1"', payload)
        self.assertNotIn("[action]", payload)
        self.assertFalse(any(0xE000 <= ord(ch) <= 0xF8FF for ch in payload))

    def test_dnd_tag_restore_survives_custom_data_attribute_removal(self):
        def transform(value, _request):
            return re.sub(r'\sdata-sm-dnd="\d+"', '', value)

        translator = self._translator(
            FakeGoogleClient(transform),
            {"attack": "공격"},
        )
        source = "<p>Use the [action]Attack[/action] action.</p>"
        result = translator.translate(source)
        self.assertIn("[action]공격[/action]", result)
        self.assertNotIn("sm-dnd-", result)

    def test_structured_sentinels_prevent_markup_from_moving_into_sentence(self):
        def transform(value, _request):
            return (
                value.replace(
                    "You have a mind for tactics on and off the battlefield.",
                    "당신은 전장 안팎에서 전술적인 감각을 지니고 있습니다.",
                )
                .replace("When you fail an ability check,", "능력 판정에 실패하면,")
                .replace("you roll", "당신은")
            )

        translator = self._translator(FakeGoogleClient(transform), {})
        source = (
            "<p>You have a mind for tactics on and off the battlefield. "
            "When you fail an ability check, you roll 1d10 and add it.</p>"
        )
        result = translator.translate(source)

        self.assertEqual(structure_tokens(source), structure_tokens(result))
        self.assertTrue(result.startswith("<p>"))
        self.assertTrue(result.endswith("</p>"))
        self.assertEqual(result.count("<p>"), 1)
        self.assertEqual(result.count("</p>"), 1)
        self.assertIn("1d10", result)

    def test_particle_spacing_cleanup_for_glossary_terms(self):
        client = FakeGoogleClient()
        translator = self._translator(client, {})
        result = translator._canonicalize_translation(
            "A familiar uses a Bonus Action.",
            "사역마 의 추가 행동 으로 사용합니다.",
        )
        self.assertEqual(result, "사역마의 추가 행동으로 사용합니다.")

    def test_healers_kit_use_count_cannot_attach_to_hp(self):
        client = FakeGoogleClient()
        translator = self._translator(client, {})
        source = (
            "A Healer's Kit has ten uses. As a Utilize action, you can expend "
            "one of its uses to stabilize an Unconscious creature that has 0 Hit Points."
        )
        result = translator._canonicalize_translation(
            source,
            "치유사의 도구는 10번 사용할 수 있습니다. 사용 횟수 1 HP 를 소모하여 "
            "체력이 0인 의식 불명 생명체를 안정시킬 수 있습니다.",
        )
        self.assertIn("사용 횟수 1회를 소모", result)
        self.assertNotIn("1 HP", result)

    def test_familiar_rule_uses_one_consistent_entity_and_correct_limit(self):
        client = FakeGoogleClient()
        translator = self._translator(client, {})
        source = (
            "You can’t have more than one familiar at a time. "
            "If you cast this spell while you have a familiar, it changes form."
        )
        result = translator._canonicalize_translation(
            source,
            "한 번에 한 마리 이상의 사역마를 거느릴 수 없습니다. "
            "소환수는 새로운 형태로 변합니다.",
        )
        self.assertIn("한 번에 사역마를 한 마리만 거느릴 수 있습니다.", result)
        self.assertIn("사역마는 새로운 형태로 변합니다.", result)
        self.assertNotIn("소환수", result)

    def test_cloud_glossary_id_tracks_only_uploaded_subset(self):
        base = {
            "thunder damage": "천둥 피해",
            "hit points": "HP",
        }
        changed_excluded = {
            "thunder damage": "천둥 피해",
            "hit points": "체력",
        }
        changed_uploaded = {
            "thunder damage": "천둥 손상",
            "hit points": "HP",
        }
        self.assertEqual(glossary_id_for(base), glossary_id_for(changed_excluded))
        self.assertNotEqual(glossary_id_for(base), glossary_id_for(changed_uploaded))


    def test_structured_exact_mechanical_labels_are_canonical(self):
        translator = self._translator(FakeGoogleClient(), {})
        source = (
            "<table><tr><td>Strength or Dexterity</td>"
            "<td>D10 per Fighter level</td>"
            "<td>Strength and Constitution</td>"
            "<td>Simple and Martial weapons</td>"
            "<td>Light, Medium, and Heavy armor and Shields</td></tr></table>"
        )
        result = translator.translate(source)
        self.assertIn("<td>근력 또는 민첩</td>", result)
        self.assertIn("<td>전사 레벨당 d10</td>", result)
        self.assertIn("<td>근력 및 건강</td>", result)
        self.assertIn("<td>단순 무기 및 군용 무기</td>", result)
        self.assertIn("<td>경갑, 평갑, 중갑 및 방패</td>", result)
        self.assertNotIn("손재주", result)
        self.assertNotIn("중갑, 중갑", result)

    def test_variant_human_stat_line_is_not_left_in_english(self):
        translator = self._translator(FakeGoogleClient(), {})
        source = (
            "<p><strong>Racial Traits</strong><br />"
            "+1 to All Ability Scores, Extra Language</p>"
        )
        result = translator.translate(source)
        self.assertIn("모든 능력치 +1, 추가 언어", result)
        self.assertNotIn("+1 to All Ability Scores, Extra Language", result)

    def test_booming_blade_voluntary_trigger_and_level_labels_are_repaired(self):
        translator = self._translator(FakeGoogleClient(), {})
        source = (
            "If the target willingly moves 5 feet or more before then, "
            "the target takes 1d8 thunder damage, and the spell ends. "
            "At 5th level, the damage increases. At 11th level (2d8 and 3d8), "
            "it increases again."
        )
        translated = (
            "대상이 그 전에 5피트 이상 자발적으로 이동 대상은 1d8 천둥 피해 입고 "
            "주문이 종료됩니다. 5레벨 레벨에 피해가 증가합니다. "
            "11레벨 (2d8 및 3d8) 레벨에 다시 증가합니다."
        )
        result = translator._canonicalize_translation(source, translated)
        self.assertIn(
            "대상이 그 전에 자발적으로 5피트 이상 이동하면, "
            "대상은 1d8 천둥 피해를 입고 주문이 종료됩니다.",
            result,
        )
        self.assertIn("5레벨에", result)
        self.assertIn("11레벨 (2d8 및 3d8)에", result)
        self.assertNotIn("레벨 레벨", result)

    def test_live_request_uses_no_private_use_markers(self):
        def transform(value, request):
            self.assertEqual(request["mime_type"], "text/html")
            self.assertFalse(any(0xE000 <= ord(ch) <= 0xF8FF for ch in value))
            self.assertIn('>15-foot</span>', value)
            self.assertIn('>2d8</span>', value)
            spans = re.findall(
                r'<span[^>]*id="sm-mech-\d+"[^>]*>.*?</span>', value
            )
            self.assertEqual(len(spans), 2)
            return (
                "<p>각 생명체는 " + spans[0] + " 정육면체 안에서 "
                + spans[1] + " 천둥 피해를 받습니다.</p>"
            )

        translator = self._translator(FakeGoogleClient(transform), {})
        source = "<p>Each creature in a 15-foot Cube takes 2d8 Thunder damage.</p>"
        result = translator.translate(source)
        self.assertTrue(result.startswith("<p>"))
        self.assertTrue(result.endswith("</p>"))
        self.assertIn("15피트", result)
        self.assertIn("2d8", result)
        self.assertFalse(any(0xE000 <= ord(ch) <= 0xF8FF for ch in result))


    def test_backpack_rule_translates_without_opaque_marker_fallback(self):
        def transform(value, request):
            self.assertEqual(request["mime_type"], "text/html")
            self.assertFalse(any(0xE000 <= ord(ch) <= 0xF8FF for ch in value))
            self.assertIn('id="sm-mech-0"', value)
            self.assertIn('>30 pounds</span>', value)
            self.assertIn('>1</span> cubic foot', value)
            spans = re.findall(
                r'<span[^>]*id="sm-mech-\d+"[^>]*>.*?</span>', value
            )
            self.assertEqual(len(spans), 2)
            return (
                "<p>배낭은 최대 " + spans[0] + "를 "
                + spans[1] + "세제곱피트 안에 담을 수 있습니다. "
                "안장 가방으로도 사용할 수 있습니다.</p>"
            )

        translator = self._translator(FakeGoogleClient(transform), {})
        source = (
            "<p>A Backpack holds up to 30 pounds within 1 cubic foot. "
            "It can also serve as a saddlebag.</p>"
        )
        result = translator.translate(source)
        self.assertIn("30파운드", result)
        self.assertIn("1세제곱피트", result)
        self.assertNotIn("A Backpack holds", result)
        self.assertFalse(any("보호 토큰" in warning for warning in translator.warnings))


    def test_thunderwave_audible_distance_is_not_sound_size(self):
        translator = self._translator(FakeGoogleClient(), {})
        source = (
            "In addition, unsecured objects that are entirely within the Cube are "
            "pushed 10 feet away from you, and a thunderous boom is audible within 300 feet."
        )
        result = translator._canonicalize_translation(
            source,
            "또한, 고정되지 않은 물체는 10피트만큼 밀려나고, "
            "300피트만큼 큰 폭발음이 들립니다.",
        )
        self.assertIn("천둥 같은 폭발음이 300피트 이내에서 들립니다", result)
        self.assertNotIn("300피트만큼 큰", result)

    def test_find_familiar_choice_and_normal_actions_are_not_misread(self):
        translator = self._translator(FakeGoogleClient(), {})
        source = (
            "It is a Celestial, Fey, or Fiend (your choice) instead of a Beast. "
            "A familiar can’t attack, but it can take other actions as normal."
        )
        result = translator._canonicalize_translation(
            source,
            "야수 대신 천상체, 요정, 또는 악마 (선택 사항)입니다. "
            "사역마는 공격할 수 없지만, 다른 행동은 일반적인 사역마와 마찬가지로 할 수 있습니다.",
        )
        self.assertIn("(선택)", result)
        self.assertIn("다른 행동은 정상적으로 할 수 있습니다.", result)
        self.assertNotIn("선택 사항", result)

    def test_core_fighter_skill_list_and_javelins_use_canonical_terms(self):
        translator = self._translator(FakeGoogleClient(), {})
        source = (
            "Acrobatics, Animal Handling, Athletics, History, Insight, Intimidation, "
            "Persuasion, Perception, or Survival. 8 Javelins"
        )
        result = translator._canonicalize_translation(
            source,
            "곡예, 동물 조련, 운동 능력, 역사, 통찰력, 위협, 설득력, 지각력 또는 생존 능력. 창 8개",
        )
        self.assertIn("투창 8개", result)

    def test_old_unversioned_partial_is_not_imported_into_new_pipeline(self):
        with tempfile.TemporaryDirectory() as td:
            partial = Path(td) / "old-partial.json"
            cache = Path(td) / "cache.json"
            partial.write_text(
                json.dumps(
                    {
                        "original": {"description": "Old English"},
                        "translated": {"description": "오래된 번역"},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            with mock.patch.dict(
                os.environ,
                {
                    "SHEETMOVER_RESUME_PARTIAL": str(partial),
                    "SHEETMOVER_TRANSLATION_CACHE": str(cache),
                },
                clear=False,
            ):
                translator = self._translator(FakeGoogleClient(), {})

            self.assertNotIn("Old English", translator.cache)
            self.assertTrue(
                any("파이프라인 버전과 달라" in warning for warning in translator.warnings)
            )

    def test_missing_remote_glossary_is_created_automatically(self):
        class NotFound(Exception):
            pass

        class MissingThenReadyGlossaryClient(FakeGoogleClient):
            def __init__(self):
                super().__init__()
                self.glossary_checks = 0

            def get_glossary(self, request=None, name=None):
                self.glossary_checks += 1
                if self.glossary_checks == 1:
                    raise NotFound("Glossary not found")
                return object()

        client = MissingThenReadyGlossaryClient()
        translator = self._translator(client, {})
        translator._client_injected = False
        with mock.patch(
            "sheet_mover.google_glossary.setup_google_glossary",
            return_value={"glossary_id": translator.glossary_id},
        ) as setup:
            translator._ensure_remote_glossary()

        setup.assert_called_once_with(
            glossary_path=translator.glossary_path,
            project_id="sheet-mover-test",
        )
        self.assertEqual(client.glossary_checks, 2)
        self.assertTrue(translator._glossary_preflight_done)
        self.assertEqual(client.requests, [])

    def test_existing_remote_glossary_skips_automatic_setup(self):
        class ExistingGlossaryClient(FakeGoogleClient):
            def __init__(self):
                super().__init__()
                self.glossary_checks = 0

            def get_glossary(self, request=None, name=None):
                self.glossary_checks += 1
                return object()

        client = ExistingGlossaryClient()
        translator = self._translator(client, {})
        translator._client_injected = False
        with mock.patch(
            "sheet_mover.google_glossary.setup_google_glossary"
        ) as setup:
            translator._ensure_remote_glossary()

        setup.assert_not_called()
        self.assertEqual(client.glossary_checks, 1)
        self.assertTrue(translator._glossary_preflight_done)

    def test_tough_hp_cleanup_removes_duplicate_hp_and_missing_particles(self):
        translator = self._translator(FakeGoogleClient(), {})
        source = (
            "Your Hit Point maximum increases by an amount equal to twice your character level "
            "when you gain this feat. Whenever you gain a character level thereafter, your Hit "
            "Point maximum increases by an additional 2 Hit Points."
        )
        result = translator._canonicalize_translation(
            source,
            "이 재주 얻으면 최대 HP 캐릭터 레벨 두 배만큼 증가합니다. "
            "이후 캐릭터 레벨 오를 때마다 최대 HP HP 로 2씩 증가합니다.",
        )
        self.assertIn("이 특기를 얻으면", result)
        self.assertIn("최대 HP가 캐릭터 레벨의 두 배", result)
        self.assertIn("캐릭터 레벨이 오를 때마다", result)
        self.assertIn("최대 HP가 추가로 2씩 증가", result)
        self.assertNotIn("HP HP", result)

    def test_booming_blade_preserves_willing_movement_trigger(self):
        translator = self._translator(FakeGoogleClient(), {})
        source = (
            "If the target willingly moves 5 feet or more before then, "
            "the target takes 1d8 thunder damage."
        )
        result = translator._canonicalize_translation(
            source,
            "대상이 그 전에 5피트 이상 이동하면 1d8 천둥 피해를 입습니다.",
        )
        self.assertIn("자발적으로 5피트 이상 이동하면", result)

    def test_thunderwave_origin_is_not_changed_to_centered_cube(self):
        translator = self._translator(FakeGoogleClient(), {})
        source = (
            "Each creature in a 15-foot Cube originating from you makes a "
            "Constitution saving throw."
        )
        result = translator._canonicalize_translation(
            source,
            "당신을 중심으로 15피트 정육면체 안의 각 생명체가 건강 내성 굴림을 합니다.",
        )
        self.assertIn("당신에게서 시작되는 15피트 정육면체", result)
        self.assertNotIn("당신을 중심으로", result)

    def test_creature_rule_is_not_narrowed_to_enemy(self):
        translator = self._translator(FakeGoogleClient(), {})
        source = (
            "When you reduce a creature to 0 Hit Points with a melee weapon, "
            "you can make one melee weapon attack."
        )
        result = translator._canonicalize_translation(
            source,
            "근접 무기로 적의 체력을 0으로 만들면 근접 무기 공격 한 번 할 수 있습니다.",
        )
        self.assertIn("생명체의 HP를 0", result)
        self.assertNotIn("적의 체력을 0", result)

    def test_structured_mastery_heading_uses_exact_property_term(self):
        translator = self._translator(
            FakeGoogleClient(),
            {"sap": "약화", "graze": "스침"},
        )
        source = "<p><strong><em>Sap.</em></strong> If you hit a creature.</p>"
        translated = "<p> <strong> <em> 흡수. </em> </strong> 생명체를 명중시키면. </p>"
        result = translator._canonicalize_translation(source, translated)
        self.assertIn("약화.", result)
        self.assertNotIn("흡수", result)

    def test_surface_glitches_are_cleaned_without_changing_mechanics(self):
        translator = self._translator(FakeGoogleClient(), {})
        graze = translator._canonicalize_translation(
            "damage equal to the ability modifier you used",
            "사용한 능력 수정치 정치만큼의 피해",
        )
        torch = translator._canonicalize_translation(
            "casting Bright Light in a 20-foot radius and Dim Light for an additional 20 feet",
            "반경 20피트 내에 밝은 빛, 추가로 20피트 내에 희미한 빛 춥니다",
        )
        self.assertIn("능력 수정치만큼", graze)
        self.assertIn("희미한 빛을 비춥니다", torch)

    def test_common_dnd_term_drift_is_normalized_from_source_context(self):
        translator = self._translator(FakeGoogleClient(), {})
        topple = translator._canonicalize_translation(
            "DC 8 plus the ability modifier used to make the attack roll and your Proficiency Bonus",
            "DC 8 + 명중 굴림에 사용한 능력 수정치 정치 + 숙련 보너스",
        )
        heavy = translator._canonicalize_translation(
            "if it is a Ranged weapon and your Dexterity score is not at least 13",
            "원거리 무기이고 민첩 민첩 능력치가 13 미만이면",
        )
        fighter = translator._canonicalize_translation(
            "When you reach certain Fighter levels, you gain more uses.",
            "특정 전투원 레벨에 도달하면 사용 횟수가 증가합니다.",
        )
        hp = translator._canonicalize_translation(
            "regain Hit Points equal to 1d10 plus your Fighter level",
            "1d10 + 전사 레벨만큼의 히트 포인트를 회복합니다.",
        )
        feat = translator._canonicalize_translation(
            "You gain one feat of your choice.",
            "원하는 재주 하나를 얻습니다.",
        )
        self.assertNotIn("수정치 정치", topple)
        self.assertNotIn("민첩 민첩", heavy)
        self.assertIn("전사 레벨", fighter)
        self.assertIn("HP", hp)
        self.assertIn("특기", feat)

    def test_gwm_action_creature_is_not_narrowed_when_source_uses_hp_abbreviation(self):
        translator = self._translator(FakeGoogleClient(), {})
        source = (
            "On your turn, when you score a critical hit with a melee weapon or "
            "reduce a creature to 0 HP with one, you can make one melee weapon attack."
        )
        result = translator._canonicalize_translation(
            source,
            "근접 무기로 치명타를 입히거나 적의 HP를 0으로 만들면 근접 무기 공격을 할 수 있습니다.",
        )
        self.assertIn("생명체의 HP를 0", result)
        self.assertNotIn("적의 HP를 0", result)

    def test_cache_replace_permission_error_falls_back_to_direct_write(self):
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td) / "cache.json"
            with mock.patch.dict(
                os.environ,
                {"SHEETMOVER_TRANSLATION_CACHE": str(cache)},
                clear=False,
            ):
                translator = self._translator(FakeGoogleClient(), {})
            translator.cache["Hello"] = "안녕하세요"

            with mock.patch("sheet_mover.translator.os.replace", side_effect=PermissionError("locked")):
                translator._save_persistent_cache()

            payload = json.loads(cache.read_text(encoding="utf-8"))
            self.assertEqual(payload["entries"]["Hello"], "안녕하세요")
            self.assertFalse(
                any("번역 캐시 저장 실패" in warning for warning in translator.warnings)
            )


    def test_mechanics_accepts_ordinal_word_rendered_as_digit(self):
        source = (
            "As a Utilize action, you can spread Caltrops to cover a 5-foot-square "
            "area within 5 feet. A creature that enters this area for the first time "
            "on a turn must succeed on a DC 15 Dexterity saving throw or take 1 "
            "Piercing damage and have its Speed reduced to 0. It takes 10 minutes "
            "to recover the Caltrops."
        )
        translated = (
            "활용 행동으로 마름쇠를 펼쳐 5피트 정사각형 영역을 덮습니다. "
            "5피트 이내의 생명체가 턴에 처음 들어오면 턴당 1회 DC 15 민첩 "
            "내성 굴림에 성공해야 하며, 실패하면 1 관통 피해를 받고 이동속도가 "
            "0이 됩니다. 마름쇠를 회수하는 데 10분이 걸립니다."
        )
        self.assertTrue(mechanics_compatible(source, translated))

    def test_structured_mechanics_are_restored_even_if_google_rewrites_span_body(self):
        def transform(value, _request):
            # Simulate Cloud trying to rewrite values inside the no-translate
            # spans. Restoration must use the source lock, not translated body.
            value = re.sub(r">10</span>", ">11</span>", value)
            value = value.replace("It takes", "걸리는 시간은")
            value = value.replace("minutes to recover the Caltrops.", "분입니다.")
            return value

        client = FakeGoogleClient(transform)
        translator = self._translator(client, {})
        source = "<p>It takes 10 minutes to recover the Caltrops.</p>"

        result = translator.translate(source)

        self.assertIn("10", result)
        self.assertNotIn("11", result)
        self.assertTrue(mechanics_compatible(source, result))
        self.assertFalse(translator.original_preserved)


    def test_mechanics_accepts_both_rendered_as_digit(self):
        source = (
            "Both damage rolls increase by 1d8 at 11th level "
            "(2d8 and 3d8)."
        )
        translated = (
            "2개의 피해 굴림은 11레벨에 1d8씩 증가합니다 "
            "(2d8 및 3d8)."
        )
        self.assertTrue(mechanics_compatible(source, translated))

    def test_mechanics_accepts_half_rendered_as_fraction(self):
        source = (
            "On a failed save, a creature takes 2d8 Thunder damage and is pushed "
            "10 feet away. On a successful save, it takes half as much damage only."
        )
        translated = (
            "실패하면 생명체는 2d8 천둥 피해를 받고 10피트 밀려납니다. "
            "성공하면 피해를 1/2만 받습니다."
        )
        self.assertTrue(mechanics_compatible(source, translated))

    def test_metric_distance_conversion_is_semantically_allowed(self):
        translator = self._translator(FakeGoogleClient(), {})
        waterskin_source = (
            "A Waterskin holds up to 4 pints. If you don’t drink sufficient water, "
            "you risk dehydration."
        )
        waterskin = translator._canonicalize_translation(
            waterskin_source,
            "물주머니에는 최대 4파인트(약 1.9리터)의 물을 담을 수 있습니다. "
            "충분한 물을 마시지 않으면 탈수 위험이 있습니다.",
        )
        self.assertNotIn("1.9리터", waterskin)
        self.assertTrue(mechanics_compatible(waterskin_source, waterskin))

        thunder_source = (
            "In addition, unsecured objects that are entirely within the Cube are "
            "pushed 10 feet away from you, and a thunderous boom is audible within "
            "300 feet."
        )
        thunder = translator._canonicalize_translation(
            thunder_source,
            "또한 고정되지 않은 물체는 10피트(약 3미터) 밀려나고, "
            "300피트(약 91미터)만큼 큰 폭발음이 들립니다.",
        )
        self.assertIn("3미터", thunder)
        self.assertIn("91미터", thunder)
        self.assertIn("300피트(약 91미터) 이내에서 들립니다", thunder)
        self.assertTrue(mechanics_compatible(thunder_source, thunder))
        self.assertTrue(
            mechanics_compatible(
                "A creature within 300 feet is affected.",
                "91미터 이내의 생명체가 영향을 받습니다.",
            )
        )
        self.assertTrue(
            mechanics_compatible(
                "Push the target 10 feet.",
                "대상을 3미터 밀어냅니다.",
            )
        )
        self.assertFalse(
            mechanics_compatible(
                "A creature within 300 feet is affected.",
                "300미터 이내의 생명체가 영향을 받습니다.",
            )
        )

    def test_light_property_fixed_rule_is_deterministic(self):
        translator = self._translator(FakeGoogleClient(), {})
        source = (
            "<p>When you take the [action]Attack[/action] action on your turn and "
            "attack with a Light weapon, you can make one extra attack as a Bonus "
            "Action later on the same turn. That extra attack must be made with a "
            "different Light weapon, and you don’t add your ability modifier to the "
            "extra attack’s damage unless that modifier is negative. For example, you "
            "can attack with a [items]Shortsword[/items] in one hand and a "
            "[items]Dagger[/items] in the other using the [action]Attack[/action] "
            "action and a Bonus Action, but you don’t add your Strength or Dexterity "
            "modifier to the damage roll of the Bonus Action unless that modifier is "
            "negative.</p>"
        )
        broken = (
            "<p>복용할 때[action]공격[/action] 자신의 턴에 행동으로 경량 무기 사용하여 "
            "공격하면 [items]소검[/items]과 [items]단검[/items]을 사용하고 "
            "[action]공격[/action] 행동을 합니다. 근력 이나 민첩 정치.</p>"
        )
        result = translator._canonicalize_translation(source, broken)
        self.assertIn("자신의 턴에 [action]공격[/action] 행동", result)
        self.assertIn("다른 경량 무기", result)
        self.assertIn("근력 또는 민첩 수정치", result)
        self.assertNotIn("복용", result)
        self.assertNotIn("민첩 정치", result)
        self.assertEqual(structure_tokens(source), structure_tokens(result))

    def test_core_fighter_row_repair_is_not_duplicated_and_javelin_is_idempotent(self):
        translator = self._translator(FakeGoogleClient(), {})
        source = (
            "<table><tr><th>Skill Proficiencies</th><td><em>Choose 2:</em> "
            "Acrobatics, Animal Handling, Athletics, History, Insight, Intimidation, "
            "Persuasion, Perception, or Survival</td></tr>"
            "<tr><th>Starting Equipment</th><td>8 Javelins</td></tr></table>"
        )
        broken = (
            "<table><tr><th>기술 숙련</th><td>곡예, 동물 조련, 운동 능력, 역사, 통찰력, "
            "위협, 설득력, 지각력, 생존력 <em>2개 선택:</em>곡예, 동물 조련, 운동, 역사, "
            "통찰, 위협, 설득, 지각 또는 생존</td></tr>"
            "<tr><th>시작 장비</th><td>투투투창 8개</td></tr></table>"
        )
        result = translator._canonicalize_translation(source, broken)
        again = translator._canonicalize_translation(source, result)
        self.assertEqual(result, again)
        self.assertEqual(result.count("곡예, 동물 조련, 운동, 역사, 통찰, 위협, 설득, 지각 또는 생존"), 1)
        self.assertIn("<em>2개 선택:</em>", result)
        self.assertIn("투창 8개", result)
        self.assertNotIn("투투", result)

    def test_find_familiar_layout_and_initiative_are_repaired(self):
        translator = self._translator(FakeGoogleClient(), {})
        source = (
            "<p><a href=\"x\">another Beast that has a Challenge Rating of 0</a>. "
            "Appearing in an unoccupied space within range, the familiar appears.</p>"
            "<p><strong><em>Combat.</em></strong> The familiar is an ally to you and "
            "your allies. It rolls its own Initiative and acts on its own turn. "
            "A familiar can’t attack, but it can take other actions as normal.</p>"
            "<p><strong><em>One Familiar Only.</em></strong> You can’t have more than "
            "one familiar at a time. If you cast this spell while you have a familiar, "
            "you instead cause it to adopt a new eligible form.</p>"
        )
        broken = (
            "<p><a href=\"x\">도전 지수 0인 다른 야수</a>사정거리 내 빈 공간에 나타납니다.</p>"
            "<p><strong><em>전투.</em></strong> 사역마는 당신과 당신의 아군에게 아군입니다. "
            "사역마는 스스로 행동 순서를 정하고 자신의 턴에 행동합니다. 사역마는 공격할 수 "
            "없지만, 다른 행동은 정상적으로 할 수 있습니다.</p>"
            "<p>한 번에 한 마리의 사역마 <strong><em>사역마는 하나만.</em></strong> 거느릴 수 "
            "있습니다. 사역마를 거느린 상태에서 이 주문을 시전하면 새로운 형태가 됩니다.</p>"
        )
        result = translator._canonicalize_translation(source, broken)
        self.assertIn("</a>. 사정거리", result)
        self.assertIn("스스로 주도권 굴림을 하고 자신의 턴에 행동합니다", result)
        self.assertIn(
            "<p><strong><em>사역마는 하나만.</em></strong> 한 번에 사역마를 한 마리만 거느릴 수 있습니다.",
            result,
        )
        self.assertNotIn("한 번에 한 마리의 사역마 <strong>", result)

    def test_war_bond_failure_and_fighter_term_are_semantically_normalized(self):
        translator = self._translator(FakeGoogleClient(), {})
        bond_source = (
            "The bond fails if another Fighter is bonded to the weapon or if the weapon "
            "is a magic item to which someone else is attuned."
        )
        bond = translator._canonicalize_translation(
            bond_source,
            "다른 전사가 해당 무기와 결속되어 있거나 다른 사람이 조율한 마법 아이템이면 "
            "결속이 끊어집니다.",
        )
        self.assertIn("결속 의식은 실패합니다", bond)
        self.assertNotIn("결속이 끊어집니다", bond)

        fighter = translator._canonicalize_translation(
            "When you reach certain Fighter levels, you gain more uses.",
            "전투기 레벨이 특정 수준에 도달하면 사용 횟수가 늘어납니다.",
        )
        self.assertIn("전사 레벨", fighter)
        self.assertNotIn("전투기 레벨", fighter)

    def test_original_preserved_fallback_is_not_cached_and_is_summarized(self):
        def transform(value, _request):
            # A genuinely malformed response: remove the structural anchor for
            # the protected mechanic. This must still fall back safely.
            return re.sub(
                r'<span[^>]*id="sm-mech-0"[^>]*>.*?</span>',
                "11",
                value,
                count=1,
            )

        client = FakeGoogleClient(transform)
        translator = self._translator(client, {})
        source = "<p>It takes 10 minutes to recover the Caltrops.</p>"

        result = translator._translate_batch_resilient([source])

        self.assertEqual(result[source], source)
        self.assertNotIn(source, translator.cache)
        translator._remember_translation(source, result[source])
        self.assertNotIn(source, translator.cache)
        self.assertEqual(translator.translation_summary()["status"], "partial")
        self.assertEqual(
            translator.translation_summary()["original_preserved_count"],
            1,
        )


    def test_identity_entries_are_not_loaded_from_persistent_cache(self):
        with tempfile.TemporaryDirectory() as td:
            cache_path = Path(td) / "cache.json"
            with mock.patch.dict(
                os.environ,
                {"SHEETMOVER_TRANSLATION_CACHE": str(cache_path)},
                clear=False,
            ):
                client = FakeGoogleClient()
                translator = self._translator(client, {})

            cache_path.write_text(
                json.dumps(
                    {
                        "fingerprint": translator._cache_fingerprint,
                        "entries": {
                            "Retry me": "Retry me",
                            "Hello": "안녕하세요",
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            loaded = translator._load_persistent_cache()
            self.assertNotIn("Retry me", loaded)
            self.assertEqual(loaded["Hello"], "안녕하세요")

    def test_unexpected_script_is_rejected(self):
        client = FakeGoogleClient(lambda _value, _request: "தமிழ்")
        translator = self._translator(client, {})
        with self.assertRaises(TranslationError):
            translator.translate("Humans are pioneers.")


    def test_mechanics_validation_ignores_numbers_inside_html_attributes(self):
        source = (
            '<a href="/search?type=0&cr=2&min=1">Challenge Rating 0 Beast</a>'
        )
        translated = (
            '<a href="/search?type=999&cr=888&min=777">도전 지수 0 야수</a>'
        )
        # Attribute integrity is structure_tokens' job; visible mechanics sees
        # only the CR 0 shown to the player.
        self.assertTrue(mechanics_compatible(source, translated))
        self.assertNotEqual(structure_tokens(source), structure_tokens(translated))

    def test_unseen_multi_mechanic_rule_uses_structural_locks_not_special_cases(self):
        def transform(value, request):
            self.assertEqual(request["mime_type"], "text/html")
            # Deliberately rewrite every protected span body. The restorer must
            # recover 9, 3d6, 120 and 17 from the source without knowing the rule.
            value = re.sub(
                r'(<span[^>]*id="sm-mech-\d+"[^>]*>).*?(</span>)',
                r'\g<1>999\g<2>',
                value,
            )
            value = value.replace("At ", "")
            value = value.replace("th level, roll ", "레벨에 ")
            value = value.replace(" and affect a creature within ", "을 굴리고 ")
            value = value.replace(" feet if its DC is ", "피트 이내 생명체에 적용합니다. DC는 ")
            value = value.replace(".", "입니다.")
            return value

        client = FakeGoogleClient(transform)
        translator = self._translator(client, {})
        source = "At 9th level, roll 3d6 and affect a creature within 120 feet if its DC is 17."
        result = translator.translate(source)

        self.assertTrue(mechanics_compatible(source, result), mechanics_mismatch_text(source, result))
        for token in ("9", "3d6", "120", "17"):
            self.assertIn(token, result)
        self.assertNotIn("999", result)


    def test_semantic_mechanical_atoms_keep_distance_and_hp_together(self):
        def transform(value, request):
            self.assertEqual(request["mime_type"], "text/html")
            self.assertIn(">30 feet</span>", value)
            self.assertEqual(value.count(">0 Hit Points</span>"), 2)
            value = re.sub(r">30 feet</span>", ">999 meters</span>", value)
            value = re.sub(r">0 Hit Points</span>", ">77 Hit Points</span>", value)
            value = value.replace("A spirit disappears when it drops to ", "영체는 ")
            value = value.replace(" and returns within ", "이 되면 사라지고 ")
            value = value.replace(". If it drops to ", " 이내에 돌아옵니다. 다시 ")
            value = value.replace(" again, repeat the effect.", "이 되면 효과를 반복합니다.")
            return value

        translator = self._translator(FakeGoogleClient(transform), {})
        source = (
            "<p>A spirit disappears when it drops to 0 Hit Points and returns within "
            "30 feet. If it drops to 0 Hit Points again, repeat the effect.</p>"
        )
        result = translator.translate(source)

        self.assertEqual(result.count("HP 0"), 2)
        self.assertIn("30피트", result)
        self.assertNotIn("999", result)
        self.assertNotIn("77", result)
        self.assertTrue(mechanics_compatible(source, result), mechanics_mismatch_text(source, result))

    def test_fragmented_fallback_preserves_atoms_when_cloud_drops_spans(self):
        def transform(value, request):
            if "sm-mech-" in value:
                # Simulate the live failure class where Cloud damages the
                # no-translate span. The structured/text-node attempt must then
                # fall back to translating prose fragments around the atoms.
                return re.sub(r'<span[^>]*id="sm-mech-\d+"[^>]*>.*?</span>', "", value)
            replacements = {
                "Creatures within": "범위 내 생명체는",
                "take": "받고",
                "damage and drop to": "피해를 받은 뒤",
            }
            core = value.strip()
            if core in replacements:
                leading = value[: len(value) - len(value.lstrip())]
                trailing = value[len(value.rstrip()):]
                return leading + replacements[core] + trailing
            return value.replace(".", "이 됩니다.")

        translator = self._translator(FakeGoogleClient(transform), {})
        source = "<p>Creatures within 60 feet take 4d10 damage and drop to 0 Hit Points.</p>"
        result = translator.translate(source)

        self.assertIn("60피트", result)
        self.assertIn("4d10", result)
        self.assertIn("HP 0", result)
        self.assertIn("범위 내 생명체", result)
        self.assertTrue(mechanics_compatible(source, result), mechanics_mismatch_text(source, result))
        self.assertFalse(translator.original_preserved)

    def test_wrong_same_number_metric_distance_is_rejected(self):
        self.assertFalse(
            mechanics_compatible(
                "The effect reaches 30 feet.",
                "효과는 30미터까지 도달합니다.",
            )
        )

    def test_mechanics_mismatch_diagnostic_names_missing_and_extra_tokens(self):
        detail = mechanics_mismatch_text(
            "Deal 2d8 damage within 30 feet.",
            "30피트 이내에서 3d8 피해를 줍니다. 추가 수치 99.",
        )
        self.assertIn("누락=2d8", detail)
        self.assertIn("추가=3d8", detail)
        self.assertIn("99", detail)

    def test_find_familiar_one_limit_repair_never_replaces_disappearance_block(self):
        translator = self._translator(FakeGoogleClient(), {})
        source = (
            "<p><strong><em>Disappearance of the Familiar.</em></strong> "
            "When the familiar drops to 0 Hit Points, it disappears. As a Magic "
            "action, it can reappear within 30 feet. Whenever it drops to 0 Hit "
            "Points, it leaves carried objects behind.</p>\r\n"
            "<p><strong><em>One Familiar Only.</em></strong> You can’t have more "
            "than one familiar at a time. If you cast this spell while you have "
            "a familiar, it adopts a new eligible form.</p>"
        )
        translated = (
            "<p><strong><em>사역마의 소멸.</em></strong> 사역마의 HP가 0이 되면 "
            "사라집니다. 마법 행동으로 30피트 이내에 다시 나타날 수 있습니다. "
            "사역마의 HP가 0이 되면 운반하던 물건을 남깁니다.</p>\r\n"
            "<p><strong><em>사역마는 하나만.</em></strong> 사역마를 여러 마리 "
            "둘 수 없습니다. 다시 시전하면 형태가 바뀝니다.</p>"
        )

        result = translator._canonicalize_translation(source, translated)

        self.assertEqual(result.count("HP가 0"), 2)
        self.assertIn("30피트", result)
        self.assertIn("<strong><em>사역마의 소멸.</em></strong>", result)
        self.assertIn(
            "<strong><em>사역마는 하나만.</em></strong> 한 번에 사역마를 한 마리만",
            result,
        )
        self.assertTrue(mechanics_compatible(source, result), mechanics_mismatch_text(source, result))

    def test_structured_postprocess_cannot_corrupt_already_safe_mechanics(self):
        translator = self._translator(FakeGoogleClient(), {})
        source = "<p>A spirit returns within 30 feet at 0 Hit Points.</p>"
        translated = "<p>영체는 30피트 이내에 돌아오며 HP가 0입니다.</p>"

        with mock.patch.object(
            translator,
            "_canonicalize_translation",
            return_value="<p>영체는 돌아옵니다.</p>",
        ):
            result = translator._validate_structured_candidate(source, translated)

        self.assertEqual(result, translated)
        self.assertTrue(any("후처리 보정" in warning for warning in translator.warnings))

    def test_partially_preserved_structured_value_is_never_success_cached(self):
        translator = self._translator(FakeGoogleClient(), {})
        source = "<p>First paragraph.</p><p>Second paragraph.</p>"

        def fail_batch(_sources, forced_terms_by_source=None):
            raise TranslationError(
                "구조화 번역 중 숫자·주사위식·Roll20 수식이 바뀌었습니다"
            )

        def chunk_fallback(chunk):
            if "First paragraph" in chunk:
                translator._warn_original_preserved(
                    chunk,
                    "테스트용 문단 fallback",
                )
                return chunk
            return chunk.replace("Second paragraph.", "두 번째 문단입니다.")

        with mock.patch.object(translator, "_google_translate_batch", side_effect=fail_batch), mock.patch.object(
            translator,
            "_translate_structured_chunk",
            side_effect=chunk_fallback,
        ):
            result = translator._translate_structured(source)

        self.assertIn("First paragraph.", result)
        self.assertIn("두 번째 문단입니다.", result)
        self.assertIn(source, translator._uncacheable_sources)
        translator._remember_translation(source, result)
        self.assertNotIn(source, translator.cache)

    def test_live_rope_condition_parentheses_are_repaired_by_source_pattern(self):
        translator = self._translator(FakeGoogleClient(), {})
        source = (
            "You can bind an unwilling creature with the Rope only if the creature "
            "has the Grappled, Incapacitated, or Restrained condition. If the "
            "creature’s legs are bound, the creature has the Restrained condition "
            "until it escapes. Escaping the Rope requires the creature to make a "
            "successful DC 15 Dexterity (Acrobatics) check as an action."
        )
        broken = (
            "밧줄 사용하면 생명체가 붙잡힘), 행동 불능) 또는 구속) 상태일 때만 "
            "원치 않는 생명체 묶을 수 있습니다. 생명체의 다리가 묶여 있으면 구속 "
            "상태가 됩니다. 밧줄 탈출하려면 생명체 행동으로 민첩 (곡예) 판정"
            "(DC 15)에 성공해야 합니다."
        )

        result = translator._canonicalize_translation(source, broken)

        self.assertIn("붙잡힘, 행동 불능 또는 구속 상태", result)
        self.assertIn("밧줄을 사용하면", result)
        self.assertIn("생명체를 묶을 수", result)
        self.assertIn("밧줄에서 탈출하려면", result)
        self.assertNotIn("붙잡힘)", result)

    def test_live_thunderwave_word_order_is_repaired_without_spell_name_check(self):
        translator = self._translator(FakeGoogleClient(), {})
        source = (
            "You unleash a wave. Each creature in a 15-foot Cube originating from "
            "you makes a Constitution saving throw. On a failed save, it is pushed "
            "10 feet away from you."
        )
        broken = (
            "파동을 방출합니다. 당신에게서 시작되는 정육면체 15피트 정육면체 내의 "
            "모든 생명체 건강 내성 굴림 합니다. 실패하면 사용자로부터 10피트 "
            "밀려납니다."
        )

        result = translator._canonicalize_translation(source, broken)

        self.assertIn(
            "당신에게서 시작되는 15피트 정육면체 내의 각 생명체는 건강 내성 굴림을 합니다.",
            result,
        )
        self.assertIn("당신으로부터 10피트", result)
        self.assertNotIn("정육면체 15피트 정육면체", result)

    def test_common_spellcasting_templates_repair_live_noun_piles(self):
        translator = self._translator(FakeGoogleClient(), {})
        source = (
            "The number of spells on your list increases as you gain Fighter levels, "
            "as shown in the Prepared Spells column of the Eldritch Knight "
            "Spellcasting table. Intelligence is your spellcasting ability for your "
            "Wizard spells. You can use an Arcane Focus as a Spellcasting Focus for "
            "your Wizard spells."
        )
        broken = (
            "전사 레벨이 오를수록 주문 수가 증가하며, 이는 엘드리치 나이트 주문시전 "
            "준비된 주문 열 &#39; 열에 표시됩니다. 지능 마법사 주문의 주문시전 "
            "능력치 입니다. 마법사는 비전 매개체를 주문 주문시전 매개체로 사용할 수 "
            "있습니다."
        )

        result = translator._canonicalize_translation(source, broken)

        self.assertIn("엘드리치 나이트 주문시전 표의 준비된 주문 열", result)
        self.assertIn("지능은 당신의 위저드 주문의 주문시전 능력치입니다.", result)
        self.assertIn("위저드 주문의 주문시전 매개체로 비전 매개체를 사용할 수 있습니다.", result)
        self.assertNotIn("열 &#39; 열", result)
        self.assertNotIn("주문 주문시전", result)

    def test_nested_link_punctuation_and_healer_mechanics_are_cleaned(self):
        translator = self._translator(FakeGoogleClient(), {})
        race_source = (
            '<p>If your campaign uses rules from <em><a href="x">Player’s Handbook</a></em>, '
            "your Dungeon Master might allow them.</p>"
        )
        race_broken = (
            '<p>캠페인에서 규칙을 사용하는 경우 <em><a href="x">플레이어 핸드북</a></em>'
            "던전 마스터가 허용할 수 있습니다.</p>"
        )
        self.assertIn(
            "</a></em>, 던전 마스터",
            translator._canonicalize_translation(race_source, race_broken),
        )

        kit_source = (
            "As a Utilize action, you can expend one of its uses to stabilize an "
            "Unconscious creature that has 0 Hit Points without needing to make a "
            "Wisdom (Medicine) check."
        )
        kit_broken = (
            "활용 행동으로, 지혜 (의학) 판정 없이도 사용 횟수 1 HP 0 인 의식 불명 "
            "생명체 안정시킬 수 있습니다."
        )
        kit = translator._canonicalize_translation(kit_source, kit_broken)
        self.assertIn("사용 횟수 1회를 소모하여", kit)
        self.assertIn("HP가 0인 의식 불명 생명체를 안정화", kit)
        self.assertTrue(mechanics_compatible(kit_source, kit), mechanics_mismatch_text(kit_source, kit))


    def test_legacy_structured_partial_result_is_never_success_cached(self):
        translator = self._translator(FakeGoogleClient(), {})
        translator._legacy_test_client = True
        translator.cache = {}
        source = (
            "<table><tr><td>Fighter Level</td>"
            "<td>Spells Prepared</td></tr></table>"
        )
        partial = (
            "<table><tr><td>전사 레벨</td>"
            "<td>Spells Prepared</td></tr></table>"
        )

        def fake_text_nodes(value):
            self.assertEqual(value, source)
            translator._warn_original_preserved(
                "Spells Prepared",
                "번역 조각 재시도 실패",
            )
            return partial

        with mock.patch.object(
            translator,
            "_translate_structured_text_nodes",
            side_effect=fake_text_nodes,
        ), mock.patch.object(translator, "_save_persistent_cache"):
            result = translator.translate(source)

        self.assertEqual(result, partial)
        self.assertNotIn(source, translator.cache)
        self.assertIn(source, translator._uncacheable_sources)

    def test_structured_postprocess_never_masks_explicit_original_fallback(self):
        translator = self._translator(FakeGoogleClient(), {})
        source = (
            "<table><tr><td>Fighter Level</td>"
            "<td>Spells Prepared</td></tr></table>"
        )
        raw = (
            "<table><tr><td>전사 레벨</td>"
            "<td>Spells Prepared</td></tr></table>"
        )
        translator._warn_original_preserved(
            "Spells Prepared",
            "번역 조각 재시도 실패",
        )

        result = translator._canonicalize_structured_safely(source, raw)

        self.assertIn("전사 레벨", result)
        self.assertIn("Spells Prepared", result)
        self.assertNotIn("준비된 주문", result)
        self.assertEqual(translator.translation_summary()["status"], "partial")



    def test_semantic_rule_anchor_rejects_silent_exception_and_addition_loss(self):
        translator = self._translator(FakeGoogleClient(), {})
        source = (
            "<p>On your turn, you can take one additional action, except the "
            "[action]Magic[/action] action.</p>"
        )
        broken = "<p>당신의 차례에 [action]마법[/action] 행동.</p>"

        with self.assertRaisesRegex(TranslationError, "규칙 의미 앵커"):
            translator._validate_structured_candidate(source, broken)

        good = (
            "<p>자신의 차례에 [action]마법[/action] 행동을 제외하고 "
            "추가 행동을 하나 할 수 있습니다.</p>"
        )
        self.assertEqual(
            translator._validate_structured_candidate(source, good),
            good,
        )

    def test_semantic_rule_anchor_is_markup_agnostic_for_equivalent_rules(self):
        translator = self._translator(FakeGoogleClient(), {})
        plain = (
            "<p>On your turn, you can take one additional action, except the "
            "Magic action.</p>"
        )
        tagged = (
            "<p>On your turn, you can take one additional action, except the "
            "[action]Magic[/action] action.</p>"
        )
        self.assertEqual(
            translator._rule_equivalence_key(plain),
            translator._rule_equivalence_key(tagged),
        )

    def test_semantic_rule_anchor_covers_common_rule_relations_without_numbers(self):
        translator = self._translator(FakeGoogleClient(), {})
        cases = [
            (
                "You can use this feature instead of making an attack.",
                "공격하는 대신 이 특성을 사용할 수 있습니다.",
            ),
            (
                "You can use the weapon unless you are Incapacitated.",
                "행동 불능 상태가 아닌 한 이 무기를 사용할 수 있습니다.",
            ),
            (
                "The score must be at least 13.",
                "능력치는 최소 13이어야 합니다.",
            ),
        ]
        for source, translated in cases:
            with self.subTest(source=source):
                self.assertEqual(
                    translator._missing_semantic_rule_anchors(source, translated),
                    [],
                )


if __name__ == "__main__":
    unittest.main()
