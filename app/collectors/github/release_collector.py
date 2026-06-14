import re
from typing import List, Dict, Optional


def get_release_type(tag_name: str) -> Optional[str]:
    clean = tag_name.lstrip("v")
    match = re.match(r'^(\d+)\.(\d+)\.(\d+)', clean)
    if not match:
        return None
    major, minor, patch = int(match.group(1)), int(match.group(2)), int(match.group(3))
    if minor == 0 and patch == 0:
        return "major"
    if patch == 0:
        return "minor"
    return "patch"


def collect_releases(client, owner: str, repo: str) -> List[Dict]:
    url = f"{client.BASE_URL}/repos/{owner}/{repo}/releases"
    releases = []

    for r in client.paginate(url):
        releases.append({
            "tag_name": r.get("tag_name", ""),
            "name": r.get("name", ""),
            "base_commit_sha": r.get("target_commitish"),
            "is_prerelease": r.get("prerelease", False),
            "published_at": r.get("published_at"),
            "release_type": get_release_type(r.get("tag_name", "")),
        })

    return releases
