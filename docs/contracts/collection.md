# 수집 경로와 근거 품질 계약

검토일: 2026-10-23 · 코드 기준: `a8351f0` · [문서 안내](../index.md)

## 확인한 경로 구분

| 경로 | 목적 | 근거 |
|---|---|---|
| 전체 분석 | 저장소 raw 이력·파일·PR·리뷰 수집 후 분석 snapshot 저장 | [분석 worker](../../app/tasks/analyze_repository.py), [기존 전달 원본](../archive/originals/docs/DATA_COLLECTION_HANDOFF.md) |
| AI PR 입력 | 전체 분석을 기다리지 않고 PR 목록·고정 revision·선택 코드 취득 | [PR collector](../../app/collectors/github/ai_pr_collector.py), [diff collector](../../app/collectors/git/ai_diff_collector.py) |
| AI 커밋 입력 | 고정 커밋을 첫 부모와 비교. root는 빈 트리 | [commit collector](../../app/collectors/github/ai_commit_collector.py) |

과거 수집 과제와 최초 S3 설계는 [전달 원문](../archive/originals/docs/DATA_COLLECTION_HANDOFF.md)에 보존했다. 현재 계약은 아래 내용과 collector·content policy·DTO를 함께 기준으로 검토한다.

## 현재 소비자가 지켜야 할 구분

- 전체 파일 목록·선택 파일·실제 코드 확보·모델 분석 범위는 각각 별도다. 모든 선택 파일에는 read/excluded/failed와 사유가 남는다.
- 코드 위치는 고정 SHA의 path·LEFT/RIGHT·실제 줄 번호다. 삭제·추가·rename의 좌우 경로를 섞지 않는다.
- GitHub 페이지 목록과 고정 입력은 다르다. 준비 전후 SHA 변경은 다시 선택·준비할 사유다.
- 생성 파일·바이너리·링크/서브모듈·너무 큰 파일·의심 비밀은 [content policy](../../app/services/ai/content_policy.py)로 제한한다. 탐지가 모든 비밀을 찾아낸다는 보장은 없다.
- 수집 대상 저장소를 실행·빌드·테스트하는 기능으로 해석하지 않는다. 입력 확정 뒤 새 코드를 몰래 추가하지 않는다.
- 전체 분석 job done은 PR·리뷰 수집 완전성이 아니다. PR 상세가 존재하는 비율과 페이지 완전성을 구분한다.
- 스쿼시·리베이스 PR 원본 커밋 취득 범위는 [METRICS](metrics.md)의 최근 PR head 제한·재수집 한계를 따른다. 현재 자료만으로 전 PR 원본이 확보됐다고 단정하지 않는다.

## 과거 과제의 취급

확인: 원본에는 기여자 재생성·별칭, 인증 없는 GraphQL, 누락 상세 재수집, 대규모 변경 정책 등 당시 문제가 기록되어 있다. 단순히 문서에 남았다는 이유로 현재 미해결이라고 판정하지 않는다.

추정: 과제별로 재현 조건·관련 코드·회귀 검사·마지막 실행을 연결한 후 완료/부분/미확인으로 분류한다.

미확정: 실제 OAuth·private 권한 회수·신규 원격 커밋·force push·전체 페이지 완전성·수집 반복 안정성의 현재 결과는 [검증 상태](../validation/current-status.md)에 남긴다. migration 문서의 과거 head는 현재 설치 지침으로 쓰지 않는다.
