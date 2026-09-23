import json
from .base import LLMProvider, user_content, ResponseContentError
from .schemas import Output


class DeepSeekProvider(LLMProvider):
    def build_request(self, data, prompt, feedback=''):
        return ('https://api.deepseek.com/chat/completions', {'Authorization': f'Bearer {self.key}'},
                {'model': self.model, 'messages': [
                    {'role': 'system', 'content': prompt + '\nJSON schema:\n' + json.dumps(getattr(self, 'schema', None) or Output.model_json_schema())},
                    {'role': 'user', 'content': user_content(data, feedback)}],
                 'response_format': {'type': 'json_object'}, 'thinking': {'type': 'disabled'},
                 'max_tokens': self.output_limit, 'temperature': 0.3, 'stream': False})

    def parse_response(self, body):
        choices = body.get('choices', [])
        if not choices:
            raise ResponseContentError('DeepSeek không có choice', 'empty_response')
        reason = choices[0].get('finish_reason')
        if reason != 'stop':
            raise ResponseContentError('DeepSeek kết thúc với lý do: ' + str(reason),
                                       'output_truncated' if reason == 'length' else 'content_refusal')
        raw = choices[0].get('message', {}).get('content')
        if not raw or not raw.strip():
            raise ResponseContentError('Nội dung rỗng', 'empty_response')
        return raw, body.get('usage', {})
