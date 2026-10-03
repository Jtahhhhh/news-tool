"""S3/B2 adapter. Inject a boto3-compatible client; never serialize credentials."""
from pathlib import Path


class S3Storage:
    def __init__(self, client, bucket: str, prefix: str = ''):
        self.client, self.bucket, self.prefix = client, bucket, prefix.strip('/')

    def key(self, key):
        if not key or '\\' in key or any(p in ('', '.', '..') for p in key.split('/')):
            raise ValueError('Invalid object key')
        return '/'.join(p for p in (self.prefix, key) if p)

    def upload(self, source: Path, key: str) -> None:
        self.client.upload_file(str(source), self.bucket, self.key(key))

    def download(self, key: str, destination: Path) -> None:
        self.client.download_file(self.bucket, self.key(key), str(destination))

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=self.key(key))

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=self.key(key))
            return True
        except Exception as exc:
            if getattr(exc, 'response', {}).get('Error', {}).get('Code') in ('404', 'NoSuchKey', 'NotFound'):
                return False
            raise

    def get_url(self, key: str) -> str:
        return self.get_signed_url(key)

    def get_signed_url(self, key: str, expires: int = 300) -> str:
        if not 1 <= expires <= 3600: raise ValueError('Expiry must be 1–3600 seconds')
        return self.client.generate_presigned_url('get_object', Params={'Bucket': self.bucket, 'Key': self.key(key)}, ExpiresIn=expires)
