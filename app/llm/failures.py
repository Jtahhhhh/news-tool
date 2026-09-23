"""Conservative structured classification; HTTP 403 alone never disables a key."""
def classify(provider, status, body):
    error = body.get('error', {}) if isinstance(body, dict) else {}
    if not isinstance(error, dict):
        error = {}
    reasons = {str(error.get('code', '')), str(error.get('type', ''))}
    quotas = []
    details = error.get('details', [])
    if isinstance(details, list):
        for detail in details:
            if not isinstance(detail, dict):
                continue
            if str(detail.get('@type', '')).endswith('google.rpc.ErrorInfo'):
                reasons.add(str(detail.get('reason', '')))
            if str(detail.get('@type', '')).endswith('google.rpc.QuotaFailure'):
                quotas += [str(v.get('quotaId', '')) for v in detail.get('violations', []) if isinstance(v, dict)]
    if reasons & {'API_KEY_INVALID', 'API_KEY_EXPIRED', 'invalid_api_key', 'authentication_error'} or (provider == 'deepseek' and status == 401):
        return 'invalid_key'
    if status == 429 and any('perday' in q.lower().replace('_', '') for q in quotas):
        return 'daily_quota'
    if status == 402:
        return 'billing_quota'
    if status == 429:
        return 'rate_limit'
    if status in (500, 502, 503, 504):
        return 'service_error'
    if status in (400, 401, 403, 404, 422):
        return 'configuration_error'
    return 'unknown_error'
