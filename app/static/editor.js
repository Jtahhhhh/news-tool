/* Browser-only preview. FFmpeg is invoked only by Export. */
(()=>{'use strict';
const $=id=>document.getElementById(id), M=window.EditorModel, N=window.EditorMotion, data=JSON.parse($('editor-data').textContent);
const history=new M.History(data.composition);let project=M.clone(history.current),selected=null,time=0,playing=false,px=45,scale=.2,cropMode=false,dirty=false,busy=false,revision=data.revision,kind='all',media=data.media;
let originTime=0,originClock=0,drag=null,exportKey=crypto.randomUUID();const nodes=new Map();let layerKey='',keyIndex=0,wordIndex=0;
const csrf=document.querySelector('meta[name="csrf-token"]').content;
function message(text,error=false){$('editor-message').textContent=text;$('editor-message').classList.toggle('error',error);}
function el(tag,cls,text){const node=document.createElement(tag);if(cls)node.className=cls;if(text!==undefined)node.textContent=text;return node;}
function chosen(){return M.find(project,selected);}
function mediaFor(c){return media.find(m=>m.id===(c.audio_id||c.asset_id)&&(c.audio_id?m.ref_type==='tts':m.ref_type!=='tts'));}
function mark(){dirty=true;exportKey=crypto.randomUUID();$('save-state').textContent='Chưa lưu';}
function commit(){if(history.commit(project))mark();draw();}
function mutate(fn){if(busy)return;pause();const previous=M.clone(project);try{fn();commit();}catch(e){project=previous;draw();message(e.message,true);}}
function format(t){return `${String(Math.floor(t/60)).padStart(2,'0')}:${(t%60).toFixed(2).padStart(5,'0')}`;}
async function post(url,body){const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json','x-csrf-token':csrf},body:JSON.stringify(body)});let b;try{b=await r.json();}catch{throw Error('Không đọc được phản hồi máy chủ.');}if(!r.ok)throw Error(typeof b.detail==='string'?b.detail:JSON.stringify(b.detail));return b;}
function setBusy(value){busy=value;document.body.classList.toggle('busy',value);$('editor-workspace').inert=value;$('save').disabled=value;$('export').disabled=value;}
async function persist(){const snapshot=M.clone(project);const result=await post(`/videos/editor/${data.script_id}/draft`,{job_id:data.job_id,revision,composition:snapshot});revision=result.revision;dirty=false;$('save-state').textContent='Đã lưu';}
$('save').onclick=async()=>{pause();setBusy(true);try{await persist();message('Đã lưu nháp trên máy chủ.');}catch(e){message(e.message,true);}finally{setBusy(false);}};
$('export').onclick=async()=>{pause();setBusy(true);try{if(!M.duration(project))throw Error('Thêm ít nhất một clip trước khi export.');if(dirty)await persist();const job=await post(`/videos/editor/${data.script_id}/export`,{job_id:data.job_id,revision,composition:project,parent_version_id:data.parent_version_id,idempotency_key:exportKey});message(`Đã xếp hàng export #${job.id}.`);location.assign('/videos');}catch(e){message(e.message,true);setBusy(false);}};
window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';}});

function renderLibrary(){
 const list=$('media-list');list.replaceChildren();
 for(const m of media.filter(m=>kind==='all'||m.kind===kind)){
  const card=el('div','media-card');card.draggable=false;card.tabIndex=0;card.setAttribute('role','button');card.title='Kéo vào track, hoặc nhấp đúp để thêm tại playhead';
  card.append(el('span','media-icon',m.kind==='audio'?'♫':m.kind==='image'?'▧':'▷'));
  const info=el('div');info.append(el('div','media-name',m.name),el('div','media-meta',`${m.kind.toUpperCase()} · ${m.kind==='image'?`${m.width} × ${m.height}`:format(m.duration)}${m.use_proxy?' · PROXY':''}${m.test_only?' · TEST':''}`));card.append(info);
  card.onpointerdown=e=>libraryDrag(e,m);
  card.ondblclick=()=>insertMedia(m,null,time);card.onkeydown=e=>{if(e.key==='Enter')insertMedia(m,null,time);};list.append(card);
 }
 if(!list.children.length)list.append(el('p','empty-state','Chưa có media trong mục này.'));
}
document.querySelectorAll('[data-kind]').forEach(b=>b.onclick=()=>{kind=b.dataset.kind;document.querySelectorAll('[data-kind]').forEach(x=>x.classList.toggle('active',x===b));renderLibrary();});
$('import-media').onchange=async e=>{const file=e.target.files[0];if(!file)return;setBusy(true);message(`Đang tải và chuẩn hóa ${file.name}… Video lớn có thể cần vài phút.`);try{const form=new FormData();form.append('file',file);if(data.test_only)form.append('test_only','true');const r=await fetch('/assets',{method:'POST',headers:{Accept:'application/json','x-csrf-token':csrf},body:form});const result=await r.json();if(!r.ok)throw Error(result.detail||'Tải lên thất bại');media=media.filter(m=>m.id!==result.id||m.ref_type==='tts');media.push(result);renderLibrary();message('Đã import. Kéo media vào track Video hoặc audio.');}catch(x){message(x.message,true);}finally{setBusy(false);e.target.value='';}};
function libraryDrag(e,m){
 if(e.button!==0||busy)return;
 const x=e.clientX,y=e.clientY;let ghost=null;
 const move=ev=>{if(!ghost&&Math.hypot(ev.clientX-x,ev.clientY-y)>5){ghost=el('div','media-drag-ghost',m.name);document.body.append(ghost);}if(ghost){ghost.style.left=(ev.clientX+14)+'px';ghost.style.top=(ev.clientY+14)+'px';ev.preventDefault();}};
 const finish=ev=>{window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',finish);window.removeEventListener('pointercancel',cancel);if(!ghost)return;ghost.remove();const row=document.elementFromPoint(ev.clientX,ev.clientY)?.closest('.track-row');if(row)insertMedia(m,row.dataset.track,snap((ev.clientX-row.getBoundingClientRect().left)/px));else message('Thả media vào một track phù hợp trên timeline.');};
 const cancel=()=>{ghost?.remove();ghost=null;window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',finish);window.removeEventListener('pointercancel',cancel);};
 window.addEventListener('pointermove',move,{passive:false});window.addEventListener('pointerup',finish,{once:true});window.addEventListener('pointercancel',cancel,{once:true});
}
function insertMedia(m,trackId,at){mutate(()=>{
 const track=project.tracks.find(t=>trackId?t.id===trackId:t.type===(m.kind==='audio'?'audio':'video')&&(m.kind!=='audio'||t.id==='voice'));
 if(!track)throw Error('Không tìm thấy track phù hợp.');
 if(!['video','audio'].includes(track.type)||(track.type==='video'&&m.kind==='audio')||(track.type==='audio'&&!m.has_audio))throw Error('Media không tương thích với track này.');
 const d=m.kind==='image'?5:m.duration;
 const c=M.defaults({name:m.name,timeline_start:Math.max(0,at),duration:d,source_end:d,...(m.ref_type==='tts'?{audio_id:m.id}:{asset_id:m.id}),transform:track.id==='video-overlay'?{width:.45,height:.35}:undefined});
 if(!M.fits(track,c))throw Error('Vị trí này đã có clip. Chọn khoảng trống hoặc track khác.');
 if(track.type==='video')c.crop.mode=N.audit(m,project.target).recommended_mode;
 if(m.kind==='image'&&project.animation_preset!=='NONE')c.animation.loop='zoom_in';
 track.clips.push(c);selected=c.id;message(track.type==='video'&&N.audit(m,project.target).requires_fit?'Tỷ lệ nguồn khác target: đã áp dụng Blur Background. Đổi Fit/Fill/Smart Crop trong Inspector.':'Đã thêm clip. Kéo hai đầu để trim; S để split.');
});}
function addText(type){mutate(()=>{const track=project.tracks.find(t=>t.type===type);const c=M.defaults({text:type==='text'?'Tiêu đề mới':'Phụ đề mới',timeline_start:time,transform:{width:.9,height:.2,y:type==='text'?-.32:.32}});if(!M.fits(track,c))throw Error('Track đã có clip tại đây; di chuyển playhead vào khoảng trống.');track.clips.push(c);selected=c.id;});}
$('add-text').onclick=()=>addText('text');$('add-subtitle').onclick=()=>addText('subtitle');

function position(node,c,animated=null){const t={...c.transform,...animated},w=project.canvas.width,h=project.canvas.height;Object.assign(node.style,{left:`${(t.x+.5)*w}px`,top:`${(t.y+.5)*h}px`,width:`${t.width*w}px`,height:`${t.height*h}px`,transform:`translate(-50%,-50%) rotate(${t.rotation}deg) scale(${t.scale})`});}
function nearClips(){return project.tracks.flatMap(track=>[...track.clips].sort((a,b)=>a.timeline_start-b.timeline_start).map((clip,i,rows)=>({track,clip,outgoing:rows[i+1]?.transition||null}))).filter(({clip:c})=>time>=c.timeline_start-2&&time<M.end(c)+2);}
function rebuildLayers(){
 for(const n of nodes.values())for(const item of [n.media,...(n.extras||[])])if(item){item.pause();item.removeAttribute('src');item.load();}
 nodes.clear();$('layers').replaceChildren();const nearby=nearClips();layerKey=nearby.map(n=>n.clip.id).join('|');
 for(const {track,clip:c,outgoing} of nearby){
  let layer,element,textSpan,fitLayer,wordSpans=[];const extras=[];
  if(track.type==='text'||track.type==='subtitle'){
   layer=el('div','canvas-layer');const content=el('div','text-content');textSpan=el('span','',c.text);content.append(textSpan);layer.append(content);
   Object.assign(content.style,{fontFamily:c.style.font,fontSize:`${c.style.size}px`,color:c.style.color,paintOrder:'stroke fill',webkitTextStroke:`${c.style.stroke}px #000`,textShadow:c.style.shadow?`${c.style.shadow}px ${c.style.shadow}px #000`:'none'});
   if(c.style.background)Object.assign(textSpan.style,{background:'#0009',padding:'8px'});
   if(c.subtitle_animation==='word_highlight'&&c.words.length){textSpan.replaceChildren();for(const word of c.words){const span=el('span','',word.text+' ');textSpan.append(span);wordSpans.push({span,word});}}
  }else{
   const m=mediaFor(c);if(!m){message('Một media đang thiếu. Hãy import lại hoặc xóa clip.',true);continue;}
   element=document.createElement(track.type==='audio'?'audio':m.kind==='image'?'img':'video');element.src=m.url;
   if(element instanceof HTMLMediaElement){element.preload='metadata';element.playsInline=true;element.onerror=()=>message(`Không phát được ${m.name}. Hãy kiểm tra proxy hoặc import lại.`,true);}
   if(track.type==='video'){
    layer=el('div','canvas-layer');const content=el('div','visual-content'),foreground=el('div','foreground');foreground.append(element);
    if(c.crop.mode==='fit')content.style.background=c.crop.background;
    if(c.crop.mode==='fit'&&c.crop.background_asset_id){const bg=el('img','image-background');bg.src=media.find(m=>m.id===c.crop.background_asset_id&&m.ref_type==='asset')?.url||'';content.append(bg);}
    if(c.crop.mode==='blur_background'){const bg=element.cloneNode();bg.className='blur-copy';bg.style.filter=`blur(${c.crop.blur}px) brightness(${1-c.crop.darken})`;bg.style.transform='none';if(bg instanceof HTMLMediaElement){bg.muted=true;extras.push(bg);}content.append(bg);}
    content.append(foreground);fitLayer=el('div','fit-content');fitLayer.append(...content.childNodes);content.append(fitLayer);fitLayer.style.background=content.style.background;fitLayer.style.transform=`scale(${c.crop.zoom})`;layer.append(content);
    Object.assign(element.style,{objectFit:['fit','blur_background'].includes(c.crop.mode)?'contain':c.crop.mode==='stretch'?'fill':'cover',objectPosition:`${c.crop.x*100}% ${c.crop.y*100}%`});
   }
  }
  if(layer){layer.dataset.clip=c.id;position(layer,c);layer.style.opacity=c.transform.opacity;layer.onpointerdown=e=>canvasStart(e,c.id);$('layers').append(layer);}
  nodes.set(c.id,{layer,media:element instanceof HTMLMediaElement?element:null,visual:element,clip:c,outgoing,extras,textSpan,wordSpans,fitLayer});
 }
 sync(true);
}
function sync(force=false){
 if(nearClips().map(n=>n.clip.id).join('|')!==layerKey){rebuildLayers();return;}
 let black=false;
 for(const n of nodes.values()){
  const {layer,clip:c,outgoing}=n,active=time>=c.timeline_start&&time<M.end(c),local=Math.max(0,time-c.timeline_start),st=N.state(c,local,outgoing);
  if(layer){layer.hidden=!active;position(layer,c,st);layer.style.opacity=st.opacity;}
  if(c.transition?.type==='fade_black'&&active&&local<c.transition.duration)black=true;
  if(n.visual&&layer){n.visual.style.objectPosition=`${st.crop_x*100}% ${st.crop_y*100}%`;if(n.fitLayer)n.fitLayer.style.transformOrigin=n.visual.style.objectPosition;for(const bg of n.fitLayer?.querySelectorAll('.blur-copy')||[])bg.style.objectPosition=n.visual.style.objectPosition;}
  if(n.textSpan){if(n.wordSpans.length){for(const {word,span} of n.wordSpans)span.style.color=local+c.animation_offset>=word.start&&local+c.animation_offset<word.end?c.style.highlight:c.style.color;}else{const value=N.text(c,local);if(n.textSpan.textContent!==value)n.textSpan.textContent=value;}}
  for(const element of [n.media,...n.extras]){
   if(!element)continue;if(!active){if(!element.paused)element.pause();continue;}
   const target=c.source_start+local*c.speed;element.playbackRate=c.speed;element.muted=c.muted||element!==n.media;
   const fade=Math.min(1,c.fade_in?local/c.fade_in:1,c.fade_out?(c.duration-local)/c.fade_out:1);element.volume=Math.max(0,Math.min(1,st.volume*fade));
   if(force||Math.abs(element.currentTime-target)>.18){try{element.currentTime=target;}catch{}}
   if(playing&&element.paused&&!element.dataset.starting){element.dataset.starting='1';element.play().catch(()=>{if(playing){pause();message('Trình duyệt chưa phát được media. Nhấn Play để thử lại.',true);}}).finally(()=>delete element.dataset.starting);}
   if(!playing&&!element.paused)element.pause();
  }
 }
 $('stage').style.background=black?'#000':project.target.background;
 $('timecode').textContent=format(time);$('total-time').textContent='/ '+format(M.duration(project));$('playhead').style.left=`${116+time*px}px`;
 const item=chosen();$('selection-box').hidden=!item||item.track.type==='audio'||time<item.clip.timeline_start||time>=M.end(item.clip);
 if(item)position($('selection-box'),item.clip,N.state(item.clip,Math.max(0,time-item.clip.timeline_start),nodes.get(item.clip.id)?.outgoing));
}
function selectionBox(){const item=chosen();if(item&&item.track.type!=='audio')position($('selection-box'),item.clip);sync();}
function pause(){playing=false;$('play').textContent='▶';for(const n of nodes.values())for(const m of [n.media,...n.extras])m?.pause();}
function seek(value){time=Math.max(0,Math.min(M.duration(project),value));if(playing){originTime=time;originClock=performance.now();}sync(true);}
function play(){if(playing)return pause();if(!M.duration(project))return;if(time>=M.duration(project))time=0;playing=true;originTime=time;originClock=performance.now();$('play').textContent='Ⅱ';sync(true);}
function tick(now){if(playing){time=originTime+(now-originClock)/1000;if(time>=M.duration(project)){time=M.duration(project);pause();}sync();}requestAnimationFrame(tick);}
$('play').onclick=play;
new ResizeObserver(()=>{const rect=$('stage-area').getBoundingClientRect();scale=Math.min((rect.width-70)/project.canvas.width,(rect.height-50)/project.canvas.height);$('stage').style.transform=`scale(${Math.max(.03,scale)})`;}).observe($('stage-area'));

function renderTimeline(){
 const max=Math.max(30,M.duration(project)+10),width=Math.max($('timeline-scroll').clientWidth,116+max*px);$('timeline').style.width=width+'px';$('ruler').replaceChildren();
 const step=px<15?10:px<40?5:px<100?2:1;
 for(let s=0;s<=max;s+=step){const tick=el('span','tick',format(s));tick.style.left=s*px+'px';$('ruler').append(tick);}
 $('track-rows').replaceChildren();
 for(const track of [...project.tracks].reverse()){
  const row=el('div','track-row');row.dataset.track=track.id;row.append(el('div','track-label',track.name||track.id));
  row.ondragover=e=>{e.preventDefault();e.dataTransfer.dropEffect='copy';};row.ondrop=e=>{e.preventDefault();try{const ref=JSON.parse(e.dataTransfer.getData('application/x-editor-media'));const m=media.find(x=>x.id===ref.id&&x.kind===ref.kind);if(m)insertMedia(m,track.id,snap((e.clientX-row.getBoundingClientRect().left)/px));}catch{}};
  row.onpointerdown=e=>{if(e.target===row){pause();seek((e.clientX-row.getBoundingClientRect().left)/px);}};
  for(const c of track.clips){const clip=el('div',`timeline-clip ${track.type}${c.id===selected?' selected':''}`);clip.dataset.clip=c.id;clip.style.left=c.timeline_start*px+'px';clip.style.width=c.duration*px+'px';clip.tabIndex=0;clip.setAttribute('aria-label',`${c.name||c.text||track.name}, ${format(c.timeline_start)}, ${format(c.duration)}`);clip.title=`${c.name||c.text||track.name}\n${format(c.timeline_start)} → ${format(M.end(c))}`;clip.append(el('span','clip-caption',c.name||c.text||mediaFor(c)?.name||track.name));for(const side of ['left','right']){const h=el('span',`trim-handle ${side}`);h.dataset.trim=side;clip.append(h);}clip.onpointerdown=e=>timelineStart(e,track,c);clip.onkeydown=e=>{if(e.key==='Enter'){selected=c.id;pause();seek(c.timeline_start);draw();}};row.append(clip);}
  if(track.type==='video'){const sorted=[...track.clips].sort((a,b)=>a.timeline_start-b.timeline_start);for(let i=1;i<sorted.length;i++){const c=sorted[i];if(c.transition||Math.abs(c.timeline_start-M.end(sorted[i-1]))<.002){const b=el('button','transition-button',c.transition?'◆':'+');b.style.left=(c.timeline_start+(c.transition?.duration||0)/2)*px-9+'px';b.title='Transition giữa hai clip';b.onclick=()=>{pause();selected=c.id;renderInspector();$('inspector').querySelector('.frame-picker')?.scrollIntoView();};row.append(b);}}}
  $('track-rows').append(row);
 }
 sync();
}
$('ruler').onpointerdown=e=>{pause();const update=x=>seek((x-$('ruler').getBoundingClientRect().left)/px);update(e.clientX);const move=ev=>update(ev.clientX);const up=()=>{window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',up);};window.addEventListener('pointermove',move);window.addEventListener('pointerup',up,{once:true});};
function snap(value,id){return $('snap').checked?M.snap(project,value,id,8/px,time):Math.max(0,value);}
function timelineStart(e,track,c){
 if(e.button!==0||busy)return;e.preventDefault();e.stopPropagation();pause();selected=c.id;const original=M.clone(c),before=M.clone(project),x=e.clientX,side=e.target.dataset.trim;
 renderInspector();selectionBox();document.querySelectorAll('.timeline-clip').forEach(n=>n.classList.toggle('selected',n.dataset.clip===selected));
 let changed=false;
 const move=ev=>{
  const delta=(ev.clientX-x)/px;let candidate;
  if(side){candidate=M.trim(original,side,snap((side==='left'?original.timeline_start:M.end(original))+delta,c.id),mediaFor(c)?.kind==='image'?Infinity:(mediaFor(c)?.duration??Infinity));}
  else {candidate=M.clone(original);candidate.timeline_start=snap(original.timeline_start+delta,c.id);if($('snap').checked){const snappedEnd=snap(candidate.timeline_start+candidate.duration,c.id);if(Math.abs(snappedEnd-(candidate.timeline_start+candidate.duration))>0)candidate.timeline_start=Math.max(0,snappedEnd-candidate.duration);}}
  const target=side?track:project.tracks.find(t=>t.id===document.elementFromPoint(ev.clientX,ev.clientY)?.closest('.track-row')?.dataset.track)||track;
  if(target.type!==track.type||!M.fits(target,candidate)||M.end(candidate)>7200)return;
  for(const t of project.tracks)t.clips=t.clips.filter(v=>v.id!==c.id);target.clips.push(candidate);changed=true;renderTimeline();
 };
 const up=()=>{window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',up);window.removeEventListener('pointercancel',cancel);if(changed)commit();else{seek(c.timeline_start);draw();}};
 const cancel=()=>{project=before;changed=false;up();};window.addEventListener('pointermove',move);window.addEventListener('pointerup',up,{once:true});window.addEventListener('pointercancel',cancel,{once:true});
}

function canvasStart(e,id,handle){
 if(e.button!==0||busy)return;e.preventDefault();e.stopPropagation();pause();selected=id;const item=chosen();if(!item)return;renderInspector();selectionBox();
 const original=M.clone(item.clip),startX=e.clientX,startY=e.clientY,rect=$('stage').getBoundingClientRect();
 const center={x:rect.left+(original.transform.x+.5)*rect.width,y:rect.top+(original.transform.y+.5)*rect.height};
 const initialAngle=Math.atan2(e.clientY-center.y,e.clientX-center.x),before=M.clone(project);
 const move=ev=>{
  const c=chosen().clip,t=c.transform,o=original.transform,dx=(ev.clientX-startX)/scale,dy=(ev.clientY-startY)/scale;
  if(handle==='rotate'){t.rotation=Math.max(-360,Math.min(360,o.rotation+(Math.atan2(ev.clientY-center.y,ev.clientX-center.x)-initialAngle)*180/Math.PI));}
  else if(handle){const angle=o.rotation*Math.PI/180,lx=dx*Math.cos(angle)+dy*Math.sin(angle),ly=-dx*Math.sin(angle)+dy*Math.cos(angle),sx=handle.includes('e')?1:-1,sy=handle.includes('s')?1:-1;
   t.width=Math.max(.01,Math.min(2,4/o.scale,o.width+sx*lx/(project.canvas.width*o.scale)));t.height=Math.max(.01,Math.min(2,4/o.scale,o.height+sy*ly/(project.canvas.height*o.scale)));
   const cx=(t.width-o.width)*project.canvas.width*o.scale*sx/2,cy=(t.height-o.height)*project.canvas.height*o.scale*sy/2;
   t.x=Math.max(-2,Math.min(2,o.x+(cx*Math.cos(angle)-cy*Math.sin(angle))/project.canvas.width));t.y=Math.max(-2,Math.min(2,o.y+(cx*Math.sin(angle)+cy*Math.cos(angle))/project.canvas.height));
  }else if(cropMode&&item.track.type==='video'){
   c.crop.manual=true;for(const k of c.keyframes){delete k.crop_x;delete k.crop_y;}c.crop.x=Math.max(0,Math.min(1,original.crop.x-dx/(project.canvas.width*o.width)));c.crop.y=Math.max(0,Math.min(1,original.crop.y-dy/(project.canvas.height*o.height)));
   const visual=nodes.get(c.id)?.layer?.querySelector('video,img');if(visual){visual.style.objectPosition=`${c.crop.x*100}% ${c.crop.y*100}%`;visual.style.transformOrigin=visual.style.objectPosition;}
  }else{t.x=Math.max(-2,Math.min(2,o.x+dx/project.canvas.width));t.y=Math.max(-2,Math.min(2,o.y+dy/project.canvas.height));}
  const layer=nodes.get(c.id)?.layer;if(layer)position(layer,c);position($('selection-box'),c);
 };
 const up=()=>{window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',up);window.removeEventListener('pointercancel',cancel);commit();};
 const cancel=()=>{project=before;up();};window.addEventListener('pointermove',move);window.addEventListener('pointerup',up,{once:true});window.addEventListener('pointercancel',cancel,{once:true});
}
$('selection-box').querySelectorAll('[data-handle]').forEach(h=>h.onpointerdown=e=>canvasStart(e,selected,h.dataset.handle));
$('crop-mode').onclick=()=>{cropMode=!cropMode;$('crop-mode').classList.toggle('active',cropMode);message(cropMode?'Crop: kéo hình trên canvas; chỉnh Crop zoom trong Inspector.':'Kéo để đổi vị trí; kéo góc để resize, nút ↻ để xoay.');};

function updateTarget(){
 const t=project.target;$('target-profile').value=`${t.width}x${t.height}`;$('target-readout').textContent=`${t.width} × ${t.height} · ${t.fps} FPS`;
 $('stage').style.width=t.width+'px';$('stage').style.height=t.height+'px';const r=$('stage-area').getBoundingClientRect();scale=Math.max(.03,Math.min((r.width-70)/t.width,(r.height-50)/t.height));$('stage').style.transform=`scale(${scale})`;
 $('safe-area').checked=t.safe_area;$('safe-preset').value=t.safe_area_preset;$('safe-area-overlay').hidden=!t.safe_area||t.safe_area_preset==='none';$('safe-area-overlay').dataset.preset=t.safe_area_preset;
 $('project-bg').value=t.background;$('animation-preset').value=project.animation_preset;
}
$('target-profile').onchange=e=>mutate(()=>{const [width,height]=e.target.value.split('x').map(Number);Object.assign(project.target,{width,height});for(const m of media)delete m.analysis;Object.assign(project.canvas,{width,height});for(const tr of project.tracks)if(tr.type==='video')for(const c of tr.clips)if(!c.crop.manual){const m=mediaFor(c);if(m)c.crop.mode=N.audit(m,project.target).recommended_mode;}message('Đã đổi target. Crop thủ công và keyframe được giữ lại.');});
$('safe-area').onchange=e=>mutate(()=>project.target.safe_area=e.target.checked);$('safe-preset').onchange=e=>mutate(()=>project.target.safe_area_preset=e.target.value);$('project-bg').onchange=e=>mutate(()=>project.target.background=e.target.value);
$('apply-preset').onclick=()=>mutate(()=>{const name=$('animation-preset').value;N.preset(project,mediaFor,name);if(name==='STORY')for(const track of project.tracks.filter(t=>t.type==='video')){const rows=[...track.clips].sort((a,b)=>a.timeline_start-b.timeline_start);for(let i=1;i<rows.length;i++)if(!rows[i].transition&&Math.abs(rows[i].timeline_start-M.end(rows[i-1]))<.002&&Math.min(rows[i-1].duration,rows[i].duration)>.5)N.transition(track,rows[i],'crossfade',.2);}message('Đã áp dụng preset; Undo để khôi phục animation trước đó.');});
function action(container,label,handler){const b=el('button','',label);b.type='button';b.onclick=handler;container.append(b);return b;}
function picker(container,label,values,current,handler){const row=el('label','',label),select=el('select');for(const [value,name] of values)select.append(new Option(name,value));select.value=current;select.onchange=()=>handler(select.value);row.append(select);container.append(row);return select;}
function renderAdvanced(item,c,container,field,section){
 const mediaItem=mediaFor(c);
 if(item.track.type==='video'){
  section('SOURCE → TARGET');const a=mediaItem?N.audit(mediaItem,project.target):null;
  if(mediaItem){container.append(el('p','source-info',`${mediaItem.width} × ${mediaItem.height} · ${mediaItem.video_codec||mediaItem.kind} · ${Number(mediaItem.fps||0).toFixed(2)} FPS${mediaItem.use_proxy?' · Preview proxy':''}`));if(a.requires_fit)container.append(el('p','audit-note',`Tỷ lệ khác target. Gợi ý: Fill / Smart Crop / Blur Background.`));}
  const buttons=el('div','action-row');container.append(buttons);
  action(buttons,'Auto Fit',()=>mutate(()=>{if(c.crop.manual){message('Đang giữ crop thủ công. Chọn Reset để bỏ crop cũ.');return;}if(a)c.crop.mode=a.recommended_mode;}));
  action(buttons,'Reset',()=>mutate(()=>{c.crop={...M.defaults().crop,mode:a?.recommended_mode||'fill'};c.transform=M.defaults().transform;c.keyframes=c.keyframes.map(k=>{const r={...k};delete r.crop_x;delete r.crop_y;return r;});}));
  field('Màu nền fit','crop.background',{type:'color'});field('Blur px','crop.blur',{min:20,max:40,step:1});field('Darken','crop.darken',{min:0,max:.8});
  picker(container,'Ảnh nền',[['','Không'],...media.filter(m=>m.kind==='image').map(m=>[String(m.id),m.name])],String(c.crop.background_asset_id||''),value=>mutate(()=>c.crop.background_asset_id=value?Number(value):null));
  if(mediaItem?.kind==='video')action(container,'Phân tích crop / scene / silence',()=>analyzeClip(c));
  if(mediaItem?.analysis?.cuts){container.append(el('p','hint',`Phát hiện ${Math.max(0,mediaItem.analysis.cuts.length-2)} mốc cắt; crop: ${mediaItem.analysis.method}.`));action(container,'Áp dụng Smart Crop',()=>applySmart(c,mediaItem.analysis));action(container,'Chia theo mốc phát hiện',()=>applyCuts(c,mediaItem.analysis));}
  section('TRANSITION VỚI CLIP TRƯỚC');picker(container,'Transition',['none','crossfade','fade_black','slide','push','zoom'].map(v=>[v,v]),c.transition?.type||'none',value=>mutate(()=>N.transition(item.track,c,value,c.transition?.duration||.4)));
  if(c.transition){const label=el('label','','Duration (s)'),input=el('input');input.type='number';input.min=.03;input.max=1;input.step=.01;input.value=c.transition.duration;input.onchange=()=>mutate(()=>N.transition(item.track,c,c.transition.type,Number(input.value)));label.append(input);container.append(label);}
 }
 if(item.track.type!=='audio'){
  section('ANIMATION');const effects=['none','fade','slide_left','slide_right','slide_up','slide_down','zoom','pop',...(['text','subtitle'].includes(item.track.type)?['typewriter']:[])];
  for(const direction of ['in','out']){picker(container,direction==='in'?'Animation In':'Animation Out',effects.filter(v=>direction==='in'||v!=='typewriter').map(v=>[v,v]),c.animation[direction]?.type||'none',value=>mutate(()=>{c.animation[direction]=value==='none'?null:{type:value,duration:Math.min(.3,c.duration/2),easing:'ease-in-out'};c.animation_duration=0;c.animation_offset=0;}));if(c.animation[direction]){field(`${direction} duration`,`animation.${direction}.duration`,{min:.01,max:.8});field(`${direction} easing`,`animation.${direction}.easing`,{values:['linear','ease-in','ease-out','ease-in-out']});}}
  field('Motion','animation.loop',{values:['static','zoom_in','zoom_out','pan_left','pan_right','pan_up','pan_down']});
 }
 section('KEYFRAMES');const controls=el('div','action-row');container.append(controls);
 action(controls,'＋ Tại playhead',()=>mutate(()=>{const local=Math.max(0,time-c.timeline_start)+(c.animation_offset||0),st=Object.fromEntries(Object.entries({x:c.transform.x,y:c.transform.y,scale:1,rotation:0,opacity:1,volume:1}).map(([k,v])=>[k,N.interpolate(c.keyframes,k,local,v)]));const key={time:Number(local.toFixed(4)),x:st.x,y:st.y,scale:Math.max(.05,Math.min(1.35,st.scale)),rotation:Math.max(-10,Math.min(10,st.rotation)),opacity:st.opacity,volume:st.volume,easing:'linear'};c.keyframes=c.keyframes.filter(k=>Math.abs(k.time-key.time)>.0001);c.keyframes.push(key);c.keyframes.sort((a,b)=>a.time-b.time);keyIndex=c.keyframes.indexOf(key);}));
 if(c.keyframes.length){keyIndex=Math.min(keyIndex,c.keyframes.length-1);picker(container,'Mốc',c.keyframes.map((k,i)=>[String(i),format(k.time)]),String(keyIndex),value=>{keyIndex=Number(value);pause();seek(c.timeline_start+c.keyframes[keyIndex].time-c.animation_offset);renderInspector();});const k=c.keyframes[keyIndex];for(const name of ['time','x','y','scale','rotation','opacity','volume']){if(k[name]==null)continue;field(name,`keyframes.${keyIndex}.${name}`,{min:{time:0,x:-2,y:-2,scale:.05,rotation:-10,opacity:0,volume:0}[name],max:{time:7200,x:2,y:2,scale:1.35,rotation:10,opacity:1,volume:1}[name]});}field('Easing',`keyframes.${keyIndex}.easing`,{values:['linear','ease-in','ease-out','ease-in-out']});action(container,'Xóa keyframe',()=>mutate(()=>c.keyframes.splice(keyIndex,1)));}
 if(item.track.type==='subtitle'){
  section('SUBTITLE ANIMATION');field('Preset','subtitle_animation',{values:['static','fade','pop','word_highlight']});field('Highlight','style.highlight',{type:'color'});
  action(container,'Ước lượng mốc từ',()=>mutate(()=>{const words=c.text.trim().split(/\s+/).filter(Boolean),step=c.duration/Math.max(1,words.length);c.words=words.map((text,i)=>({text,start:i*step,end:(i+1)*step}));message('Mốc từ chia theo thời lượng, chưa phải ASR. Có thể chỉnh lại từng từ.');}));
  if(c.words.length){wordIndex=Math.min(wordIndex,c.words.length-1);picker(container,'Từ',c.words.map((w,i)=>[String(i),w.text]),String(wordIndex),v=>{wordIndex=Number(v);renderInspector();});field('Nội dung',`words.${wordIndex}.text`,{type:'text'});field('Bắt đầu (s)',`words.${wordIndex}.start`,{min:0,max:7200});field('Kết thúc (s)',`words.${wordIndex}.end`,{min:.001,max:7200});}
 }
}
async function analyzeClip(c){const m=mediaFor(c);if(!m||m.ref_type!=='asset')return;pause();setBusy(true);message('Đang phân tích chuyển động, cảnh và khoảng lặng…');try{m.analysis=await post(`/assets/${m.id}/analysis`,{target:project.target});setBusy(false);renderInspector();message(`Đã phân tích: ${m.analysis.method}. Chọn áp dụng crop hoặc mốc cắt.`);}catch(e){message(e.message,true);}finally{setBusy(false);}}
function applySmart(c,analysis){mutate(()=>{if(c.crop.manual)throw Error('Crop thủ công được ưu tiên. Reset crop trước khi áp Smart Crop.');const points=analysis.keyframes,first=[...points].reverse().find(k=>k.time<=c.source_start)||points[0];if(!first)return;const keys=[{...first,time:0},...points.filter(k=>k.time>c.source_start&&k.time<c.source_end).map(k=>({...k,time:(k.time-c.source_start)/c.speed}))];for(const k of keys){const current=c.keyframes.find(x=>Math.abs(x.time-k.time)<.001);if(current)Object.assign(current,k);else c.keyframes.push(k);}c.keyframes.sort((a,b)=>a.time-b.time);c.crop.mode='smart_crop';c.crop.x=first.crop_x;c.crop.y=first.crop_y;c.animation_offset=0;c.animation_duration=0;});}
function applyCuts(c,analysis){mutate(()=>{const item=M.find(project,c.id);if(item.track.clips.some(c=>c.transition))throw Error('Bỏ transition trước khi Smart Cut.');let parts=[c];for(const source of analysis.cuts.filter(x=>x>c.source_start+.1&&x<c.source_end-.1)){const last=parts.pop(),at=c.timeline_start+(source-c.source_start)/c.speed;parts.push(...M.split(last,at,crypto.randomUUID()));}item.track.clips.splice(item.track.clips.indexOf(c),1,...parts);selected=parts[0].id;message(`Đã chia ${parts.length} đoạn từ mốc nội dung phát hiện.`);});}
function renderInspector(){
 const container=$('inspector');container.replaceChildren();const item=chosen();if(!item){container.append(el('p','empty-state','Chọn clip trên timeline hoặc canvas để chỉnh sửa.'));return;}
 const c=item.clip;container.append(el('div','inspector-name',item.track.name),el('p','hint',c.name||c.id));
 function section(text){container.append(el('h3','',text));}
 function field(label,path,options={}){
  const wrapper=el('label','',label);let input;
  const get=(obj)=>path.split('.').reduce((v,k)=>v[k],obj);
  if(options.values){input=el('select');for(const v of options.values)input.append(new Option(v,v));}
  else{input=el('input');input.type=options.type||'number';if(input.type==='number'){input.step=options.step??'any';if(options.min!==undefined)input.min=options.min;if(options.max!==undefined)input.max=options.max;}}
  input.dataset.field=path;if(input.type==='checkbox')input.checked=get(c);else input.value=get(c);wrapper.append(input);container.append(wrapper);
  input.onchange=()=>{if(!input.checkValidity()){input.reportValidity();input.value=get(c);return;}mutate(()=>{
   const target=chosen().clip,keys=path.split('.'),key=keys.pop(),obj=keys.reduce((v,k)=>v[k],target),value=input.type==='checkbox'?input.checked:input.type==='number'?Number(input.value):input.value;
   if(typeof value==='number'&&!Number.isFinite(value))throw Error('Giá trị phải là số hữu hạn.');obj[key]=value;
   if(path==='speed')target.duration=(target.source_end-target.source_start)/target.speed;
   if(path.startsWith('crop.')&&path!=='crop.mode')target.crop.manual=true;if(['crop.x','crop.y','crop.zoom'].includes(path))for(const k of target.keyframes){delete k.crop_x;delete k.crop_y;}
   if(path.startsWith('keyframes.')){target.keyframes.sort((a,b)=>a.time-b.time);if(new Set(target.keyframes.map(k=>k.time)).size!==target.keyframes.length)throw Error('Mỗi keyframe cần thời điểm riêng.');}
   if(['duration','source_start'].includes(path))target.source_end=target.source_start+target.duration*target.speed;
   const source=mediaFor(target);if(source&&source.kind!=='image'&&target.source_end>source.duration+.001)throw Error('Điểm trim vượt quá thời lượng nguồn.');
   if(target.fade_in+target.fade_out>target.duration)throw Error('Tổng fade không được vượt thời lượng clip.');
   if(target.transform.width*target.transform.scale>4||target.transform.height*target.transform.scale>4)throw Error('Kích thước layer vượt giới hạn 4 lần canvas.');
   if(!M.fits(item.track,target))throw Error('Clip chồng lên clip khác cùng track.');
   if(M.end(target)>7200)throw Error('Timeline tối đa 2 giờ.');
  });};
 }
 section('TIMING');field('Start (s)','timeline_start',{min:0,max:7200});field('Duration (s)','duration',{min:M.frame,max:7200});
 if(['video','audio'].includes(item.track.type)){field('Source in (s)','source_start',{min:0,max:86400});field('Speed','speed',{min:.5,max:2,step:.1});}
 if(item.track.type!=='audio'){
  section('TRANSFORM');field('Position X','transform.x',{min:-2,max:2});field('Position Y','transform.y',{min:-2,max:2});field('Width','transform.width',{min:.01,max:2});field('Height','transform.height',{min:.01,max:2});field('Scale','transform.scale',{min:.05,max:4});field('Rotation °','transform.rotation',{min:-360,max:360,step:1});field('Opacity','transform.opacity',{min:0,max:1});
 }
 if(item.track.type==='video'){section('CROP');field('Mode','crop.mode',{values:['fill','fit','crop','blur_background','smart_crop','stretch']});field('Crop X','crop.x',{min:0,max:1});field('Crop Y','crop.y',{min:0,max:1});field('Crop zoom','crop.zoom',{min:1,max:4});}
 if(['video','audio'].includes(item.track.type)){section('AUDIO');field('Volume','volume',{min:0,max:1});field('Mute','muted',{type:'checkbox'});field('Fade in (s)','fade_in',{min:0,max:60});field('Fade out (s)','fade_out',{min:0,max:60});}
 else{section('TEXT');const input=el('textarea');input.value=c.text;input.maxLength=4000;input.setAttribute('aria-label','Nội dung text');input.onchange=()=>mutate(()=>{chosen().clip.text=input.value;});container.append(input);field('Font','style.font',{values:['DejaVu Sans','DejaVu Serif','DejaVu Sans Mono']});field('Size','style.size',{min:12,max:180,step:1});field('Color','style.color',{type:'color'});field('Stroke','style.stroke',{min:0,max:10});field('Shadow','style.shadow',{min:0,max:10});field('Background','style.background',{type:'checkbox'});}
 renderAdvanced(item,c,container,field,section);
}
function split(){mutate(()=>{const item=chosen();if(!item)throw Error('Chọn clip trước khi split.');const following=item.track.clips.find(c=>c.transition?.from_clip_id===item.clip.id);if(following&&M.end(item.clip)-time<=following.transition.duration)throw Error('Đặt mốc split trước transition cuối clip.');const pair=M.split(item.clip,time,crypto.randomUUID());for(const c of item.track.clips)if(c.transition?.from_clip_id===item.clip.id)c.transition.from_clip_id=pair[1].id;item.track.clips.splice(item.track.clips.indexOf(item.clip),1,...pair);selected=pair[1].id;});}
function remove(){mutate(()=>{const item=chosen();if(item){item.track.clips=item.track.clips.filter(c=>c.id!==selected);for(const c of item.track.clips)if(c.transition?.from_clip_id===selected)c.transition=null;}selected=null;});}
function undo(redo=false){if(busy)return;pause();if(redo?history.redo():history.undo()){project=M.clone(history.current);mark();draw();}}
function reorder(direction){mutate(()=>{const item=chosen();if(item){if(item.track.clips.some(c=>c.transition))throw Error('Bỏ transition trên track trước khi đổi thứ tự clip.');M.reorder(item.track,item.clip.id,direction);}});}
$('split').onclick=split;$('delete').onclick=remove;$('undo').onclick=()=>undo();$('redo').onclick=()=>undo(true);$('move-left').onclick=()=>reorder(-1);$('move-right').onclick=()=>reorder(1);
$('detach-audio').onclick=()=>mutate(()=>{const item=chosen();if(!item||item.track.type!=='video'||!mediaFor(item.clip)?.has_audio)throw Error('Chọn video có audio.');const track=project.tracks.find(t=>t.id==='original-audio');const c=M.clone(item.clip);c.id=crypto.randomUUID();c.muted=false;c.transition=null;if(!M.fits(track,c))throw Error('Original audio đã có clip ở vị trí này.');track.clips.push(c);item.clip.muted=true;selected=c.id;});
function zoom(value){px=Math.max(8,Math.min(200,value));$('zoom').value=px;renderTimeline();}
$('zoom').oninput=e=>zoom(Number(e.target.value));$('zoom-in').onclick=()=>zoom(px*1.25);$('zoom-out').onclick=()=>zoom(px/1.25);
document.addEventListener('keydown',e=>{
 if(busy||e.target.closest('input,textarea,select,[contenteditable=true]'))return;
 const mod=e.ctrlKey||e.metaKey,key=e.key.toLowerCase();
 if(mod&&key==='z'){e.preventDefault();undo(e.shiftKey);}else if(mod&&key==='y'){e.preventDefault();undo(true);}
 else if(mod&&['+','=','-'].includes(key)){e.preventDefault();zoom(px*(key==='-'?.8:1.25));}
 else if(mod&&key==='s'){e.preventDefault();$('save').click();}
 else if(e.code==='Space'){e.preventDefault();play();}else if(key==='s'){e.preventDefault();split();}
 else if(key==='delete'||key==='backspace'){e.preventDefault();remove();}
 else if(key==='arrowleft'||key==='arrowright'){e.preventDefault();pause();seek(time+(key==='arrowleft'?-1:1)*(e.shiftKey?1:M.frame));}
});
function draw(){updateTarget();time=Math.min(time,M.duration(project));renderTimeline();renderInspector();rebuildLayers();$('undo').disabled=!history.history.length;$('redo').disabled=!history.future.length;}
renderLibrary();draw();requestAnimationFrame(tick);
})();



