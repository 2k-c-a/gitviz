"""인수인계 초안 렌더러(순수 함수, DB·모델 호출 없음).

입력 컨텍스트에 있는 값만 옮겨 적는다. 사용자·AI가 쓴 문장은 모두 이스케이프해 Markdown 구조로 해석되지 않게 하고,
AI의 설명·지적과 '미확인' 항목은 어디에서도 확정 사실처럼 쓰지 않는다(출처와 상태를 항상 함께 적는다).
"""
import hashlib
from pathlib import Path

from app.services.ai.markdown_safety import code_span, escape_inline, quote_block

TEMPLATE_VERSION = 'handover-v1'
SCHEMA_VERSION = 'handover-draft-v1'
TEXT_FILE = Path(__file__).parent / 'prompts' / 'handover_v1.txt'
TEXT_DIGEST = 'ba3152308e71c66e6c626b6f71ddb804a66fb48b140f3a83a04908a15ab66486'   # 테스트가 현재 파일과 대조한다(내용을 바꾸면 새 버전 파일을 만든다)
ITEM_KEYS = ('assignee', 'description', 'run_method', 'verify_method', 'joint_review', 'receiver_run')
MAX_FINDINGS_SHOWN = 40
MAX_CO_CHANGE_SHOWN = 5


def load_texts() -> dict[str, str]:
    out = {}
    for line in TEXT_FILE.read_text(encoding='utf-8').splitlines():
        if line.startswith('#') or ':' not in line:
            continue
        key, _, value = line.partition(':')
        out[key.strip()] = value.strip()
    return out


def text_digest() -> str:
    return hashlib.sha256(TEXT_FILE.read_bytes().replace(b'\r\n', b'\n')).hexdigest()        # 체크아웃 줄바꿈 설정과 무관하게 같은 값


T = load_texts()


def label_of(key: str) -> str:
    return T[f'ITEM_{key}']


def status_label(status: str) -> str:
    return T[f'STATUS_{status}']


def _loc(f: dict) -> str:
    start, end = f.get('start_line'), f.get('end_line')
    lines = f':{start}' if start == end else f':{start}-{end}'
    return f"{f.get('path')}{lines if start else ''}"


def _short(sha) -> str:
    return (sha or '')[:7] or '-'


def _history_lines(h: dict | None) -> list[str]:
    if not h or h.get('status') == 'none':
        return [T['NO_HISTORY_SNAPSHOT']]
    if h['status'] == 'excluded':
        return [T['HISTORY_EXCLUDED'].format(reasons=escape_inline(' · '.join(h.get('reason_texts') or h.get('reasons') or [])))]
    b, q = h['basis'], h.get('quality') or {}
    lines = [T['HISTORY_BASIS'].format(snapshot_id=b['snapshot_id'], sha=escape_inline(_short(b.get('target_sha'))),
                                       window_end=escape_inline((b.get('window_end') or '-')[:10])), '']
    for p in h['paths']:
        status = {'observed': '이력 관측됨', 'no_observations': '관측 기간 내 변경 없음', 'no_history': '이력 없음', 'not_in_snapshot': 'snapshot에 없음'}.get(p['status'], p['status'])
        lines.append(f"- {code_span(p['path'])} — {escape_inline(status)}")
        if p.get('matched_path') and p.get('match') == 'old_path':
            lines.append(f"  - 이전 경로 {code_span(p['matched_path'])}의 이력입니다.")
        o, w = p.get('observations'), p.get('ownership')
        if o:
            lines.append('  - ' + T['HISTORY_OBSERVED'].format(change_freq=o.get('change_freq'), churn=o.get('churn'), bugfix_count=o.get('bugfix_count')))
        if w:
            lines.append('  - ' + T['HISTORY_OWNERSHIP'].format(observation_count=w.get('observation_count'), contributor_count=w.get('contributor_count'),
                                                                top_share=round((w.get('top_owner_share') or 0) * 100)))
        for limit in p.get('limitations') or []:
            lines.append(f'  - {escape_inline(limit)}')
        if p['status'] == 'no_history':
            lines.append('  - ' + T['HISTORY_NONE'])
        if p['status'] == 'no_observations':
            lines.append('  - ' + T['HISTORY_NO_OBSERVATIONS'])
    lines += ['', T['HISTORY_QUALITY'].format(commit_count=q.get('commit_count'), unidentified=q.get('unidentified_commits'),
                                              unit=escape_inline(str(q.get('co_change_unit'))))]
    return lines


def _co_change_lines(h: dict | None, targets: list[str]) -> list[str]:
    if not h or h.get('status') != 'ok':
        return [T['CO_CHANGE_NOTE']]
    lines, found = [T['CO_CHANGE_NOTE'], ''], False
    for p in h['paths']:
        co = (p.get('co_change') or [])[:MAX_CO_CHANGE_SHOWN]
        if not co:
            continue
        found = True
        lines.append(f"- {code_span(p['path'])}")
        for c in co:
            extra = [f"함께 변경 {c['co_change_count']}회"]
            if c.get('low_support'):
                extra.append('관측 적음')
            if c['path'] in targets:
                extra.append(T['CO_CHANGE_IN_TARGET'])
            lines.append(f"  - {code_span(c['path'])} — {escape_inline(', '.join(extra))}")
    if not found:
        lines.append(T['CO_CHANGE_NONE'])
    return lines


def _summary_lines(s: dict | None) -> list[str]:
    if not s:
        return [T['SUMMARY_NONE']]
    lines = [T['SUMMARY_UNVERIFIED'], '']
    lines += quote_block(s.get('overview') or '')
    if s.get('author_intent'):
        lines += ['', '작성자 의도(PR 설명 기반, 미검증):'] + quote_block(s['author_intent'])
    for c in s.get('file_changes') or []:
        lines += ['', f"- {code_span(c['path'])}"] + ['  ' + q for q in quote_block(c.get('summary') or '', limit=500)]
    if s.get('unknowns'):
        lines += ['', 'AI가 알 수 없다고 한 것:'] + [f'- {escape_inline(u)}' for u in s['unknowns']]
    return lines


def _findings_lines(r: dict | None) -> list[str]:
    if not r:
        return [T['FINDINGS_NONE']]
    findings = r.get('findings') or []
    lines = []
    if not findings:
        lines.append(T['FINDINGS_EMPTY'])
    groups = (('unverified', 'FINDINGS_UNVERIFIED_HEAD'), ('valid', 'FINDINGS_VALID_HEAD'), ('uncertain', 'FINDINGS_UNCERTAIN_HEAD'),
              ('false_positive', 'FINDINGS_FALSE_HEAD'))
    shown = 0
    for verdict, head in groups:
        rows = [f for f in findings if f['verdict'] == verdict]
        if not rows:
            continue
        lines += ['', f'**{T[head]}** ({len(rows)}건)']
        for f in rows:
            if shown >= MAX_FINDINGS_SHOWN:
                break
            shown += 1
            lines.append(f"- {escape_inline(f['title'], limit=200)} — {escape_inline(f['category'])}, {code_span(_loc(f))}")
    if len(findings) > shown:
        lines.append(f'- … 외 {len(findings) - shown}건')
    if r.get('omitted'):
        lines += ['', T['FINDINGS_OMITTED'].format(count=r['omitted'])]
    return lines


def _preparation_lines(p: dict) -> list[str]:
    lines = []
    if p['version'] == 0:
        lines.append(T['PREP_NONE'])
    else:
        lines.append(T['PREP_VERSION'].format(version=p['version'], target_version=p['target_version']))
        if p.get('needs_reconfirm'):
            lines.append(T['PREP_STALE'])
    lines.append('')
    for key in ITEM_KEYS:
        item = p['items'][key]
        lines.append(f"- **{escape_inline(label_of(key))}** — {escape_inline(status_label(item['status']))}")
        if item.get('text'):
            lines += ['  ' + q for q in quote_block(item['text'])]
        if item.get('link'):
            lines.append(f"  - 링크(저장만 함): {code_span(item['link'])}")
    return lines


def _questions(ctx: dict) -> list[str]:
    asks = []
    prep = ctx['preparation']
    for key in ITEM_KEYS:
        status = prep['items'][key]['status']
        if status in ('unknown', 'needed'):
            asks.append(T[f'ASK_{status}'].format(label=escape_inline(label_of(key))))
    for f in ((ctx.get('review') or {}).get('findings') or []):
        if f['verdict'] == 'unverified' and len([a for a in asks if a.startswith('AI 지적')]) < 10:
            asks.append(T['ASK_finding'].format(title=escape_inline(f['title'], limit=120), location=code_span(_loc(f))))
    return asks or [T['ASK_none']]


def _checklist(ctx: dict) -> list[str]:
    prep = ctx['preparation']
    lines = []
    for key in ITEM_KEYS:
        status = prep['items'][key]['status']
        mark = 'x' if status == 'ready' else ' '
        lines.append(f"- [{mark}] {escape_inline(label_of(key))} — {escape_inline(status_label(status))}")
    unverified = len([f for f in ((ctx.get('review') or {}).get('findings') or []) if f['verdict'] == 'unverified'])
    if unverified:
        lines.append('- [ ] ' + T['CHECK_AI'].format(count=unverified))
    if (ctx.get('history') or {}).get('status') == 'ok':
        lines.append('- [ ] ' + T['CHECK_HISTORY'])
    return lines


def _basis_lines(ctx: dict) -> list[str]:
    s, r = ctx.get('summary'), ctx.get('review')
    lines = [T['BASIS_LINE'].format(target_version=ctx['target']['version'], preparation_version=ctx['preparation']['version'],
                                    generated_at=escape_inline(ctx['generated_at']))]
    for label, src, key in (('AI 변경 설명', s, 'BASIS_SOURCE_SUMMARY'), ('AI 리뷰', r, 'BASIS_SOURCE_REVIEW')):
        if not src:
            lines.append(f"- {label}: {T['BASIS_SOURCE_NONE']}")
        else:
            pr = src.get('pr') or {}
            lines.append('- ' + T[key].format(id=escape_inline(src['result_id'][:8]), pr=escape_inline(str(pr.get('number'))), head=escape_inline(_short(pr.get('head_sha'))),
                                              digest=escape_inline((src.get('feedback_digest') or '-')[:12])))
    return lines


def render(ctx: dict) -> tuple[dict, str]:
    """(저장용 구조, Markdown). 같은 ctx에서는 항상 같은 결과다."""
    t = ctx['target']
    lines = [f"# {T['TITLE']}: {escape_inline(t['name'], limit=120)}", '', T['INTRO'], '',
             f"- {T['CAVEAT_AI']}", f"- {T['CAVEAT_UNKNOWN']}", f"- {T['CAVEAT_READY']}", f"- {T['CAVEAT_LINK']}", f"- {T['CAVEAT_HISTORY']}", '']
    lines += [f"## {T['S_TARGET']}", '']
    for label, value in (('목적', t.get('purpose')), ('인계자(메모)', t.get('giver_name')), ('인수자(메모)', t.get('receiver_name'))):
        if value:
            lines.append(f'- {label}: {escape_inline(value, limit=500)}')
    lines.append('- 대상 파일:')
    lines += [f'  - {code_span(p)}' for p in t['selected_paths']]
    lines += ['', f"## {T['S_HISTORY']}", ''] + _history_lines(ctx.get('history'))
    lines += ['', f"## {T['S_CO_CHANGE']}", ''] + _co_change_lines(ctx.get('history'), t['selected_paths'])
    lines += ['', f"## {T['S_AI_SUMMARY']}", ''] + _summary_lines(ctx.get('summary'))
    lines += ['', f"## {T['S_AI_FINDINGS']}", ''] + _findings_lines(ctx.get('review'))
    lines += ['', f"## {T['S_PREPARATION']}", ''] + _preparation_lines(ctx['preparation'])
    lines += ['', f"## {T['S_QUESTIONS']}", ''] + [f'- {a}' for a in _questions(ctx)]
    lines += ['', f"## {T['S_CHECKLIST']}", ''] + _checklist(ctx)
    lines += ['', f"## {T['S_BASIS']}", ''] + _basis_lines(ctx)
    markdown = '\n'.join(lines).rstrip('\n') + '\n'
    return {'schema': SCHEMA_VERSION, 'template_version': TEMPLATE_VERSION, 'context': ctx}, markdown


def export_footer(*, document_id: str, version: int, generation_number: int) -> str:
    return '\n---\n\n' + T['EXPORT_FOOTER'].format(document=escape_inline(document_id[:8]), version=version, generation=generation_number) + '\n'
