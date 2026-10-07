# Stage 8 v1.7 — 피처 이름/설명 무결성 + Ghost Feat 제거

수정 대상:

- 실제 선택하지 않은 `Dark Bargain`이 D&D Beyond top-level feat 후보에서 들어오는 문제
- Roll20 `repeating_traits` 새 행 생성 시 이름과 설명이 서로 다른 피처처럼 표시되는 문제

변경 내용:

- 선택/부여 증거가 없는 top-level feat는 보유 feat로 간주하지 않음
- `name/source/source_type/description/options-flag`를 동시 저장하지 않고 순서대로 저장
- 현재 결과에 없는 이전 Sheet Mover 특성행만 정리
- 일반 Roll20 수동 행은 삭제하지 않음
- 이름/설명 및 stale 행을 서버에서 최종 재검증

적용:

```powershell
python apply_stage8_integrity_v17.py --check
python apply_stage8_integrity_v17.py
python -m unittest discover -s tests -v
python main.py
```

다시 `견본2`를 옮기면 기존의 잘못된 설명은 현재 source_key별 값으로 다시 교정되고,
`Dark Bargain`처럼 현재 feature plan에 없는 Sheet Mover 행은 제거됩니다.
