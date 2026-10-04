import time
import requests
import os
from typing import List, Dict, Optional
from requests.exceptions import ConnectionError as RequestsConnectionError, Timeout

GRAPHQL_URL = "https://api.github.com/graphql"

PR_DETAILS_QUERY = """
query GetPRDetails($owner: String!, $repo: String!, $number: Int!) {
  rateLimit { remaining resetAt cost }
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      changedFiles
      commits(first: 30) {
        nodes { commit { oid } }
        pageInfo { hasNextPage endCursor }
      }
      reviews(first: 30) {
        nodes {
          author { login }
          state
          submittedAt
        }
        pageInfo { hasNextPage endCursor }
      }
      files(first: 30) {
        nodes { path additions deletions changeType }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
"""

PR_COMMITS_NEXT_QUERY = """
query($owner: String!, $repo: String!, $number: Int!, $cursor: String!) {
  rateLimit { remaining resetAt }
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      commits(first: 100, after: $cursor) {
        nodes { commit { oid } }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
"""

PR_REVIEWS_NEXT_QUERY = """
query($owner: String!, $repo: String!, $number: Int!, $cursor: String!) {
  rateLimit { remaining resetAt }
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      reviews(first: 100, after: $cursor) {
        nodes { author { login } state submittedAt }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
"""

PR_FILES_NEXT_QUERY = """
query($owner: String!, $repo: String!, $number: Int!, $cursor: String!) {
  rateLimit { remaining resetAt }
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      files(first: 100, after: $cursor) {
        nodes { path additions deletions changeType }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
"""

STATUS_MAP = {
    "added": "added",
    "changed": "modified",
    "deleted": "removed",
    "renamed": "renamed",
    "copied": "copied",
}


class GraphQLClient:
    def __init__(self, token: Optional[str] = None, on_rate_limit=None, on_resume=None):
        self.token = token or os.getenv("GITHUB_TOKEN")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )
        if self.token:
            self.session.headers["Authorization"] = f"Bearer {self.token}"

        self.rate_limit_hits = 0
        self.total_wait_seconds = 0
        self.on_rate_limit = on_rate_limit
        self.on_resume = on_resume

        # 선제 감속(throttle): 남은 포인트가 threshold 미만이면 reset까지 시간에 맞춰 미리 페이스 조절.
        # 한도 0에 부딪혀 멈추는 대신 에러 자체를 피해 부드럽게 지나가게 한다.
        self.throttle_threshold = int(os.getenv("GRAPHQL_THROTTLE_THRESHOLD", "100"))
        self.throttle_max_sleep = float(os.getenv("GRAPHQL_THROTTLE_MAX_SLEEP", "3"))
        self.throttle_wait_seconds = 0.0

    def _maybe_throttle(self, rate_limit: Optional[dict]) -> None:
        """남은 포인트가 적으면 reset까지 남은 시간을 남은 호출 수로 나눠 미리 잠깐 쉰다."""
        if not rate_limit:
            return
        remaining = rate_limit.get("remaining")
        if remaining is None or remaining >= self.throttle_threshold or remaining <= 0:
            return
        import datetime
        try:
            reset_dt = datetime.datetime.fromisoformat(
                (rate_limit.get("resetAt") or "").replace("Z", "+00:00")
            )
            secs = (reset_dt - datetime.datetime.now(datetime.timezone.utc)).total_seconds()
        except Exception:
            return
        if secs <= 0:
            return
        wait = min(secs / remaining, self.throttle_max_sleep)
        if wait > 0:
            self.throttle_wait_seconds += wait
            time.sleep(wait)

    def query(self, query_str: str, variables: dict) -> dict:
        attempt = 0
        max_retries = 5

        while True:
            try:
                response = self.session.post(
                    GRAPHQL_URL,
                    json={"query": query_str, "variables": variables},
                    timeout=30,
                )
            except (RequestsConnectionError, Timeout) as e:
                attempt += 1
                if attempt >= max_retries:
                    raise
                wait = 5 * attempt
                print(f"Network error (attempt {attempt}/{max_retries}), retrying in {wait}s: {e}")
                time.sleep(wait)
                continue

            if response.status_code in (403, 429):
                remaining = int(response.headers.get("X-RateLimit-Remaining", 1))
                reset_at = int(response.headers.get("X-RateLimit-Reset", 0))
                retry_after = int(response.headers.get("Retry-After", 0))

                if retry_after > 0:
                    wait = retry_after + 1
                elif remaining == 0:
                    wait = max(reset_at - int(time.time()) + 5, 5)
                else:
                    # 403/429 but not a rate limit (e.g. token permission issue)
                    response.raise_for_status()

                print(f"GitHub rate limit hit ({response.status_code}). Waiting {wait}s...")
                self.rate_limit_hits += 1
                self.total_wait_seconds += wait
                if self.on_rate_limit:
                    self.on_rate_limit(wait)
                time.sleep(wait)
                if self.on_resume:
                    self.on_resume()
                continue

            response.raise_for_status()
            data = response.json()

            # GraphQL rate limit check
            rate_limit = (
                data.get("data", {}).get("rateLimit") if data.get("data") else None
            )
            if rate_limit and rate_limit.get("remaining", 1) == 0:
                import datetime

                reset_str = rate_limit.get("resetAt", "")
                try:
                    reset_dt = datetime.datetime.fromisoformat(
                        reset_str.replace("Z", "+00:00")
                    )
                    wait = max(
                        int(
                            (
                                reset_dt - datetime.datetime.now(datetime.timezone.utc)
                            ).total_seconds()
                        )
                        + 5,
                        5,
                    )
                except Exception:
                    wait = 60
                print(f"GitHub GraphQL rate limit hit. Waiting {wait}s...")
                self.rate_limit_hits += 1
                self.total_wait_seconds += wait
                if self.on_rate_limit:
                    self.on_rate_limit(wait)
                time.sleep(wait)
                if self.on_resume:
                    self.on_resume()
                continue

            if "errors" in data:
                errors = data["errors"]
                # rate limit error
                if any("rate limit" in str(e).lower() for e in errors):
                    print("GraphQL rate limit error, waiting 60s...")
                    self.rate_limit_hits += 1
                    self.total_wait_seconds += 60
                    time.sleep(60)
                    continue
                raise Exception(f"GraphQL errors: {errors}")

            # 선제 감속: 남은 포인트가 적으면 미리 천천히 (한도 0 충돌 회피)
            self._maybe_throttle(rate_limit)
            return data


def _review_obj(node: dict) -> dict:
    return {
        "reviewer_login": node["author"]["login"] if node.get("author") else None,
        "state": (node.get("state") or "").lower(),
        "submitted_at": node.get("submittedAt"),
    }


def _file_obj(node: dict) -> dict:
    change_type = (node.get("changeType") or "").lower()
    return {
        "filename": node.get("path", ""),
        "additions": node.get("additions", 0),
        "deletions": node.get("deletions", 0),
        "status": STATUS_MAP.get(change_type, change_type),
    }


def _page_all(client, query, variables, cursor, section, extract):
    """커서 페이지를 끝까지 따라가며 모은다.
    중간 페이지가 실패하면 예외를 삼키지 않고 (부분 결과, 실패 사유)를 함께 돌려준다 —
    호출측이 '부분 수집'과 '완전 수집'을 구분해 기록할 수 있어야 하기 때문이다."""
    out = []
    while cursor:
        try:
            r = client.query(query, {**variables, "cursor": cursor})
            conn = r["data"]["repository"]["pullRequest"][section]
        except Exception as e:
            return out, _failure_reason(e)
        out += extract(conn)
        cursor = conn["pageInfo"]["endCursor"] if conn["pageInfo"]["hasNextPage"] else None
    return out, None


def _failure_reason(exc: Exception) -> str:
    """실패 사유를 수집 기록용 코드로 분류한다(인증 없음/권한 부족/한도/그 외 페이지 오류)."""
    msg = str(exc).lower()
    if "401" in msg or "bad credentials" in msg or "requires authentication" in msg:
        return "unauthenticated"
    if "403" in msg or "forbidden" in msg or "not accessible" in msg:
        return "forbidden"
    if "rate limit" in msg or "429" in msg:
        return "rate_limited"
    return "page_error"


def _parse_pr_node(
    client: "GraphQLClient", owner: str, repo: str, number: int, pr: Optional[dict]
) -> Dict:
    """단일 PR 노드(첫 30개 포함) 파싱. 30개 초과 연결은 per-PR 커서로 보충.

    반환에 changed_files(GitHub이 보고한 전체 변경 파일 수)와 files_complete /
    failure_reason 을 포함한다. 상세가 1/100만 수집돼도 '수집됨'으로 보이던 문제를
    호출측에서 구분하기 위한 값이다."""
    if not pr:
        return {"commits": [], "reviews": [], "files": [],
                "changed_files": None, "files_complete": False,
                "failure_reason": "page_error"}
    variables = {"owner": owner, "repo": repo, "number": number}
    reason = None

    commits_conn = pr.get("commits") or {}
    commits = [n["commit"]["oid"] for n in commits_conn.get("nodes", []) if n.get("commit")]
    if commits_conn.get("pageInfo", {}).get("hasNextPage"):
        more, r = _page_all(
            client, PR_COMMITS_NEXT_QUERY, variables,
            commits_conn["pageInfo"]["endCursor"], "commits",
            lambda c: [n["commit"]["oid"] for n in c.get("nodes", []) if n.get("commit")])
        commits += more
        reason = reason or r

    reviews_conn = pr.get("reviews") or {}
    reviews = [_review_obj(n) for n in reviews_conn.get("nodes", [])]
    if reviews_conn.get("pageInfo", {}).get("hasNextPage"):
        more, r = _page_all(
            client, PR_REVIEWS_NEXT_QUERY, variables,
            reviews_conn["pageInfo"]["endCursor"], "reviews",
            lambda c: [_review_obj(n) for n in c.get("nodes", [])])
        reviews += more
        reason = reason or r

    files_conn = pr.get("files") or {}
    files = [_file_obj(n) for n in files_conn.get("nodes", [])]
    files_reason = None
    if files_conn.get("pageInfo", {}).get("hasNextPage"):
        more, files_reason = _page_all(
            client, PR_FILES_NEXT_QUERY, variables,
            files_conn["pageInfo"]["endCursor"], "files",
            lambda c: [_file_obj(n) for n in c.get("nodes", [])])
        files += more
        reason = reason or files_reason

    # GitHub이 보고한 전체 변경 파일 수. 수집한 상세 수와 다르면 부분 수집이다.
    changed_files = pr.get("changedFiles")
    files_complete = files_reason is None and (
        changed_files is None or len(files) >= changed_files)

    return {"commits": commits, "reviews": reviews, "files": files,
            "changed_files": changed_files, "files_complete": files_complete,
            "failure_reason": reason if not files_complete else None}


def collect_pr_details_graphql(
    client: GraphQLClient,
    owner: str,
    repo: str,
    pr_number: int,
) -> Dict:
    """PR 1개 상세(커밋/리뷰/파일) 수집."""
    data = client.query(PR_DETAILS_QUERY, {"owner": owner, "repo": repo, "number": pr_number})
    pr = data.get("data", {}).get("repository", {}).get("pullRequest")
    return _parse_pr_node(client, owner, repo, pr_number, pr)


# ── 배치 수집: GraphQL alias로 PR 여러 개를 한 요청에 묶는다 ──
_PR_DETAILS_FIELDS = """
    changedFiles
    commits(first: 30) { nodes { commit { oid } } pageInfo { hasNextPage endCursor } }
    reviews(first: 30) { nodes { author { login } state submittedAt } pageInfo { hasNextPage endCursor } }
    files(first: 30) { nodes { path additions deletions changeType } pageInfo { hasNextPage endCursor } }
"""


def _build_batch_query(pr_numbers: List[int]) -> str:
    aliases = "\n".join(
        f"    pr{i}: pullRequest(number: {int(num)}) {{{_PR_DETAILS_FIELDS}    }}"
        for i, num in enumerate(pr_numbers)
    )
    return (
        "query($owner: String!, $repo: String!) {\n"
        "  rateLimit { remaining resetAt cost }\n"
        "  repository(owner: $owner, name: $repo) {\n"
        f"{aliases}\n"
        "  }\n"
        "}\n"
    )


def collect_pr_details_graphql_batch(
    client: GraphQLClient,
    owner: str,
    repo: str,
    pr_numbers: List[int],
) -> Dict[int, Dict]:
    """여러 PR 상세를 alias로 한 요청에 묶어 수집.
    반환: {pr_number: {commits, reviews, files}}. 30개 초과 연결은 _parse_pr_node가 보충."""
    if not pr_numbers:
        return {}
    query = _build_batch_query(pr_numbers)
    data = client.query(query, {"owner": owner, "repo": repo})
    repo_node = (data.get("data") or {}).get("repository") or {}
    out: Dict[int, Dict] = {}
    for i, num in enumerate(pr_numbers):
        out[num] = _parse_pr_node(client, owner, repo, num, repo_node.get(f"pr{i}"))
    return out
