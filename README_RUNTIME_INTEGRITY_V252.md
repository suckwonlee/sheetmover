# Sheet Mover runtime-integrity v2.5.2

v2.5.1의 389개 테스트 중 실패한 2개만 수정한 재발행본입니다.

실패 원인:
- 정규식의 `\d`, 괄호 escaping이 한 번 더 들어가 번역 진행 로그를 못 걸렀음
- `_DiagnosticLogStream`이 실제 줄바꿈 `\n`이 아니라 문자 `\\n`을 찾고 있었음

v2.5.2에서 위 escaping을 바로잡았습니다.

게시 전 검증:
- 설치기 py_compile 통과
- 설치기가 생성하는 테스트 3개 ast.parse 통과
- 실패했던 `번역 33/155 (배치 처리)` 필터를 실제 함수로 직접 실행해 통과
- 실패했던 `주문 1/21`, `특성 1/21` 필터를 실제 stream으로 직접 실행해 통과
- `Bardic 2/2` 자원 요약은 유지되는 것 확인
- v2.4.2 -> v2.5.2 버전 게이트 확인

실행:

```powershell
python apply_runtime_integrity_v252.py
```

복제본 전체 테스트가 전부 통과한 뒤 실제 프로젝트에 적용됩니다.
성공 후:

```powershell
python main.py
```
