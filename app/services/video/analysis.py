"""Content-based cut candidates and bounded motion tracking; no invented ASR."""
import re
import subprocess
from .fit_service import crop_window


def boundaries(duration, scenes=(), silences=(), sentences=(), minimum=.75):
    candidates=[(float(t),'scene') for t in scenes]
    candidates += [((a+b)/2,'silence') for a,b in silences]
    candidates += [(float(t),'sentence') for t in sentences]
    groups=[]
    for time,kind in sorted(candidates):
        if time<minimum or time>duration-minimum: continue
        if groups and time-groups[-1][-1][0]<minimum: groups[-1].append((time,kind))
        else: groups.append([(time,kind)])
    # Prefer a supplied sentence boundary, then a visual cut, then silence.
    selected=[min(g,key=lambda x:({'sentence':0,'scene':1,'silence':2}[x[1]],x[0])) for g in groups]
    return [0,*[round(t,3) for t,_ in selected],round(duration,3)], [dict(time=round(t,3),reason=k) for t,k in selected]


def analyze(path,source,target,sentences=()):
    duration=source['duration']
    # Scene scores are evaluated at 5 FPS; cut precision is consequently ~200 ms.
    cmd=['ffmpeg','-hide_banner','-nostats','-fflags','+genpts','-i',str(path),
         '-vf',"scale=160:-2,fps=5,select='gt(scene,0.3)',showinfo",'-an','-f','null','-']
    result=subprocess.run(cmd,capture_output=True,text=True,timeout=600)
    if result.returncode: raise ValueError('Không phân tích được video')
    scenes=[float(x) for x in re.findall(r'pts_time:([0-9.]+)',result.stderr)]
    silences=[]
    if source.get('has_audio'):
        result=subprocess.run(['ffmpeg','-hide_banner','-nostats','-i',str(path),'-vn','-af',
            'silencedetect=noise=-35dB:d=0.3','-f','null','-'],capture_output=True,text=True,timeout=600)
        if result.returncode: raise ValueError('Không phân tích được audio')
        start=None
        for kind,raw in re.findall(r'silence_(start|end):\s*([0-9.]+)',result.stderr):
            if kind=='start': start=float(raw)
            elif start is not None: silences.append((start,float(raw)));start=None
        if start is not None: silences.append((start,duration))
    rate=min(.5,299/max(duration,1))
    result=subprocess.run(['ffmpeg','-v','error','-i',str(path),'-vf',f'fps={rate},scale=160:90',
        '-frames:v','300','-pix_fmt','gray','-f','rawvideo','-'],capture_output=True,timeout=600)
    if result.returncode: raise ValueError('Không phân tích được chuyển động')
    data=result.stdout;size=160*90;previous=None;cx=cy=.5;keys=[];motion=False
    window=crop_window(source,target)
    rx=window['width']/source['width'];ry=window['height']/source['height']
    for index,offset in enumerate(range(0,len(data)-size+1,size)):
        frame=data[offset:offset+size]
        if previous is not None:
            weights=[max(0,abs(a-b)-20) for a,b in zip(frame,previous)];total=sum(weights)
            if total>size*2:
                nx=sum((i%160+.5)*v for i,v in enumerate(weights))/(160*total)
                ny=sum((i//160+.5)*v for i,v in enumerate(weights))/(90*total)
                cx=.65*cx+.35*nx;cy=.65*cy+.35*ny;motion=True
        previous=frame
        x=max(0,min(1,(cx-rx/2)/(1-rx))) if rx<.999 else .5
        y=max(0,min(1,(cy-ry/2)/(1-ry))) if ry<.999 else .5
        keys.append(dict(time=round(index/rate,3),crop_x=round(x,4),crop_y=round(y,4),easing='ease-in-out'))
    if not keys: keys=[dict(time=0,crop_x=.5,crop_y=.5,easing='linear')]
    cuts,reasons=boundaries(duration,scenes,silences,sentences)
    return dict(cuts=cuts,reasons=reasons,keyframes=keys,method='motion center' if motion else 'center fallback',
                scene_precision_seconds=.2,has_sentence_boundaries=bool(sentences),face_detection=False)
