# 운영 DB·브라우저 통합 검증

검증일: 2026-10-02 (Asia/Seoul)

## 검증 환경

- 프로젝트 `.env`에 설정된 운영 PostgreSQL DB의 기존 데이터 사용.
- 실제 PostgreSQL 트랜잭션에서 `SHOW transaction_read_only = on` 확인.
- `scripts/serve_readonly_validation.py`에서 기존 repository/analytics/auth 라우터와 프론트를 실행. `app.main`의 테이블 생성 및 startup DDL은 실행하지 않음.
- 변경 HTTP 메서드 및 OAuth 로그인/콜백은 차단. 수집·재분석·마이그레이션·운영 데이터 변경 없음.
- 기존 `venv`의 Python 3.11 실행 파일 경로가 없어 `.venv`에 Python 3.12와 프로젝트 requirements를 설치. `pip check` 통과.

## DB 및 API

통합 검증 32개 통과, 이 중 HTTP 조회 22개 모두 200.
세부 결과 및 측정 시간은 `INTEGRATION_RESULTS.json`에 기록.

| 대상 | 검증 결과 |
| --- | --- |
| repo 6, 단일 파일 | 현재 파일 1개, 트리/요약/네트워크 정상 응답 |
| repo 9, GitVizz | 현재 파일 209개, 트리·위험도·BF·결합도·파일 상세·기여자 상세 계약 일치 |
| repo 1, FastAPI | 현재 파일 3,139개, 트리 조회 약 4.17초, 네트워크 약 0.38초 |
| repo 11, jest | 커밋 0개, 현재 파일 목록 빈 결과, 빈 트리/네트워크 정상 응답 |

공동 수정 SQL은 API와 별개로 실제 PR 커밋의 파일 변경 레코드를 Python에서 PR·파일별 기여자 집합으로 집계해 대조함.

- repo 9: 변경 레코드 145개, 공동 수정 쌍 1개, 리뷰 포함 방향 엣지 4개.
- repo 1: 변경 레코드 75개, 공동 수정 쌍 0개, 리뷰 포함 방향 엣지 645개.
- 두 저장소 모두 독립 집계와 일치. 결과는 수집된 PR·커밋 연결 데이터의 범위이며, 저장소 전체 협업의 완전성을 의미하지 않음.

## 브라우저

Codex 인앱 브라우저에서 실제 로컬 HTTP 페이지와 운영 DB 응답을 사용.

- GitVizz 코드 분석: 평균 점수 0.09, BF=1 파일 161개, 강결합 파일 13개 표시.
- BF순/영향도순 전환과 범례 변경 정상.
- 실제 기여 비중 80%·20% 파일에서 BF=1, 소유권 설명, 상대 점수 설명 확인.
- 파일 상세의 공동 변경 후보 2건·결합도 67% 표시 확인.
- 파일 → 기여자 상세 이동과 소유 파일·협업자 목록 확인.
- 협업 네트워크: 노드 9개/엣지 4개, 기여자 9명/클러스터 6개/고립 5명 표시.
- 단일 파일: 점수 0.00/BF=1 표시와 정규화 설명 확인.
- 빈 결과: 빈 트리·빈 목록 표시 및 모두 펼치기 비활성화 확인.
- FastAPI: 현재 파일 3,139개, 평균 점수 0.03, BF=1 파일 1,380개, 강결합 164개, 목록 20행과 대규모 펼치기 제한 확인.
- 정상 연결 후 코드 분석/네트워크 콘솔 오류 없음. 최초 샌드박스 DB 네트워크 차단으로 발생한 500은 허용된 네트워크 환경에서 서버를 재실행한 뒤 해소됨.

## 검증 중 수정

협업 데이터가 표시돼도 헤더에 고정으로 남던 “분석 연동 예정”을 수정.
실제 조회 상태에 따라 “결과 조회 중/완료/실패”를 표시하며 브라우저에서 완료 상태 확인.

## 재실행

프로젝트 루트에서 다음 명령을 사용. `.venv`와 `.env`가 필요함.

```powershell
.\.venv\Scripts\python.exe -m uvicorn scripts.serve_readonly_validation:app --host 127.0.0.1 --port 8000
```

다른 터미널에서:

```powershell
.\.venv\Scripts\python.exe scripts/verify_production_integration.py
.\.venv\Scripts\python.exe scripts/test_analytics_calculations.py
node scripts/test_metric_display.cjs
```

OAuth 로그인 후 내 프로젝트 화면과 신규 수집/재분석은 이번 읽기 전용 검증 범위에서 제외. 로그인 전용 화면의 문구는 이전 프론트 변경에 포함됐지만, 로그인 상태의 브라우저 통합 검증은 수행하지 않음.
