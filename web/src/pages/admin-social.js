import { auth, call } from '../api.js';
import { esc, toast } from '../ui.js';

const labels = { pending: '待处理', resolved: '已处理', dismissed: '已驳回' };

export function mountSocialReports(root) {
  const token = auth.token;
  const hash = location.hash;
  let status = 'pending';
  let before = 0;
  let seq = 0;
  const active = () => root.isConnected && token === auth.token && hash === location.hash;
  root.innerHTML = '<div class="cm-social-actions" role="group" aria-label="举报状态">' +
    Object.keys(labels).map(key => '<mdui-button compact variant="text" data-status="' + key + '">' + labels[key] + '</mdui-button>').join('') +
    '</div><div data-reports aria-live="polite"></div><mdui-button variant="text" data-report-more hidden>加载更多</mdui-button>';
  const results = root.querySelector('[data-reports]');
  const more = root.querySelector('[data-report-more]');
  const load = async (append = false) => {
    const turn = ++seq;
    root.querySelectorAll('[data-status]').forEach(button => {
      button.variant = status === button.dataset.status ? 'tonal' : 'text';
      button.setAttribute('aria-pressed', String(status === button.dataset.status));
    });
    more.hidden = true;
    if (!append) results.textContent = '正在读取举报…';
    try {
      const data = await call('GET', '/admin/social/reports?status=' + status + '&before=' + before, { auth: true });
      if (!active() || turn !== seq) return;
      if (!append) results.textContent = '';
      results.insertAdjacentHTML('beforeend', data.reports.map(report =>
        '<article class="cm-social-report"><strong>' + esc(report.reporter.nickname) + ' 举报 ' + esc(report.target.nickname) + '</strong>' +
        '<p>' + esc(report.reason) + '</p><small>' + new Date(report.createdAt * 1000).toLocaleString('zh-CN') + '</small>' +
        (report.status === 'pending' ? '<div class="cm-social-actions">' +
          '<mdui-button variant="tonal" data-review="resolved" data-id="' + report.id + '">标为已处理</mdui-button>' +
          '<mdui-button variant="text" data-review="dismissed" data-id="' + report.id + '">驳回</mdui-button></div>' : '') +
        '</article>').join(''));
      if (!append && !data.reports.length) results.textContent = '暂无' + labels[status] + '举报';
      before = data.nextBefore;
      more.hidden = !before;
    } catch (error) { if (active() && turn === seq) results.textContent = error.message; }
  };
  root.onclick = async event => {
    const button = event.target.closest('mdui-button');
    if (!button || button.loading || !active()) return;
    if (button.dataset.status) {
      status = button.dataset.status;
      before = 0;
      load();
    } else if (button === more) {
      if (before) load(true);
    } else if (button.dataset.review) {
      button.loading = true;
      try {
        await call('PUT', '/admin/social/reports/' + Number(button.dataset.id), {
          auth: true, body: { status: button.dataset.review },
        });
        if (active()) { before = 0; load(); }
      } catch (error) { toast(error.message); }
      finally { button.loading = false; }
    }
  };
  load();
}
