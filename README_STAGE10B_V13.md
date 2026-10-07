# Stage 10B v1.3 — Moonbeam link dispatch 수정

v1.2에서 `rollcontent`는 일반 writer에서 분리됐지만,
같은 링크 쌍인 `spellattackid`는 여전히 `wait:true` writer에 남아 있었습니다.

최신 실패는:

```text
save_repeating_spell-2_..._spellattackid_timeout
```

이며 persisted state에서도 `spellattackid`가 빈 문자열로 남았습니다.

v1.3은 두 링크 필드를 함께 일반 writer에서 분리합니다.

1. `spellattackid`
2. `rollcontent`

둘 다 `wait:false`로 저장 요청을 보내고, callback이 아니라 persisted server state를
polling해서 실제 저장 여부를 확인합니다.

저장 순서는 `spellattackid` -> `rollcontent`입니다.

적용:

```powershell
python apply_stage10b_v13_link_dispatch.py --check
python apply_stage10b_v13_link_dispatch.py
python -m unittest discover -s tests -v
python main.py
```
