(() => {
  const token=document.querySelector('meta[name="csrf-token"]')?.content;
  async function send(path,data){
    const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json',Accept:'application/json','x-csrf-token':token},body:JSON.stringify(data)});
    const result=await response.json();
    if(!response.ok)throw new Error(typeof result.detail==='string'?result.detail:JSON.stringify(result.detail));
    return result;
  }
  function handle(form,build,destination){
    if(!form)return;
    const key=crypto.randomUUID();
    form.addEventListener('submit',async event=>{
      event.preventDefault();const button=event.submitter;button.disabled=true;
      try{const [path,data]=build(new FormData(form),key);const result=await send(path,data);destination?location.assign(destination(result)):location.reload();}
      catch(error){form.querySelector('.script-message').textContent=error.message;button.disabled=false;}
    });
  }
  handle(document.getElementById('llm-policy'),f=>{
    const supported=['groq','gemini','deepseek'];
    const ordered=(f.get('order')||'').split(',').map(v=>v.trim().toLowerCase()).filter(Boolean);
    if(ordered.some(n=>!supported.includes(n)))throw new Error('Thứ tự chỉ nhận groq, gemini, deepseek (tên đúng là groq).');
    const allowed=supported.filter(n=>f.has('allow_'+n));
    if(f.has('fallback_enabled')&&!allowed.length)throw new Error('Hãy cho phép ít nhất một provider trước khi bật fallback.');
    // Old policies have no Groq route. A checked provider must survive saving.
    const names=[...new Set([...ordered,...allowed])].filter(n=>!allowed.length||allowed.includes(n));
    if(!names.length)throw new Error('Nhập thứ tự provider hoặc tích chọn provider muốn dùng.');
    return ['/llm/policy',{routes:names.map(provider=>({provider,model:f.get('model_'+provider),reservation_microusd:Math.round(Number(f.get('cost_'+provider))*1000000)})),
      allowed_providers:allowed,fallback_enabled:f.has('fallback_enabled'),fallback_on:['service_error','quota'].filter(n=>f.has('fallback_'+n)),
      retry_base_seconds:Number(f.get('retry_base_seconds')),retry_cap_seconds:Number(f.get('retry_cap_seconds')),retry_window_seconds:Number(f.get('retry_window_seconds')),max_concurrent:Number(f.get('max_concurrent')),max_attempts:Number(f.get('max_attempts')),max_output_tokens_total:Number(f.get('max_output_tokens_total')),budget_microusd:Math.round(Number(f.get('budget'))*1000000),
      circuit_threshold:Number(f.get('circuit_threshold')),circuit_seconds:Number(f.get('circuit_seconds'))}];
  });
  document.querySelectorAll('.credential-form').forEach(form=>handle(form,f=>['/llm/credentials'+(form.dataset.id?'/'+form.dataset.id:''),{
    name:f.get('name'),provider:f.get('provider'),project_id:f.get('project_id'),secret_ref:f.get('secret_ref'),quota_group:f.get('quota_group'),
    allowed_models:f.get('allowed_models').split(',').map(v=>v.trim()).filter(Boolean),priority:Number(f.get('priority'))}]));
  document.querySelectorAll('.credential-toggle').forEach(button=>button.addEventListener('click',async()=>{
    button.disabled=true;try{await send('/llm/credentials/'+button.dataset.id+'/toggle',{enabled:button.dataset.enabled==='true'});location.reload();}
    catch(error){button.textContent=error.message;button.disabled=false;}
  }));
  document.querySelectorAll('.connection-test').forEach(form=>handle(form,(f,key)=>['/llm/credentials/'+form.dataset.id+'/test',{
    model:f.get('model'),acknowledge_cost:f.has('acknowledge_cost'),idempotency_key:key}],r=>'/llm/jobs/'+r.id));
  handle(document.getElementById('quota-config'),f=>['/llm/quotas',{credential_id:Number(f.get('credential_id')),model:f.get('model'),
    configured_limit:f.get('configured_limit')?Number(f.get('configured_limit')):null,window_seconds:f.get('window_seconds')?Number(f.get('window_seconds')):null}]);
  document.querySelectorAll('.quota-release').forEach(form=>handle(form,f=>['/llm/quotas/'+form.dataset.id+'/release',{confirmed_dashboard:f.has('confirmed_dashboard')}]));
  document.querySelectorAll('.job-retry').forEach(form=>handle(form,f=>['/llm/jobs/'+form.dataset.id+'/retry',{acknowledge_unknown:f.has('acknowledge_unknown')}]));
  document.querySelectorAll('.job-cancel').forEach(form=>handle(form,()=>['/llm/jobs/'+form.dataset.id+'/cancel',{}]));
})();
