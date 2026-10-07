# Stage 10B v1.2 — Moonbeam rollcontent callback timeout 제거

v1.1의 순차 저장으로도 `rollcontent` 자체의 Backbone success callback이 끝나지 않는 경우가
남아 있었습니다.

v1.2는 `rollcontent`를 일반 writer에서 완전히 분리합니다.

- 일반 주문 전투/링크 필드: 기존 writer
- `rollcontent`: `wait:false`로 저장 요청만 dispatch
- callback 성공 여부는 사용하지 않음
- 대신 persisted server state를 최대 8회 읽어 실제 값이 맞는지 검증
- 실제 서버 값이 맞아야 성공

`rollcontent`는 주문 버튼이 linked repeating_attack을 호출하는 데 필요하므로 삭제하지 않습니다.

적용:

```powershell
python apply_stage10b_v12_rollcontent_dispatch.py --check
python apply_stage10b_v12_rollcontent_dispatch.py
python -m unittest discover -s tests -v
python main.py
```
