(() => {
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content;
  async function post(url, data) {
    const response = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json','Accept':'application/json','x-csrf-token':csrf}, body:JSON.stringify(data)});
    let body;
    try { body = await response.json(); } catch { throw new Error('Không đọc được phản hồi. Tải lại trang để kiểm tra trạng thái.'); }
    if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail));
    return body;
  }
  const create = document.getElementById('script-create');
  if (create) {
    let key = crypto.randomUUID();
    create.addEventListener('change', () => { key = crypto.randomUUID(); });
    create.addEventListener('submit', async event => {
      event.preventDefault();
      const button = create.querySelector('button'); button.disabled = true;
      const fields = new FormData(create), data = {event_id:Number(fields.get('event_id')), provider:fields.get('provider'), tone:fields.get('tone'), target_seconds:Number(fields.get('target_seconds')), feedback:fields.get('feedback'), refresh_sources:fields.get('refresh_sources') === 'on', idempotency_key:key};
      if (fields.get('base_version_id')) data.base_version_id = Number(fields.get('base_version_id'));
      try { const job = await post(create.dataset.endpoint, data); location.assign('/scripts/' + job.event_id); }
      catch (error) { create.querySelector('.script-message').textContent = error.message; button.disabled = false; }
    });
  }
  const statusLabels = {queued:'Đang chờ worker',running:'Đang xử lý',retry_wait:'Đang chờ thử lại',waiting_quota:'Đang chờ quota',unknown_outcome:'Kết quả chưa xác định — không tự gửi lại',succeeded:'Đã hoàn tất',failed:'Không hoàn tất'};
  document.querySelectorAll('[data-script-job]').forEach(element => {
    let stopped = false;
    const initiallyActive = !['succeeded','failed','unknown_outcome'].includes(element.dataset.initialStatus);
    async function poll() {
      if (stopped) return;
      try {
        const response = await fetch('/script-jobs/' + element.dataset.scriptJob, {headers:{Accept:'application/json'}});
        if (!response.ok) throw new Error('Không đọc được trạng thái');
        const job = await response.json();
        if (element.dataset.refreshHistory && (job.status !== element.dataset.initialStatus || job.attempts !== Number(element.dataset.attempts))) {
          location.reload(); return;
        }
        element.textContent = statusLabels[job.status] + ' · ' + job.attempts + '/' + job.max_attempts + ' lần gửi' + (['retry_wait','waiting_quota'].includes(job.status) ? (job.next_attempt_at ? ' · Thử lại lúc ' + new Date(job.next_attempt_at).toLocaleString('vi-VN') : ' · Chờ quản trị kiểm tra quota') : '') + (job.error ? ' · ' + job.error : '');
        stopped = ['succeeded','failed','unknown_outcome'].includes(job.status);
        if (stopped && initiallyActive && element.dataset.refresh) location.reload();
      } catch { element.textContent = 'Tạm mất kết nối. Đang kiểm tra lại trạng thái…'; }
      if (!stopped) setTimeout(poll, 2500);
    }
    poll();
  });
  const editor = document.getElementById('script-editor');
  let dirty = false;
  if (editor) {
    const output = JSON.parse(document.getElementById('script-data').textContent);
    const sources = JSON.parse(document.getElementById('source-data').textContent);
    const scenes = document.getElementById('scene-editors');
    function field(parent, labelText, value, name, type='textarea') {
      const label = document.createElement('label'); label.append(document.createTextNode(labelText));
      const input = document.createElement(type==='textarea' ? 'textarea' : 'input');
      input.className = 'form-control'; input.dataset.field = name; input.value = value;
      if (type==='number') { input.type='number'; input.min='1'; input.max='90'; }
      if (type==='textarea') input.rows=3;
      label.append(input); parent.append(label); return input;
    }
    function addScene(scene) {
      const box = document.createElement('div'); box.className='scene-editor';
      const tools = document.createElement('div'); tools.className='scene-tools';
      const heading = document.createElement('h3'); heading.textContent='Cảnh'; tools.append(heading);
      const remove = document.createElement('button'); remove.type='button'; remove.className='btn btn-sm btn-outline-secondary'; remove.textContent='Xóa cảnh';
      remove.addEventListener('click', () => { box.remove(); dirty=true; renumber(); syncHook(); }); tools.append(remove); box.append(tools);
      field(box,'Lời đọc',scene.narration,'narration'); field(box,'Chữ trên màn hình',scene.on_screen_text,'on_screen_text'); field(box,'Gợi ý hình ảnh',scene.visual_brief,'visual_brief');
      field(box,'Thời lượng dự kiến (giây)',scene.seconds,'seconds','number'); field(box,'Mã khẳng định đã dẫn chứng (phân cách dấu phẩy)',scene.claim_ids.join(', '),'claim_ids','text');
      scenes.append(box); renumber();
    }
    function renumber() { scenes.querySelectorAll('.scene-editor h3').forEach((el,i) => el.textContent='Cảnh ' + (i+1)); }
    function syncHook() { const first=scenes.querySelector('[data-field="narration"]'); if(first) first.value=editor.querySelector('[data-output="hook"]').value; }
    output.scenes.forEach(addScene);
    document.getElementById('add-scene').addEventListener('click', () => {addScene({narration:'',on_screen_text:'',visual_brief:'',seconds:5,claim_ids:[]});dirty=true;});
    editor.addEventListener('input', event => {
      dirty=true;
      if(event.target.dataset.output==='hook') syncHook();
      if(event.target===scenes.querySelector('[data-field="narration"]')) editor.querySelector('[data-output="hook"]').value=event.target.value;
    });
    const claims=document.getElementById('claim-editors');
    output.claims.forEach(claim => {
      const box=document.createElement('div'); box.className='claim-editor'; box.dataset.claimId=claim.claim_id;
      field(box,'Khẳng định ' + claim.claim_id,claim.text,'text');
      claim.evidence.forEach((evidence,index) => {
        const group=document.createElement('div'); group.className='claim-evidence';
        const label=document.createElement('label'); label.textContent='Nguồn dẫn';
        const select=document.createElement('select'); select.className='form-select'; select.dataset.field='source_id';
        sources.forEach(source => { const option=document.createElement('option');option.value=source.source_id;option.textContent=source.title;option.selected=source.source_id===evidence.source_id;select.append(option); });
        label.append(select);group.append(label);field(group,'Trích dẫn nguyên văn',evidence.quote,'quote');
        const link=document.createElement('a');link.textContent='Đối chiếu trích dẫn →';
        link.href='#evidence-' + (index+1) + '-' + claim.claim_id + '-' + evidence.source_id;
        select.addEventListener('change',() => {dirty=true;link.href='#source-'+select.value;});group.append(link);box.append(group);
      }); claims.append(box);
    });
    editor.addEventListener('submit',async event => {
      event.preventDefault();const button=editor.querySelector('[type="submit"]'); button.disabled=true;
      const data=structuredClone(output);
      editor.querySelectorAll('[data-output]').forEach(el => data[el.dataset.output]=el.value);
      data.scenes=Array.from(scenes.children).map((box,index) => {const scene={scene_id:index+1};box.querySelectorAll('[data-field]').forEach(el => scene[el.dataset.field]=el.dataset.field==='seconds' ? Number(el.value) : el.dataset.field==='claim_ids' ? el.value.split(',').map(s=>s.trim()).filter(Boolean) : el.value);return scene;});
      data.claims=Array.from(claims.children).map(box => ({claim_id:box.dataset.claimId,text:box.querySelector('[data-field="text"]').value,evidence:Array.from(box.querySelectorAll('.claim-evidence')).map(group=>({source_id:group.querySelector('select').value,quote:group.querySelector('textarea').value}))}));
      try {await post('/scripts/'+editor.dataset.event+'/versions',{base_version_id:Number(editor.dataset.version),data});dirty=false;location.assign('/scripts/'+editor.dataset.event);}
      catch(error){editor.querySelector('.script-message').textContent=error.message;button.disabled=false;}
    });
  }
  const review=document.getElementById('script-review');
  if(review) review.addEventListener('submit',async event=>{
    event.preventDefault();
    if(dirty){review.querySelector('.script-message').textContent='Bạn có chỉnh sửa chưa lưu. Lưu bản nháp mới rồi duyệt đúng phiên bản.';return;}
    const fields=new FormData(review);const buttons=review.querySelectorAll('button');buttons.forEach(b=>b.disabled=true);
    try{await post('/scripts/'+review.dataset.event+'/reviews',{version_id:Number(review.dataset.version),decision:event.submitter.value,reviewer:fields.get('reviewer'),comment:fields.get('comment')});location.reload();}
    catch(error){review.querySelector('.script-message').textContent=error.message;buttons.forEach(b=>b.disabled=false);}
  });
})();
