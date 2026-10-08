# 시트 이동기 ability-choice v2.6.2

대상 문제:
- D&D Beyond에서 Ability Score Improvement 대신 Feat을 선택했는데 generic `choose-an-ability-score +1/+1` modifier가 raw 데이터에 남는 경우
- 기존 보수적 계산기는 이 값을 확정할 수 없으므로 모든 능력치를 null로 유지하고 Roll20 쓰기를 중단함

v2.6.2 처리:
- DDB `choiceDefinitions`에서 부모 선택이 실제 `Feat`인지 확인
- 같은 choice group인지 확인
- 같은 `componentId`인지 확인
- `componentTypeId`가 있으면 정확히 일치하는지 확인
- 실제 Feat child choice가 존재하고 `raw_source.feats`의 feat인지 확인
- 위 조건이 모두 맞는 generic ASI modifier만 제거
- 정보가 빠지거나 애매하면 제거하지 않고 기존 fail-closed 동작 유지
- 실제 ASI, Resilient 보너스, Tome 효과, Belt set STR 25 유지
- 동일 `raw_source`의 기존 번역 결과를 재사용하고 계산값만 갱신

이번 캐릭터 기대 능력치:
- STR 25
- DEX 8
- CON 16
- INT 8
- WIS 10
- CHA 21

## 적용

ZIP의 폴더 구조를 유지한 채 `E:\sheet_mover`에 풀어 주세요.

그 다음 `E:\sheet_mover`에서:

```powershell
python apply_ability_choice_v262.py
```

설치기는 실제 프로젝트를 수정하기 전에 임시 복제본을 만들고 전체 unittest를 먼저 실행합니다.
전체 테스트가 실패하면 실제 `source.py`, `full_run.py`는 수정하지 않습니다.

적용 성공 후 다음 단계:

```powershell
python main.py
```
