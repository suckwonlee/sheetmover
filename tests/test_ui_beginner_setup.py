import unittest
from pathlib import Path


class BeginnerSetupUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (Path("sheet_mover") / "ui.py").read_text(encoding="utf-8")

    def test_beginner_tabs_exist(self):
        for text in ("처음 설정", "1. Google 번역", "2. Ollama", "3. Roll20", "고급 설정"):
            self.assertIn(text, self.source)

    def test_google_walkthrough_has_action_buttons(self):
        for text in ("Google Cloud 콘솔 열기", "Cloud Translation API 열기", "Cloud Storage API 열기", "Google Cloud CLI 설치 페이지", "Google 계정 로그인 시작", "Google 연결 확인"):
            self.assertIn(text, self.source)

    def test_ollama_walkthrough_has_action_buttons(self):
        for text in ("Ollama 설치 페이지 열기", "gpt-oss:20b 모델 설치 시작", "Ollama 연결 확인"):
            self.assertIn(text, self.source)

    def test_advanced_terms_are_moved_out_of_beginner_flow(self):
        self.assertIn("일반 사용자는 건드리지 않아도 됩니다", self.source)


if __name__ == "__main__":
    unittest.main()
