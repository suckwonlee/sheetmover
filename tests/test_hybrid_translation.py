import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sheet_mover.hybrid_translator as hybrid_module
from sheet_mover.hybrid_translator import MODEL_NAME, TranslationError, Translator
from sheet_mover.semantic_validator import critical_codes
from sheet_mover.semantic_preprocessor import build_template, SemanticPreprocessError
from sheet_mover.translation_render import strip_dnd_display_tags
from sheet_mover.translation_units import collect_translation_units, fixed_label_translation


class _Translation:
    def __init__(self, text):
        self.translated_text = text


class _GoogleResponse:
    def __init__(self, values):
        self.glossary_translations = [_Translation(value) for value in values]
        self.translations = []


class FakeGoogleClient:
    def __init__(self, transform=None):
        self.transform = transform or (lambda value, _request: value)
        self.requests = []

    def translate_text(self, request=None, timeout=None):
        self.requests.append((request, timeout))
        return _GoogleResponse([
            self.transform(value, request)
            for value in request["contents"]
        ])


class FakeReviewClient:
    def __init__(self, models=None, replies=None, chat_error=None):
        self.models = list(models if models is not None else [MODEL_NAME])
        self.replies = list(replies or [])
        self.chat_error = chat_error
        self.chats = []
        self.list_calls = 0

    def list(self):
        self.list_calls += 1
        return {"models": [{"model": name} for name in self.models]}

    def chat(self, **kwargs):
        self.chats.append(kwargs)
        if self.chat_error is not None:
            raise self.chat_error
        payload = self.replies.pop(0) if self.replies else {
            "decision": "keep",
            "translation": kwargs["messages"][-1]["content"],
            "note": "",
        }
        return {"message": {"content": json.dumps(payload, ensure_ascii=False)}}


class HybridTranslationTests(unittest.TestCase):
    def _translator(self, google=None, reviewer=None, glossary=None, cache_only=False):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        glossary_path = root / "glossary.json"
        glossary_path.write_text(json.dumps(glossary or {}, ensure_ascii=False), encoding="utf-8")
        patcher = mock.patch.dict(
            os.environ,
            {
                "SHEETMOVER_TRANSLATION_CACHE": str(root / "google-cache.json"),
                "SHEETMOVER_REVIEW_CACHE": str(root / "review-cache.json"),
            },
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        return Translator(
            glossary_path=glossary_path,
            client=google or FakeGoogleClient(),
            project_id="sheet-mover-test",
            review_client=reviewer or FakeReviewClient(),
            cache_only=cache_only,
        )

    def test_ollama_model_is_mandatory_before_google(self):
        google = FakeGoogleClient()
        reviewer = FakeReviewClient(models=[])
        translator = self._translator(google, reviewer)
        with self.assertRaisesRegex(TranslationError, "필수 Ollama 모델"):
            translator.translate("Normal translation")
        self.assertEqual(google.requests, [])

    def test_action_surge_critical_omission_forces_second_review(self):
        source = "On your turn, you can take one additional action, except the Magic action."
        google = "당신의 차례에 마법 행동."
        repaired = "당신의 차례에 마법 행동을 제외하고 추가 행동을 하나 할 수 있습니다."
        reviewer = FakeReviewClient(replies=[
            {"decision": "keep", "translation": google},
            {"decision": "replace", "translation": repaired},
        ])
        translator = self._translator(reviewer=reviewer)
        translator._ensure_ollama_ready()
        result = translator._review_translation_if_needed(source, google)
        self.assertEqual(result, repaired)
        self.assertEqual(translator.review_stats["forced_retries"], 1)
        self.assertEqual(translator.review_stats["ollama_calls"], 2)

    def test_unresolved_critical_omission_becomes_partial_source_fallback(self):
        source = "On your turn, you can take one additional action, except the Magic action."
        google = "당신의 차례에 마법 행동."
        reviewer = FakeReviewClient(replies=[
            {"decision": "keep", "translation": google},
            {"decision": "keep", "translation": google},
        ])
        translator = self._translator(reviewer=reviewer)
        translator._ensure_ollama_ready()
        self.assertEqual(translator._review_translation_if_needed(source, google), source)
        self.assertEqual(translator.translation_summary()["status"], "partial")
        self.assertEqual(translator.review_stats["critical_fallbacks"], 1)



    def test_long_english_name_is_not_treated_as_untranslated_prose(self):
        source = "Farmer Ability Score Improvements"
        self.assertEqual(critical_codes(source, source), [])

    def test_long_english_google_fallback_cannot_be_complete(self):
        source = "You can move up to 30 feet and must stop before entering another creature's space."
        google = source
        reviewer = FakeReviewClient(replies=[
            {"decision": "keep", "translation": google},
            {"decision": "keep", "translation": google},
        ])
        translator = self._translator(reviewer=reviewer)
        translator._ensure_ollama_ready()
        result = translator._review_translation_if_needed(source, google)
        self.assertEqual(result, source)
        self.assertEqual(translator.translation_summary()["status"], "partial")
        self.assertTrue(any("korean-missing-prose" in x for x in critical_codes(source, google)))

    def test_negation_loss_is_critical(self):
        source = "A familiar can't attack, but it can take other actions as normal."
        bad = "사역마는 공격할 수 있으며 다른 행동도 정상적으로 할 수 있습니다."
        self.assertTrue(any("missing-negation" in code for code in critical_codes(source, bad)))

    def test_count_and_turn_limit_loss_is_critical(self):
        source = "Starting at level 17, you can use it twice before a rest but only once on a turn."
        bad = "17레벨부터 휴식 전에 사용할 수 있습니다."
        codes = critical_codes(source, bad)
        self.assertTrue(any("missing-twice" in code for code in codes))
        self.assertTrue(any("missing-once-per-turn" in code for code in codes))

    def test_segment_validation_prevents_later_sentence_from_hiding_loss(self):
        source = (
            "You can take one additional action, except the Magic action. "
            "Another feature excludes magic items."
        )
        bad = "추가 행동을 하나 할 수 있습니다. 다른 특성은 마법 아이템을 제외합니다."
        codes = critical_codes(source, bad)
        self.assertTrue(any("missing-exception" in code and code.startswith("s1:") for code in codes))

    def test_structured_review_only_rewrites_risky_block(self):
        source = (
            "<p>Ordinary harmless description.</p>"
            "<p>You can take one additional action, except the [action]Magic[/action] action.</p>"
        )
        google = (
            "<p>평범한 설명입니다.</p>"
            "<p>추가 행동을 하나 할 수 있습니다. [action]마법[/action] 행동.</p>"
        )
        repaired_block = "<p>[action]마법[/action] 행동을 제외하고 추가 행동을 하나 할 수 있습니다.</p>"
        reviewer = FakeReviewClient(replies=[
            {"decision": "replace", "translation": repaired_block}
        ])
        translator = self._translator(reviewer=reviewer)
        translator._ensure_ollama_ready()
        result = translator._review_translation_if_needed(source, google)
        self.assertTrue(result.startswith("<p>평범한 설명입니다.</p>"))
        self.assertIn("제외하고 추가 행동", result)
        self.assertEqual(len(reviewer.chats), 1)

    def test_review_cache_is_separate_and_reused(self):
        source = "You can use this feature instead of making an attack."
        google = "공격하는 대신 이 특성을 사용할 수 있습니다."
        reviewer = FakeReviewClient(replies=[{"decision": "keep", "translation": google}])
        translator = self._translator(reviewer=reviewer)
        translator._ensure_ollama_ready()
        first = translator._review_translation_if_needed(source, google)
        second = translator._review_translation_if_needed(source, google)
        self.assertEqual(first, google)
        self.assertEqual(second, google)
        self.assertEqual(len(reviewer.chats), 1)
        self.assertEqual(translator.review_stats["review_cache_hits"], 1)

    def test_google_cache_keeps_first_pass_not_ollama_replacement(self):
        source = "You can use this feature instead of making an attack."
        google_value = "공격하는 대신 이 특성을 사용할 수 있습니다."
        final = "공격 대신 이 기능을 사용할 수 있습니다."
        google = FakeGoogleClient(lambda _value, _request: google_value)
        reviewer = FakeReviewClient(replies=[{"decision": "replace", "translation": final}])
        translator = self._translator(google=google, reviewer=reviewer)
        self.assertEqual(translator.translate(source), final)
        self.assertEqual(translator.cache[source], google_value)
        self.assertNotEqual(translator.cache[source], final)

    def test_equivalent_rule_is_grouped_before_translation_and_reuses_safe_copy(self):
        plain = (
            "<p>You can push yourself beyond your normal limits for a moment. "
            "On your turn, you can take one additional action, except the Magic action.</p>"
        )
        tagged = plain.replace("Magic", "[action]Magic[/action]")
        character = {
            "features": [{"kind": "class_feature", "name": "Action Surge", "original_name": "Action Surge", "description": plain}],
            "actions": [{"kind": "action:class", "name": "Action Surge", "original_name": "Action Surge", "description": tagged}],
        }
        korean_core = (
            "잠시 동안 평소의 한계를 뛰어넘을 수 있습니다. "
            "자신의 차례에 마법 행동을 제외하고 추가 행동을 하나 할 수 있습니다."
        )
        korean_plain = f"<p>{korean_core}</p>"
        google = FakeGoogleClient(lambda _value, _request: korean_core)
        reviewer = FakeReviewClient(replies=[{"decision": "keep", "translation": korean_plain}])
        translator = self._translator(google=google, reviewer=reviewer)
        translator._prepare_equivalent_groups(character)
        first = translator.translate(plain)
        request_count = len(google.requests)
        second = translator.translate(tagged)
        self.assertEqual(first, korean_plain)
        self.assertIn("[action]마법[/action]", second)
        self.assertEqual(len(google.requests), request_count)
        self.assertEqual(translator.review_stats["deterministic_repairs"], 1)


    def test_equivalent_batch_does_not_translate_canonical_twice(self):
        plain = (
            "<p>You can push yourself beyond your normal limits. "
            "You can take one additional action, except the Magic action.</p>"
        )
        tagged = plain.replace("Magic", "[action]Magic[/action]")
        character = {
            "features": [{"kind": "class_feature", "name": "Action Surge", "original_name": "Action Surge", "description": plain}],
            "actions": [{"kind": "action:class", "name": "Action Surge", "original_name": "Action Surge", "description": tagged}],
        }
        korean_core = "평소의 한계를 뛰어넘을 수 있습니다. 마법 행동을 제외하고 추가 행동을 하나 할 수 있습니다."
        korean = f"<p>{korean_core}</p>"
        google = FakeGoogleClient(lambda _value, _request: korean_core)
        reviewer = FakeReviewClient(replies=[{"decision": "keep", "translation": korean}])
        translator = self._translator(google=google, reviewer=reviewer)
        translator._prepare_equivalent_groups(character)
        resolved, failed = translator._translate_batch_once([plain, tagged])
        self.assertEqual(failed, [])
        self.assertIn("[action]마법[/action]", resolved[tagged])
        self.assertEqual(len(google.requests), 1)

    def test_ambiguous_tag_projection_refuses_guess(self):
        plain = "<p>Magic affects Magic action rules and you gain one additional action.</p>"
        tagged = "<p>Magic affects [action]Magic[/action] action rules and you gain one additional action.</p>"
        korean = "<p>마법은 마법 행동 규칙에 영향을 주며 추가 행동을 하나 얻습니다.</p>"
        translator = self._translator()
        self.assertIsNone(translator._project_equivalent_translation(plain, korean, tagged))

    def test_llm_cannot_change_dice_or_tags(self):
        translator = self._translator()
        source = "<p>Deal 2d8 damage except with the [action]Magic[/action] action.</p>"
        google = "<p>[action]마법[/action] 행동을 제외하고 2d8 피해를 줍니다.</p>"
        bad = "<p>마법 행동을 제외하고 3d8 피해를 줍니다.</p>"
        safe, reason = translator._candidate_is_safe(source, google, bad)
        self.assertFalse(safe)
        self.assertTrue("태그" in reason or "기계적" in reason)

    def test_review_request_uses_low_thinking_and_schema_grounding(self):
        source = "You can use this feature instead of making an attack."
        google = "공격하는 대신 이 특성을 사용할 수 있습니다."
        reviewer = FakeReviewClient(replies=[{"decision": "keep", "translation": google}])
        translator = self._translator(reviewer=reviewer)
        translator._ensure_ollama_ready()
        translator._review_translation_if_needed(source, google)
        call = reviewer.chats[0]
        self.assertEqual(call["think"], "low")
        self.assertIsInstance(call["format"], dict)
        self.assertIn("JSON Schema", call["messages"][0]["content"])


    def test_known_valid_korean_equivalents_do_not_become_critical(self):
        cases = [
            (
                "Once you have bonded a weapon to yourself, you can't be disarmed of that weapon unless you have the Incapacitated condition.",
                "일단 무기를 자신에게 결속시키면, 행동 불능 상태가 아닌 이상 그 무기를 빼앗길 수 없습니다.",
            ),
            (
                "When you make the extra attack of the Light property, you can make it as part of the Attack action instead of as a Bonus Action.",
                "경량 속성의 추가 공격을 할 때, 추가 행동이 아닌 공격 행동의 일부로 할 수 있습니다.",
            ),
            (
                "You have Disadvantage on attack rolls with a Heavy weapon if it's a Melee weapon and your Strength score isn't at least 13.",
                "근접 중량 무기를 사용할 때 근력 능력치가 13 미만이면 공격 굴림에 불리점을 받습니다.",
            ),
            (
                "Your Hit Point maximum increases by an amount equal to twice your character level when you gain this feat.",
                "이 특기를 얻으면 최대 HP가 캐릭터 레벨의 두 배만큼 증가합니다.",
            ),
        ]
        for source, translated in cases:
            with self.subTest(source=source):
                self.assertEqual(critical_codes(source, translated), [])

    def test_final_render_strips_pseudo_tags_without_changing_internal_text(self):
        internal = "[action]마법[/action] 행동을 제외하고 [condition]행동 불능[/condition] 상태를 확인합니다."
        rendered = strip_dnd_display_tags(internal)
        self.assertEqual(rendered, "마법 행동을 제외하고 행동 불능 상태를 확인합니다.")
        self.assertIn("[action]", internal)


    def test_full_action_surge_character_flow_reuses_feature_translation(self):
        plain = (
            "<p>You can push yourself beyond your normal limits for a moment. "
            "On your turn, you can take one additional action, except the Magic action.</p>\r\n"
            "<p>Once you use this feature, you can’t do so again until you finish a Short or Long Rest. "
            "Starting at level 17, you can use it twice before a rest but only once on a turn.</p>"
        )
        tagged = (
            "<p>You can push yourself beyond your normal limits for a moment. "
            "On your turn, you can take one additional action, except the [action]Magic[/action] action.</p>\r\n"
            "<p>Once you use this feature, you can&rsquo;t do so again until you finish a Short or Long Rest. "
            "Starting at level 17, you can use it twice before a rest but only once on a turn.</p>"
        )
        korean = (
            "<p>잠시 동안 평소의 한계를 뛰어넘을 수 있습니다. "
            "자신의 차례에 마법 행동을 제외하고 추가 행동을 하나 할 수 있습니다.</p>\r\n"
            "<p>이 기능을 한 번 사용하면 짧은 휴식이나 긴 휴식을 마칠 때까지 다시 사용할 수 없습니다. "
            "17레벨부터는 휴식 전에 두 번 사용할 수 있지만, 턴당 한 번만 사용할 수 있습니다.</p>"
        )
        translator = self._translator(cache_only=True)
        translator.cache["Action Surge"] = "행동 쇄도"
        translator.cache[plain] = korean
        character = {
            "features": [{"kind": "class_feature", "name": "Action Surge", "original_name": "Action Surge", "description": plain}],
            "actions": [{"kind": "action:class", "name": "Action Surge", "original_name": "Action Surge", "description": tagged}],
        }
        result = translator.translate_character(character)
        self.assertEqual(result["features"][0]["description"], korean)
        action_text = result["actions"][0]["description"]
        self.assertIn("[action]마법[/action] 행동을 제외하고 추가 행동을 하나", action_text)
        self.assertNotEqual(action_text, tagged)
        self.assertEqual(result["translation_summary"]["status"], "complete")
        self.assertEqual(result["translation_summary"]["original_preserved_count"], 0)
        self.assertGreaterEqual(result["translation_summary"]["review_stats"]["deterministic_repairs"], 1)

    def test_preserved_summary_tracks_final_state_not_retry_count(self):
        source = "On your turn, you can take one additional action, except the Magic action."
        translator = self._translator()
        translator._warn_original_preserved(source, "first failure")
        translator._warn_original_preserved(source, "second failure")
        summary = translator.translation_summary()
        self.assertEqual(summary["original_preserved_count"], 1)
        self.assertEqual(summary["original_preserved"][0]["reason"], "second failure")
        translator._mark_source_recovered(source)
        summary = translator.translation_summary()
        self.assertEqual(summary["status"], "complete")
        self.assertEqual(summary["original_preserved_count"], 0)


    def test_cache_only_miss_blocks_google_without_request(self):
        google = FakeGoogleClient(lambda _value, _request: "호출되면 안 됩니다")
        translator = self._translator(google=google, cache_only=True)
        with self.assertRaisesRegex(TranslationError, "cache-only 모드"):
            translator.translate("This sentence is intentionally absent from the Google cache.")
        self.assertEqual(google.requests, [])
        self.assertEqual(translator.review_stats["google_api_calls"], 0)
        self.assertGreaterEqual(translator.review_stats["cache_only_misses"], 1)

    def test_cache_only_skips_remote_glossary_preflight(self):
        translator = self._translator(cache_only=True)
        with mock.patch.object(
            Translator.__mro__[1],
            "_ensure_remote_glossary",
            side_effect=AssertionError("Google glossary preflight must not run"),
        ):
            translator._ensure_remote_glossary()
        self.assertTrue(translator._glossary_preflight_done)

    def test_cache_only_summary_is_explicit(self):
        translator = self._translator(cache_only=True)
        pipeline = translator.translation_summary()["translation_pipeline"]
        self.assertTrue(pipeline["cache_only"])
        self.assertEqual(pipeline["build"], "2026-10-06-v16.7-cache-precedence")

    def test_translation_summary_contains_exact_build_identity(self):
        translator = self._translator()
        pipeline = translator.translation_summary()["translation_pipeline"]
        self.assertEqual(pipeline["build"], "2026-10-06-v16.7-cache-precedence")
        self.assertFalse(pipeline["cache_only"])
        self.assertRegex(pipeline["code_sha256"], r"^[0-9a-f]{64}$")


    def test_translation_units_use_normalized_category_not_html_guessing(self):
        character = {
            "features": [{
                "kind": "racial_trait",
                "name": "Adaptable",
                "original_name": "Adaptable",
                "description": "<p>Choose one skill proficiency.</p>",
            }],
            "actions": [{
                "kind": "action:class",
                "name": "Action Surge",
                "original_name": "Action Surge",
                "description": "<p>Take one additional action.</p>",
            }],
            "skill_proficiencies": [{
                "name": "Perception",
                "original_name": "Perception",
                "type": "proficiency",
            }],
        }
        units = collect_translation_units(character)
        self.assertTrue(any(u.category == "racial_feature" and u.field == "description" for u in units))
        self.assertTrue(any(u.category == "action:class" and u.field == "description" for u in units))
        self.assertTrue(any(u.category == "skill_proficiency" and u.policy == "fixed_label" for u in units))

    def test_fixed_skill_and_language_labels_do_not_need_google(self):
        self.assertEqual(fixed_label_translation("Perception"), "지각")
        self.assertEqual(fixed_label_translation("Common"), "공용어")
        google = FakeGoogleClient(lambda _value, _request: "호출되면 안 됩니다")
        translator = self._translator(google=google, cache_only=True)
        character = {
            "skill_proficiencies": [{
                "name": "Perception",
                "original_name": "Perception",
                "type": "proficiency",
            }],
            "languages": ["Common"],
        }
        result = translator.translate_character(character)
        self.assertEqual(result["skill_proficiencies"][0]["name"], "지각")
        self.assertEqual(result["languages"], ["공용어"])
        self.assertEqual(google.requests, [])

    def test_structured_semantic_path_sends_plain_text_without_markup(self):
        source = (
            "<p>On your turn, you can take one additional action, "
            "except the [action]Magic[/action] action.</p>"
        )
        korean_plain = "자신의 차례에 마법 행동을 제외하고 추가 행동을 하나 할 수 있습니다."

        def transform(value, request):
            self.assertEqual(request["mime_type"], "text/plain")
            self.assertNotIn("<p>", value)
            self.assertNotIn("[action]", value)
            if "except the Magic action" in value:
                return korean_plain
            if value.strip() == "Action Surge":
                return "행동 쇄도"
            return value

        google = FakeGoogleClient(transform)
        translator = self._translator(google=google)
        character = {
            "features": [{
                "kind": "class_feature",
                "name": "Action Surge",
                "original_name": "Action Surge",
                "description": source,
            }],
        }
        result = translator.translate_character(character)
        translated = result["features"][0]["description"]
        self.assertEqual(
            translated,
            "<p>자신의 차례에 [action]마법[/action] 행동을 제외하고 추가 행동을 하나 할 수 있습니다.</p>",
        )
        stats = result["translation_summary"]["review_stats"]
        self.assertGreaterEqual(stats["semantic_html_documents"], 1)
        self.assertEqual(stats["google_html_requests"], 0)
        self.assertGreaterEqual(stats["google_plain_requests"], 1)

    def test_structured_semantic_path_preserves_inline_html_template(self):
        source = "<p><strong>Cantrips.</strong> You know two cantrips.</p>"
        translations = {
            "Cantrips.": "소마법.",
            "You know two cantrips.": "소마법 두 개를 알고 있습니다.",
        }
        google = FakeGoogleClient(lambda value, _request: translations.get(value.strip(), value))
        translator = self._translator(google=google)
        character = {
            "features": [{
                "kind": "class_feature",
                "name": "Spellcasting",
                "original_name": "Spellcasting",
                "description": source,
            }],
        }
        result = translator.translate_character(character)
        translated = result["features"][0]["description"]
        self.assertIn("<strong>소마법.</strong>", translated)
        self.assertIn("소마법 두 개를 알고 있습니다.", translated)
        self.assertEqual(result["translation_summary"]["review_stats"]["google_html_requests"], 0)

    def test_review_prompt_receives_semantic_unit_context(self):
        source = "You can use this feature instead of making an attack."
        google_text = "공격하는 대신 이 특성을 사용할 수 있습니다."
        reviewer = FakeReviewClient(replies=[{"decision": "keep", "translation": google_text}])
        translator = self._translator(reviewer=reviewer)
        character = {
            "features": [{
                "kind": "class_feature",
                "name": "Tactical Choice",
                "original_name": "Tactical Choice",
                "description": source,
            }],
        }
        translator._prepare_translation_units(character)
        messages = translator._review_messages(source, google_text, ["instead"], False)
        payload = json.loads(messages[-1]["content"])
        self.assertEqual(payload["unit_context"][0]["category"], "class_feature")
        self.assertEqual(payload["unit_context"][0]["item_name"], "Tactical Choice")
        self.assertEqual(payload["unit_context"][0]["field"], "description")

    def test_fixed_label_policy_does_not_leak_into_other_semantic_roles(self):
        source = "Carpenter's Tools"
        translator = self._translator(glossary={source: "목수 도구"})
        character = {
            "equipment": [{
                "name": source,
                "original_name": source,
            }],
            "proficiencies": [source],
        }
        translator._prepare_translation_units(character)
        contexts = translator._translation_units_by_source[source]
        self.assertTrue(any(row.policy == "fixed_label" for row in contexts))
        self.assertTrue(any(row.policy == "name" for row in contexts))
        self.assertIsNone(translator._deterministic_unit_translation(source))

    def test_unknown_dnd_inline_atom_uses_conservative_text_node_fallback(self):
        source = (
            "<p>You can draw the [items]Dagger[/items] and make one attack.</p>"
        )
        fallback = (
            "<p>당신은 [items]단검[/items]을 뽑고 공격을 한 번 할 수 있습니다.</p>"
        )
        google = FakeGoogleClient(lambda _value, _request: "호출되면 안 됩니다")
        translator = self._translator(google=google)
        character = {
            "features": [{
                "kind": "class_feature",
                "name": "Knife Trick",
                "original_name": "Knife Trick",
                "description": source,
            }],
        }
        translator._prepare_translation_units(character)
        with mock.patch.object(
            Translator.__mro__[1],
            "_translate_structured_text_nodes",
            return_value=fallback,
        ) as text_node_fallback:
            translated = translator._translate_structured(source)
        self.assertEqual(translated, fallback)
        text_node_fallback.assert_called_once_with(translator, source)
        self.assertEqual(google.requests, [])
        self.assertEqual(translator.review_stats["google_html_requests"], 0)
        self.assertEqual(translator.review_stats["semantic_preprocess_fallbacks"], 1)



    def test_cache_only_auto_seed_requires_character_source_id(self):
        translator = self._translator(cache_only=True)
        with mock.patch.object(
            translator,
            "_cache_only_seed_candidates",
            side_effect=AssertionError("ID 없는 부분 객체가 로컬 결과 파일을 검색하면 안 됩니다."),
        ):
            translator._prepare_cache_only_seed({"features": []})
        self.assertEqual(translator._semantic_seed_source, "")
        self.assertEqual(translator.review_stats["semantic_seed_files"], 0)

    def test_equivalent_seed_prime_prefers_google_cache_over_legacy_seed(self):
        plain = (
            "<p>You can push yourself beyond your normal limits for a moment. "
            "On your turn, you can take one additional action, except the Magic action.</p>\r\n"
            "<p>Once you use this feature, you can’t do so again until you finish a Short or Long Rest. "
            "Starting at level 17, you can use it twice before a rest but only once on a turn.</p>"
        )
        tagged = (
            "<p>You can push yourself beyond your normal limits for a moment. "
            "On your turn, you can take one additional action, except the [action]Magic[/action] action.</p>\r\n"
            "<p>Once you use this feature, you can&rsquo;t do so again until you finish a Short or Long Rest. "
            "Starting at level 17, you can use it twice before a rest but only once on a turn.</p>"
        )
        current = (
            "<p>잠시 동안 평소의 한계를 뛰어넘을 수 있습니다. "
            "자신의 차례에 마법 행동을 제외하고 추가 행동을 하나 할 수 있습니다.</p>\r\n"
            "<p>이 기능을 한 번 사용하면 짧은 휴식이나 긴 휴식을 마칠 때까지 다시 사용할 수 없습니다. "
            "17레벨부터는 휴식 전에 두 번 사용할 수 있지만, 턴당 한 번만 사용할 수 있습니다.</p>"
        )
        legacy = (
            "<p>잠시 동안 평소의 한계를 뛰어넘을 수 있습니다. "
            "자신의 차례에 마법 행동 제외한 추가 행동을 하나 할 수 있습니다.</p>\r\n"
            "<p>이 기능을 한 번 사용하면 짧은 휴식이나 긴 휴식 마칠 때까지 다시 사용할 수 없습니다. "
            "레벨 17 부터는 휴식 전에 두 번 사용할 수 있지만, 턴당 한 번만 사용할 수 있습니다.</p>"
        )
        character = {
            "source_id": "170892133",
            "features": [{"kind": "class_feature", "name": "Action Surge", "original_name": "Action Surge", "description": plain}],
            "actions": [{"kind": "action:class", "name": "Action Surge", "original_name": "Action Surge", "description": tagged}],
        }
        translator = self._translator(cache_only=True)
        translator.cache[plain] = current
        translator._prepare_translation_units(character)
        self.assertTrue(translator._register_structured_seed(plain, legacy))
        translator._prepare_equivalent_groups(character)
        translator._prime_equivalent_groups_from_cache_seed()

        self.assertEqual(translator._equivalent_final[plain], current)
        self.assertIn("[action]마법[/action] 행동을 제외하고", translator._equivalent_final[tagged])
        self.assertNotIn("마법 행동 제외한", translator._equivalent_final[tagged])

    def test_cache_only_seed_candidates_prefer_filename_timestamp_over_mtime(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        older = root / "sheet-result-170892133-20260916-160418.json"
        newer = root / "sheet-result-170892133-20260921-164344.json"
        older.write_text("{}", encoding="utf-8")
        newer.write_text("{}", encoding="utf-8")

        # Simulate an older result copied/touched after the newer run.
        os.utime(older, (2_000_000_000, 2_000_000_000))
        os.utime(newer, (1_000_000_000, 1_000_000_000))

        ordered = sorted(
            [older, newer],
            key=Translator._result_candidate_sort_key,
            reverse=True,
        )
        self.assertEqual(ordered[0], newer)
        self.assertEqual(
            Translator._result_filename_timestamp(newer),
            "20260921164344",
        )
        duplicate = root / "sheet-result-170892133-20260921-164344(2).json"
        self.assertEqual(
            Translator._result_filename_timestamp(duplicate),
            "20260921164344",
        )

    def test_cache_only_reuses_safe_previous_result_as_semantic_seed(self):
        source = (
            "<p>You can push yourself beyond your normal limits for a moment. "
            "On your turn, you can take one additional action, except the Magic action.</p>"
        )
        translated = (
            "<p>잠시 동안 평소의 한계를 뛰어넘을 수 있습니다. "
            "자신의 차례에 마법 행동을 제외하고 추가 행동을 하나 할 수 있습니다.</p>"
        )
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        seed_path = Path(td.name) / "sheet-result-170892133-old.json"
        seed_path.write_text(
            json.dumps({
                "original": {
                    "source_id": "170892133",
                    "features": [{
                        "kind": "class_feature",
                        "name": "Action Surge",
                        "original_name": "Action Surge",
                        "description": source,
                    }],
                },
                "translated": {
                    "source_id": "170892133",
                    "features": [{
                        "kind": "class_feature",
                        "name": "행동 쇄도 (Action Surge)",
                        "original_name": "Action Surge",
                        "description": translated,
                    }],
                },
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        google = FakeGoogleClient(lambda _value, _request: self.fail("Google 호출 금지"))
        translator = self._translator(google=google, cache_only=True)
        character = {
            "source_id": "170892133",
            "features": [{
                "kind": "class_feature",
                "name": "Action Surge",
                "original_name": "Action Surge",
                "description": source,
            }],
        }
        with mock.patch.object(hybrid_module, "SEMANTIC_SEED_RESULT", str(seed_path)):
            result = translator.translate_character(character)
        self.assertEqual(google.requests, [])
        self.assertEqual(result["features"][0]["description"], translated)
        stats = result["translation_summary"]["review_stats"]
        self.assertEqual(stats["google_api_calls"], 0)
        self.assertEqual(stats["cache_only_misses"], 0)
        self.assertGreaterEqual(stats["semantic_seed_hits"], 2)
        self.assertGreater(stats["semantic_seed_pairs"], 0)
        self.assertEqual(
            result["translation_summary"]["translation_pipeline"]["cache_only_seed_result"],
            seed_path.name,
        )


    def test_cache_only_seed_ignores_whitespace_only_html_slot_differences(self):
        source = (
            '<p><strong>Ability Scores:</strong>&nbsp;Strength, Constitution, Wisdom<br />'
            '<strong>Feat:</strong>&nbsp;Tough<br />'
            '<strong>Skill Proficiencies:</strong> Animal Handling and Nature<br />'
            '<strong>Tool Proficiency:</strong>&nbsp;Carpenter\'s Tools</p>'
        )
        translated = (
            '<p><strong>능력치:</strong> 근력, 건강, 지혜<br /> '
            '<strong>특기:</strong> 강인함<br /> '
            '<strong>기술 숙련:</strong> 동물 조련 및 자연<br /> '
            '<strong>도구 활용 능력:</strong> 목공 도구</p>'
        )
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        seed_path = Path(td.name) / "sheet-result-170892133-20260921-164344.json"
        seed_path.write_text(
            json.dumps({
                "original": {
                    "source_id": "170892133",
                    "background": {"description": source},
                },
                "translated": {
                    "source_id": "170892133",
                    "background": {"description": translated},
                },
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        translator = self._translator(cache_only=True)
        with mock.patch.object(hybrid_module, "SEMANTIC_SEED_RESULT", str(seed_path)):
            translator._prepare_cache_only_seed({"source_id": "170892133"})

        self.assertEqual(
            translator._semantic_plain_seed.get("Animal Handling and Nature"),
            "동물 조련 및 자연",
        )
        self.assertEqual(
            translator._semantic_plain_seed.get("Carpenter's Tools"),
            "목공 도구",
        )

    def test_context_seed_keeps_safe_location_specific_translation_on_global_conflict(self):
        source_core = (
            "If you hit a creature with this weapon, that creature has Disadvantage "
            "on its next attack roll before the start of your next turn."
        )
        parent_a = f"<p>{source_core}</p>"
        parent_b = f"<div><p>{source_core}</p></div>"
        translated_a = "이 무기로 생명체를 명중시키면, 그 생명체는 다음 공격 굴림에 불리점을 받습니다."
        translated_b = "이 무기로 대상을 명중시키면, 대상은 다음 명중 굴림에 불리점을 받습니다."
        translator = self._translator(cache_only=True)
        self.assertTrue(translator._register_semantic_context_seed(parent_a, 0, source_core, translated_a))
        self.assertTrue(translator._register_semantic_context_seed(parent_b, 0, source_core, translated_b))
        self.assertTrue(translator._register_semantic_seed(source_core, translated_a))
        self.assertFalse(translator._register_semantic_seed(source_core, translated_b))
        self.assertNotIn(source_core, translator._semantic_plain_seed)

        template = build_template(parent_a, translator._dnd_tag_target)
        rows = [(0, template.slots[0])]
        resolved = translator._translate_semantic_plain_cores(
            rows, parent_source=parent_a, visible_ordinals={0: 0}
        )
        self.assertEqual(resolved[0], translated_a)
        self.assertEqual(translator.review_stats["semantic_context_seed_hits"], 1)

    def test_cache_only_seed_can_supply_previously_unknown_dnd_atom(self):
        source = "<p>Attack with [items]Dagger[/items].</p>"
        translated = "<p>[items]단검[/items]으로 공격합니다.</p>"
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        seed_path = Path(td.name) / "sheet-result-170892133-20260921-164344.json"
        seed_path.write_text(
            json.dumps({
                "original": {"source_id": "170892133", "description": source},
                "translated": {"source_id": "170892133", "description": translated},
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        translator = self._translator(cache_only=True)
        with mock.patch.object(hybrid_module, "SEMANTIC_SEED_RESULT", str(seed_path)):
            translator._prepare_cache_only_seed({"source_id": "170892133"})
        self.assertEqual(translator._dnd_tag_target("items", "Dagger"), "단검")
        self.assertGreaterEqual(translator.review_stats["semantic_atom_seed_pairs"], 1)

    def test_cache_only_seed_rejects_different_character(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        seed_path = Path(td.name) / "sheet-result-other.json"
        seed_path.write_text(
            json.dumps({
                "original": {"source_id": "999", "languages": ["Common"]},
                "translated": {"source_id": "999", "languages": ["공용어"]},
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        translator = self._translator(cache_only=True)
        with mock.patch.object(hybrid_module, "SEMANTIC_SEED_RESULT", str(seed_path)):
            translator._prepare_cache_only_seed({"source_id": "170892133"})
        self.assertEqual(translator._semantic_seed_source, "")
        self.assertEqual(translator.review_stats["semantic_seed_files"], 0)
        self.assertEqual(translator._cache_only_direct_seed, {})

    def test_conflicting_semantic_seed_is_discarded(self):
        translator = self._translator(cache_only=True)
        source = "You can make one additional action."
        self.assertTrue(translator._register_semantic_seed(source, "추가 행동을 하나 할 수 있습니다."))
        self.assertFalse(translator._register_semantic_seed(source, "행동을 하나 더 수행할 수 있습니다."))
        self.assertNotIn(source, translator._semantic_plain_seed)
        self.assertIn(source, translator._semantic_seed_conflicts)

    def test_cache_only_uses_verified_whole_structured_seed_when_semantic_restore_is_ambiguous(self):
        source = (
            "<p>When you take the [action]Attack[/action] action on your turn, "
            "you can make one extra attack. You can attack again with the "
            "[action]Attack[/action] action.</p>"
        )
        translated = (
            "<p>자신의 턴에 [action]공격[/action] 행동을 취하면 추가 공격을 한 번 할 수 있습니다. "
            "[action]공격[/action] 행동으로 다시 공격할 수 있습니다.</p>"
        )
        translator = self._translator(cache_only=True)
        character = {
            "features": [{
                "kind": "class_feature",
                "name": "Repeated Attack",
                "original_name": "Repeated Attack",
                "description": source,
            }],
        }
        translator._prepare_translation_units(character)
        self.assertTrue(translator._register_structured_seed(source, translated))
        translator._mark_source_recovered = lambda *_args, **_kwargs: None
        with mock.patch.object(
            translator,
            "_translate_structured_semantic",
            side_effect=SemanticPreprocessError("ambiguous repeated D&D atom"),
        ), mock.patch.object(
            Translator.__mro__[1],
            "_translate_structured_text_nodes",
            create=True,
            side_effect=AssertionError("text-node fallback must not run in cache-only"),
        ):
            self.assertEqual(translator._translate_structured(source), translated)
        self.assertEqual(translator.review_stats["semantic_structured_seed_hits"], 1)
        self.assertEqual(translator.review_stats["cache_only_misses"], 0)

    def test_cache_only_preflight_reports_all_unresolved_before_translation(self):
        google = FakeGoogleClient(lambda _value, _request: "호출되면 안 됩니다")
        translator = self._translator(google=google, cache_only=True)
        character = {
            "features": [{
                "kind": "class_feature",
                "name": "Unknown Feature",
                "original_name": "Unknown Feature",
                "description": "A completely new rule sentence that is absent from every cache.",
            }],
        }
        translator._prepare_translation_units(character)
        translator._translate_glossary_composite = lambda _value: None
        with self.assertRaisesRegex(TranslationError, "cache-only 실제 경로 사전 점검"):
            translator._cache_only_preflight()
        self.assertEqual(google.requests, [])
        self.assertGreaterEqual(translator.review_stats["cache_only_preflight_unresolved"], 1)
        summary = translator.translation_summary()
        self.assertEqual(summary["status"], "failed")
        self.assertIn("fatal_error", summary)

    def test_cache_only_preflight_accepts_verified_structured_seed(self):
        source = "<p>A new structured rule absent from the Google cache.</p>"
        translated = "<p>Google 캐시에 없는 새 구조화 규칙입니다.</p>"
        translator = self._translator(cache_only=True)
        character = {
            "features": [{
                "kind": "class_feature",
                "name": "Seeded Feature",
                "original_name": "Seeded Feature",
                "description": source,
            }],
        }
        translator._prepare_translation_units(character)
        translator._translate_glossary_composite = lambda _value: None
        translator._register_direct_seed("Seeded Feature", "시드 기능")
        self.assertTrue(translator._register_structured_seed(source, translated))
        translator._cache_only_preflight()
        self.assertEqual(translator.review_stats["cache_only_preflight_unresolved"], 0)


    def test_direct_seed_treats_bilingual_name_suffix_as_same_translation(self):
        translator = self._translator(cache_only=True)
        source = "Carpenter's Tools"
        self.assertTrue(translator._register_direct_seed(source, "목공 도구 (Carpenter's Tools)"))
        self.assertTrue(translator._register_direct_seed(source, "목공 도구"))
        self.assertEqual(
            translator._cache_only_direct_seed[source],
            "목공 도구 (Carpenter's Tools)",
        )

    def test_conflicting_whole_structured_seed_is_discarded(self):
        source = "<p>You can make one attack.</p>"
        translator = self._translator(cache_only=True)
        self.assertTrue(translator._register_structured_seed(source, "<p>공격을 한 번 할 수 있습니다.</p>"))
        self.assertFalse(translator._register_structured_seed(source, "<p>한 차례 공격할 수 있습니다.</p>"))
        self.assertNotIn(source, translator._cache_only_structured_seed)
        self.assertIn(source, translator._cache_only_structured_conflicts)


    def test_cache_only_plain_text_fallback_uses_semantic_seed_without_google(self):
        source = (
            "You can push yourself beyond your normal limits for a moment. "
            "On your turn, you can take one additional action, except the Magic action."
        )
        translated = (
            "잠시 동안 평소의 한계를 뛰어넘을 수 있습니다. "
            "자신의 차례에 마법 행동 제외한 추가 행동을 하나 할 수 있습니다."
        )
        google = FakeGoogleClient(lambda _value, _request: "호출되면 안 됩니다")
        translator = self._translator(google=google, cache_only=True)
        translator._ensure_ollama_ready()
        self.assertTrue(translator._register_semantic_seed(source, translated))
        with mock.patch.object(
            translator,
            "_review_translation_if_needed",
            side_effect=lambda _source, candidate: candidate,
        ):
            self.assertEqual(translator.translate(source), translated)
        self.assertEqual(google.requests, [])
        self.assertEqual(translator.review_stats["cache_only_misses"], 0)
        self.assertEqual(
            translator.review_stats["semantic_plain_fallback_seed_hits"], 1
        )

    def test_cache_only_whole_structured_seed_recovers_semantic_child_cache_miss(self):
        source = "<p>A structured rule whose semantic child is missing.</p>"
        translated = "<p>의미 조각 캐시가 없어도 전체 구조 seed로 복구되는 규칙입니다.</p>"
        translator = self._translator(cache_only=True)
        character = {
            "features": [{
                "kind": "class_feature",
                "name": "Whole Seed Recovery",
                "original_name": "Whole Seed Recovery",
                "description": source,
            }],
        }
        translator._prepare_translation_units(character)
        self.assertTrue(translator._register_structured_seed(source, translated))
        translator._mark_source_recovered = lambda *_args, **_kwargs: None
        miss = TranslationError(
            "Google Cloud Translation 호출에 실패: cache-only 모드에서 "
            "Google 번역 캐시에 없는 항목을 발견했습니다. Google API는 호출하지 않았습니다."
        )
        def fail_semantic(_value):
            translator.review_stats["cache_only_misses"] += 2
            translator._fatal_translation_error = str(miss)
            raise miss

        with mock.patch.object(
            translator,
            "_translate_structured_semantic",
            side_effect=fail_semantic,
        ):
            self.assertEqual(translator._translate_structured(source), translated)
        self.assertEqual(translator._fatal_translation_error, "")
        self.assertEqual(translator.review_stats["semantic_structured_seed_hits"], 1)
        self.assertEqual(translator.review_stats["cache_only_misses"], 0)
        self.assertEqual(translator.review_stats["cache_only_recovered_misses"], 2)

    def test_cache_only_preflight_runs_real_translate_path_not_prediction_only(self):
        google = FakeGoogleClient(lambda _value, _request: "호출되면 안 됩니다")
        translator = self._translator(google=google, cache_only=True)
        character = {
            "features": [{
                "kind": "class_feature",
                "name": "Unknown Feature",
                "original_name": "Unknown Feature",
                "description": "A completely unseen runtime sentence.",
            }],
        }
        translator._prepare_translation_units(character)
        translator._prepare_equivalent_groups(character)
        with mock.patch.object(
            translator,
            "_cache_only_source_resolvable",
            return_value=True,
        ):
            with self.assertRaisesRegex(TranslationError, "실제 경로 사전 점검"):
                translator._cache_only_preflight()
        self.assertEqual(google.requests, [])
        self.assertGreaterEqual(
            translator.review_stats["cache_only_preflight_unresolved"], 1
        )

    def test_cache_only_preflight_warms_successful_source_for_later_batch(self):
        source = "A cache-only sentence available from a verified semantic seed."
        translated = "검증된 의미 seed에서 가져온 cache-only 문장입니다."
        google = FakeGoogleClient(lambda _value, _request: "호출되면 안 됩니다")
        translator = self._translator(google=google, cache_only=True)
        character = {
            "features": [{
                "kind": "class_feature",
                "name": "Seeded Runtime",
                "original_name": "Seeded Runtime",
                "description": source,
            }],
        }
        translator._prepare_translation_units(character)
        translator._prepare_equivalent_groups(character)
        translator._register_direct_seed("Seeded Runtime", "시드 런타임")
        self.assertTrue(translator._register_semantic_seed(source, translated))
        with mock.patch.object(
            translator,
            "_review_translation_if_needed",
            side_effect=lambda _source, candidate: candidate,
        ):
            translator._cache_only_preflight()
            self.assertEqual(translator._equivalent_final[source], translated)
            before = translator.review_stats["semantic_plain_fallback_seed_hits"]
            self.assertEqual(translator.translate(source), translated)
            self.assertEqual(
                translator.review_stats["semantic_plain_fallback_seed_hits"], before
            )
        self.assertEqual(google.requests, [])
        self.assertEqual(translator.review_stats["cache_only_misses"], 0)


    def test_cache_only_local_seed_cannot_pollute_google_first_pass_cache(self):
        translator = self._translator(cache_only=True)
        translator._inside_google_translate = True
        translator._remember_translation(
            "A locally migrated seed.",
            "로컬에서 승계한 seed입니다.",
        )
        self.assertNotIn("A locally migrated seed.", translator.cache)
        self.assertNotIn(
            "A locally migrated seed.",
            translator._google_first_pass,
        )


    def test_cache_only_legacy_action_surge_seed_is_primed_before_preflight(self):
        plain = (
            "<p>You can push yourself beyond your normal limits for a moment. "
            "On your turn, you can take one additional action, except the Magic action.</p>\r\n"
            "<p>Once you use this feature, you can’t do so again until you finish a Short or Long Rest. "
            "Starting at level 17, you can use it twice before a rest but only once on a turn.</p>"
        )
        tagged = (
            "<p>You can push yourself beyond your normal limits for a moment. "
            "On your turn, you can take one additional action, except the [action]Magic[/action] action.</p>\r\n"
            "<p>Once you use this feature, you can&rsquo;t do so again until you finish a Short or Long Rest. "
            "Starting at level 17, you can use it twice before a rest but only once on a turn.</p>"
        )
        legacy_korean = (
            "<p>잠시 동안 평소의 한계를 뛰어넘을 수 있습니다. "
            "자신의 차례에 마법 행동 제외한 추가 행동을 하나 할 수 있습니다.</p>\r\n"
            "<p>이 기능을 한 번 사용하면 짧은 휴식이나 긴 휴식 마칠 때까지 다시 사용할 수 없습니다. "
            "레벨 17 부터는 휴식 전에 두 번 사용할 수 있지만, 턴당 한 번만 사용할 수 있습니다.</p>"
        )
        character = {
            "source_id": "170892133",
            "features": [{
                "kind": "class_feature",
                "name": "Action Surge",
                "original_name": "Action Surge",
                "description": plain,
            }],
            "actions": [{
                "kind": "action:class",
                "name": "Action Surge",
                "original_name": "Action Surge",
                "description": tagged,
            }],
        }
        legacy_payload = {
            "original": character,
            "translated": {
                "source_id": "170892133",
                "features": [{
                    "kind": "class_feature",
                    "name": "행동 쇄도 (Action Surge)",
                    "original_name": "Action Surge",
                    "description": legacy_korean,
                }],
                "actions": [{
                    "kind": "action:class",
                    "name": "행동 쇄도 (Action Surge)",
                    "original_name": "Action Surge",
                    # This is the real v15 failure shape: action copy stayed English.
                    "description": tagged,
                }],
            },
        }
        google = FakeGoogleClient(lambda _value, _request: "호출되면 안 됩니다")
        translator = self._translator(google=google, cache_only=True)
        translator._prepare_translation_units(character)
        self.assertGreater(translator._seed_from_result_payload(legacy_payload, character), 0)
        translator._prepare_equivalent_groups(character)
        self.assertIn(plain, translator._cache_only_structured_seed)
        self.assertNotIn(tagged, translator._cache_only_structured_seed)

        translator._prime_equivalent_groups_from_cache_seed()
        projected = translator._equivalent_final[tagged]
        self.assertIn("[action]마법[/action]", projected)
        self.assertIn("추가 행동", projected)
        self.assertIn("턴당 한 번", projected)

        translator._cache_only_preflight()
        self.assertEqual(translator.review_stats["cache_only_preflight_unresolved"], 0)
        self.assertEqual(translator.review_stats["cache_only_misses"], 0)
        self.assertGreaterEqual(translator.review_stats["equivalent_seed_primes"], 1)
        self.assertEqual(google.requests, [])

    def test_equivalent_slot_projection_recovers_when_global_projection_declines(self):
        plain = (
            "<p>You can push yourself beyond your normal limits for a moment. "
            "On your turn, you can take one additional action, except the Magic action.</p>\r\n"
            "<p>Once you use this feature, you can’t do so again until you finish a Short or Long Rest. "
            "Starting at level 17, you can use it twice before a rest but only once on a turn.</p>"
        )
        tagged = plain.replace("Magic", "[action]Magic[/action]").replace("can’t", "can&rsquo;t")
        legacy_korean = (
            "<p>잠시 동안 평소의 한계를 뛰어넘을 수 있습니다. "
            "자신의 차례에 마법 행동 제외한 추가 행동을 하나 할 수 있습니다.</p>\r\n"
            "<p>이 기능을 한 번 사용하면 짧은 휴식이나 긴 휴식 마칠 때까지 다시 사용할 수 없습니다. "
            "레벨 17 부터는 휴식 전에 두 번 사용할 수 있지만, 턴당 한 번만 사용할 수 있습니다.</p>"
        )
        translator = self._translator(cache_only=True)
        with mock.patch.object(
            translator,
            "_project_equivalent_translation",
            return_value=None,
        ):
            projected = translator._project_equivalent_translation_with_fallback(
                plain, legacy_korean, tagged
            )
        self.assertIsNotNone(projected)
        self.assertIn("[action]마법[/action]", projected)
        self.assertEqual(translator.review_stats["equivalent_slot_repairs"], 1)



if __name__ == "__main__":
    unittest.main()
