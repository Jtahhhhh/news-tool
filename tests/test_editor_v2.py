import json
import subprocess
from copy import deepcopy
import pytest
from pydantic import ValidationError
from app.services.video.validator import validate
from app.services.video.ffmpeg import compile_composition
from app.services.video.media_probe import audit,prepare
from app.services.video.animation import state
from app.services.video.timeline import split_clip
from app.services.video.analysis import boundaries,analyze
from app.services.video.fit_service import auto_fit,audit_target
from tests.test_editor import sources


def run(args):
    r=subprocess.run(['ffmpeg','-v','error','-y',*args],capture_output=True,timeout=90)
    assert r.returncode==0,r.stderr.decode()[-5000:]
    return r.stdout


def project(clips,kind='video'):
    return validate(dict(target=dict(width=320,height=320),tracks=[dict(id='v',type=kind,clips=clips)]))


def test_version_target_animation_and_transition_validation():
    c=dict(id='a',asset_id=1,duration=2,source_end=2)
    p=project([c]);assert p['version']==p['schema_version']==2
    assert p['canvas']['width']==320 and p['target']['aspect_ratio']=='1:1'
    for target in ({'width':0},{'width':'bad'},{'width':321}):
        with pytest.raises(ValidationError): validate(dict(target=target,tracks=p['tracks']))
    for change in ({'keyframes':[dict(time=0,scale=1.5)]},{'keyframes':[dict(time=1),dict(time=0)]},
                   {'animation':{'in':{'type':'fade','duration':1}}}):
        with pytest.raises(ValidationError):project([{**c,**change}])
    b={**c,'id':'b','timeline_start':1.6,'transition':dict(type='crossfade',duration=0.4,from_clip_id='a')}
    project([c,b])
    with pytest.raises(ValidationError):project([c,{**b,'timeline_start':1.5}])
    with pytest.raises(ValidationError):validate({**p,'version':3})


def test_publish_across_separate_compose_volumes(tmp_path,monkeypatch):
    import errno
    from pathlib import Path
    from app.services.video.render import publish_output
    pending=tmp_path/'render.mp4';output=tmp_path/'published.mp4';pending.write_bytes(b'validated video')
    replace=Path.replace
    def cross_volume(self,target):
        if self==pending: raise OSError(errno.EXDEV,'Invalid cross-device link')
        return replace(self,target)
    monkeypatch.setattr(Path,'replace',cross_volume)
    publish_output(pending,output)
    assert output.read_bytes()==b'validated video' and not pending.exists()
    assert not list(tmp_path.glob('*.partial'))


def test_animation_split_fit_and_content_boundaries():
    c=project([dict(id='a',asset_id=1,duration=4,source_end=4,animation={'loop':'zoom_in'},
                   keyframes=[dict(time=0,x=-.2),dict(time=4,x=.2,easing='ease-in-out')])])['tracks'][0]['clips'][0]
    a,b=split_clip(c,1.5,'b')
    for t in (0,.1,1,2): assert state(b,t)==state(c,t+1.5)
    source=dict(width=1920,height=1080)
    assert audit_target(source,dict(width=1080,height=1920))['requires_fit']
    assert auto_fit(c,source,dict(width=1080,height=1920))['crop']['mode']=='blur_background'
    c['crop'].update(manual=True,x=.8)
    assert auto_fit(c,source,dict(width=1080,height=1920))['crop']==c['crop']
    cuts,reasons=boundaries(20,[6,13],[(6.1,6.7)],[6.2])
    assert cuts==[0,6.2,13,20] and reasons[0]['reason']=='sentence'


@pytest.mark.parametrize('extension,options',[
    ('mp4',['-c:v','libx264']),('mov',['-c:v','libx264']),('mkv',['-c:v','ffv1']),
    ('webm',['-c:v','libvpx-vp9']),('avi',['-c:v','mpeg4']),('m4v',['-c:v','libx264','-f','mp4']),
    ('ts',['-c:v','mpeg2video']),('gif',[]),
])
def test_video_formats_proxy_and_original_preserved(tmp_path,extension,options):
    source=tmp_path/f'original.{extension}'
    run(['-f','lavfi','-i','testsrc2=s=160x90:r=12:d=0.5',*options,str(source)])
    before=source.read_bytes();meta=audit(source)
    assert meta['type']=='video' and meta['width']==160 and meta['decoder_ok']
    proxy=prepare(source,meta)
    if meta['needs_proxy']:
        assert proxy and proxy.suffix=='.mp4'
        assert audit(proxy)['video_codec']=='h264'
        assert prepare(source,meta)==proxy
    assert source.read_bytes()==before


@pytest.mark.parametrize('extension',['mp3','wav','m4a','aac','ogg','flac'])
def test_audio_upload_formats(client,tmp_path,extension):
    source=tmp_path/f'audio.{extension}'
    run(['-f','lavfi','-i','sine=duration=0.4',str(source)])
    r=client.post('/assets',files={'file':(source.name,source.read_bytes(),'application/octet-stream')})
    assert r.status_code==201,r.text
    assert r.json()['kind']=='audio' and r.json()['ref_type']=='asset' and r.json()['has_audio']


@pytest.mark.parametrize('extension',['jpg','jpeg','png','webp'])
def test_image_formats(tmp_path,extension):
    source=tmp_path/f'image.{extension}'
    run(['-f','lavfi','-i','color=s=160x90','-frames:v','1','-threads','1',str(source)])
    assert audit(source)['type']=='image'


def test_phone_rotation_before_aspect_audit(tmp_path,sources):
    source=tmp_path/'phone.mov'
    run(['-display_rotation','90','-i',str(sources[0]),'-c','copy',str(source)])
    meta=audit(source)
    assert (meta['width'],meta['height'])==(120,160) and meta['rotation']==90
    assert meta['needs_proxy']
    proxy=audit(prepare(source,meta))
    assert (proxy['width'],proxy['height'])==(120,160) and not proxy['rotation']


@pytest.mark.parametrize('mode,effect,motion',[
    ('fit','fade','zoom_in'),('fill','slide_left','pan_right'),('crop','zoom','zoom_out'),
    ('blur_background','pop','pan_left'),('smart_crop','slide_up','pan_down'),('stretch','slide_right','pan_up'),
])
def test_render_dynamic_animation_and_fit(tmp_path,sources,mode,effect,motion):
    p=project([dict(id='a',asset_id=1,duration=.8,source_end=.8,
        crop={'mode':mode},animation={'in':{'type':effect,'duration':.3},'out':{'type':'fade','duration':.2},'loop':motion},
        keyframes=[dict(time=0,rotation=-5,crop_x=.2,volume=.1),dict(time=.8,rotation=5,crop_x=.8,volume=.5,easing='ease-in-out')])])
    output=tmp_path/'out.mp4'
    cmd=compile_composition(p,{('asset',1):dict(path=str(sources[0]),kind='video',has_audio=True)},tmp_path,output)
    r=subprocess.run(cmd,capture_output=True,text=True,timeout=90)
    assert r.returncode==0,r.stderr[-5000:]
    meta=audit(output);assert (meta['width'],meta['height'])==(320,320)
    frame1=run(['-ss','0.1','-i',str(output),'-frames:v','1','-vf','scale=1:1','-pix_fmt','rgb24','-f','rawvideo','-'])
    frame2=run(['-ss','0.5','-i',str(output),'-frames:v','1','-vf','scale=1:1','-pix_fmt','rgb24','-f','rawvideo','-'])
    assert sum(frame2)>sum(frame1), 'Fade must affect exported pixels'


@pytest.mark.parametrize('effect',['crossfade','fade_black','slide','push','zoom'])
def test_transition_export_pixels(tmp_path,effect):
    refs={}
    for i,color in enumerate(('red','blue'),1):
        path=tmp_path/f'{color}.png';run(['-f','lavfi','-i',f'color=c={color}:s=320x320','-frames:v','1','-threads','1',str(path)])
        refs[('asset',i)]=dict(path=str(path),kind='image',has_audio=False)
    p=project([dict(id='a',asset_id=1,duration=1,source_end=1),dict(id='b',asset_id=2,timeline_start=.6,duration=1,source_end=1,transition=dict(type=effect,duration=0.4,from_clip_id='a'))])
    out=tmp_path/'out.mp4';r=subprocess.run(compile_composition(p,refs,tmp_path,out),capture_output=True,text=True,timeout=90)
    assert r.returncode==0,r.stderr[-5000:]
    rgb=run(['-ss','0.8','-i',str(out),'-frames:v','1','-vf','scale=1:1','-pix_fmt','rgb24','-f','rawvideo','-'])
    if effect=='crossfade': assert rgb[0]>70 and rgb[2]>70,rgb
    if effect=='fade_black': assert sum(rgb)<40,rgb
    rgb=run(['-ss','1.2','-i',str(out),'-frames:v','1','-vf','scale=1:1','-pix_fmt','rgb24','-f','rawvideo','-'])
    assert rgb[2]>200 and rgb[0]<20


def test_scene_analysis_endpoint(client,tmp_path):
    path=tmp_path/'cuts.mp4'
    run(['-f','lavfi','-i','color=c=red:s=160x90:d=1.5','-f','lavfi','-i','color=c=blue:s=160x90:d=1.5',
         '-filter_complex','[0:v][1:v]concat=n=2:v=1:a=0','-c:v','libx264',str(path)])
    r=client.post('/assets',files={'file':(path.name,path.read_bytes(),'video/mp4')});assert r.status_code==201,r.text
    result=client.post(f'/assets/{r.json()["id"]}/analysis',json={'target':{'width':1080,'height':1920}})
    assert result.status_code==200,result.text
    data=result.json();assert any(abs(t-1.5)<.25 for t in data['cuts'][1:-1])
    assert data['keyframes'] and not data['has_sentence_boundaries']

