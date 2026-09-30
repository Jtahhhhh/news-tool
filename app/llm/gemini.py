from urllib.parse import quote
from .base import LLMProvider, user_content, ResponseContentError
from .schemas import Output


class GeminiProvider(LLMProvider):
    name = 'gemini'
    def build_request(self, data, prompt, feedback=''):
        return (f'https://generativelanguage.googleapis.com/v1beta/models/{quote(self.model, safe="")}:generateContent',
                {'x-goog-api-key': self.key},
                {'systemInstruction': {'parts': [{'text': prompt}]},
                 'contents': [{'role': 'user', 'parts': [{'text': user_content(data, feedback)}]}],
                 'generationConfig': {'maxOutputTokens': self.output_limit,
                     'responseMimeType': 'application/json',
                     'responseJsonSchema': getattr(self, 'schema', None) or Output.model_json_schema()}})

    def parse_response(self, body):
        candidates = body.get('candidates', [])
        if not candidates:
            raise ResponseContentError('Gemini không có candidate: ' + str(body.get('promptFeedback', {}).get('blockReason', 'rỗng')),
                                       'content_refusal' if body.get('promptFeedback', {}).get('blockReason') else 'empty_response')
        reason = candidates[0].get('finishReason')
        if reason != 'STOP':
            raise ResponseContentError('Gemini kết thúc với lý do: ' + str(reason),
                                       'output_truncated' if reason == 'MAX_TOKENS' else 'content_refusal')
        raw = ''.join(p.get('text', '') for p in candidates[0].get('content', {}).get('parts', []) if not p.get('thought'))
        if not raw.strip():
            raise ResponseContentError('Nội dung rỗng', 'empty_response')
        return raw, body.get('usageMetadata', {})
