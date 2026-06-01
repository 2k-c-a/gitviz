import httpx
from datetime import datetime, timedelta


class GitHubService:
    BASE_URL = "https://api.github.com"

    async def get_commits(self, owner: str, repo: str):

        six_months_ago = (
            datetime.utcnow() - timedelta(days=180)
        ).isoformat() + "Z"

        url = f"{self.BASE_URL}/repos/{owner}/{repo}/commits"

        params = {
            "since": six_months_ago
        }

        async with httpx.AsyncClient() as client:

            response = await client.get(
                url,
                params=params
            )

            response.raise_for_status()

            return response.json()