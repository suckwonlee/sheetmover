import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sheet_mover.hybrid_translator import MODEL_NAME, TranslationError, Translator
from sheet_mover.semantic_validator import critical_codes
from sheet_mover.translation_render import strip_dnd_display_tags


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
    def _translator(self, google=None, reviewer=None, glossary=None):
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
        character = {"features": [{"description": plain}], "actions": [{"description": tagged}]}
        korean_plain = (
            "<p>잠시 동안 평소의 한계를 뛰어넘을 수 있습니다. "
            "자신의 차례에 마법 행동을 제외하고 추가 행동을 하나 할 수 있습니다.</p>"
        )
        google = FakeGoogleClient(lambda _value, _request: korean_plain)
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
        character = {"features": [{"description": plain}], "actions": [{"description": tagged}]}
        korean = "<p>평소의 한계를 뛰어넘을 수 있습니다. 마법 행동을 제외하고 추가 행동을 하나 할 수 있습니다.</p>"
        google = FakeGoogleClient(lambda _value, _request: korean)
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
        translator = self._translator()
        translator.cache[plain] = korean
        character = {
            "features": [{"kind": "class_feature", "description": plain}],
            "actions": [{"kind": "action:class", "description": tagged}],
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

    def test_translation_summary_contains_exact_build_identity(self):
        translator = self._translator()
        pipeline = translator.translation_summary()["translation_pipeline"]
        self.assertEqual(pipeline["build"], "2026-09-21-v15.1-equivalent-finalstate")
        self.assertRegex(pipeline["code_sha256"], r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
