"""Compile target, fit, keyframes, transitions and audio into one filter graph."""
import math
from .animation import state, Expr
from .audio import filters as audio_filters
from .subtitle import write_ass
from .timeline import duration


def escape_path(path):
    return str(path).replace('\\','/').replace(':',r'\:').replace("'",r"\'")


def compile_composition(comp,refs,work,output):
    w,h,fps=(comp['canvas'][k] for k in ('width','height','fps'))
    end=duration(comp)
    if end<=0: raise ValueError('Timeline trống')
    background=comp.get('target',{}).get('background','#000000')
    cmd=['ffmpeg','-y','-filter_complex_threads','1','-f','lavfi','-i',f'color=c={background}:s={w}x{h}:r={fps}:d={end}',
         '-f','lavfi','-i',f'anullsrc=r=48000:cl=stereo:d={end}']
    graph=['[0:v]format=yuv420p[v0]'];current='v0';audio=['1:a'];index=2;layer=0
    for ti,track in enumerate(comp['tracks']):
        rows=sorted(track['clips'],key=lambda c:c['timeline_start'])
        if track['type'] in ('subtitle','text'):
            if rows:
                ass=write_ass(work/f'layer-{ti}.ass',rows,w,h)
                layer+=1;graph.append(f"[{current}]subtitles=filename='{escape_path(ass)}'[v{layer}]");current=f'v{layer}'
            continue
        for c in rows:
            tr=c.get('transition')
            if tr and tr['type']=='fade_black':
                layer+=1;graph.append(f"[{current}]drawbox=color=black:t=fill:enable='gte(t,{c['timeline_start']})*lt(t,{c['timeline_start']+tr['duration']})'[v{layer}]");current=f'v{layer}'
        for ci,c in enumerate(rows):
            outgoing=rows[ci+1].get('transition') if ci+1<len(rows) else None
            ref=refs[('audio',c['audio_id']) if c.get('audio_id') else ('asset',c['asset_id'])]
            if ref['kind']=='image': cmd+=['-loop','1','-framerate',str(fps)]
            cmd+=['-fflags','+genpts','-i',ref['path']]
            inp=index;index+=1
            if track['type']=='video':
                t,cr=c['transform'],c['crop'];bw=max(2,round(w*t['width']/2)*2);bh=max(2,round(h*t['height']/2)*2)
                anim=state(c,Expr('t'),outgoing);alpha=state(c,Expr('T'),outgoing)['opacity'];position=state(c,Expr(f'(t-{c["timeline_start"]})'),outgoing)
                graph.append(f"[{inp}:v]trim=start={c['source_start']}:end={c['source_end']},setpts=(PTS-STARTPTS)/{c['speed']},fps={fps},format=rgba,scale=iw*sar:ih,setsar=1[src{inp}]")
                src=f'src{inp}'
                def cover(): return f"scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh}:x='(iw-ow)*({anim['crop_x']})':y='(ih-oh)*({anim['crop_y']})'"
                if cr['mode']=='blur_background':
                    graph.append(f'[{src}]split[front{inp}][back{inp}]')
                    graph.append(f"[back{inp}]{cover()},gblur=sigma={cr['blur']},lutrgb=r='val*{1-cr['darken']}':g='val*{1-cr['darken']}':b='val*{1-cr['darken']}'[bg{inp}]")
                    graph.append(f'[front{inp}]scale={bw}:{bh}:force_original_aspect_ratio=decrease[fg{inp}]')
                    graph.append(f'[bg{inp}][fg{inp}]overlay=(W-w)/2:(H-h)/2:format=auto[fit{inp}]')
                elif cr['mode']=='fit' and cr.get('background_asset_id'):
                    bgref=refs[('asset',cr['background_asset_id'])];bgindex=index;index+=1
                    cmd+=['-loop','1','-framerate',str(fps),'-i',bgref['path']]
                    graph.append(f'[{bgindex}:v]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},format=rgba[bg{inp}]')
                    graph.append(f'[{src}]scale={bw}:{bh}:force_original_aspect_ratio=decrease[fg{inp}]')
                    graph.append(f'[bg{inp}][fg{inp}]overlay=(W-w)/2:(H-h)/2:shortest=1:format=auto[fit{inp}]')
                else:
                    fit=(f'scale={bw}:{bh}:force_original_aspect_ratio=decrease,pad={bw}:{bh}:(ow-iw)/2:(oh-ih)/2:color={cr["background"]}' if cr['mode']=='fit' else f'scale={bw}:{bh}' if cr['mode']=='stretch' else cover())
                    graph.append(f'[{src}]{fit}[fit{inp}]')
                chain='format=rgba'
                if cr['zoom']!=1:
                    chain+=f",scale={round(bw*cr['zoom']/2)*2}:{round(bh*cr['zoom']/2)*2},crop={bw}:{bh}:x='(iw-ow)*({anim['crop_x']})':y='(ih-oh)*({anim['crop_y']})'"
                chain+=f",scale=w='max(2,trunc({bw}*({anim['scale']})/2)*2)':h='max(2,trunc({bh}*({anim['scale']})/2)*2)':eval=frame"
                if t['rotation'] or any(k.get('rotation') for k in c.get('keyframes',[])):
                    diagonal=math.ceil(math.hypot(bw,bh)*t['scale']*1.35/2)*2
                    chain+=f",pad={diagonal}:{diagonal}:(ow-iw)/2:(oh-ih)/2:color=black@0:eval=frame,rotate=angle='({anim['rotation']})*PI/180':c=none"
                elif isinstance(anim['scale'],Expr):
                    # Alpha framesync requires fixed dimensions while the image zooms.
                    pw=math.ceil(bw*t['scale']*1.35/2)*2;ph=math.ceil(bh*t['scale']*1.35/2)*2
                    chain+=f",pad={pw}:{ph}:(ow-iw)/2:(oh-ih)/2:color=black@0:eval=frame"
                chain+=',setsar=1'
                graph.append(f'[fit{inp}]{chain}[motion{inp}]')
                if isinstance(alpha,Expr):
                    commands=work/f'opacity-{inp}.cmd'
                    with commands.open('w',encoding='utf-8') as stream:
                        previous=None
                        for frame in range(math.ceil(c['duration']*fps)):
                            value=round(state(c,frame/fps,outgoing)['opacity'],6)
                            if value!=previous:
                                stream.write(f'{max(0,frame/fps-1e-6):.9f} colorchannelmixer@alpha{inp} aa {value};\n')
                                previous=value
                    graph.append(f"[motion{inp}]sendcmd=f='{escape_path(commands)}',colorchannelmixer@alpha{inp}=aa={state(c,0,outgoing)['opacity']}[opacity{inp}]")
                else: graph.append(f'[motion{inp}]colorchannelmixer=aa={alpha}[opacity{inp}]')
                graph.append(f"[opacity{inp}]setpts=PTS+{c['timeline_start']}/TB[clip{inp}]")
                layer+=1
                graph.append(f"[{current}][clip{inp}]overlay=x='{w}*(.5+({position['x']}))-overlay_w/2':y='{h}*(.5+({position['y']}))-overlay_h/2':eof_action=pass:repeatlast=0:enable='gte(t,{c['timeline_start']})*lt(t,{c['timeline_start']+c['duration']})'[v{layer}]")
                current=f'v{layer}'
            if ref['has_audio'] and not c['muted']:
                graph.append(f'[{inp}:a]{audio_filters(c,outgoing)}[a{inp}]');audio.append(f'a{inp}')
    graph.append(''.join(f'[{a}]' for a in audio)+f'amix=inputs={len(audio)}:duration=first:normalize=0,alimiter=limit=0.95:latency=1[aout]')
    script=work/'composition.ffgraph';script.write_text(';\n'.join(graph),encoding='utf-8')
    return cmd+['-filter_complex_script',str(script),'-map',f'[{current}]','-map','[aout]','-t',str(end),'-r',str(fps),
        '-c:v','libx264','-preset','medium','-crf','22','-pix_fmt','yuv420p','-c:a','aac','-b:a','160k','-movflags','+faststart',str(output)]
def probe(path):
    import subprocess, json
    result=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration,size:stream=codec_name,width,height,sample_rate,channels','-of','json',str(path)],capture_output=True,text=True,timeout=30)
    if result.returncode:raise RuntimeError('ffprobe: '+result.stderr[-1000:])
    data=json.loads(result.stdout);duration=float(data.get('format',{}).get('duration') or 0)
    if duration<=0:raise RuntimeError('File media rỗng hoặc không có thời lượng')
    return data,duration
