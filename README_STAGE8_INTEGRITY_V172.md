# Stage 8 v1.7.2 핫픽스

v1.7.1에서 `source.py` 안에 존재하지 않는 `_text()` helper를 사용해서
`NameError: name '_text' is not defined`가 발생했습니다.

이번 핫픽스는 해당 부분만 수정합니다.

- 일반 top-level feat 유지
- `__DISGUISE_FEAT`만 제외
- Great Weapon Master 액션 유지
- Dark Bargain 제외
- Stage 8 순차 반복행 저장 유지

적용:

```powershell
python apply_stage8_integrity_v172.py --check
python apply_stage8_integrity_v172.py
python -m unittest discover -s tests -v
python main.py
```
