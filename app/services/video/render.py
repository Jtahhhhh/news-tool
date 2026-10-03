"""Render one immutable composition, preserving job recovery and review history."""
import shutil
import subprocess
from sqlalchemy import select, func
from app.models import VideoVersion, utcnow
from .timeline import from_scenes
from .validator import validate
from .project import resolve
from .ffmpeg import compile_composition


def publish_output(pending,output):
    """Atomic publication also when Compose mounts tmp/video on different volumes."""
    import errno
    import uuid
    try:
        pending.replace(output)
    except OSError as error:
        if error.errno!=errno.EXDEV: raise
        staged=output.with_name(output.name+'.'+uuid.uuid4().hex+'.partial')
        try:
            shutil.copyfile(pending,staged)
            staged.replace(output)
            pending.unlink()
        finally:
            staged.unlink(missing_ok=True)


def render(job, session):
    from app.services.video_service import DATA, TMP, VIDEOS, synthesize, ensure_active, probe
    snap = job.input_snapshot
    cfg = snap['config']
    if snap.get('composition'):
        composition = validate(snap['composition'])
        job.checkpoint = {**job.checkpoint,'composition':composition}
    elif job.checkpoint.get('composition'):
        composition = validate(job.checkpoint['composition'])
    else:
        timeline = job.checkpoint.get('timeline',[]) if job.checkpoint.get('audio_complete') else []
        if not timeline:
            cursor = 0
            for index, scene in enumerate(snap['scenes']):
                ensure_active(job,session)
                audio = synthesize(session,scene['narration'],cfg)
                timeline.append({**scene,'audio_id':audio.id,'audio_key':audio.storage_key,
                                 'start':cursor,'duration':audio.duration_seconds})
                cursor += audio.duration_seconds
                job.progress = 10+int(35*(index+1)/len(snap['scenes']))
                session.flush()
        composition = from_scenes(timeline)
        # Existing scene assets used to loop implicitly. Bound migration to real source length.
        from .project import metadata
        from app.models import MediaAsset
        for track in composition['tracks']:
            if track['type']!='video': continue
            for clip in track['clips']:
                asset=session.get(MediaAsset,clip['asset_id'])
                if asset and asset.media_type=='video':
                    clip['duration']=min(clip['duration'],metadata(DATA/asset.storage_key)['duration'])
                    clip['source_end']=clip['duration']
        from .fit_service import auto_fit,apply_preset
        from .project import describe
        media={}
        for track in composition['tracks']:
            if track['type']!='video': continue
            for i,clip in enumerate(track['clips']):
                asset=session.get(MediaAsset,clip['asset_id'])
                if asset:
                    media[asset.id]=describe(asset,DATA)
                    track['clips'][i]=auto_fit(clip,media[asset.id],composition['target'])
        composition=apply_preset(composition,media,'NEWS')
        job.checkpoint = {**job.checkpoint,'audio_complete':True,'timeline':timeline,'composition':composition}
        job.logs = [*job.logs,{'stage':'audio_complete','at':utcnow().isoformat(),'audio_count':len(timeline)}]
        session.commit()
    ensure_active(job,session)
    if cfg.get('preview_only'):
        job.status='succeeded';job.stage='audio_ready';job.progress=100
        job.finished_at=utcnow();job.owner=None;job.lease_until=None
        session.flush()
        return None
    job.status='rendering';job.stage='rendering';job.progress=55
    session.commit()
    refs,test_only = resolve(session,composition,DATA)
    cfg = {**cfg,'width':composition['canvas']['width'],'height':composition['canvas']['height'],'quality':'final','test_only':cfg.get('test_only',False) or test_only}
    version = (session.scalar(select(func.max(VideoVersion.version)).where(VideoVersion.script_version_id==job.script_version_id)) or 0)+1
    work = TMP/f'job-{job.id}'
    work.mkdir(exist_ok=True)
    output = VIDEOS/f'script-{job.script_version_id}-v{version}.mp4'
    pending = work/'output.mp4'
    command = compile_composition(composition,refs,work,pending)
    log = work/'ffmpeg.log'
    # Poll cancellation while FFmpeg runs; do not leave an orphan render after cancellation.
    with log.open('w',encoding='utf-8') as stderr:
        process = subprocess.Popen(command,stdout=subprocess.DEVNULL,stderr=stderr)
        try:
            import time
            started=time.monotonic()
            while process.poll() is None:
                if time.monotonic()-started > 7200:
                    raise RuntimeError('Render vượt thời gian tối đa')
                session.expire_all()
                ensure_active(job,session)
                from datetime import timedelta
                job.lease_until=utcnow()+timedelta(minutes=30)
                session.commit()
                time.sleep(.5)
            if process.returncode:
                raise RuntimeError('FFmpeg: '+log.read_text(encoding='utf-8')[-1600:])
        finally:
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired: process.kill();process.wait()
    ensure_active(job,session)
    meta,duration = probe(pending)
    stream=next((s for s in meta['streams'] if s.get('width')),{})
    if (stream.get('width'),stream.get('height')) != (cfg['width'],cfg['height']):
        raise RuntimeError('Sai độ phân giải đầu ra')
    publish_output(pending,output)
    from app.services.media_integrity import sha256_file
    row=VideoVersion(script_version_id=job.script_version_id,job_id=job.id,version=version,
        parent_version_id=snap.get('parent_version_id'),status='needs_review',config=cfg,timeline=composition,
        input_hash=job.request_hash,output_key=str(output.relative_to(DATA)).replace('\\','/'),
        output_sha256=sha256_file(output),probe=meta,duration_seconds=duration,test_only=cfg['test_only'])
    session.add(row)
    job.status='succeeded';job.stage='complete';job.progress=100
    job.finished_at=utcnow();job.owner=None;job.lease_until=None
    job.logs=[*job.logs,{'stage':'render_complete','at':job.finished_at.isoformat(),'output_key':row.output_key}]
    session.flush()
    shutil.rmtree(work,ignore_errors=True)
    return row
