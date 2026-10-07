# Stage 13 v2.1 — 다른 시트 테스트 보강

기준 커밋:

`520c24783d87a31eda8750426f4a3604dfbf1747`

실제 `견본2` 테스트에서 확인된 두 문제를 고칩니다.

- 동일 D&D Beyond URL이 첫 실행에는 HTTP 403, 바로 다음 실행에는 정상 수집됨
- 번역기가 위험한 조각을 손상된 번역 대신 정확한 영문 원문으로 보존했는데도
  full_run이 `partial`이라는 이유만으로 전체 Roll20 입력을 중단함

정책:

- HTTP 403 / 429 / 500 / 502 / 503 / 504: 짧게 재시도
- HTTP 401 / 404: 즉시 실패
- 번역 `complete`: 계속
- 번역 `partial` + 정확한 원문 fallback: 경고를 남기고 계속
- 실제 `TranslationError`: Roll20 입력 전에 중단

적용:

```powershell
python apply_stage13_v2_1.py --check
python apply_stage13_v2_1.py
python -m unittest discover -s tests -v
python main.py
```
