# Sheet Mover runtime-integrity v2.5.1

v2.5가 실패한 원인은 설치기 버전 게이트였습니다.
현재 로컬은 앞서 적용한 v2.4.2 계열인데 v2.5가 v2.3만 허용했습니다.

v2.5.1은 v2.3 / v2.4 / v2.4.1 / v2.4.2 / v2.5 계열을 자동 판별합니다.

수정 범위:
- Bardic Inspiration 자원 누락
- 같은 Bardic Inspiration 자원 중복 제거
- 오류 진단에 필요한 단계/traceback/저장 오류는 run.log에 유지
- 번역 n/N, 주문 n/N, 특성 n/N 같은 반복 노이즈는 제거
- worker 실행 폴더는 종료 후 run.log만 유지
- 과거 events.jsonl/settings.json 정리

건드리지 않음:
- 주문 슬롯
- Passive Perception
- Hit Dice
- 번역 품질
- Stage 8 특성 데이터

실행:

```powershell
python apply_runtime_integrity_v251.py
```

전체 복제본 테스트가 통과하면 실제 파일에 적용됩니다.
그 뒤:

```powershell
python main.py
```
