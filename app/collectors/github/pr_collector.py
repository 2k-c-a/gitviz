from typing import List, Dict


def collect_pull_requests(client, owner: str, repo: str) -> List[Dict]:
    url = f"{client.BASE_URL}/repos/{owner}/{repo}/pulls"
    prs = []

    for pr in client.paginate(url, params={"state": "closed"}):
        if not pr.get("merged_at"):
            continue
        prs.append({
            "pr_number": pr["number"],
            "title": pr.get("title", ""),
            "state": "merged",
            "base_branch": pr["base"]["ref"],
            "head_branch": pr["head"]["ref"],
            "merged_at": pr.get("merged_at"),
            "opened_at": pr.get("created_at"),
            "closed_at": pr.get("closed_at"),
            # GitHub PR "목록" API(이 엔드포인트)는 changed_files 필드를 아예 안 준다
            # (단건 조회 GET /pulls/{number}에서만 내려줌) — 그래서 이 값은 사실상 항상 0.
            # 실제 changed_file_count·is_large_pr은 analyze_repository.py에서 PR 파일 상세를
            # GraphQL로 수집한 뒤 pull_request_files 실제 개수 기준으로 다시 계산해 덮어쓴다.
            "changed_file_count": pr.get("changed_files", 0),
            "merged_commit_sha": pr.get("merge_commit_sha"),
            "author_login": pr["user"]["login"] if pr.get("user") else None,
        })

    return prs
