// Exercise the actual browser submit handler without any network or secrets.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const script=fs.readFileSync(path.join(__dirname,'../app/static/llm-control.js'),'utf8');

async function submit(overrides={}) {
  const values={order:'deepseek,gemini',model_groq:'openai/gpt-oss-120b',model_gemini:'gemini-test',model_deepseek:'deepseek-test',
    cost_groq:'0',cost_gemini:'0',cost_deepseek:'0',retry_base_seconds:'1',retry_cap_seconds:'120',retry_window_seconds:'900',
    max_concurrent:'1',max_attempts:'6',max_output_tokens_total:'49152',budget:'0',circuit_threshold:'3',circuit_seconds:'60',...overrides};
  let callback,payload,reloaded=false;
  const message={textContent:''};
  const form={addEventListener:(name,cb)=>callback=cb,querySelector:()=>message};
  const context={document:{querySelector:()=>({content:'test-csrf'}),getElementById:id=>id==='llm-policy'?form:null,querySelectorAll:()=>[]},
    FormData:class {get(k){return values[k]??null;}has(k){return Object.hasOwn(values,k)&&values[k]!==undefined;}},
    crypto:{randomUUID:()=> 'test-id'},location:{reload:()=>reloaded=true},
    fetch:async (url,options)=>{assert.equal(url,'/llm/policy');payload=JSON.parse(options.body);return {ok:true,json:async()=>({saved:true})};}};
  vm.runInNewContext(script,context);
  await callback({preventDefault(){},submitter:{disabled:false}});
  return {payload,reloaded,error:message.textContent};
}

test('checking Groq on an old two-provider policy persists Groq',async()=>{
  const result=await submit({allow_groq:'on'});
  assert.equal(result.error,'');assert.equal(result.reloaded,true);
  assert.deepEqual(result.payload.allowed_providers,['groq']);
  assert.deepEqual(result.payload.routes,[{provider:'groq',model:'openai/gpt-oss-120b',reservation_microusd:0}]);
});
test('fallback excludes unchecked routes and retains chosen order',async()=>{
  const result=await submit({order:'groq,deepseek,gemini',allow_groq:'on',allow_gemini:'on',fallback_enabled:'on'});
  assert.equal(result.error,'');
  assert.deepEqual(result.payload.routes.map(r=>r.provider),['groq','gemini']);
  assert.deepEqual(result.payload.allowed_providers,['groq','gemini']);
});
test('enabling Groq preserves other enabled providers',async()=>{
  const result=await submit({allow_groq:'on',allow_gemini:'on'});
  assert.deepEqual(result.payload.routes.map(r=>r.provider),['gemini','groq']);
});
test('all providers can remain disabled without fallback',async()=>{
  const result=await submit();
  assert.equal(result.error,'');assert.deepEqual(result.payload.allowed_providers,[]);
});
test('normalize case and deduplicate provider order',async()=>{
  const result=await submit({order:' Groq,groq ',allow_groq:'on'});
  assert.equal(result.payload.routes.length,1);
});
test('misspelled provider explains the error before posting',async()=>{
  const result=await submit({order:'grog',allow_groq:'on'});
  assert.match(result.error,/tên đúng là groq/);assert.equal(result.payload,undefined);
});
test('fallback with no allowed providers explains the error',async()=>{
  const result=await submit({fallback_enabled:'on'});
  assert.match(result.error,/ít nhất một provider/);assert.equal(result.payload,undefined);
});
