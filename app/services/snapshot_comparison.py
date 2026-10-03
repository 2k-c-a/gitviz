"""불변 결과끼리 비교한다. 파일 이름 변경을 동일 파일로 추정하지 않는다."""
from fastapi import HTTPException
from app.services.analysis_snapshots import snapshot_metadata

METRICS = ("risk_score", "bus_factor", "coupling_score")


def compare_snapshots(before, after):
    if before.repository_id != after.repository_id:
        raise HTTPException(404, "같은 저장소의 결과만 비교할 수 있습니다.")
    if before.formula_version != after.formula_version:
        raise HTTPException(409, "계산 버전이 다른 결과는 직접 비교할 수 없습니다.")
    old = {f['path']: f for f in before.payload['tree']['files']}
    new = {f['path']: f for f in after.payload['tree']['files']}
    changes = []
    for path in sorted(old.keys() | new.keys()):
        a, b = old.get(path), new.get(path)
        status = 'added' if a is None else 'removed' if b is None else 'retained'
        metrics = {}
        for key in METRICS:
            av, bv = a.get(key) if a else None, b.get(key) if b else None
            metrics[key] = {'before': av, 'after': bv,
                            'delta': round(bv - av, 6) if av is not None and bv is not None else None}
        changed = status != 'retained' or any(v['before'] != v['after'] for v in metrics.values())
        if changed:
            changes.append({'path': path, 'status': status, 'metrics': metrics})
    return {
        'before': snapshot_metadata(before), 'after': snapshot_metadata(after),
        'summary': {'added': len(new.keys() - old.keys()), 'removed': len(old.keys() - new.keys()),
                    'retained': len(old.keys() & new.keys()),
                    'changed': sum(c['status'] == 'retained' for c in changes)},
        'changes': changes,
        'interpretation': {
            'file_matching': 'exact_path; 이름 변경은 삭제/추가로 표시',
            'presence_changes': '관측된 분석 결과에 나타남/사라짐. 미수집 파일은 실제 Git 추가/삭제와 구분할 수 없음',
            'risk_delta': '각 스냅샷에서 정규화한 상대 점수의 차이. 결함 확률이나 절대 품질 변화가 아님',
            'window_changed': before.window_start != after.window_start or before.window_end != after.window_end,
            'before_data_quality': before.payload.get('data_quality', {}),
            'after_data_quality': after.payload.get('data_quality', {}),
        },
    }
