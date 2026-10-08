# 시트 이동기 v2.6.4.2 — 범용 서브클래스 주문 + 숙련/언어 무결성

기준 커밋: `f58cf08f95e0878c0ab2a0adc1efa32ac18f18c6`

## 목적

D&D Beyond가 실제 spell 객체를 주는 경우에는 그 객체를 그대로 우선합니다.
Legacy Oath/Domain처럼 주문표만 feature HTML에 있고 spell 객체가 없는 경우에만
D&D Beyond 공개 spell catalog에서 같은 판본의 실제 definition을 찾아 보완합니다.

## 안전 규칙

- 캐릭터/클래스/서브클래스/feature/spell ID별 런타임 특례 없음
- `Expanded Spell List`는 선택 목록 확장으로 분류하여 자동 추가하지 않음
- 이름만 같은 5e/5.5e 주문을 합치지 않음
- 판본은 현재 클래스 source와 catalog spell source의 관계에서 동적으로 판정
- 후보가 0개 또는 여러 개로 남으면 추가하지 않음
- 기존 구조화 spell 객체가 있으면 우선 사용
- 기존 번역 cache hit에서는 번역 API를 다시 호출하지 않고 새 source-backed 주문만 원문 유지 가능
- Stage 11 언어/도구/무기/방어구 숙련 source→plan 무결성 검사
- 번역 언어 배열이 일부 빠지면 DDB 원문 언어 목록으로 fail-safe fallback
- Legacy OGL5e의 `repeating_proficiencies` + `simpleproficencies=complex` 구조 유지
- 수동 반복행 보존 정책 유지

## 적용

`E:\sheet_mover`에 ZIP 내용을 풀고 다음을 실행합니다.

```powershell
python apply_subclass_spells_v2642.py
```

설치기는 실제 프로젝트를 수정하기 전에 임시 복제본에서 전체 `unittest`를 실행합니다.
하나라도 실패하면 실제 프로젝트 파일을 교체하지 않습니다.


## v2.6.4.2 hotfix
- unittest는 quiet 모드로 실행하여 성공한 `... ok` 행을 출력하지 않습니다.
- 실패한 테스트와 오류 요약만 표시합니다.
- Paladin 실제 DDB 숙련 fixture에 source-backed `entityTypeId`를 반영했습니다.
