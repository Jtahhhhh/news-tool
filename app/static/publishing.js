(() => {
  const token = document.querySelector('meta[name="csrf-token"]')?.content;
  async function send(url, data = {}) {
    const response = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json', Accept: 'application/json', 'x-csrf-token': token}, body: JSON.stringify(data)});
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Dữ liệu không hợp lệ; kiểm tra lại biểu mẫu.');
    return result;
  }
  const form = document.getElementById('publish-create');
  if (form) {
    let key = crypto.randomUUID();
    form.addEventListener('change', event => {
      if (event.target.name !== 'confirmed') { key = crypto.randomUUID(); form.elements.confirmed.checked = false; }
      const video = form.elements.video_version_id.selectedOptions[0];
      const account = form.elements.account_id.selectedOptions[0];
      const preview = document.getElementById('publish-preview');
      if (video.dataset.media) { if (preview.getAttribute('src') !== video.dataset.media) preview.src = video.dataset.media; preview.hidden = false; }
      else { preview.removeAttribute('src'); preview.hidden = true; }
      document.getElementById('publish-summary').textContent = video.value && account.value ? `${video.textContent} → ${account.textContent}` : 'Chọn video và tài khoản để xác nhận.';
    });
    document.getElementById('copy-caption').addEventListener('click', async () => {
      try { await navigator.clipboard.writeText(form.elements.caption.value); form.querySelector('.script-message').textContent = 'Đã sao chép caption.'; }
      catch { form.elements.caption.select(); form.querySelector('.script-message').textContent = 'Nhấn Ctrl+C để sao chép nội dung đã chọn.'; }
    });
    form.addEventListener('submit', async event => {
      event.preventDefault(); const button = event.submitter; button.disabled = true;
      try {
        await send('/publish-jobs', {video_version_id: Number(form.elements.video_version_id.value), account_id: Number(form.elements.account_id.value), video_sha256: form.elements.video_version_id.selectedOptions[0].dataset.hash, caption: form.elements.caption.value, confirmed: form.elements.confirmed.checked, idempotency_key: key, mode: 'upload'});
        location.reload();
      } catch (error) { form.querySelector('.script-message').textContent = error.message; button.disabled = false; }
    });
  }
  document.querySelectorAll('[data-publish-job]').forEach(card => {
    const url = '/publish-jobs/' + card.dataset.publishJob;
    async function update() {
      try {
        const response = await fetch(url); if (!response.ok) return;
        const job = await response.json();
        card.querySelector('.publish-status').textContent = `${job.label} · ${job.sent_bytes} byte`;
        card.querySelector('.publish-error').textContent = job.error || '';
        card.querySelector('.publish-attempts').textContent = job.attempts.map(a => `${a.created_at} · ${a.operation} · ${a.outcome}${a.http_status ? ' · HTTP ' + a.http_status : ''}`).join('\n');
        card.querySelector('[data-action="reconcile"]').disabled = !job.has_publish_id || ['published','cancelled'].includes(job.status);
        card.querySelector('[data-action="cancel"]').disabled = ['published','cancelled','stopped'].includes(job.status);
      } catch { /* Preserve the last verified status during a local network error. */ }
    }
    card.querySelectorAll('.publish-action').forEach(button => button.addEventListener('click', async () => {
      button.disabled = true;
      try { await send(url + '/' + button.dataset.action); await update(); }
      catch (error) { card.querySelector('.script-message').textContent = error.message; button.disabled = false; }
    }));
    update(); setInterval(update, 10000);
  });
})();
