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


def _parse_pr_node(
    client: "GraphQLClient", owner: str, repo: str, number: int, pr: Optional[dict]
) -> Dict:
    """단일 PR 노드(첫 30개 포함) 파싱. 30개 초과 연결은 per-PR 커서로 보충."""
    if not pr:
        return {"commits": [], "reviews": [], "files": []}
    variables = {"owner": owner, "repo": repo, "number": number}

    # Commits
    commits_conn = pr.get("commits") or {}
    commits = [n["commit"]["oid"] for n in commits_conn.get("nodes", []) if n.get("commit")]
    if commits_conn.get("pageInfo", {}).get("hasNextPage"):
        cursor = commits_conn["pageInfo"]["endCursor"]
        while cursor:
            r = client.query(PR_COMMITS_NEXT_QUERY, {**variables, "cursor": cursor})
            conn = r["data"]["repository"]["pullRequest"]["commits"]
            commits += [n["commit"]["oid"] for n in conn.get("nodes", []) if n.get("commit")]
            cursor = conn["pageInfo"]["endCursor"] if conn["pageInfo"]["hasNextPage"] else None

    # Reviews
    reviews_conn = pr.get("reviews") or {}
    reviews = [_review_obj(n) for n in reviews_conn.get("nodes", [])]
    if reviews_conn.get("pageInfo", {}).get("hasNextPage"):
        cursor = reviews_conn["pageInfo"]["endCursor"]
        while cursor:
            r = client.query(PR_REVIEWS_NEXT_QUERY, {**variables, "cursor": cursor})
            conn = r["data"]["repository"]["pullRequest"]["reviews"]
            reviews += [_review_obj(n) for n in conn.get("nodes", [])]
            cursor = conn["pageInfo"]["endCursor"] if conn["pageInfo"]["hasNextPage"] else None

    # Files
    files_conn = pr.get("files") or {}
    files = [_file_obj(n) for n in files_conn.get("nodes", [])]
    if files_conn.get("pageInfo", {}).get("hasNextPage"):
        cursor = files_conn["pageInfo"]["endCursor"]
        while cursor:
            r = client.query(PR_FILES_NEXT_QUERY, {**variables, "cursor": cursor})
            conn = r["data"]["repository"]["pullRequest"]["files"]
            files += [_file_obj(n) for n in conn.get("nodes", [])]
            cursor = conn["pageInfo"]["endCursor"] if conn["pageInfo"]["hasNextPage"] else None

    return {"commits": commits, "reviews": reviews, "files": files}


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
