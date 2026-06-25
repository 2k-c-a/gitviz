# GitViz 핸드오프 노트 — 시각화 → 분석 모듈

> 작성: 시각화 담당 / 대상: 분석 모듈 담당
> 관련 PR: #10 (`fix/db-url-from-env` → `main`)

이 문서는 **시각화 쪽에서 끝낸 부분**과 **분석 모듈 쪽에서 채워야 할 부분(데이터 계약)** 을 정리한다.

---

## 1. 한눈에 보는 현재 상태

| 영역 | 상태 | 비고 |
|---|---|---|
| 수집 파이프라인 | ✅ 동작 | `POST /repositories` → Celery 수집 → DB |
| 결과 Analytics API | ✅ 동작 | `app/api/analytics.py`, repo_id 범용 DB read |
| 타임라인 페이지 | ✅ 실데이터 | 헤더·지표·커밋차트·도넛·릴리즈·상위기여자 |
| 검색→분석→결과 이동 | ✅ 동작 | 완료 시 `timeline.html?repo=<id>` |
| 위험도/협업네트워크/코드영향도/BusFactor | ⏳ 플레이스홀더 | **파생 데이터 필요 = 분석 모듈 작업** |
| 타임라인 기여자 히트맵 | ⏳ 플레이스홀더 | 주별 집계(raw로 가능, 미구현) |

**경계(계약):** 시각화는 **DB read만** 한다. 분석 모듈이 파생 데이터를 **DB에 적재(write)** 하면, 시각화가 그걸 읽어 그린다. 접점은 DB 테이블 스키마다.

---

## 2. 로컬 실행

```bash
# 1) .env 준비 (gitignore — 저장소에 없음. 직접 작성)
#    DATABASE_URL, REDIS_URL, GITHUB_TOKEN, GITHUB_CLIENT_ID/SECRET,
#    SESSION_SECRET_KEY, FRONTEND_URL(=http://localhost:8000)

# 2) Redis (Docker)
docker run -d --name gitviz-redis -p 6379:6379 redis:7-alpine

# 3) Celery 워커 (Windows는 --pool=solo 필수)
python -m celery -A app.core.celery_app:celery worker --loglevel=info --pool=solo

# 4) 웹/API 서버 (프론트도 함께 서빙)
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

접속은 `http://localhost:8000` (OAuth 콜백이 localhost 기준이라 `127.0.0.1`로 들어가면 로그인 state 오류 — **localhost로 통일**).

**DB 내 테스트 표본:** react(`repo_id=1`, 풀 데이터) · Hello-World(`12`) · Spoon-Knife(`13`).

---

## 3. 결과 Analytics API (이미 구현됨, 패턴 참고용)

`app/api/analytics.py` — prefix `/repositories/{repository_id}/analytics`:

- `GET /summary` — 총 커밋·기여자·릴리즈·버그픽스%·최근30일
- `GET /timeline?months=36` — 월별 커밋/활성기여자/버그픽스%
- `GET /commit-types` — 커밋유형 분포(도넛)
- `GET /releases?limit=5` — 릴리즈 목록
- `GET /top-contributors?limit=5` — 상위 기여자

**새 페이지를 붙일 때 이 패턴을 그대로 복제**: 분석 결과 테이블을 `text()`/ORM으로 읽어 JSON 반환 → 프론트가 fetch.

프론트 연결 지점:
- `frontend/js/api.js` — fetch 헬퍼 + `getRepoId()`(`?repo=` → localStorage → 1)
- `frontend/js/timeline.js` — 타임라인 렌더(참고 구현)
- `frontend/js/repoHeader.js` — 분석페이지 헤더(`.js-repo-title`)에 실 repo 바인딩

---

## 4. 데이터 계약 — 4개 페이지 (분석 모듈이 채울 부분)

각 페이지는 아래 파생 데이터가 DB에 있어야 한다. 테이블/컬럼명은 **ERD(gitviz_erd_v8) 기준으로 합의**. 적재되면 시각화 쪽에서 read API + 렌더를 붙인다(플레이스홀더 자리 그대로).

### 4-1. 위험도 (`risk.html`)
- **지표카드**: 고위험 파일 수(score≥0.7), 평균 Risk Score, Hidden Coupling 수, 평균 Entropy
- **파일별 히트맵**: 파일경로 + 변경빈도·Churn·Entropy·버그비율·Co-change·종합 점수
- **Risk Score 시계열**: 파일별 월간 추이
- 산식 예: `Risk = w1·변경빈도 + w2·Churn + w3·Entropy + w4·버그비율 + w5·Co-change`
- 소스 raw: `commits`, `commit_file_changes`(additions/deletions), `commit_type='bugfix'`

### 4-2. 협업 네트워크 (`network.html`)
- **지표카드**: 전체 기여자(노드), 협업 엣지 수, 클러스터 수, 고립 기여자 비율
- **그래프**: 노드=기여자(크기=커밋수, 중심성), 엣지=협업 가중치(co-edit + review), 색=클러스터
- **허브 기여자**(중심성순), **네트워크 지표**(밀도/평균강도), **상위 협업 쌍**
- 소스 raw: `commit_file_changes`(공동 수정), `pr_reviews`(리뷰어↔작성자)

### 4-3. 코드 영향도 (`coupling.html`)
- **지표카드**: 분석된 파일쌍, 강결합 쌍(≥70%), 평균 결합도, 제외된 커밋(노이즈)
- **영향도 그래프**: 선택 파일과 함께 변경되는 파일(결합도%)
- **결합도 분포** 히스토그램
- 소스 raw: `commit_file_changes`(같은 커밋/PR에서 함께 변경된 파일쌍) + IDF 보정

### 4-4. Bus Factor (`busfactor.html`)
- **지표카드**: 프로젝트 BF(파일별 중앙값), 위험 파일(BF=1), 평균 Entropy, 위험 모듈
- **BF=1 고위험 파일**, **BF 분포 차트**, **기여자별 독점 파일 수**, **파일 소유권 상세**
- 소스 raw: `commit_file_changes`(파일별 기여자 변경 집중도 → Ownership Entropy/BF)

---

## 5. 주의사항 (운영)

- ⚠️ **검색창에 facebook/react 넣지 말 것** — 커밋 3.4만·PR 수천이라 재분석이 solo 워커를 장시간 점유. react 결과는 분석 말고 `timeline.html?repo=1`로 직접 확인.
- 분석 모달 진행률은 **완료(done) 전까지 90%에서 고정**(총량 미상). 멈춘 게 아님.
- 테스트는 **작은 레포**(Hello-World, Spoon-Knife 등)로.
- IoT(`arduino/`, `app/api/iot.py`)는 졸업작품 범위 외 — gitignore로 제외됨(로컬 전용).

---

## 6. 수집→분석 체이닝 (참고)
`app/tasks/analyze_repository.py` 끝에서 분석 Task를 체이닝하면 자연스럽다. 전달값: `repository_id`, `analysis_job_id`, `co_change_unit_used`, `is_incremental`.
