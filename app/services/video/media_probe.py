"""Audit local originals and create reusable browser media without losing sources."""
import json
import math
import subprocess
import uuid
from fractions import Fraction
from pathlib import Path
from fastapi import HTTPException

EXTENSIONS={'.mp4','.mov','.mkv','.webm','.avi','.m4v','.ts',
            '.jpg','.jpeg','.png','.webp','.gif','.mp3','.wav','.m4a','.aac','.ogg','.flac'}
IMAGE_EXTENSIONS={'.jpg','.jpeg','.png','.webp','.gif'}


def number(value,default=0):
    try:
        n=float(Fraction(str(value)))
        return n if math.isfinite(n) else default
    except (ValueError,ZeroDivisionError,TypeError): return default


def run(args,timeout=60):
    try:
        r=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
    except (OSError,subprocess.TimeoutExpired) as e:
        raise HTTPException(422,'Không thể đọc/chuẩn hóa media trong thời gian cho phép') from e
    if r.returncode:
        raise HTTPException(422,'File media hỏng hoặc decoder không đọc được: '+r.stderr[-300:])
    return r.stdout


def audit(path):
    path=Path(path)
    raw=json.loads(run(['ffprobe','-v','error','-protocol_whitelist','file,pipe,crypto,data',
                       '-show_streams','-show_format','-of','json',str(path)]))
    streams=raw.get('streams',[])
    v=next((s for s in streams if s.get('codec_type')=='video' and not s.get('disposition',{}).get('attached_pic')),None)
    a=next((s for s in streams if s.get('codec_type')=='audio'),None)
    if not v and not a: raise HTTPException(422,'Không có stream video hoặc audio')
    rotation=number((v or {}).get('tags',{}).get('rotate'))
    for side in (v or {}).get('side_data_list',[]):
        if 'rotation' in side: rotation=number(side['rotation'])
    rotation=rotation%360
    w,h=int((v or {}).get('width',0)),int((v or {}).get('height',0))
    sar=number((v or {}).get('sample_aspect_ratio','1').replace(':','/'),1)
    dw,dh=round(w*sar),h
    if round(rotation)%180==90: dw,dh=dh,dw
    fmt=raw.get('format',{})
    d=number(fmt.get('duration')) or number((v or a).get('duration'))
    animated=path.suffix.lower()=='.gif' and (number((v or {}).get('nb_frames'))>1 or d>0)
    kind='image' if v and path.suffix.lower() in IMAGE_EXTENSIONS and not animated else 'video' if v else 'audio'
    if kind!='image' and d<=0: raise HTTPException(422,'Media không có thời lượng hợp lệ')
    if v and (not w or not h): raise HTTPException(422,'File media hỏng: kích thước video không hợp lệ')
    fps=number((v or {}).get('avg_frame_rate')) or number((v or {}).get('r_frame_rate'))
    nominal=number((v or {}).get('r_frame_rate'))
    issues=[]
    if v and kind!='image' and abs(fps-nominal)>.05: issues.append('VARIABLE_FPS')
    if number(fmt.get('start_time'))!=0: issues.append('TIMESTAMP_OFFSET')
    if rotation: issues.append('ROTATION_METADATA')
    if v and not a and kind!='image': issues.append('MISSING_AUDIO')
    container=fmt.get('format_name','')
    direct=(kind=='image' and path.suffix.lower() in {'.jpg','.jpeg','.png','.webp'}) or (
        kind=='video' and path.suffix.lower() in {'.mp4','.m4v'} and v.get('codec_name')=='h264'
        and v.get('pix_fmt')=='yuv420p' and (not a or a.get('codec_name')=='aac')) or (
        kind=='audio' and path.suffix.lower() in {'.mp3','.wav','.m4a'} and a.get('codec_name') in {'mp3','aac','pcm_s16le'})
    if not direct: issues.append('BROWSER_NORMALIZATION')
    needs_proxy=not direct or (kind=='video' and (w*h>1920*1080 or fps>30.05 or rotation or 'VARIABLE_FPS' in issues or 'TIMESTAMP_OFFSET' in issues or path.stat().st_size>80*1024*1024))
    # Decode a bounded sample; warnings about timestamps trigger normalization.
    check=['ffmpeg','-v','warning','-protocol_whitelist','file,pipe,crypto,data','-i',str(path),'-t','1','-f','null','-']
    try: sample=subprocess.run(check,capture_output=True,text=True,timeout=60)
    except (OSError,subprocess.TimeoutExpired) as e: raise HTTPException(422,'Decoder vượt thời gian kiểm tra media') from e
    if sample.returncode: raise HTTPException(422,'Decoder không đọc được media: '+sample.stderr[-300:])
    if any(s in sample.stderr.lower() for s in ('non-monoton','invalid timestamp','invalid dts')):
        issues.append('BROKEN_TIMESTAMP');needs_proxy=True
    return dict(type=kind,container=container,video_codec=(v or {}).get('codec_name'),audio_codec=(a or {}).get('codec_name'),
        width=dw,height=dh,coded_width=w,coded_height=h,duration=d,fps=fps,aspect_ratio=dw/dh if dh else 0,
        rotation=rotation,pixel_format=(v or {}).get('pix_fmt'),audio_channels=(a or {}).get('channels',0),
        audio_sample_rate=int((a or {}).get('sample_rate',0)),has_audio=bool(a),animated=animated,
        issues=issues,needs_proxy=bool(needs_proxy),decoder_ok=True,source_bytes=path.stat().st_size)


def prepare(path,probe):
    """Return the cached preview path. Exports always resolve the original."""
    if not probe['needs_proxy']: return None
    path=Path(path)
    suffix='.mp4' if probe['type']=='video' else '.m4a' if probe['type']=='audio' else '.png'
    proxy=path.with_name(path.stem+'.preview'+suffix)
    if proxy.is_file(): return proxy
    temporary=proxy.with_name(proxy.stem+'.'+uuid.uuid4().hex+suffix)
    cmd=['ffmpeg','-v','error','-y','-fflags','+genpts','-protocol_whitelist','file,pipe,crypto,data','-i',str(path)]
    if probe['type']=='video':
        cmd+=['-map','0:v:0','-map','0:a:0?','-vf',"scale=iw*sar:ih,setsar=1,scale=w='min(1280,iw)':h='min(1280,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,fps=30",
              '-c:v','libx264','-preset','veryfast','-crf','24','-pix_fmt','yuv420p','-c:a','aac','-ar','48000','-ac','2','-movflags','+faststart']
    elif probe['type']=='audio': cmd+=['-vn','-c:a','aac','-ar','48000','-ac','2','-movflags','+faststart']
    else: cmd+=['-frames:v','1']
    try:
        run(cmd+[str(temporary)],timeout=7200)
        temporary.replace(proxy)
    finally: temporary.unlink(missing_ok=True)
    return proxy
