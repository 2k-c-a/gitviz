# P00 문서 기준과 실행 기록

기록일: 2026-10-24 (Asia/Seoul). [단계 계획](../13-implementation-plan.md) · [검증 기준](../12-testing-strategy.md) · [결정](../14-decisions-and-open-questions.md).

## 승인·현재 상태

사용자의 “다음 구현 승인할게”를 직전 제안의 첫 단계 P00 문서 반영 승인으로 적용했다. 소스·설정·패키지·DB·설치·빌드·배포는 이번 범위에 없다. 문서 기준 반영과 전체 P00 정책/의존성 게이트 완료를 구분한다. 상태: P00 부분 구현, P01 미착수. exact dependency lock·호환성/라이선스·Q/D 정책은 별도 검토/승인 대기다.

## 작업 시작 기준

- repository: GitViz, branch `fix/search-score-caption`, HEAD `a8351f0bf24c78625990f8756e5a03fc42a002c6`.
- 기존 dirty 상태: 수정12·삭제29·미추적75개. 보존·원복하지 않음.
- tracked/untracked 비무시 파일389개의 SHA-256 또는 부재 상태를 프로세스 메모리에 수집. 실제 .env·ignored 파일/secret은 읽거나 기록하지 않음.
- 기존 Windows 구현 경로 desktop/app-workspace/tests-workspace는 없음. 설치된 도구 관찰값 Node24.20.0·npm11.19.0·Python3.12.10·Git2.53.0.windows.2. 제품 지원 버전 확정 아님.

## 변경 대상과 근거

03 ADR 대응·06 P01 schema·07 RPC/parser/bootstrap·08 책임/수명·12 검증·13 파일/의존성·14 승인/미결 상태 및 이 기록만 변경한다. REQ-P01/P03/P08의 기반 계약과 전체 E/N/P 추적 방식에 관련되며 제품 요구사항 검증 완료를 뜻하지 않는다. 정책 미정·제품 코드 미구현은 계속 남긴다.

## 실제 검증

문서 검사는 수행 후 이 표에 실제 결과만 기록한다. 앱 unit/integration·PG/Redis·migration·Electron·clean VM·AI·성능은 이번 실행하지 않는다. 앞선 단계 A의 계약29/계산11/정책12/UI38 등의 결과를 이번 실행으로 승계하지 않는다.

| 명령/검사 | 종료코드·결과 |
|---|---|
| `python -X utf8 -B docs/audit/check_documents.py` | exit0; 원문31·인벤토리31·Markdown65·링크/앵커607 통과 |
| 저장하지 않는 Python 정적 검사 | 최초 exit1: 검사 자체가 동일 제한을 공백 유/무 두 형태 모두 요구한 오류. 공백 허용 정규식으로 수정해 재실행 exit0; ADR01~09 단일 제목·RPC 수치/operation·P01 목록/승인 게이트·fence 검사 통과. 제품 테스트 아님 |
| 메모리 SHA-256 기준선 비교 | 변경7개·신규1개 모두 승인된 migration 문서. 나머지382개 상태/바이트 동일; source/config/package/DB 변경 없음 |

## 복구와 다음 게이트

문제 발생 시 이번 문서 편집만 작업 전 내용과 대조해 수정한다. Git reset·기존 dirty 변경 복원·서버 DB downgrade는 사용하지 않는다. 다음 검토는 exact package 버전·전이 의존성/hash·라이선스/SBOM·취약점·Windows wheel/DLL·Forge/TS 조합과 P01 구현 승인이다. 설치·build/package는 명령·영향·복구안 검토 후 별도 승인한다.
