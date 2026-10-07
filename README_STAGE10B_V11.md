# Stage 10B v1.1 — Moonbeam rollcontent timeout 수정

기준 GitHub 커밋: `2359d39e5617b022ecbbab028eb38d8e4457557f`

현재 실패는 `달빛 (Moonbeam)`의 `spellattackid`/`rollcontent` 같은 같은 주문행 링크 필드를
공용 writer가 `Promise.all()`로 동시에 저장하는 과정에서 `rollcontent` save callback이 timeout 난 것입니다.

수정:

- attack row 필드는 기존 병렬 writer 유지
- spell row의 전투/링크 필드는 순차 저장
- save callback이 timeout이어도 persisted server read가 정확하면 성공 처리
- 실제로 서버 값이 안 맞으면 순차 writer로 1회 재시도 후 다시 검증

적용:

```powershell
python apply_stage10b_v11_sequential.py --check
python apply_stage10b_v11_sequential.py
python -m unittest discover -s tests -v
```

전체 테스트 통과 후:

```powershell
python main.py
```
