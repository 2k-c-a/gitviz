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


def collect_pr_details_graphql(
    client: GraphQLClient,
    owner: str,
    repo: str,
    pr_number: int,
) -> Dict:
    variables = {"owner": owner, "repo": repo, "number": pr_number}
    data = client.query(PR_DETAILS_QUERY, variables)

    pr = data.get("data", {}).get("repository", {}).get("pullRequest")
    if not pr:
        return {"commits": [], "reviews": [], "files": []}

    # Commits
    commits_conn = pr.get("commits", {})
    commits = [
        node["commit"]["oid"]
        for node in commits_conn.get("nodes", [])
        if node.get("commit")
    ]
    if commits_conn.get("pageInfo", {}).get("hasNextPage"):
        cursor = commits_conn["pageInfo"]["endCursor"]
        while cursor:
            r = client.query(PR_COMMITS_NEXT_QUERY, {**variables, "cursor": cursor})
            conn = r["data"]["repository"]["pullRequest"]["commits"]
            commits += [
                n["commit"]["oid"] for n in conn.get("nodes", []) if n.get("commit")
            ]
            cursor = (
                conn["pageInfo"]["endCursor"]
                if conn["pageInfo"]["hasNextPage"]
                else None
            )

    # Reviews
    reviews_conn = pr.get("reviews", {})
    reviews = []
    for node in reviews_conn.get("nodes", []):
        reviews.append(
            {
                "reviewer_login": (
                    node["author"]["login"] if node.get("author") else None
                ),
                "state": (node.get("state") or "").lower(),
                "submitted_at": node.get("submittedAt"),
            }
        )
    if reviews_conn.get("pageInfo", {}).get("hasNextPage"):
        cursor = reviews_conn["pageInfo"]["endCursor"]
        while cursor:
            r = client.query(PR_REVIEWS_NEXT_QUERY, {**variables, "cursor": cursor})
            conn = r["data"]["repository"]["pullRequest"]["reviews"]
            for node in conn.get("nodes", []):
                reviews.append(
                    {
                        "reviewer_login": (
                            node["author"]["login"] if node.get("author") else None
                        ),
                        "state": (node.get("state") or "").lower(),
                        "submitted_at": node.get("submittedAt"),
                    }
                )
            cursor = (
                conn["pageInfo"]["endCursor"]
                if conn["pageInfo"]["hasNextPage"]
                else None
            )

    # Files
    files_conn = pr.get("files", {})
    files = []
    for node in files_conn.get("nodes", []):
        change_type = node.get("changeType", "").lower()
        files.append(
            {
                "filename": node.get("path", ""),
                "additions": node.get("additions", 0),
                "deletions": node.get("deletions", 0),
                "status": STATUS_MAP.get(change_type, change_type),
            }
        )
    if files_conn.get("pageInfo", {}).get("hasNextPage"):
        cursor = files_conn["pageInfo"]["endCursor"]
        while cursor:
            r = client.query(PR_FILES_NEXT_QUERY, {**variables, "cursor": cursor})
            conn = r["data"]["repository"]["pullRequest"]["files"]
            for node in conn.get("nodes", []):
                change_type = node.get("changeType", "").lower()
                files.append(
                    {
                        "filename": node.get("path", ""),
                        "additions": node.get("additions", 0),
                        "deletions": node.get("deletions", 0),
                        "status": STATUS_MAP.get(change_type, change_type),
                    }
                )
            cursor = (
                conn["pageInfo"]["endCursor"]
                if conn["pageInfo"]["hasNextPage"]
                else None
            )

    return {"commits": commits, "reviews": reviews, "files": files}
