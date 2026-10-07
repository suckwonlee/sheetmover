# Sheet Mover runtime-integrity v2.4 fixed

검토 기준 GitHub 커밋:

`c0d3905842da144121a3922ef68fdccf6f30b0c6`

이번 패치는 두 가지를 수정합니다.

1. Stage 8 서버 검증값은 맞는데 이미 열려 있던 Roll20 화면에서
   repeating trait 이름/설명이 밀려 보이는 문제
2. 한 GUI 실행의 worker 폴더에 여러 로그/프로토콜 파일이 남는 문제

변경 후:

- 모든 Roll20 쓰기가 끝난 뒤 열린 app.roll20.net 화면을 재로딩
- editor가 다시 준비된 것을 확인한 뒤에만 완료 처리
- workers/<run_id>/에는 최종적으로 run.log만 유지
- UI에서 stage JSON 경로를 로그처럼 줄줄이 출력하지 않음
- full-run report는 worker 폴더의 runtime 파일로 이동하여 검증 후 삭제

단계별 Roll20 JSON/backup은 재실행의 소유권/복구 상태이므로 이 패치에서는 삭제하지 않습니다.

안전 적용:

```powershell
python apply_runtime_integrity_v24.py
```

설치기는 먼저 프로젝트 복제본 전체 unittest를 돌립니다.
테스트가 하나라도 실패하면 실제 파일을 수정하지 않습니다.

성공 후:

```powershell
python main.py
```
