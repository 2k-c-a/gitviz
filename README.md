# GitViz

GitHub 저장소의 개발 이력을 수집하고 수정 집중도·공동 변경·협업 관계를 분석한다. 결과는 기준 SHA와 기간을 가진 불변 스냅샷으로 저장한다. 선택 기능인 AI 리뷰는 PR·커밋의 변경 설명과 코드 검토를 돕는다.

**[전체 문서 안내](docs/index.md)** · [현재 기능 범위](docs/product/current-scope.md) · [검증 상태](docs/validation/current-status.md)

## 로컬 실행

Python 3.12, Git, Docker를 준비한다. 프로젝트 루트 PowerShell에서:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
if (!(Test-Path .env)) { Copy-Item .env.example .env }
docker compose up -d db redis
```

[.env.example](.env.example)에 따라 DB·Redis·GitHub OAuth·토큰 암호화 키·고유 세션 비밀키를 설정한 뒤 실행한다. 로컬 compose DB 예시는 다음과 같다.

```text
DATABASE_URL=postgresql://gitviz:gitviz@localhost:5432/gitviz
DATABASE_URL_ASYNC=postgresql+asyncpg://gitviz:gitviz@localhost:5432/gitviz
REDIS_URL=redis://localhost:6379/0
FRONTEND_URL=http://localhost:8000
```

```powershell
python -m alembic upgrade head
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

별도 가상환경 터미널에서 일반 분석 worker를 실행한다.

```powershell
python -m celery -A app.core.celery_app:celery worker --loglevel=info --pool=solo
```

접속 주소는 `http://localhost:8000`이다. 앱은 스키마를 자동 생성하지 않으며 새 분석·재분석에는 로그인이 필요하다. [설치·프로세스 안내](docs/operations/setup.md)와 [접근·스냅샷·backfill](docs/architecture/repository-access-and-snapshots.md)을 참조한다.

## AI 리뷰

기본 비활성이다. 클라우드는 Anthropic·OpenAI·Gemini 사용자 BYOK를, 같은 PC 실행은 Ollama·LM Studio·KoboldCpp를 지원한다. 입력 고정 → 미리보기·동의 → 설명/리뷰 → 근거·피드백 흐름을 제공한다. 결과·인용·메모의 공유 읽기 조건은 [AI API](docs/contracts/ai-api.md)와 [미확정 공유 정책](docs/decisions/index.md#d01)을 확인한다.

화면은 `/pages/ai-review.html?repo=<id>`다. 설정·worker/dispatcher 실행은 [AI 운영](docs/operations/ai.md), 모델별 한도·로컬 조건은 [제공자 안내](docs/operations/providers.md)를 따른다. 로컬 실행에는 외부 전송 허용이 필요하지 않으며 클라우드는 별도 전송 허용과 공개 저장소 정책을 적용한다.

S12 인수인계·S13 보고서는 폐기했고 S14 자동 리뷰·S15 GitHub 댓글 게시는 보류다. [현재 범위](docs/product/current-scope.md)와 [후속 계획](docs/product/roadmap.md)을 구분한다. 실제 모델 품질을 구조 검증만으로 보장하지 않는다.

## 검증과 문서

```powershell
python -B docs/audit/check_documents.py
```

위 명령은 문서 링크·인벤토리·이동한 원본의 해시를 검사한다. 제품 검증은 [일관성·회귀 절차](docs/validation/protocols/document-consistency.md), [모델 평가 절차](docs/validation/protocols/ai-evaluation.md)를 따른다. DB 검사는 운영과 분리한 `gitviz_test` 이름의 DB가 필요하며 큐 통합은 전용 Redis DB를 사용한다.

| 목적 | 문서 |
|---|---|
| 지표 정의·분모·해석 | [분석 계약](docs/contracts/analytics.md) · [지표 정의](docs/contracts/metrics.md) |
| 수집·프론트 계약 | [수집](docs/contracts/collection.md) · [프론트](docs/contracts/frontend.md) |
| 정책 충돌·미확정 | [결정 목록](docs/decisions/index.md) |
| 현재 실행 결과·남은 검사 | [검증 상태](docs/validation/current-status.md) |
| 과거 기획·설계·수용·검증 원문 | [Historical 자료](docs/archive/index.md) |
| 이전→현재 경로·보존 근거 | [인벤토리](docs/audit/inventory.md) · [정리 기록](docs/audit/organization-plan.md) |
