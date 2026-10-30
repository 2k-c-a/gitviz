"""Coupled-file expansion shared by the coupling API, the file detail popup, snapshots and AI history context.

Moved out of app.api.analytics so services no longer import from the API layer. Logic unchanged."""

COUPLED_SORT_KEYS = {"coupling": "coupling_score", "idf": "adjusted_score"}


def _coupled_files_for(result: dict, file_id: int, sort_by: str | None = None) -> list[dict]:
    """_compute_coupling() 결과에서 file_id가 걸린 쌍만 뽑아 상대 파일 기준으로 펼치고
    선택 파일에서 상대 파일로의 과거 동반 비율·표본 수 순으로 정렬한다. /coupling/{file_id}와 파일 상세 팝업(files/{file_id}/detail)
    양쪽에서 공유하는 로직 — 계산 결과가 어긋나지 않게 한 곳에 모아둔다.

    sort_by(coupling | idf)를 주면 /coupling과 같은 기준(점수 → 공동 변경 횟수 → file_id)으로
    **전체 후보를 정렬**한다. 호출부는 이 정렬이 끝난 뒤에 limit으로 잘라야 한다 —
    잘린 목록을 다시 정렬하면 전체 최상위 후보가 빠질 수 있다."""
    if sort_by is not None and sort_by not in COUPLED_SORT_KEYS:
        raise ValueError("sort_by must be coupling or idf")
    coupled = []
    for p in result["pairs"]:
        if p["file_a_id"] == file_id:
            coupled.append({"file_id": p["file_b_id"], "path": p["file_b_path"],
                             "co_change_count": p["co_change_count"],
                             "coupling_score": p["coupling_score"], "adjusted_score": p["adjusted_score"],
                             "selected_change_count": p["change_count_a"], "other_change_count": p["change_count_b"],
                             "selected_to_other_ratio": p["a_to_b_ratio"], "other_to_selected_ratio": p["b_to_a_ratio"],
                             "low_support": p["low_support"]})
        elif p["file_b_id"] == file_id:
            coupled.append({"file_id": p["file_a_id"], "path": p["file_a_path"],
                             "co_change_count": p["co_change_count"],
                             "coupling_score": p["coupling_score"], "adjusted_score": p["adjusted_score"],
                             "selected_change_count": p["change_count_b"], "other_change_count": p["change_count_a"],
                             "selected_to_other_ratio": p["b_to_a_ratio"], "other_to_selected_ratio": p["a_to_b_ratio"],
                             "low_support": p["low_support"]})
    if sort_by is None:
        coupled.sort(key=lambda c: (-c["selected_to_other_ratio"], -c["co_change_count"], c["file_id"]))
    else:
        key = COUPLED_SORT_KEYS[sort_by]
        coupled.sort(key=lambda c: (-c[key], -c["co_change_count"], c["file_id"]))
    return coupled
