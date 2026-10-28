# 현재 검증 상태

검토일: 2026-10-23 (Asia/Seoul) · 코드 기준: `a8351f0bf24c78625990f8756e5a03fc42a002c6`

확인: 이 문서는 감사·추가 작성·재배치 단계에서 직접 확인한 결과와 미실행 범위를 구분한다. [과거 증거](evidence-index.md)의 통과 수를 승계하지 않는다. 작업 시작 시 `.gitignore`에 기존 로컬 변경이 있었고 해당 변경은 보존한다. 제품 동작 변경은 없으며 소스의 문서 참조, 검증 스크립트의 실행별 보고 경로, 문서 검사 CI를 갱신했다.

## 재배치 전 감사·추가 작성 단계의 실행 기록

프로젝트 루트의 기존 `venv/Scripts/python.exe`(Python 3.12.10), `-B`, `PYTHONIOENCODING=utf-8`을 사용한다. 기본 PATH Python에는 pydantic이 없어 최초 계약 실행이 import 단계에서 실패했고 기존 venv로 재실행했다. 패키지 설치·DB 준비·서버 시작은 하지 않았다.

| 명령 | 결과 | 의미·한계 |
|---|---|---|
| `venv/Scripts/python.exe -B scripts/test_ai_contracts.py` | 29개 통과, exit 0 | DTO·fixture·DB guard, 실제 DB·인증 아님 |
| `venv/Scripts/python.exe -B scripts/test_analytics_calculations.py` | 11개 통과, exit 0 | 실제 함수/SQL의 SQLite·분리 실행, 운영 PG 아님 |
| `venv/Scripts/python.exe -B scripts/test_metric_policy.py` | 12개 통과, exit 0 | 정책·결측·구간·정렬, 순위 정확도 아님 |
| `node scripts/test_ai_frontend.cjs` | 38/38 통과, exit 0 | URL·API wrapper·상태·polling, 실제 브라우저 DOM 아님 |
| `node scripts/test_metric_display.cjs` | 통과, exit 0 | 지표 집계·설명 순수 회귀 |
| `node scripts/test_viz_modules.cjs` | 통과, exit 0 | viz-state·fmt·policy·poll 순수 회귀 |
| `venv/Scripts/python.exe -B scripts/evaluate_ai_review.py --check-release` | exit 1, 미완료 4개 | 실제 final 사례 없음, criteria 미합의, needs_human_curation, 최종 모델 평가 미실행 |
| migration AST 그래프 검사 | head `a9d2f4c6e8b0` 1개, 누락 부모 없음 | 파일 의존 구조만 확인, DB upgrade 실행 아님 |
| 기존 Markdown 상대 링크 검사 | 41건 대상 파일 존재 | 일반 링크 대상, 외부·모든 anchor·prose 참조 의미 제외 |
| `python -B docs/audit/check_documents.py` | 원본 31개 보존·인벤토리 31개·새 문서 17개 도달·링크/앵커 253건 통과, exit 0 | CI·배포·의미 정확도 대체 아님 |
| 문서 검사 도구의 임시 fixture 검사 | 정상·원본 변경·파일 누락·인벤토리 누락·진입 불가·anchor 누락 6개 기대 결과 확인 | Repository 원본 대신 새 임시 자료 사용 |

문서 검사 도구는 DB·.env·네트워크를 사용하지 않으며 [절차](protocols/document-consistency.md)에 범위가 명시되어 있다. 원본 파일 해시는 [baseline](../audit/preservation-baseline.json)에 남겼다.

도구 fixture의 첫 실행은 OS 기본 임시 디렉터리 하위 작성 권한 문제로 검사 시작 전에 실패했다. 작업 공간 안의 검증용 임시 디렉터리로 재실행해 6개 사례를 확인하고 그 임시 자료를 정리했다. 이를 제품 테스트 실패나 원본 변경으로 집계하지 않는다.

## 재배치 후 검사

확인: 현재 안내와 Historical 원본을 분리했고 31개 원본의 이전 바이트 해시를 보존 위치에서 검사한다. 문서 검사 범위는 README·현재 문서·보관소 전체로 확대했다. 보존 원문은 `.gitattributes`의 `-text`로 플랫폼 줄바꿈 변환을 막는다.

| 검사 | 상태·범위 |
|---|---|
| `python -B docs/audit/check_documents.py` | 원문 31개 해시·이동표·인벤토리, Markdown 49개 도달성, 로컬 링크/앵커 409건 통과 |
| `python -B docs/audit/test_check_documents.py` | 원문 변조·누락·잘못된 이동표·이전 경로 잔류·현재 경로 누락·anchor·고립 문서 등 10개 통과 |
| `python -B scripts/test_validation_reports.py` | reports/validation의 고유 출력명·payload 보존·덮어쓰기 방지·경로 탈출 거부 3개 통과 |
| 변경 Python AST·Git diff 검사 | Python 10개 문법 통과, 문서 참조 수정 3개의 실행 AST 동일, diff 공백 검사 통과. 앱 import·DB 연결 없음 |

검사 도구는 최종 문서를 대상으로 재실행한다. 과거 17개/253건은 추가 작성 단계 결과이며 현재 폴더의 총 문서·링크 수가 아니다. 테스트는 작업 공간의 전용 `.validation` 경로를 TEMP/TMP로 사용해 새 임시 fixture만 정리했다. workflow에 문서 검사 job을 추가했지만 원격 실행은 미확인이다. 실제 DB 검증 스크립트는 실행하지 않고 결과 출력 경로만 바꿨다.

## 미실행·미확정

| 항목 | 상태·필요한 근거 |
|---|---|
| 격리 PG 빈 설치·기존 upgrade·backend/AI API/보관 회귀 | 이번 미실행. 단일 migration head가 SQL 성공 증거는 아님 |
| Redis·별도 worker·dispatcher·장애 주입 | 이번 미실행. 실제 큐 통합을 eager로 대체하지 않음 |
| 실제 OAuth·GitHub·private 권한 회수·PR 전체 수집 | 이번 미실행. 공유 read 정책의 실제 인증 사례도 남아 있음 |
| 운영 DB·현재 snapshot·배포 설정 | 이번 미조회. 로컬 과거 보고는 별도 증거 |
| 실제 cloud smoke·가격·schema·usage | 이번 미호출. 등록된 profile 값은 실측 과금 아님 |
| 로컬 런타임 설치 모델·성능·내용 품질 | 이번 미실행 |
| 사람 정답·criteria·final 평가 | manifest와 출시 점검에서 미완료 확인 |
| 실제 브라우저 DOM·모바일·코드 보기·공유 화면 | 이번 미실행. Node 검사는 별도 |
| 최신 원격 CI | 이번 조회하지 않음. workflow 존재와 원격 통과는 별개 |
| 분석 지표 경험적 타당성 | 계획 상태, 정답·미래 구간 실험 필요 |

추정: 출시 판단은 구조·안전·실인증·모델 품질·운영 환경을 각각 보고해야 한다. 현재 문서 정리 완료와 제품 출시 수용 완료는 별개다. [정리 완료 조건](../audit/organization-plan.md)과 [결정 목록](../decisions/index.md)을 함께 읽는다.

## Windows 데스크톱 작업 상태 — 2026-10-28

위 내용은 2026-10-23 문서 정리 시점 기록이다. 이후 Windows 데스크톱 전환(P01~P07)과 QA 단계 D 결함 수정이 진행됐다. 현재 상태는 [통합 추적표](../migration/validation/traceability-2026-10-28.md)와 [Stage D 기록](../migration/validation/stage-d-2026-10-28.md)을 따른다.

## P08b·P09 후속 상태 — 2026-10-28

[후속 구현·검증 기록](../migration/validation/p08b-p09-2026-10-28.md)과 [통합 추적표 증분](../migration/validation/traceability-2026-10-28.md)을 따른다. schema8 연결·원격 job 알림·공개 증명/cloud 동의·최소 진단·Windows CI 구성을 추가했다. 실제 서버 OAuth·웹/앱 동시 사용 인수는 staging 미준비로 미완료다. 현재 후보 clean VM·실제 모델·editor 화면·installer/서명/배포와 최종 보안 인수는 완료가 아니다.
