from pathlib import Path
from urllib.parse import quote
import shutil


class LocalStorage:
    def __init__(self, root: Path, base_url: str = '/media'):
        self.root = root.resolve()
        self.base_url = base_url.rstrip('/')

    def path(self, key: str) -> Path:
        if not key or '\\' in key or any(part in ('', '.', '..') for part in key.split('/')):
            raise ValueError('Invalid object key')
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root) or path == self.root:
            raise ValueError('Object key must stay inside storage')
        return path

    def upload(self, source: Path, key: str) -> None:
        path = self.path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, path)

    def download(self, key: str, destination: Path) -> None:
        shutil.copyfile(self.path(key), destination)

    def delete(self, key: str) -> None:
        self.path(key).unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        return self.path(key).is_file()

    def get_url(self, key: str) -> str:
        self.path(key)
        return self.base_url + '/' + quote(key, safe='/')

    def get_signed_url(self, key: str, expires: int = 300) -> str:
        # Local URLs require the normal authenticated application session.
        return self.get_url(key)
