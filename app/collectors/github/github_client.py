import time
import os
import requests
from requests.exceptions import ConnectionError as RequestsConnectionError, Timeout


class GitHubClient:
    BASE_URL = "https://api.github.com"

    def __init__(self, token=None, on_rate_limit=None, on_resume=None):
        self.token = token or os.getenv("GITHUB_TOKEN")
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        if self.token:
            self.session.headers["Authorization"] = f"Bearer {self.token}"

        self.rate_limit_hits = 0
        self.total_wait_seconds = 0
        # 진행 상황 보고용 콜백 (선택). 제한 발생/재개 시 호출.
        self.on_rate_limit = on_rate_limit
        self.on_resume = on_resume

    def get(self, url, params=None):
        attempt = 0
        max_retries = 5

        while True:
            try:
                response = self.session.get(url, params=params, timeout=30)
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
            return response

    def paginate(self, url, params=None):
        params = dict(params or {})
        params.setdefault("per_page", 100)
        params["page"] = 1

        while True:
            response = self.get(url, params=params)
            data = response.json()

            if not data:
                break

            yield from data

            if 'rel="next"' not in response.headers.get("Link", ""):
                break

            params["page"] += 1
