# Stage 8 v1.7.1 보정

v1.7에서 선택/부여 ID가 없을 때 top-level feat 전체를 제거한 것이
기존 `Great Weapon Master` 액션 회귀 테스트를 깨뜨렸습니다.

견본2의 `Dark Bargain` 원본은 `__DISGUISE_FEAT` 카테고리를 갖습니다.
따라서 모든 feat를 버리는 대신 이 명시적 placeholder/helper 표시만 제외합니다.

적용:

```powershell
python apply_stage8_integrity_v171.py --check
python apply_stage8_integrity_v171.py
python -m unittest discover -s tests -v
python main.py
```
