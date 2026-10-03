"""ASS files keep user text out of FFmpeg filter expressions."""
import re


def timestamp(seconds):
    cs = round(seconds*100)
    return f'{cs//360000}:{cs//6000%60:02}:{cs//100%60:02}.{cs%100:02}'


def escape_text(text):
    # Literal braces/backslashes cannot introduce ASS override commands.
    return text.replace('\\','＼').replace('{','｛').replace('}','｝').replace('\r','').replace('\n',r'\N')


def write_ass(path, clips, width=1080, height=1920):
    header = f'''[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,52,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,0,5,50,50,50,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
    from .animation import state, visible_text
    import math
    header = header.replace('[Events]', 'Style: Box,DejaVu Sans,52,&H00FFFFFF,&H00FFFFFF,&H70000000,&H70000000,0,0,0,0,100,100,0,0,3,8,0,5,50,50,50,1\n[Events]')
    with path.open('w',encoding='utf-8') as output:
        output.write(header)
        for c in clips:
            animation=c.get('animation',{})
            animated=bool(c.get('keyframes') or animation.get('in') or animation.get('out') or animation.get('loop','static')!='static' or c.get('subtitle_animation','static')!='static')
            count=math.ceil(c['duration']*30) if animated else 1
            for index in range(count):
                local=index/30 if animated else 0
                finish=min(c['duration'],(index+1)/30) if animated else c['duration']
                t=state(c,local);style=c['style'];color=style['color'][1:]
                bgr=color[4:6]+color[2:4]+color[0:2]
                alpha=round((1-t['opacity'])*255);background_alpha=round(255-143*t['opacity'])
                tags=(f"\\an5\\pos({(t['x']+.5)*width:.2f},{(t['y']+.5)*height:.2f})"
                      f"\\fn{style['font']}\\fs{style['size']*t['scale']:.2f}\\frz{-t['rotation']}"
                      f"\\1c&H{bgr}&\\alpha&H{alpha:02X}&\\bord{style['stroke']}\\shad{style['shadow']}\\q0"
                      +(f'\\3a&H{background_alpha:02X}&\\4a&H{background_alpha:02X}&' if style['background'] else ''))
                text=escape_text(visible_text(c,local))
                if c.get('subtitle_animation')=='word_highlight' and c.get('words'):
                    highlight=style['highlight'][1:];hbgr=highlight[4:6]+highlight[2:4]+highlight[0:2]
                    at=local+c.get('animation_offset',0)
                    text=' '.join(('{'+'\\1c&H'+(hbgr if word['start']<=at<word['end'] else bgr)+'&}'+escape_text(word['text'])) for word in c['words'])
                margin=max(0,round(width*(1-c['transform']['width']*t['scale'])/2))
                name='Box' if style['background'] else 'Default'
                output.write(f"Dialogue: 0,{timestamp(c['timeline_start']+local)},{timestamp(c['timeline_start']+finish)},{name},,{margin},{margin},0,,{{{tags}}}{text}\n")
    return path
