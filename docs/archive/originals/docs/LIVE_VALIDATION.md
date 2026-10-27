# 공개 저장소 실연동 검증 결과

정리일: 2026-10-03 (Asia/Seoul)

## 결론

최종 52개 검사 중 51개 통과했다. 직접 커밋 중심 저장소는 전체 통과했으며, Python 예제 저장소는 Git 이력·분석·재분석은 통과했지만 GitHub GraphQL 인증 토큰 부족으로 PR 상세 수집을 검증하지 못했다. 이 항목을 통과로 취급하지 않았다.

| 대상 | 기준 SHA | 이력/파일 | 결과 |
| --- | --- | --- | --- |
| [octocat/git-consortium](https://github.com/octocat/git-consortium) | `b33a9c7c02ad93f621fa38f0e9fc9e867e12fa0e` | 커밋 6, 현재 파일 3, 파일 변경 7, 병합 PR 0 | 26/26 통과 |
| [navdeep-G/samplemod](https://github.com/navdeep-G/samplemod) | `d469f2f5e0a472be2c7d95ff90ed46e975810dc8` | 커밋 29, 현재 파일 18, 파일 변경 42, 병합 PR 9 | 25/26 통과. PR 파일 상세 0/9 |

선정 이유: 작은 Git 이력으로 반복 수집 비용을 줄이면서 직접 커밋 경로와 PR이 있는 Python 코드 경로를 함께 확인할 수 있다. 많은 PR을 가진 Hello-World/Spoon-Knife는 제외했다.

기계 판독 결과는 [LIVE_VALIDATION_RESULTS.json](LIVE_VALIDATION_RESULTS.json), 수정 전 재현 기록은 [LIVE_VALIDATION_INITIAL.json](LIVE_VALIDATION_INITIAL.json)에 있다. 각 실행의 기준 시각은 JSON의 UTC window_end를 사용했다.

## 실제로 확인한 항목

- 공개 저장소를 실제 Git clone/fetch하고 GitHub REST에서 PR 목록·릴리스·커밋 작성자 정보를 조회했다. Git/REST 데이터 응답은 대역으로 바꾸지 않았다.
- 기본 브랜치 SHA 집합과 DB on_default_branch 집합이 일치한다.
- Git diff-tree로 커밋마다 독립 집계한 파일 변경·추가/삭제 줄 수가 DB와 일치한다.
- 기준 SHA의 Git ls-tree 파일 목록과 분석 tree 파일 목록이 일치한다.
- summary 커밋 수가 분석 종료 시각 이전의 reachable SHA 수와 일치한다.
- summary/tree/risk/BF/coupling/network/timeline/heatmap/파일 상세 HTTP 조회가 정상이다.
- 파일별 Risk/BF 응답 값과 필요한 파일 ID가 일치한다.
- 수집된 신원 매핑·commit_type을 입력으로 별도 누적 계산한 BF 80% 경계가 API와 일치한다. 기여자 신원 분류 자체의 정답 검증은 아니다.
- 같은 스냅샷의 반복 summary가 동일하다.
- 종료 시각과 SHA를 고정해 재분석했을 때 payload 해시가 동일하고, raw 커밋·파일·변경 행이 중복 증가하지 않는다.
- 동일 입력 두 스냅샷의 비교 changes가 빈 배열이다.
- job done, completed_at, last_commit_sha, parameters.snapshot_id와 실제 저장 스냅샷이 함께 확정된다.

## 실행 중 발견하고 수정한 오류

첫 git-consortium 실행은 실제로 결과를 저장하고도 DB job이 running으로 남았다. 수집 전 DB 세션을 닫은 뒤 Repository만 새 세션에서 다시 조회했고, AnalysisJob은 이전 세션에서 분리된 객체를 계속 사용하고 있었다. 따라서 완료 상태 변경이 저장되지 않았다.

`app/tasks/analyze_repository.py`에서 작업 ID를 보관하고 세션 재연결 후 같은 AnalysisJob을 다시 조회하도록 수정했다. 수집 알고리즘은 변경하지 않았다.

수정 후 실연동에서 완료 상태·snapshot_id 저장과 재분석을 확인했고, 같은 세션 재연결을 거치는 성공 워커 회귀 검사도 추가했다. 백엔드 검사 13개, 기존 계산 9개, 접근/저장 14개와 프론트 집계 검사가 통과했다.

## 통과하지 않은 PR 검사

samplemod의 병합 PR 목록 9건은 수집했으나 GraphQL용 인증 토큰이 없었다. 실제 비인증 GraphQL 호출의 한도가 0이라 수집 워커가 한 시간 대기에 진입했다. 테스트 프로세스를 종료하고, 격리 DB에서 해당 검증 job을 실패로 기록했다.

최종 검증 스크립트는 토큰 없는 GraphQL을 즉시 인증 부족으로 기록하고, 한 번의 대기 5초·collector별 누적 15초를 넘는 재시도를 중단한다. 이 제한은 검증 스크립트에만 있고 운영 collector 코드는 유지했다.

현재 수집 태스크는 PR별 상세 오류를 로그에 남기고 계속 진행하여 job이 done일 수 있다. 최종 검사에서는 별도로 `prs_with_files == merged_pr_count`를 확인하여 samplemod를 실패 처리했다. 수집 담당에게 **인증 사전 점검, PR 상세 수집 실패 상태/부분 완료 기록, 누락 상세 재수집**을 전달했다.

PR 커밋 연결 비율을 파일 상세 수집률과 혼동하지 않았다. squash/rebase PR의 원본 SHA는 기본 브랜치에서 도달하지 않을 수 있으므로 연결 비율은 별도 범위 정보다.

## 지표 해석에서 관찰한 점

- git-consortium은 기여자 1명이고 3개 파일의 BF는 모두 1이다. product-backlog.md의 상대 위험도는 0.3667이다.
- 이 저장소는 파일이 3개라 현재 10% 대형 변경 규칙에 의해 6개 커밋이 전부 제외된다. 결합도 0건은 변경이 없다는 뜻이 아니다. 작은 저장소의 필터 정책은 수집 담당 문서에 추가했다.
- samplemod는 기여자 11명, 계정 미연결 2명이다. Makefile BF=3, README.rst BF=4로 독립 누적 집계와 일치한다.
- samplemod 협업 네트워크의 엣지 0건은 PR 상세·리뷰 미수집을 포함한 결과이므로 협업이 없다는 결론을 내릴 수 없다.

## 환경과 검증 한계

- 운영 DB와 별개인 PostgreSQL 18, localhost:55432의 gitviz_test_live DB 및 `.validation/live-clones`를 사용했다. 운영 데이터·기존 storage/repos는 변경하지 않았다.
- 세션은 격리 DB의 테스트 사용자와 서명 쿠키로 준비했다. 실제 OAuth 로그인/콜백은 실행하지 않았다.
- Celery의 실제 작업 함수는 실행했지만 큐 transport는 eager/in-memory로 사용했다. Redis 연결·별도 워커 프로세스 전달은 이번 검증 범위가 아니다.
- GitHub 이메일 Search 단계는 비인증 한도를 아끼기 위해 껐다. commit API 식별은 실제로 실행했다.
- 실제 신규 원격 커밋 추가, force push, 비공개 저장소, PR 상세·리뷰의 정상 인증 수집은 검증하지 않았다. 대형 저장소 성능과 점수의 예측 정확도도 이 결과로 보장하지 않는다.

## 재실행

프로젝트 requirements를 설치하고 운영 DB와 별개인 전용 DB를 만든 뒤 실행한다. PR 상세까지 검사하려면 허용된 GitHub 토큰을 로컬 환경에 설정해야 한다. 토큰을 검증 결과나 팀 공유 문서에 넣지 않는다.

```powershell
$env:GITVIZ_TEST_DATABASE_URL='postgresql://gitviz_test:validation@localhost:5432/gitviz_test_live'
python scripts/verify_live_repository.py
```

실패한 검사 하나라도 있으면 종료 코드 1을 반환한다. 인증이 없는 현재 결과의 51/52는 그 정책에 따른 예상 실패이며 전체 통과로 표시하지 않는다.
