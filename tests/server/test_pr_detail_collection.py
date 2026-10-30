"""PR 상세 수집의 완전성 판정과 실패 경로 테스트 (외부 의존 없음).

검증 대상:
  1) classify_pr_detail_status — 파일·커밋·리뷰 세 영역을 모두 보고 판정하는가.
     파일만 완전하고 커밋/리뷰가 실패한 PR을 complete 로 기록하면, 재수집 판정이
     그 상태만 보기 때문에 누락이 영구히 복구되지 않는다.
  2) _failure_reason — 인증 없음/권한 부족/한도/그 외 페이지 오류 분류.
  3) _page_all — 중간 페이지 실패 시 예외를 삼키지 않고 (부분 결과, 사유)를 반환.
  4) _parse_pr_node — 섹션별 완전성 플래그를 따로 돌려주는가.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.collectors.github.graphql_pr_collector import (
    _failure_reason, _page_all, _parse_pr_node,
)
from app.tasks.analyze_repository import classify_pr_detail_status, GITHUB_PR_FILE_CAP


def details(files=0, commits=0, reviews=0, changed=None, files_complete=True,
            commits_complete=True, reviews_complete=True, **reasons):
    return {
        "files": [{"filename": f"f{i}", "additions": 0, "deletions": 0, "status": "modified"}
                  for i in range(files)],
        "commits": [f"sha{i}" for i in range(commits)],
        "reviews": [{"reviewer_login": "r", "state": "approved", "submitted_at": None}
                    for _ in range(reviews)],
        "changed_files": changed,
        "files_complete": files_complete,
        "commits_complete": commits_complete,
        "reviews_complete": reviews_complete,
        "files_reason": reasons.get("files_reason"),
        "commits_reason": reasons.get("commits_reason"),
        "reviews_reason": reasons.get("reviews_reason"),
        "failure_reason": reasons.get("failure_reason"),
    }


class FakeClient:
    """지정한 호출 횟수 이후 실패하는 GraphQL 클라이언트 대역."""

    def __init__(self, pages, fail_at=None, exc=None):
        self.pages, self.fail_at, self.exc = pages, fail_at, exc or RuntimeError("boom")
        self.calls = 0

    def query(self, query_str, variables):
        self.calls += 1
        if self.fail_at is not None and self.calls >= self.fail_at:
            raise self.exc
        page = self.pages[min(self.calls - 1, len(self.pages) - 1)]
        return {"data": {"repository": {"pullRequest": {"commits": page}}}}


class ClassifyStatus(unittest.TestCase):
    def test_all_sections_complete(self):
        s, reason, collected, total = classify_pr_detail_status(
            details(files=3, commits=2, reviews=1, changed=3))
        self.assertEqual((s, reason, collected, total), ("complete", None, 3, 3))

    def test_files_complete_but_commits_failed_is_not_complete(self):
        """핵심 회귀: 파일이 완전해도 커밋 페이지가 실패하면 complete 가 아니다."""
        s, reason, _c, _t = classify_pr_detail_status(
            details(files=3, commits=30, changed=3,
                    commits_complete=False, commits_reason="page_error"))
        self.assertEqual(s, "partial")
        self.assertEqual(reason, "page_error")

    def test_files_complete_but_reviews_failed_is_not_complete(self):
        s, reason, _c, _t = classify_pr_detail_status(
            details(files=1, reviews=30, changed=1,
                    reviews_complete=False, reviews_reason="rate_limited"))
        self.assertEqual((s, reason), ("partial", "rate_limited"))

    def test_api_file_limit_only_when_other_sections_complete(self):
        capped = details(files=GITHUB_PR_FILE_CAP, commits=1, changed=GITHUB_PR_FILE_CAP + 10,
                         files_complete=False, files_reason="page_error")
        self.assertEqual(classify_pr_detail_status(capped)[1], "api_file_limit")

    def test_api_file_limit_not_used_when_commits_incomplete(self):
        """커밋이 미완이면 재시도로 복구되므로 재수집 제외 사유를 쓰면 안 된다."""
        capped = details(files=GITHUB_PR_FILE_CAP, commits=1, changed=GITHUB_PR_FILE_CAP + 10,
                         files_complete=False, files_reason="page_error",
                         commits_complete=False, commits_reason="rate_limited")
        self.assertNotEqual(classify_pr_detail_status(capped)[1], "api_file_limit")

    def test_nothing_collected_is_failed(self):
        s, reason, _c, _t = classify_pr_detail_status(
            details(files_complete=False, failure_reason="unauthenticated"))
        self.assertEqual((s, reason), ("failed", "unauthenticated"))

    def test_partial_when_files_short_of_total(self):
        s, _r, collected, total = classify_pr_detail_status(
            details(files=5, changed=20, files_complete=False, files_reason="page_error"))
        self.assertEqual((s, collected, total), ("partial", 5, 20))


class FailureReason(unittest.TestCase):
    def test_classification_table(self):
        cases = {
            "GraphQL HTTP 401: Bad credentials": "unauthenticated",
            "GraphQL 403 unauthenticated: 토큰이 없어": "unauthenticated",
            "requires authentication": "unauthenticated",
            "403 Forbidden": "forbidden",
            "Resource not accessible by integration": "forbidden",
            "API rate limit exceeded": "rate_limited",
            "HTTP 429 Too Many Requests": "rate_limited",
            "HTTP 502 bad gateway": "page_error",
        }
        for message, expected in cases.items():
            with self.subTest(message=message):
                self.assertEqual(_failure_reason(Exception(message)), expected)

    def test_unauthenticated_wins_over_forbidden(self):
        """'403 unauthenticated'처럼 두 신호가 겹치면 인증 없음으로 판정한다."""
        self.assertEqual(
            _failure_reason(Exception("GraphQL 403 unauthenticated: no token")),
            "unauthenticated")


class PageAll(unittest.TestCase):
    PAGE = {"nodes": [{"commit": {"oid": "a"}}], "pageInfo": {"hasNextPage": True, "endCursor": "c"}}

    def extract(self, conn):
        return [n["commit"]["oid"] for n in conn.get("nodes", [])]

    def test_mid_page_failure_returns_partial_and_reason(self):
        client = FakeClient([self.PAGE], fail_at=3, exc=Exception("HTTP 502 bad gateway"))
        out, reason = _page_all(client, "q", {}, "cursor0", "commits", self.extract)
        self.assertEqual(reason, "page_error")
        self.assertEqual(out, ["a", "a"])          # 실패 전까지 받은 2페이지는 보존

    def test_success_returns_no_reason(self):
        last = {"nodes": [{"commit": {"oid": "b"}}], "pageInfo": {"hasNextPage": False, "endCursor": None}}
        client = FakeClient([last])
        out, reason = _page_all(client, "q", {}, "cursor0", "commits", self.extract)
        self.assertEqual((out, reason), (["b"], None))

    def test_unauthenticated_failure_is_classified(self):
        client = FakeClient([self.PAGE], fail_at=1, exc=Exception("GraphQL HTTP 401"))
        _out, reason = _page_all(client, "q", {}, "cursor0", "commits", self.extract)
        self.assertEqual(reason, "unauthenticated")


class ParsePrNode(unittest.TestCase):
    def test_missing_pr_node_marks_all_sections_failed(self):
        d = _parse_pr_node(None, "o", "r", 1, None)
        self.assertFalse(d["files_complete"])
        self.assertFalse(d["commits_complete"])
        self.assertFalse(d["reviews_complete"])
        self.assertEqual(classify_pr_detail_status(d)[0], "failed")

    def test_single_page_node_is_complete(self):
        node = {
            "changedFiles": 1,
            "commits": {"nodes": [{"commit": {"oid": "a"}}], "pageInfo": {"hasNextPage": False}},
            "reviews": {"nodes": [], "pageInfo": {"hasNextPage": False}},
            "files": {"nodes": [{"path": "a.py", "additions": 1, "deletions": 0, "changeType": "MODIFIED"}],
                      "pageInfo": {"hasNextPage": False}},
        }
        d = _parse_pr_node(None, "o", "r", 1, node)
        self.assertTrue(d["files_complete"] and d["commits_complete"] and d["reviews_complete"])
        self.assertEqual(classify_pr_detail_status(d)[0], "complete")

    def test_commit_page_failure_keeps_files_complete_but_pr_incomplete(self):
        """파일은 한 페이지로 끝나고 커밋만 추가 페이지에서 실패하는 실제 형태."""
        node = {
            "changedFiles": 1,
            "commits": {"nodes": [{"commit": {"oid": "a"}}],
                        "pageInfo": {"hasNextPage": True, "endCursor": "c"}},
            "reviews": {"nodes": [], "pageInfo": {"hasNextPage": False}},
            "files": {"nodes": [{"path": "a.py", "additions": 1, "deletions": 0, "changeType": "MODIFIED"}],
                      "pageInfo": {"hasNextPage": False}},
        }
        client = FakeClient([{"nodes": [], "pageInfo": {"hasNextPage": True, "endCursor": "c2"}}],
                            fail_at=1, exc=Exception("HTTP 502"))
        d = _parse_pr_node(client, "o", "r", 1, node)
        self.assertTrue(d["files_complete"])
        self.assertFalse(d["commits_complete"])
        self.assertEqual(classify_pr_detail_status(d)[0], "partial")


if __name__ == "__main__":
    unittest.main(verbosity=2)
