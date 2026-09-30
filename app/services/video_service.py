import hashlib,json,os,re,shutil,subprocess,time,uuid,wave
from datetime import timedelta
from pathlib import Path
import httpx
from fastapi import HTTPException
from sqlalchemy import select,func
from app.models import (ScriptVersion,ScriptReview,VideoJob,VideoVersion,VideoReview,TTSAudio,MediaAsset,utcnow)

DATA=Path(os.getenv('MEDIA_ROOT','/data')); ASSETS=DATA/'assets'; AUDIO=DATA/'audio'; VIDEOS=DATA/'video'; TMP=DATA/'tmp'
for folder in (ASSETS,AUDIO,VIDEOS,TMP):folder.mkdir(parents=True,exist_ok=True)
ACTIVE=('queued','generating_audio','rendering')
DEFAULTS=dict(voice='Hải Đăng',template='news-dark',width=720,height=1280,quality='draft',target_seconds=45,
              pronunciation={},test_only=False,model='vieneu-v3-turbo',model_version='v3-turbo')

def digest(value):
    raw=value if isinstance(value,(bytes,bytearray)) else json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
    if isinstance(raw,str):raw=raw.encode()
    return hashlib.sha256(raw).hexdigest()

def normalize(text,rules):
    value=text
    for source,target in sorted(rules.items(),key=lambda x:-len(x[0])):
        value=re.sub(r'(?<!\w)'+re.escape(source)+r'(?!\w)',target,value,flags=re.I)
    return re.sub(r'\s+',' ',value).strip()

def snapshot(script,config,scenes=None):
    source=script.data; cfg={**DEFAULTS,'target_seconds':sum(float(s.get('seconds') or 0) for s in source['scenes']) or DEFAULTS['target_seconds'],**config}
    rows=scenes or [dict(scene_id=s['scene_id'],narration=s['narration'],on_screen_text=s['on_screen_text'],
        visual_brief=s['visual_brief'],asset_id=None,crop=dict(x=0,y=0,scale=1),illustration_label=True) for s in source['scenes']]
    return dict(script_version_id=script.id,script_hash=digest(source),config=cfg,scenes=rows,title=source['title'])

def enqueue(session,script_version_id,idempotency_key,config=None,scenes=None,parent_version_id=None):
    existing=session.scalar(select(VideoJob).where(VideoJob.idempotency_key==idempotency_key))
    script=session.get(ScriptVersion,script_version_id)
    if not script or script.status!='approved' or script.outcome!='draft':raise HTTPException(409,'Chỉ dựng từ kịch bản hợp lệ đã duyệt')
    if script.provider != 'fake':
        from app.services.script_service import checked_output
        checked_output(session,script,script.data)
    audited=session.scalar(select(ScriptReview.id).where(ScriptReview.version_id==script.id,ScriptReview.decision=='approved').order_by(ScriptReview.id.desc()).limit(1))
    if not audited and not (script.provider=='fake' and (config or {}).get('test_only')):raise HTTPException(409,'Kịch bản chưa có quyết định duyệt hợp lệ')
    latest=session.scalar(select(ScriptVersion.id).join(ScriptReview,ScriptReview.version_id==ScriptVersion.id).where(ScriptVersion.event_id==script.event_id,ScriptVersion.status=='approved',ScriptVersion.outcome=='draft',ScriptReview.decision=='approved').order_by(ScriptVersion.version.desc()).limit(1))
    if audited and latest!=script.id:raise HTTPException(409,'Chỉ dựng bản mới nhất trong các kịch bản đã duyệt')
    config=dict(config or {});source_scenes={str(row['scene_id']):row for row in script.data['scenes']}
    if scenes:
        if {str(row.get('scene_id')) for row in scenes}!={*source_scenes}:raise HTTPException(422,'Danh sách cảnh không khớp kịch bản đã duyệt')
        for row in scenes:
            original=source_scenes[str(row['scene_id'])]
            if row.get('narration')!=original['narration']:raise HTTPException(409,'Lời đọc đã đổi ý nghĩa; hãy tạo và duyệt phiên bản kịch bản mới')
            asset_id=row.get('asset_id')
            if asset_id:
                asset=session.get(MediaAsset,int(asset_id))
                if not asset:raise HTTPException(422,'Asset của cảnh không tồn tại')
                if asset.test_only:config['test_only']=True
    data=snapshot(script,config,scenes); request_hash=digest(data)
    if existing:
        if existing.request_hash!=request_hash:raise HTTPException(409,'Idempotency key đã dùng cho cấu hình khác')
        return existing
    if session.scalar(select(VideoJob.id).where(VideoJob.script_version_id==script.id,VideoJob.status.in_(ACTIVE))):
        raise HTTPException(409,'Kịch bản đang có job video hoạt động')
    if parent_version_id:data['parent_version_id']=parent_version_id
    job=VideoJob(script_version_id=script.id,idempotency_key=idempotency_key,request_hash=request_hash,
        input_snapshot=data,checkpoint={},logs=[]);session.add(job);session.flush();return job

def claim(session):
    job=session.scalar(select(VideoJob).where(VideoJob.status=='queued',VideoJob.cancelled.is_(False))
        .order_by(VideoJob.id).with_for_update(skip_locked=True).limit(1))
    if not job:return None
    job.status='generating_audio';job.stage='generating_audio';job.progress=5;job.owner=uuid.uuid4().hex
    job.lease_until=utcnow()+timedelta(minutes=30);return job.id,job.owner

def probe(path):
    result=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration,size:stream=codec_name,width,height,sample_rate,channels','-of','json',str(path)],capture_output=True,text=True,timeout=30)
    if result.returncode:raise RuntimeError('ffprobe: '+result.stderr[-1000:])
    data=json.loads(result.stdout);duration=float(data.get('format',{}).get('duration') or 0)
    if duration<=0:raise RuntimeError('File media rỗng hoặc không có thời lượng')
    return data,duration

def synthesize(session,text,config):
    spoken=normalize(text,config.get('pronunciation',{}));key=digest(dict(text=spoken,voice=config['voice'],model=config['model'],version=config['model_version']))
    cached=session.scalar(select(TTSAudio).where(TTSAudio.cache_key==key,TTSAudio.valid.is_(True)))
    if cached and (DATA/cached.storage_key).exists():return cached
    path=AUDIO/f'{key}.wav';started=time.monotonic()
    with httpx.Client(timeout=float(os.getenv('TTS_TIMEOUT_SECONDS','900'))) as client:
        response=client.post(os.getenv('TTS_URL','http://tts:8000')+'/v1/audio/speech',json=dict(
            model=config['model'],voice=config['voice'],input=spoken,response_format='wav'))
        response.raise_for_status();path.write_bytes(response.content)
    meta,duration=probe(path)
    streams=meta.get('streams',[]);audio=next((s for s in streams if s.get('codec_name')),{})
    if path.stat().st_size<1000:raise RuntimeError('TTS trả file rỗng hoặc hỏng')
    row=TTSAudio(text=spoken,text_hash=digest(spoken),cache_key=key,model=config['model'],model_version=config['model_version'],
        voice=config['voice'],config={'elapsed_seconds':time.monotonic()-started},storage_key=str(path.relative_to(DATA)).replace('\\','/'),
        duration_seconds=duration,sample_rate=int(audio.get('sample_rate') or 48000),valid=True)
    session.add(row);session.flush();return row

def esc(value):return value.replace('\\','/').replace(':','\\:').replace("'","\\'")
def ensure_active(job,session):
    session.refresh(job,['cancelled'])
    if job.cancelled:raise RuntimeError('Job đã được hủy')
def render(job,session):
    snap=job.input_snapshot;cfg=snap['config']
    if job.checkpoint.get('audio_complete'):
        timeline=job.checkpoint['timeline']
    else:
        timeline=[];cursor=0.0
        for index,scene in enumerate(snap['scenes']):
            ensure_active(job,session);audio=synthesize(session,scene['narration'],cfg)
            timeline.append({**scene,'audio_id':audio.id,'audio_key':audio.storage_key,'start':cursor,'duration':audio.duration_seconds})
            cursor+=audio.duration_seconds
            job.progress=10+int(35*(index+1)/len(snap['scenes']));session.flush()
        job.checkpoint={'audio_complete':True,'timeline':timeline};job.status='rendering';job.stage='rendering';job.progress=50
        job.logs=[*job.logs,{'stage':'audio_complete','at':utcnow().isoformat(),'audio_count':len(timeline)}];session.commit()
    if cfg.get('preview_only'):
        job.status='succeeded';job.stage='audio_ready';job.progress=100;job.finished_at=utcnow();job.owner=None;job.lease_until=None
        job.logs=[*job.logs,{'stage':'audio_ready','at':job.finished_at.isoformat()}];session.flush();return None
    width,height=int(cfg['width']),int(cfg['height']);work=TMP/f'job-{job.id}';work.mkdir(exist_ok=True)
    parts=[]
    for i,scene in enumerate(timeline):
        ensure_active(job,session)
        out=work/f'scene-{i}.mp4';asset=session.get(MediaAsset,scene.get('asset_id')) if scene.get('asset_id') else None
        duration=scene['duration']; label='HÌNH MINH HỌA' if scene.get('illustration_label',True) else ''
        text=(scene.get('on_screen_text') or '').replace("'","’").replace(':','\\:')
        draw=f"drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:text='{text}':fontcolor=white:fontsize={max(28,width//18)}:x=(w-text_w)/2:y=h*0.16:box=1:boxcolor=black@0.55:boxborderw=18"
        labeldraw=f",drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:text='{label}':fontcolor=white:fontsize={max(18,width//30)}:x=24:y=24:box=1:boxcolor=black@0.55:boxborderw=8" if label else ''
        if asset:
            src=DATA/asset.storage_key
            crop=scene.get('crop') or {};scale=max(1.0,min(3.0,float(crop.get('scale',1))));x=max(0.0,min(1.0,float(crop.get('x',.5))));y=max(0.0,min(1.0,float(crop.get('y',.5))))
            command=['ffmpeg','-y','-loop','1','-i',str(src)] if asset.media_type=='image' else ['ffmpeg','-y','-stream_loop','-1','-i',str(src)]
            command += ['-t',str(duration),'-vf',f'scale={int(width*scale)}:{int(height*scale)}:force_original_aspect_ratio=increase,crop={width}:{height}:(iw-ow)*{x}:(ih-oh)*{y},{draw}{labeldraw}']
        else:
            command=['ffmpeg','-y','-f','lavfi','-i',f'color=c=#17344a:s={width}x{height}:d={duration}','-vf',draw+labeldraw]
        command += ['-r','30','-c:v','libx264','-pix_fmt','yuv420p','-an',str(out)]
        subprocess.run(command,check=True,capture_output=True,timeout=300);parts.append(out)
    concat=work/'concat.txt';concat.write_text(''.join(f"file '{str(p).replace(chr(92),'/')}'\n" for p in parts),encoding='utf-8')
    video=work/'visual.mp4';subprocess.run(['ffmpeg','-y','-f','concat','-safe','0','-i',str(concat),'-c','copy',str(video)],check=True,capture_output=True,timeout=300)
    audio_list=work/'audio.txt';audio_list.write_text(''.join(f"file '{str(DATA/s['audio_key']).replace(chr(92),'/')}'\n" for s in timeline),encoding='utf-8')
    combined=work/'voice.wav';subprocess.run(['ffmpeg','-y','-f','concat','-safe','0','-i',str(audio_list),'-c:a','pcm_s16le',str(combined)],check=True,capture_output=True,timeout=300)
    srt=[]
    def stamp(v):
        ms=int(round(v*1000));return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}'
    for i,s in enumerate(timeline):srt += [str(i+1),f"{stamp(s['start'])} --> {stamp(s['start']+s['duration'])}",s['narration'],'']
    subtitle=work/'subtitles.srt';subtitle.write_text('\n'.join(srt),encoding='utf-8')
    version=(session.scalar(select(func.max(VideoVersion.version)).where(VideoVersion.script_version_id==job.script_version_id)) or 0)+1
    output=VIDEOS/f'script-{job.script_version_id}-v{version}.mp4'
    subtitle_filter=f"subtitles='{esc(str(subtitle))}':force_style='FontName=DejaVu Sans,FontSize=14,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,MarginV=90,Alignment=2'"
    subprocess.run(['ffmpeg','-y','-i',str(video),'-i',str(combined),'-vf',subtitle_filter,'-c:v','libx264','-preset','medium','-crf','22','-c:a','aac','-b:a','160k','-shortest','-movflags','+faststart',str(output)],check=True,capture_output=True,timeout=900)
    meta,duration=probe(output);video_stream=next((s for s in meta['streams'] if s.get('width')),{})
    if int(video_stream.get('width',0))!=width or int(video_stream.get('height',0))!=height:raise RuntimeError('Sai độ phân giải đầu ra')
    parent=snap.get('parent_version_id');row=VideoVersion(script_version_id=job.script_version_id,job_id=job.id,version=version,parent_version_id=parent,
        status='needs_review',config=cfg,timeline={'scenes':timeline,'subtitles':srt},input_hash=job.request_hash,
        output_key=str(output.relative_to(DATA)).replace('\\','/'),probe=meta,duration_seconds=duration,test_only=cfg.get('test_only',False))
    from app.services.media_integrity import sha256_file
    row.output_sha256=sha256_file(output)
    session.add(row);job.status='succeeded';job.stage='complete';job.progress=100;job.finished_at=utcnow();job.owner=None;job.lease_until=None
    job.logs=[*job.logs,{'stage':'render_complete','at':job.finished_at.isoformat(),'output_key':row.output_key}];session.flush();shutil.rmtree(work,ignore_errors=True);return row

def execute(job_id,owner):
    from app.database import SessionLocal,session_scope
    try:
        # render() commits after audio generation so that a container restart can
        # resume from the durable checkpoint instead of losing completed TTS work.
        with SessionLocal() as session:
            job=session.scalar(select(VideoJob).where(VideoJob.id==job_id,VideoJob.owner==owner).with_for_update())
            if not job or job.cancelled:return
            render(job,session)
            session.commit()
    except Exception as exc:
        with session_scope() as session:
            job=session.get(VideoJob,job_id)
            if job and job.owner==owner:
                if not job.cancelled:job.status='failed';job.stage='failed';job.error=str(exc)[-2000:]
                job.finished_at=utcnow();job.owner=None;job.lease_until=None
                job.logs=[*job.logs,{'stage':job.stage,'at':job.finished_at.isoformat(),'error':str(exc)[-500:]}]

def recover(session):
    for job in session.scalars(select(VideoJob).where(VideoJob.status.in_(('generating_audio','rendering')),VideoJob.lease_until<utcnow()).with_for_update(skip_locked=True)):
        version=session.scalar(select(VideoVersion).where(VideoVersion.job_id==job.id))
        if version and (DATA/version.output_key).exists():job.status='succeeded';job.progress=100;job.stage='complete'
        else:job.status='queued';job.stage='recovered';job.owner=None;job.lease_until=None
