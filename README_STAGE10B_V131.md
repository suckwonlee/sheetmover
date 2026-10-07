# Stage 10B v1.3.1 — 잔여 v1.2 테스트 보정

v1.3 코어 패치 자체는 적용됐고 새 v1.3 테스트도 통과했습니다.

실패한 것은 v1.2 때 추가된 오래된 테스트 파일:

`tests/test_stage10b_rollcontent_dispatch.py`

이 파일이 삭제된 함수 이름 `_split_rollcontent_attrs`를 계속 import하고 있어서
unittest loader 단계에서 ImportError가 난 것입니다.

v1.3에서는 링크 필드 정책이 바뀌었습니다.

- 일반 필드: `spelloutput` 등
- 링크 필드: `spellattackid`, `rollcontent`

따라서 오래된 테스트를 `_split_link_attrs` 기준으로 갱신합니다.

적용:

```powershell
python apply_stage10b_v131_test_compat.py --check
python apply_stage10b_v131_test_compat.py
python -m unittest discover -s tests -v
```

이 패치는 `roll20_spell_attacks.py`를 건드리지 않습니다.
