"""Normalize redundant generated fields, then run the unchanged strict validator."""
import json
from pydantic import ValidationError
from .schemas import validate_output


def validate_generated_output(data, raw):
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return validate_output(data, raw)
    changed = False
    if isinstance(payload, dict) and payload.get('decision') == 'draft':
        scenes = payload.get('scenes')
        first = scenes[0] if isinstance(scenes, list) and scenes else None
        hook = payload.get('hook')
        narration = first.get('narration') if isinstance(first, dict) else None
        if isinstance(hook, str) and hook.strip() and isinstance(narration, str) and narration.strip():
            if hook.strip() != narration.strip():
                # Narration is the text actually spoken and linked to claim evidence.
                # Never overwrite it with an independently generated headline.
                payload['hook'] = narration
                changed = True
    result = validate_output(data, json.dumps(payload, ensure_ascii=False))
    if changed:
        result.warnings.append('Đã đồng bộ hook theo lời đọc cảnh đầu; phản hồi gốc được giữ trong lịch sử.')
    return result


def validation_feedback(exc):
    """Actionable errors without Pydantic's input_value/full response dump."""
    if isinstance(exc, ValidationError):
        issues = exc.errors(include_input=False, include_url=False, include_context=False)
        return '; '.join('.'.join(map(str, item['loc'])) + ': ' + item['msg'] for item in issues[:8])[:2000]
    return str(exc)[:2000]
