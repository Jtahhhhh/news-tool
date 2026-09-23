"""Optional: prefetch pinned Linux x86_64 / Python 3.12 wheels for slow build networks.

Uses PyPI's release JSON API, verifies every SHA-256, and marks the cache ready
only after all requirements have been fetched. Requires pip on the host.
"""
import hashlib
import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from pip._vendor.packaging.tags import compatible_tags, cpython_tags
from pip._vendor.packaging.utils import parse_wheel_filename

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / '.wheels'
PLATFORMS = ['manylinux_2_28_x86_64', 'manylinux_2_24_x86_64', 'manylinux2014_x86_64', 'manylinux_2_17_x86_64']
TAGS = set(cpython_tags((3, 12), abis=['cp312'], platforms=PLATFORMS)) | set(compatible_tags((3, 12), interpreter='cp312', platforms=PLATFORMS))


def download(requirement):
    name, version = requirement.split('==')
    for attempt in range(5):
        try:
            with urllib.request.urlopen(f'https://pypi.org/pypi/{name}/{version}/json', timeout=30) as response:
                release = json.load(response)
            candidates = [item for item in release['urls'] if item['filename'].endswith('.whl')
                          and parse_wheel_filename(item['filename'])[3] & TAGS]
            if not candidates:
                raise RuntimeError(f'No compatible binary wheel for {requirement}')
            item = candidates[0]
            target = CACHE / item['filename']
            expected = item['digests']['sha256']
            if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == expected:
                return f'Cached {requirement}'
            if item['size'] > 1_048_576:
                def part(start):
                    end = min(start + 1_048_575, item['size'] - 1)
                    request = urllib.request.Request(item['url'], headers={'Range': f'bytes={start}-{end}'})
                    with urllib.request.urlopen(request, timeout=30) as response:
                        chunk = response.read()
                        if response.status != 206 or len(chunk) != end - start + 1:
                            raise RuntimeError('Incomplete ranged download')
                        return chunk
                with ThreadPoolExecutor(max_workers=4) as parts:
                    body = b''.join(parts.map(part, range(0, item['size'], 1_048_576)))
            else:
                with urllib.request.urlopen(item['url'], timeout=30) as response:
                    body = response.read()
            if hashlib.sha256(body).hexdigest() != expected:
                raise RuntimeError(f'Incomplete or corrupt download: {requirement}')
            target.write_bytes(body)
            return f'Verified {requirement}'
        except Exception:
            if attempt == 4:
                raise
            time.sleep(1 + attempt)


if __name__ == '__main__':
    CACHE.mkdir(exist_ok=True)
    (CACHE / 'READY').unlink(missing_ok=True)
    requirements = [line.strip() for line in (ROOT / 'requirements.lock').read_text().splitlines()
                    if line.strip() and not line.startswith('#')]
    with ThreadPoolExecutor(max_workers=8) as pool:
        for result in pool.map(download, requirements):
            print(result, flush=True)
    (CACHE / 'READY').write_text('Linux x86_64 / CPython 3.12\n')
    print('Wheel cache ready. Run docker compose up --build.', flush=True)
