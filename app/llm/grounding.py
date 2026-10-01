"""Conservative deterministic checks, not a substitute for editorial review."""
import re
import unicodedata


class GroundingError(ValueError):
    pass


RISK_PHRASES = (
    'tùy theo tiêu chí đánh giá', 'trong suốt thời gian học',
    'giảm gánh nặng chi phí', 'nâng cao chất lượng nguồn nhân lực',
    'được đảm bảo', 'chắc chắn', 'tất cả sinh viên', 'miễn điều kiện',
)
NUMBER = re.compile(r'\d+(?:[.,]\d+)*(?:\s*[%]|\s*(?:triệu|tỷ|nghìn|ngàn)\b)?', re.I)
QUALIFIERS = ('đề xuất', 'dự kiến', 'dự thảo', 'có thể', 'chưa', 'không')
CONNECTORS = set('theo thông tin cho biết về đây là mức nhóm các và của được từ đến này đó mới trong với ở có một những cụ thể gồm bao'.split())


def normalized(text):
    return ' '.join(unicodedata.normalize('NFKC', text).casefold().split())


def check_text(text, evidence, label, *, preserve_qualifiers=False):
    text, evidence = normalized(text), normalized(evidence)
    errors = []
    numbers = {normalized(m.group()) for m in NUMBER.finditer(evidence)}
    for number in NUMBER.finditer(text):
        if normalized(number.group()) not in numbers:
            errors.append(f'grounding: {label}: số liệu/đơn vị không có trong chứng cứ')
    for phrase in RISK_PHRASES:
        if phrase in text and phrase not in evidence:
            errors.append(f'grounding: {label}: nhận định không có trong chứng cứ ({phrase})')
    if preserve_qualifiers:
        # Deliberately extractive: unfamiliar factual vocabulary needs editorial/source support.
        novel = set(re.findall(r'[^\W\d_]+', text)) - set(re.findall(r'[^\W\d_]+', evidence)) - CONNECTORS
        if novel:
            words = ', '.join(sorted(novel)[:12])
            errors.append(f'grounding: {label}: từ ngữ chưa có trong trích dẫn ({words}); bỏ phần chưa được dẫn hoặc bổ sung trích dẫn nguyên văn có đủ ngữ cảnh từ nguồn')
        for qualifier in QUALIFIERS:
            if re.search(r'\b' + qualifier + r'\b', evidence) and not re.search(r'\b' + qualifier + r'\b', text):
                errors.append(f'grounding: {label}: thiếu mức độ chắc chắn/phủ định ({qualifier})')
    if errors:
        raise GroundingError('\n'.join(dict.fromkeys(errors)))


def validate_grounding(data, output):
    if output.decision != 'draft':
        return
    claims = {c.claim_id: c for c in output.claims}
    errors = []
    def check(*args, **kwargs):
        try:
            check_text(*args, **kwargs)
        except GroundingError as exc:
            errors.append(str(exc))
    for claim in output.claims:
        evidence = ' '.join(e.quote for e in claim.evidence)
        check(claim.text, evidence, f'claim {claim.claim_id}', preserve_qualifiers=True)
    for scene in output.scenes:
        evidence = ' '.join(e.quote for cid in scene.claim_ids for e in claims[cid].evidence)
        check(scene.narration, evidence, f'scene {scene.scene_id}', preserve_qualifiers=True)
        check(scene.on_screen_text, evidence, f'scene {scene.scene_id} text')
        check(scene.visual_brief, evidence, f'scene {scene.scene_id} visual')
    evidence = ' '.join(s.title + ' ' + s.text for s in data.sources)
    for field in ('title', 'hook', 'caption'):
        check(getattr(output, field), evidence, field)
    if errors:
        raise GroundingError('\n'.join(errors))
