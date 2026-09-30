"""Read-only media checks shared by review and publishing."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
from fastapi import HTTPException


def video_path(key):
    root = (Path(os.getenv('MEDIA_ROOT', '/data')) / 'video').resolve()
    path = (root.parent / key).resolve()
    if path.parent != root or path.suffix.lower() != '.mp4' or not path.is_file():
        raise HTTPException(409, 'File video không tồn tại hoặc đường dẫn không hợp lệ')
    return path


def sha256_file(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def inspect_video(path):
    try:
        result = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format',
                                 '-of', 'json', str(path)], capture_output=True, timeout=30, check=True)
        data = json.loads(result.stdout)
        streams = data['streams']
        video = next(s for s in streams if s.get('codec_type') == 'video')
        duration = float(data['format']['duration'])
        if duration <= 0 or not video.get('width') or not video.get('height'):
            raise ValueError()
        return data
    except (subprocess.SubprocessError, ValueError, KeyError, StopIteration, OSError):
        raise HTTPException(409, 'File MP4 hỏng hoặc không đọc được') from None


def seal_review(version):
    path = video_path(version.output_key)
    before = sha256_file(path)
    if version.output_sha256 and before != version.output_sha256:
        raise HTTPException(409, 'File đã thay đổi sau render; hãy tạo phiên bản mới')
    meta = inspect_video(path)
    if sha256_file(path) != before:
        raise HTTPException(409, 'File thay đổi trong lúc kiểm tra')
    version.output_sha256 = before
    version.probe = meta
    return before
