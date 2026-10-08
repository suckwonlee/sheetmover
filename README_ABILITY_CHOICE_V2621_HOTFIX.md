# 시트 이동기 ability-choice v2.6.2.1 hotfix

v2.6.2에서 D&D Beyond `choiceDefinitions`의 실제 위치를 잘못 읽던 문제를 수정합니다.

실제 DDB 구조:
- `raw_source.choices.class`
- `raw_source.choices.choiceDefinitions`

그래서 v2.6.2에서는 hook은 실행됐지만 `removed_count = 0`이었고,
기존 능력치 null 상태가 그대로 남았습니다.

v2.6.2.1:
- 중첩된 `choices.choiceDefinitions`를 우선 사용
- 구형 top-level 위치는 호환 fallback으로 유지
- `choiceDefinitions`, `definitionKeyNameMap`을 실제 선택 행에서 제외
- class + componentTypeId + componentId가 모두 맞는 generic modifier만 제거
- 실제 케이스에서 1729, 1821 두 개만 제거

거인 힘의 벨트도 테스트에 포함했습니다.
- 착용 + 조율: STR 25
- 미착용: STR 14
- 어느 경우에도 벨트 자체 때문에 6능력치 전체가 null이 되면 테스트 실패

적용:
```powershell
python apply_ability_choice_v2621_hotfix.py
```
