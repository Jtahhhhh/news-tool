/* Keep numerical evaluation in parity with services/video/animation.py. */
(function(root,factory){const api=factory();if(typeof module==='object')module.exports=api;else root.EditorMotion=api;})(globalThis,()=>{
 const clamp=x=>Math.min(1,Math.max(0,x));
 const ease=(x,name)=>{const p=clamp(x);return name==='ease-in'?p*p:name==='ease-out'?1-(1-p)*(1-p):name==='ease-in-out'?p*p*(3-2*p):p;};
 function interpolate(keys,field,time,fallback){let p=keys.filter(k=>k[field]!=null);if(!p.length)return fallback;if(p[0].time>0)p=[{time:0,[field]:fallback},...p];for(let i=1;i<p.length;i++)if(time<p[i].time){const a=p[i-1],b=p[i],v=ease((time-a.time)/(b.time-a.time),b.easing||'linear');return a[field]+(b[field]-a[field])*v;}return p.at(-1)[field];}
 function state(c,local,outgoing=null){
  const t=c.transform,keys=c.keyframes||[],time=local+(c.animation_offset||0),total=c.animation_duration||c.duration;
  const s={};for(const [key,value] of Object.entries({x:t.x,y:t.y,scale:1,rotation:0,opacity:1,volume:1,crop_x:c.crop.x,crop_y:c.crop.y}))s[key]=interpolate(keys,key,time,value);
  const motion=c.animation?.loop||'static',p=clamp(time/total);
  if(motion==='zoom_in')s.scale*=1+.15*p;if(motion==='zoom_out')s.scale*=1.15-.15*p;
  for(const [name,axis,sign] of [['pan_left','x',-1],['pan_right','x',1],['pan_up','y',-1],['pan_down','y',1]])if(motion===name)s[axis]+=sign*.08*p;
  let a=c.animation||{};if(['fade','pop'].includes(c.subtitle_animation))a={...a,in:{type:c.subtitle_animation,duration:Math.min(.3,total/2),easing:'ease-out'}};
  for(const direction of ['in','out']){const effect=a[direction];if(!effect)continue;const p=ease((direction==='in'?time:total-time)/effect.duration,effect.easing||'linear');if(effect.type!=='typewriter')s.opacity*=p;if(['zoom','pop'].includes(effect.type))s.scale*=.85+.15*p+(effect.type==='pop'?.12*Math.sin(p*Math.PI):0);for(const [name,axis,sign] of [['slide_left','x',-1],['slide_right','x',1],['slide_up','y',-1],['slide_down','y',1]])if(effect.type===name)s[axis]+=sign*.15*(1-p);}
  s.scale=Math.min(1.35,s.scale)*t.scale;s.rotation+=t.rotation;s.opacity*=t.opacity;s.volume*=c.volume;
  const tr=c.transition;if(tr){const p=clamp(local/tr.duration);if(['crossfade','zoom'].includes(tr.type))s.opacity*=p;if(tr.type==='fade_black')s.opacity*=Math.max(0,2*p-1);if(['slide','push'].includes(tr.type))s.x+=1-p;if(tr.type==='zoom')s.scale*=.85+.15*p;s.volume*=p;}
  if(outgoing){const p=clamp((local-c.duration+outgoing.duration)/outgoing.duration);if(outgoing.type==='push')s.x-=p;if(outgoing.type==='fade_black')s.opacity*=Math.max(0,1-2*p);s.volume*=1-p;}
  return s;
 }
 function text(c,local){const e=c.animation?.in;return e?.type==='typewriter'?Array.from(c.text).slice(0,Math.ceil(Array.from(c.text).length*clamp((local+(c.animation_offset||0))/e.duration))).join(''):c.text;}
 function audit(source,target){const sr=source.aspect_ratio||source.width/Math.max(1,source.height),tr=target.width/target.height,mismatch=!!sr&&Math.abs(sr-tr)/tr>.02;return {requires_fit:mismatch,recommended_mode:mismatch?'blur_background':'fill',source_ratio:sr,target_ratio:tr};}
 function transition(track,clip,type,duration=.4){
  const rows=[...track.clips].sort((a,b)=>a.timeline_start-b.timeline_start),i=rows.indexOf(clip),prev=rows[i-1];
  if(!prev){if(type!=='none')throw Error('Transition cần clip đứng trước trên cùng track.');clip.transition=null;return;}
  const existing=clip.transition?.duration||0,gap=clip.timeline_start-(prev.timeline_start+prev.duration)+existing;
  if(Math.abs(gap)>.002&&type!=='none')throw Error('Đặt hai clip liền kề trước khi thêm transition.');
  const d=type==='none'?0:Math.min(1,Math.max(.03,duration));if(d>=Math.min(prev.duration,clip.duration))throw Error('Transition phải ngắn hơn cả hai clip.');
  const delta=existing-d;for(let j=i;j<rows.length;j++)rows[j].timeline_start+=delta;
  clip.transition=type==='none'?null:{type,duration:d,from_clip_id:prev.id};
  for(let j=2;j<rows.length;j++)if(rows[j-2].timeline_start+rows[j-2].duration>rows[j].timeline_start+.001)throw Error('Transition tạo chồng lấn ba clip.');
 }
 function preset(project,media,name){project.animation_preset=name;let i=0;const motions=['zoom_in','pan_right','zoom_out','pan_left'];for(const t of project.tracks)for(const c of t.clips){c.animation={in:null,out:null,loop:'static'};c.subtitle_animation='static';if(name==='NONE')continue;if(t.type==='video'&&media(c)?.kind==='image')c.animation.loop=motions[i++%4];if(t.type==='text')c.animation.in={type:['MODERN','DRAMA'].includes(name)?'pop':'fade',duration:Math.min(.3,c.duration/2),easing:'ease-out'};if(t.type==='subtitle')c.subtitle_animation=['STORY','DRAMA'].includes(name)?'pop':'fade';}}
 return {clamp,ease,interpolate,state,text,audit,transition,preset};
});
