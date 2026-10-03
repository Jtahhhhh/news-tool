"""Isolated UI fixture. Refuses to run against a non-test database.

Run with POSTGRES_DB=codex_editor_test, MEDIA_ROOT=/tmp/editor-preview.
This creates synthetic media and a labelled test project; no TTS/network service call.
"""
import os
import subprocess
import uuid
from pathlib import Path
from sqlalchemy import select
from app.database import engine, session_scope
from app.start import migrate
from app.models import Event, ScriptSourceSnapshot, ScriptVersion, VideoJob, MediaAsset, TTSAudio
from app.services.video_service import ASSETS, AUDIO
from app.services.video.timeline import from_scenes
from app.services.video.validator import validate


def main():
    if not engine.url.database.endswith('_test'):
        raise RuntimeError('UI fixture requires a dedicated _test database')
    migrate()
    video=ASSETS/'editor-v2-demo.mp4'
    audio=AUDIO/'editor-demo.wav'
    if not video.exists():
        subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','testsrc2=s=320x180:r=30:d=120','-f','lavfi','-i','sine=frequency=330:duration=120','-c:v','libx264','-preset','ultrafast','-c:a','aac','-shortest',str(video)],check=True)
    if not audio.exists():
        subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','sine=frequency=660:duration=8',str(audio)],check=True)
    with session_scope() as s:
        asset=s.scalar(select(MediaAsset).where(MediaAsset.storage_key=='assets/editor-v2-demo.mp4'))
        if not asset:
            asset=MediaAsset(filename='Landscape 120s · TEST.mp4',storage_key='assets/editor-v2-demo.mp4',media_type='video',source='upload',sha256='d'*64,test_only=True)
            s.add(asset)
        voice=s.scalar(select(TTSAudio).where(TTSAudio.storage_key=='audio/editor-demo.wav'))
        if not voice:
            voice=TTSAudio(text='Demo voice · synthetic test tone',text_hash='e'*64,cache_key='e'*64,model='test',model_version='1',voice='test',storage_key='audio/editor-demo.wav',duration_seconds=8,sample_rate=44100)
            s.add(voice)
        event=Event(title='Bản tin hôm nay · Editor QA',decision='selected');s.add(event);s.flush()
        snapshot=ScriptSourceSnapshot(event_id=event.id,payload={},digest='d'*64);s.add(snapshot);s.flush()
        script=ScriptVersion(event_id=event.id,version=1,snapshot_id=snapshot.id,provider='fake',model='fake',outcome='draft',status='approved',data={'title':event.title,'scenes':[{'scene_id':1,'narration':'Bản tin hôm nay','on_screen_text':'NEWS UPDATE','visual_brief':'Test','seconds':8}]},prompt_version='1',schema_version='1',prompt_hash='d'*64,schema_hash='d'*64)
        s.add(script);s.flush()
        comp=from_scenes([dict(scene_id=1,duration=8,start=0,audio_id=voice.id,narration='Khám phá không gian sáng tạo mới của thành phố.',on_screen_text='NEWS UPDATE')])
        comp['tracks'][0]['clips'].extend([
            dict(id='main-demo',asset_id=asset.id,name='Landscape · Fade + Slow Zoom',timeline_start=0,duration=7,source_start=35,source_end=42,volume=.15,crop={'mode':'blur_background'},animation={'in':{'type':'fade','duration':.4},'loop':'zoom_in'}),
            dict(id='second-demo',asset_id=asset.id,name='Crossfade + Pan',timeline_start=6.6,duration=6,source_start=42,source_end=48,volume=.15,crop={'mode':'blur_background'},animation={'loop':'pan_right'},transition={'type':'crossfade','duration':.4,'from_clip_id':'main-demo'})])
        comp['tracks'][5]['clips'][0]['animation']={'in':{'type':'pop','duration':.4}}
        comp['tracks'][4]['clips'][0]['subtitle_animation']='fade'
        comp=validate(comp)
        job=VideoJob(script_version_id=script.id,idempotency_key='demo-'+uuid.uuid4().hex,request_hash='d'*64,status='succeeded',stage='audio_ready',progress=100,input_snapshot={'config':{'test_only':True,'preview_only':True}},checkpoint={'composition':comp})
        s.add(job);s.flush()
        print(f'EDITOR_PREVIEW=/videos/editor/{script.id}',flush=True)
    import uvicorn
    uvicorn.run('app.main:app',host='0.0.0.0',port=8000)


if __name__=='__main__':
    main()
