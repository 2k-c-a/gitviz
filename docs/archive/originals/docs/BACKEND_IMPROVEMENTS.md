# 백엔드 개선 결과 — 2026-10-02

데이터 수집과 시각화 담당의 과제는 각각 DATA_COLLECTION_HANDOFF.md와 VISUALIZATION_HANDOFF.md로 분리했다. 이번 구현은 작업 실행 관리, DB 설치, 분석 이력/비교 API에 집중했다.

## 구현한 변경

### 작업 복구

- pending 15분 초과, running 6시간 초과, rate_limited의 resume_at 120초 초과를 복구 후보로 판정한다. 시간대가 없는 이전 날짜와 잘못된 resume_at도 안전하게 처리한다.
- 새 분석 요청은 저장소 행을 잠그고 진행 중인 작업을 확인한다. 모든 작업이 만료 후보일 때 워커와 같은 advisory lock을 획득해야만 기존 작업을 cancelled로 끝내고 새 작업을 만든다.
- 실제 워커가 잠금을 보유하면 409로 거부한다. 긴 정상 작업을 시간만 보고 중복 실행하지 않는다.
- 종료된 done/failed/cancelled job의 지연 전달은 실행하지 않는다. 이를 위해 analyze_repository.py에서 수집 시작 전 상태 검사만 수정했다.
- 큐 등록 실패는 failed로 기록되어 다음 요청이 다시 실행할 수 있다.
- 상태 API에 완료 결과의 snapshot_id를 추가했다.

### 설치와 스키마

- 과거 revision에서 빠진 선행 테이블과 commit_file_changes 생성을 보완했다. 과거 단수 commit_file_change가 있으면 삭제하지 않고 이름을 바꿔 데이터를 보존한다.
- 과거 수동  ALTER 변경을 고정된 migration 코드로 편입했다. SHA/파일/PR 컬럼 이름, timestamp 타입, 개인 토큰 사용자 ID, 기본 브랜치 플래그, alias unique와 조회 인덱스를 정리한다.
- 기존 e2 DB에도 적용되는 새 head `f3a4b5c6d7e8`를 추가했다. users 테이블과 토큰 컬럼도 migration에서 관리한다.
- 앱 import/startup에서 create_all을 제거했다. 서버 시작 전에 `python -m alembic upgrade head`를 실행해야 한다.
- 로컬 비동기 DB 연결은 TLS를 요구하지 않도록 보완했다. 원격 DB 설정은 기존 정책을 유지한다.
- README에 로컬 설치·Redis/worker·migration·검ㅁ증 절차를 추가하고 PostgreSQL 16 CI workflow를 작성했다.

기존 revision ID는 유지했다. 이미 적용된 revision은 다시 실행되지 않으며 새 head가 보완을 수행한다. 새 설치는 수정된 과거 이력을 처음부터 실행한다. 새 head downgrade는 어떤 컬럼이 원래 수동 생성됐는지 구분할 수 없어 데이터 보존을 위해 명시적으로 지원하지 않는다. 미버전 DB를 자동 stamp하는 기능은 없다.

### 분석 이력과 비교

- `GET /repositories/{id}/snapshots`: payload를 불러오지 않고 메타데이터만 최신순으로 조회. before_id 페이지네이션.
- `GET /repositories/{id}/snapshots/compare?before=10&after=20`: 동일 저장소·계산 버전의 불변 결과를 비교. Risk Score/BF/결합도 before/after/delta, 추가·제외·변경 건수, 관측 범위 정보를 반환.
- 변경 파일 응답은 기본 100개, 최대 1000개씩 페이지네이션한다. 비교 summary는 전체 기준이다.
- 양쪽 결과 해시를 검증하고 현재 GitHub 접근 권한도 기존 정책대로 확인한다. 캐시 헤더는 private, no-store다.
- 파일 경로 정확 일치만 사용하고 이름 변경은 결과의 제외/추가로 표시한다. 미수집 때문에 파일이 결과에서 빠질 수 있으므로 실제 Git 삭제라고 단정하지 않는다. null 지표는 0으로 보정하지 않는다.
- 상대 점수 차이가 결함 확률·절대 품질 변화가 아님을 명시한다.

## 직접 검증한 결과

격리된 로컬 PostgreSQL 18, localhost:55432의 전용 gitviz_test DB를 사용했다. 운영 .env DB는 사용하지 않았다.

- 빈 DB에서 전체 Alembic upgrade head 성공.
- 백엔드 회귀 테스트 12개 통과: 모든 앱 모델 컬럼, 구버전 데이터 보존, 단수 파일변경 테이블 보존, 누락 컬럼 보완, pending 복구, 큐 실패 후 재요청, 살아 있는 워커 잠금, cancelled 전달 차단, 시간 처리, 비교/null/버전 정책, HTTP 접근/해시/페이지네이션, 불변 trigger, 앱 DDL 없음, 로컬 async 연결.
- 기존 계산 테스트 9개, 접근·스냅샷 테스트 14개 통과.
- 검색 점수 검사와 프론트 집계 회귀 검사 통과.
- GitHub Actions workflow는 추가했지만 원격 실행은 수행하지 않았다.

## 반영 범위와 남은 검증

frontend와 collectors의 구현은 수정하지 않았다. 화면의 오류 처리/스냅샷 이동은 시각화 문서에, 실제 신규·증분·비공개 수집은 데이터 수집 문서에 남겨뒀다.
OAuth 실제 로그인·GitHub 실연동 수집·운영 DB migration은 실행하지 않았다. 운영 적용에는 명시적인 Alembic upgrade가 필요하다. 이번 결과는 격리된 DB에서의 검증이다.
분석 지표의 경험적 타당성은 검증용 라벨과 수집 범위가 필요하므로 수치를 만들어내지 않고 ANALYSIS_VALIDATION_PLAN.md에 평가 구간·정답·비교 기준을 고정했다.

대형 저장소의 전체 payload 해시 검증·중심성 계산 비용은 아직 변경하지 않았다. 이력 목록의 payload 로딩 제외와 비교 응답 페이지네이션을 먼저 적용했으며, 계산 성능 최적화는 실제 대형 입력 측정 후 진행하는 것이 적절하다.

## 실연동 후속 검증 — 2026-10-03

[LIVE_VALIDATION.md](LIVE_VALIDATION.md) 참조. 실제 clone/REST 수집에서 detached 작업 객체 때문에 결과만 저장되고 job 상태가 running으로 남는 오류를 재현했다. 재연결한 DB 세션에서 같은 AnalysisJob을 다시 조회하도록 수정했다.
이를 검증하는 성공 워커 회귀 검사를 추가해 백엔드 테스트는 13개가 됐다. 실제 공개 저장소 검사 52개 중 51개 통과했으며 PR GraphQL 인증 부족 1개는 미통과로 남겼다.
