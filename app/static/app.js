const jobs = [...document.querySelectorAll('[data-job-id]')];
if (document.getElementById('jobs-live')) {
  const state = document.getElementById('poll-state');
  const highlight = new URLSearchParams(location.search).get('highlight');
  jobs.find(el => el.dataset.jobId === highlight)?.classList.add('highlight');
  async function poll() {
    try {
      const response = await fetch('/api/jobs?ids=' + jobs.map(el => el.dataset.jobId).join(','), {cache: 'no-store'});
      if (!response.ok) throw new Error('Không thể đọc trạng thái');
      for (const job of await response.json()) {
        const card = jobs.find(el => el.dataset.jobId === String(job.id));
        if (!card) continue;
        for (const el of card.querySelectorAll('[data-field]')) {
          const field = el.dataset.field;
          el.textContent = field === 'logs' ? job.logs.map(log => `${log.at} [${log.attempt}] ${log.message}`).join('\n') : (job[field] ?? '');
        }
        card.querySelector('[data-retry]').hidden = job.status !== 'failed';
      }
      state.textContent = 'Đã cập nhật ' + new Date().toLocaleTimeString('vi-VN');
    } catch (error) {
      state.textContent = 'Mất kết nối — đang thử lại…';
    } finally { setTimeout(poll, 3000); }
  }
  setTimeout(poll, 1000);
}
// Empty optional numeric query parameters should be omitted, not sent as blank integers.
document.querySelectorAll('form[method="get"]').forEach(form => form.addEventListener('submit', () => {
  form.querySelectorAll('select[name="source_id"]').forEach(el => { if (!el.value) el.disabled = true; });
}));
