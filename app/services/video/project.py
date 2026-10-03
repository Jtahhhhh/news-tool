"""Resolve IDs server-side; client compositions cannot supply file paths."""
import json
import subprocess
from functools import lru_cache
from pathlib import Path
from fastapi import HTTPException
from sqlalchemy import select
from app.models import MediaAsset, TTSAudio, VideoJob, VideoVersion
from .timeline import migrate


@lru_cache(maxsize=512)
def _inspect(path, size, modified):
    result = subprocess.run(['ffprobe','-v','error','-show_format','-show_streams','-of','json',path],
                            capture_output=True,text=True,timeout=30)
    if result.returncode:
        raise HTTPException(422,'Không đọc được media')
    data = json.loads(result.stdout)
    streams = data.get('streams',[])
    video = next((s for s in streams if s.get('codec_type')=='video'),{})
    return dict(duration=float(data.get('format',{}).get('duration') or video.get('duration') or 0),
                width=video.get('width',0),height=video.get('height',0),
                has_audio=any(s.get('codec_type')=='audio' for s in streams))


def metadata(path):
    if not path.is_file():
        raise HTTPException(422,'File media không còn tồn tại')
    stat = path.stat()
    return _inspect(str(path),stat.st_size,stat.st_mtime_ns)


def describe(row, root):
    path = (root/row.storage_key).resolve()
    if not path.is_relative_to(root.resolve()):
        raise HTTPException(422,'Đường dẫn media không hợp lệ')
    is_audio = isinstance(row,TTSAudio)
    from .media_probe import audit
    meta = metadata(path) if is_audio else (row.probe or audit(path))
    if not is_audio and not row.probe: row.probe=meta
    preview=row.storage_key if is_audio else (row.proxy_key or row.storage_key)
    return dict(id=row.id,kind='audio' if is_audio else row.media_type,ref_type='tts' if is_audio else 'asset',
                name=(row.text[:100] if is_audio else row.filename),url='/media/'+preview,
                source_url='/media/'+row.storage_key,use_proxy=preview!=row.storage_key,
                test_only=False if is_audio else row.test_only,**meta)


def resolve(session, composition, root):
    refs = {}
    test_only = False
    for track in composition['tracks']:
        for clip in track['clips']:
            background=clip['crop'].get('background_asset_id')
            if background and ('asset',background) not in refs:
                row=session.get(MediaAsset,background)
                if not row or row.media_type!='image': raise HTTPException(422,'Background phải là ảnh')
                refs[('asset',background)]=dict(describe(row,root),path=str((root/row.storage_key).resolve()))
                test_only |= row.test_only
            kind = 'audio' if clip.get('audio_id') else 'asset'
            ref = clip.get('audio_id') or clip.get('asset_id')
            if not ref:
                continue
            key = (kind,ref)
            if key not in refs:
                row = session.get(TTSAudio if kind=='audio' else MediaAsset,ref)
                if not row or (kind=='audio' and not row.valid):
                    raise HTTPException(422,'Media hoặc TTS không còn hợp lệ')
                refs[key] = dict(describe(row,root),path=str((root/row.storage_key).resolve()))
            media = refs[key]
            if track['type']=='video' and media['kind']=='audio': raise HTTPException(422,'Audio không được đặt trên video track')
            if track['type']=='audio' and not media['has_audio']:
                raise HTTPException(422,'Nguồn này không có audio')
            if media['kind']!='image' and clip['source_end'] > media['duration']+.06:
                raise HTTPException(422,'Điểm trim vượt thời lượng nguồn')
            test_only |= media['test_only']
    return refs,test_only


def editor_source(session, script_id, job_id=None, lock=False):
    query = select(VideoJob).where(VideoJob.script_version_id==script_id,VideoJob.status=='succeeded')
    if job_id:
        query = query.where(VideoJob.id==job_id)
    if lock:
        query = query.with_for_update()
    for job in session.scalars(query.order_by(VideoJob.id.desc())):
        cp = job.checkpoint
        if cp.get('composition') or cp.get('timeline') or cp.get('editor_draft'):
            return job
    raise HTTPException(409,'Hãy tạo TTS preview trước khi mở editor')


def load_draft(job):
    cp = job.checkpoint
    return migrate(cp.get('editor_draft') or cp.get('composition') or {'scenes':cp.get('timeline',[])})


def library(session, job, root):
    audio_ids = {r['audio_id'] for r in job.checkpoint.get('timeline',[]) if r.get('audio_id')}
    for value in (job.checkpoint.get('composition'),job.checkpoint.get('editor_draft')):
        if value:
            audio_ids.update(c['audio_id'] for t in value['tracks'] for c in t['clips'] if c.get('audio_id'))
    rows = list(session.scalars(select(MediaAsset).order_by(MediaAsset.id.desc())))
    if audio_ids:
        rows += list(session.scalars(select(TTSAudio).where(TTSAudio.id.in_(audio_ids),TTSAudio.valid.is_(True))))
    result = []
    for row in rows:
        try:
            result.append(describe(row,root))
        except HTTPException:
            continue
    return result


def snapshot(script,config,scenes=None):
    from app.services.video_service import DEFAULTS, digest
    source=script.data; cfg={**DEFAULTS,'target_seconds':sum(float(s.get('seconds') or 0) for s in source['scenes']) or DEFAULTS['target_seconds'],**config}
    rows=scenes or [dict(scene_id=s['scene_id'],narration=s['narration'],on_screen_text=s['on_screen_text'],
        visual_brief=s['visual_brief'],asset_id=None,crop=dict(x=0,y=0,scale=1),illustration_label=True) for s in source['scenes']]
    return dict(script_version_id=script.id,script_hash=digest(source),config=cfg,scenes=rows,title=source['title'])
