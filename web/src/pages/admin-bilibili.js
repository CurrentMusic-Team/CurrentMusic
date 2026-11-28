import { call } from '../api.js';
import { BILIBILI_ID, originalVideoUrl } from '../bilibili.js';
import { esc, fmtDur } from '../ui.js';

export function adminBilibiliPane(root) {
  root.innerHTML = `<div class="cm-admin-section-head"><h2>哔哩哔哩</h2></div>
    <p data-session role="status"></p>
    <form class="cm-admin-form" data-settings><label><input name="enabled" type="checkbox"> 自动匹配歌曲</label>
      <button class="cm-admin-button" type="submit">保存设置</button></form>
    <form class="cm-admin-form" data-links>
      <label>平台 <select name="provider"><option value="netease">网易云音乐</option><option value="kugou">酷狗概念版</option></select></label>
      <label>歌曲 ID <input name="id" required maxlength="80"></label>
      <button type="submit" class="cm-admin-button">查看关联和候选</button>
      <label>BV号:CID <input name="videoId" maxlength="32" placeholder="BV号:CID"></label>
      <div class="cm-admin-actions"><button type="button" data-mode="manual" class="cm-admin-button primary">保存指定分 P</button>
        <button type="button" data-mode="blocked" class="cm-admin-button">禁用这首歌</button>
        <button type="button" data-mode="auto" class="cm-admin-button">恢复自动匹配</button></div>
    </form><p data-status role="status"></p><div data-rows class="cm-admin-records"></div>`;
  const settings = root.querySelector('[data-settings]'), session = root.querySelector('[data-session]');
  const form = root.querySelector('[data-links]'), status = root.querySelector('[data-status]');
  const rows = root.querySelector('[data-rows]');
  const api = (action, body) => call(body ? 'POST' : 'GET', '/admin/bilibili/' + action, { auth: true, body });
  const reference = () => ({ provider: form.elements.provider.value, id: form.elements.id.value.trim() });
  let sequence = 0;
  async function load() {
    try {
      const result = await api('status');
      settings.elements.enabled.checked = result.autoMatch;
      session.textContent = result.activeVip ? '大会员会话有效。' : result.configured ? '尚未验证可用的大会员会话。' : '本机适配服务尚未配置。';
    } catch (error) { session.textContent = error.message; }
  }
  settings.onsubmit = async event => {
    event.preventDefault();
    try {
      await api('settings', { autoMatch: settings.elements.enabled.checked });
      status.textContent = '设置已保存。';
    } catch (error) { status.textContent = error.message; }
  };
  async function lookup() {
    const request = ++sequence;
    const target = reference();
    status.textContent = '正在读取关联…';
    rows.replaceChildren();
    try {
      const result = await api('candidates', target);
      if (request !== sequence || target.id !== reference().id || target.provider !== reference().provider) return;
      status.textContent = `${result.name} · ${fmtDur(result.durationMs)} · ${{ manual: '指定', blocked: '已禁用', auto: '自动' }[result.mode]}${result.selectedId ? ' · ' + result.selectedId : ''}`;
      form.elements.videoId.value = result.selectedId || '';
      rows.innerHTML = result.items.map(item => `<div class="cm-admin-record">
        <span><strong>${esc(item.name)}</strong><small>${esc(item.id)} · ${fmtDur(item.durationMs)} · 相差 ${(item.differenceMs / 1000).toFixed(3)} 秒${item.atmos ? ' · 全景声' : ''}${item.vision ? ' · 杜比视界' : ''}</small></span>
        <a href="${esc(originalVideoUrl(item.id, item.page))}" target="_blank" rel="noopener noreferrer">预览</a>
        <button type="button" class="cm-admin-button" data-id="${esc(item.id)}">使用此分 P</button></div>`).join('');
    } catch (error) { if (request === sequence) status.textContent = error.message; }
  }
  form.onsubmit = event => { event.preventDefault(); void lookup(); };
  form.oninput = event => {
    if (event.target.name === 'id' || event.target.name === 'provider') { sequence++; rows.replaceChildren(); form.elements.videoId.value = ''; }
  };
  rows.onclick = event => {
    const id = event.target.closest('[data-id]')?.dataset.id;
    if (id) form.elements.videoId.value = id;
  };
  form.onclick = async event => {
    const mode = event.target.closest('[data-mode]')?.dataset.mode;
    if (!mode || !form.reportValidity()) return;
    const videoId = form.elements.videoId.value.trim();
    if (mode === 'manual' && !BILIBILI_ID.test(videoId)) { status.textContent = '请输入 BV号:CID。'; return; }
    try {
      await api('link', { ...reference(), mode, videoId });
      status.textContent = '关联已更新。';
    } catch (error) { status.textContent = error.message; }
  };
  return { load };
}
