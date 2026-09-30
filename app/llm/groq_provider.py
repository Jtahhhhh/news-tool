from .base import LLMProvider, ResponseContentError, user_content
from .schemas import Output


class GroqProvider(LLMProvider):
    name = 'groq'

    def build_request(self, data, prompt, feedback=''):
        return ('https://api.groq.com/openai/v1/chat/completions',
                {'Authorization': f'Bearer {self.key}'},
                {'model': self.model, 'messages': [
                    {'role': 'system', 'content': prompt},
                    {'role': 'user', 'content': user_content(data, feedback)}],
                 'response_format': {'type': 'json_schema', 'json_schema': {
                     'name': 'news_script', 'strict': True,
                     'schema': getattr(self, 'schema', None) or Output.model_json_schema()}},
                 'max_completion_tokens': self.output_limit, 'reasoning_effort': 'low',
                 'temperature': 0.2, 'stream': False})

    def parse_response(self, body):
        choices = body.get('choices', [])
        if not choices:
            raise ResponseContentError('Groq không có choice', 'empty_response')
        message = choices[0].get('message') or {}
        if message.get('refusal'):
            raise ResponseContentError('Groq từ chối nội dung', 'content_refusal')
        reason = choices[0].get('finish_reason')
        if reason != 'stop':
            raise ResponseContentError('Groq chưa hoàn tất output',
                                       'output_truncated' if reason == 'length' else 'content_refusal')
        raw = message.get('content')
        if not isinstance(raw, str) or not raw.strip():
            raise ResponseContentError('Groq trả nội dung rỗng', 'empty_response')
        return raw, body.get('usage', {})
