import json
import subprocess
from copy import deepcopy
from pathlib import Path
import pytest
from pydantic import ValidationError
from app.services.video.validator import validate
from app.services.video.timeline import from_scenes, split_clip, duration
from app.services.video.ffmpeg import compile_composition
from app.services.video.subtitle import escape_text
from tests.test_video import video_event


def composition(clips=None, kind='video'):
    return validate({'tracks':[{'id':'main','type':kind,'clips':clips or []}]})


def test_validation_rejects_bad_timing_duplicate_ids_overlap_and_nonfinite():
    c=dict(id='v',asset_id=1,duration=2,source_end=2)
    for change in ({'duration':-1},{'source_end':4},{'volume':float('nan')},{'source_start':float('inf')}):
        with pytest.raises(ValidationError):composition([{**c,**change}])
    with pytest.raises(ValidationError):composition([c,c])
    with pytest.raises(ValidationError):composition([c,{**c,'id':'b','timeline_start':1}])
    with pytest.raises(ValidationError):composition([c],'text')
    with pytest.raises(ValidationError):composition([dict(id='x')],'audio')


def test_legacy_migration_and_source_split():
    comp=from_scenes([{'scene_id':1,'narration':'Xin chào','on_screen_text':'Tin mới','audio_id':1,'start':0,'duration':4}])
    assert duration(comp)==4
    assert len(comp['tracks'])==6
    voice=comp['tracks'][3]['clips'][0]
    voice.update(source_start=35,source_end=43,speed=2)
    a,b=split_clip(voice,1.5,'second')
    assert a['source_end']==b['source_start']==38
    assert b['source_end']==43
    assert a['duration']+b['duration']==4
    validate({**comp,'tracks':[{**comp['tracks'][3],'clips':[a,b]}]})
    assert r'\pos' not in escape_text(r'{\pos(2,3)} 100% : \' quote')


@pytest.fixture
def sources(tmp_path):
    video=tmp_path/'source.mp4';image=tmp_path/'still.png';audio=tmp_path/'voice.wav'
    def run(args):
        result=subprocess.run(['ffmpeg','-v','error','-y',*args],capture_output=True,text=True)
        assert result.returncode==0,result.stderr
    run(['-f','lavfi','-i','testsrc2=s=160x120:r=30:d=3','-f','lavfi','-i','sine=frequency=440:duration=3','-c:v','libx264','-c:a','aac','-shortest',str(video)])
    run(['-f','lavfi','-i','color=c=red:s=100x100','-frames:v','1','-threads','1',str(image)])
    run(['-f','lavfi','-i','sine=frequency=880:duration=3',str(audio)])
    return video,image,audio


def test_real_ffmpeg_multitrack_speed_trim_rotation_opacity_and_ass(tmp_path,sources):
    video,image,audio=sources
    comp=validate({'tracks':[
        {'id':'main','type':'video','clips':[dict(id='v',asset_id=1,timeline_start=.2,duration=1,source_start=.5,source_end=2.5,speed=2,volume=.15)]},
        {'id':'over','type':'video','clips':[dict(id='image',asset_id=2,duration=1.5,source_end=1.5,transform=dict(width=.3,height=.2,x=.2,rotation=20,opacity=.5),crop=dict(mode='fit'))]},
        {'id':'voice','type':'audio','clips':[dict(id='a',audio_id=1,timeline_start=.5,duration=1,source_end=1,fade_in=.2,fade_out=.2)]},
        {'id':'sub','type':'subtitle','clips':[dict(id='s',text="Tiếng Việt: 100% ' {hello} \\ test",duration=1.5,source_end=1.5,transform=dict(y=.3,width=.9,height=.2),style=dict(background=True))]},
    ]})
    refs={('asset',1):dict(path=str(video),kind='video',has_audio=True),('asset',2):dict(path=str(image),kind='image',has_audio=False),('audio',1):dict(path=str(audio),kind='audio',has_audio=True)}
    output=tmp_path/'out.mp4'
    cmd=compile_composition(comp,refs,tmp_path,output)
    result=subprocess.run(cmd,capture_output=True,text=True,timeout=90)
    assert result.returncode==0,result.stderr[-5000:]
    meta=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(output)]))
    assert (meta['streams'][0]['width'],meta['streams'][0]['height'])==(1080,1920)
    assert abs(float(meta['format']['duration'])-1.5)<.1
    assert any(s['codec_type']=='audio' for s in meta['streams'])
    assert '-filter_complex_script' in cmd


def test_upload_images_and_limit(client,sources,monkeypatch):
    from app.config import get_settings
    jpeg=sources[1].with_suffix('.jpg')
    subprocess.run(['ffmpeg','-v','error','-y','-i',str(sources[1]),str(jpeg)],check=True)
    for source,mime in ((sources[1],'image/png'),(jpeg,'image/jpeg')):
        result=client.post('/assets',files={'file':(source.name,source.read_bytes(),mime)})
        assert result.status_code==201,result.text
        assert result.json()['kind']=='image'
    monkeypatch.setattr(get_settings(),'max_media_upload_mb',1)
    result=client.post('/assets',files={'file':('large.mp4',b'x'*(1024*1024+1),'video/mp4')})
    assert result.status_code==422


def test_draft_concurrency_export_snapshot_and_test_gate(db,client,sources,video_event,monkeypatch):
    from sqlalchemy import select
    from app.models import ScriptVersion, VideoJob, TTSAudio, MediaAsset, Event
    from app.services.video_service import DATA, ASSETS, AUDIO, digest
    from tests.test_scripts import request_for, run_queued
    from app.services.script_service import enqueue_script
    with db() as s: enqueue_script(s,request_for(video_event))
    run_queued(db)
    with db() as s:
        script=s.scalar(select(ScriptVersion));script.status='approved';sid=script.id
        target=AUDIO/'editor-test.wav';target.write_bytes(sources[2].read_bytes())
        audio=TTSAudio(text='Test',text_hash='b'*64,cache_key='b'*64,model='test',model_version='test',voice='test',config={},storage_key='audio/editor-test.wav',duration_seconds=3,sample_rate=44100,valid=True)
        s.add(audio);s.flush()
        comp=from_scenes([dict(duration=2,start=0,audio_id=audio.id,narration='Test',on_screen_text='Test')])
        job=VideoJob(script_version_id=sid,idempotency_key='editor-preview',request_hash='c'*64,status='succeeded',stage='audio_ready',progress=100,input_snapshot={'config':{'test_only':True,'preview_only':True}},checkpoint={'composition':comp},logs=[])
        s.add(job);s.flush();jid=job.id
    page=client.get(f'/videos/editor/{sid}');assert page.status_code==200,page.text
    data=client.get(f'/videos/editor/{sid}/data').json();assert data['job_id']==jid
    body={'job_id':jid,'revision':0,'composition':comp}
    saved=client.post(f'/videos/editor/{sid}/draft',json=body);assert saved.status_code==200,saved.text
    assert client.post(f'/videos/editor/{sid}/draft',json=body).status_code==409
    export={**body,'revision':1,'idempotency_key':'editor-export-1'}
    result=client.post(f'/videos/editor/{sid}/export',json=export);assert result.status_code==202,result.text
    assert client.post(f'/videos/editor/{sid}/export',json=export).json()['id']==result.json()['id']
    with db() as s:
        job=s.get(VideoJob,result.json()['id']);assert job.input_snapshot['composition']==comp;assert job.input_snapshot['config']['test_only'];assert not job.input_snapshot['config']['preview_only']
        source=s.get(VideoJob,jid);assert source.checkpoint['editor_revision']==1
    from app.services import video_service
    from app.models import VideoVersion
    def unexpected_tts(*args): raise AssertionError('Export attempted TTS')
    monkeypatch.setattr(video_service,'synthesize',unexpected_tts)
    with db() as s: claim=video_service.claim(s)
    video_service.execute(*claim)
    with db() as s:
        job=s.get(VideoJob,result.json()['id']);assert job.status=='succeeded',job.error
        version=s.scalar(select(VideoVersion).where(VideoVersion.job_id==job.id))
        assert version.timeline==comp and version.test_only and version.status=='needs_review'
        assert version.output_sha256 and version.config['width']==1080
        vid=version.id
    assert client.get(f'/videos/{vid}').status_code==200
    rerender_body={'script_version_id':sid,'parent_version_id':vid,'idempotency_key':'editor-rerender','quality':'final'}
    assert client.post('/video-jobs',json=rerender_body).status_code==409
    rerender=client.post('/video-jobs',json={**rerender_body,'test_only':True})
    assert rerender.status_code==202,rerender.text
    with db() as s:
        assert s.get(VideoJob,rerender.json()['id']).input_snapshot['composition']==comp
