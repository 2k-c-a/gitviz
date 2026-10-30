# 문서 정리 후 일관성 검사

검토일: 2026-10-23 · 코드 기준: `a8351f0` · [문서 안내](../../index.md)

## 문서·보존 검사

Repository 루트에서 표준 Python으로 실행한다.

```powershell
python -B docs/audit/check_documents.py
python -B docs/audit/test_check_documents.py
python -B tests/server/test_validation_reports.py
```

첫 검사는 쓰기·앱 import·.env·DB·네트워크 없이 다음을 확인한다.

- baseline 원본 31개와 이동표의 정확한 대응, 원본 바이트 해시
- 보존 위치가 archive/originals/<이전 경로>이고 현재 대응 문서가 존재하는지
- 허용한 현재 문서 외의 이전 경로가 잔류하지 않는지
- README·현재 문서·Historical 문서의 로컬 링크·명시 anchor·표준 heading anchor
- 원본 inventory와 docs/index에서의 전체 Markdown 도달성

원본 baseline을 재생성해 변조·누락을 숨기지 않는다. 보관 바이트는 Git 줄바꿈 변환을 막는다. 검사 도구는 외부 URL·backtick 속 최초 파일안·자연어 사실성·제품 정책의 승인을 보장하지 않는다.

다음 두 테스트는 새 임시 fixture를 생성·정리하며 DB·네트워크를 사용하지 않는다. 보존 실패 감지와 보고서의 고유 경로·덮어쓰기 거부를 검사한다. OS 임시 경로 권한이 제한된 경우 쓰기 가능한 전용 검증 폴더를 TEMP/TMP로 지정한다. 기존 사용자 자료를 지우지 않는다.

## 코드·계약 회귀

```powershell
$env:PYTHONIOENCODING='utf-8'
python -B tests/server/test_ai_contracts.py
python -B tests/server/test_analytics_calculations.py
python -B tests/server/test_metric_policy.py
node tests/frontend/test_ai_frontend.cjs
node tests/frontend/test_metric_display.cjs
node tests/frontend/test_viz_modules.cjs
python -B tests/server/evaluate_ai_review.py --check-release
```

실제 환경은 [현재 상태](../current-status.md)에 남긴다. 제품 코드가 바뀌지 않은 영역의 과거 실행을 이번 재실행으로 보고하지 않는다. 출시 점검의 exit 1은 미완료를 뜻하며 기준·정답을 임의로 채워 통과시키지 않는다.

## 후속 통합·실연동

| 층 | 도구·사례 | 완료 조건 |
|---|---|---|
| PG 설치·개인 API | [backend](../../../tests/server/test_backend_improvements.py), [AI schema](../../../tests/server/test_ai_schema.py), [AI API](../../../tests/server/test_ai_api.py) | 전용 DB 빈 설치·upgrade·보존, owner·CSRF·동시성·기한 |
| 공유 | test_ai_api의 SharedRead와 실제 인증 | 공개 read와 조직 구성원의 차이, owner 쓰기 제한, 원문 비공개, 메모·삭제·만료·권한 회수 |
| 큐·장애 | [queue](../../../tests/server/test_ai_queue_integration.py), [MVP integration](../../../tests/server/verify_ai_mvp_integration.py) | 전용 PG·Redis·별도 worker/dispatcher, 응답 유실·불명·취소·원문 삭제 |
| snapshot·수집 | [snapshot](../../../tests/server/test_repository_snapshots.py), [live](../../../tests/server/verify_live_repository.py) | SHA·해시·분모·페이지·반복 수집 구분 |
| 브라우저 | 실제 앱·모의 서버 | URL·선택·공유 혼입, 동의·키·오류·XSS·모바일 |
| 모델 | [평가 절차](ai-evaluation.md)·[manifest](../../../tests/server/fixtures/ai/evaluation_manifest.json) | 사전 기준·사람 정답·조정/최종 분리, 품질·비용·coverage·한계 |

전용 DB 이름은 gitviz_test로 시작해야 한다. 큐 통합은 테스트 Redis DB를 비우므로 운영 Redis를 쓰지 않는다. 실제 모델 호출은 별도 예산·자료·동의·실행 기록으로 구분한다. 신규 일반 검증 결과의 기본 위치는 reports/validation이며 과거 JSON을 덮어쓰지 않는다.

## 완료 판정

확인: 문서 자체·보존 검사는 [정리 기록](../../audit/organization-plan.md)의 완료 조건이다. 추정: 실행 보고에는 시각·commit·dirty 상태·명령·exit·환경·실제/대역·증거를 남긴다. 미확정: 실제 인증·운영·모델·최신 원격 CI는 문서 검사 통과와 별개이며 [결정 목록](../../decisions/index.md)의 미확정을 임의로 닫지 않는다.
