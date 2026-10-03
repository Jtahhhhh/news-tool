"""Audio filter chains, including source trim, speed, fades and timeline delay."""


def filters(clip,outgoing=None):
    from .animation import state,Expr
    volume=state(clip,Expr('t'),outgoing)['volume']
    d = clip['duration']
    values = [f"atrim=start={clip['source_start']}:end={clip['source_end']}",
              'asetpts=PTS-STARTPTS', f"atempo={clip['speed']}",
              f"volume='{0 if clip['muted'] else volume}':eval=frame"]
    if clip['fade_in']:
        values.append(f"afade=t=in:st=0:d={clip['fade_in']}")
    if clip['fade_out']:
        values.append(f"afade=t=out:st={d-clip['fade_out']}:d={clip['fade_out']}")
    values.append(f"adelay={round(clip['timeline_start']*1000)}:all=1")
    return ','.join(values)


def normalize(text,rules):
    import re
    value=text
    for source,target in sorted(rules.items(),key=lambda x:-len(x[0])):
        value=re.sub(r'(?<!\w)'+re.escape(source)+r'(?!\w)',target,value,flags=re.I)
    return re.sub(r'\s+',' ',value).strip()

def synthesize(session,text,config):
    import os, time, httpx
    from sqlalchemy import select
    from app.models import TTSAudio
    from app.services.video_service import DATA, AUDIO, digest
    from .ffmpeg import probe
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
