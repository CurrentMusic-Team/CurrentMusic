import { bilibiliRequest, bilibiliSong, originalVideoUrl } from '../bilibili.js';
import { mountMvPlayer } from '../mv.js';
import { player } from '../player.js';
import { esc, fmtDur } from '../ui.js';

export function renderSearch(root) {
  root.innerHTML = `<form class="cm-search-bar cm-search-submit">
    <input name="query" type="search" maxlength="100" required aria-label="搜索哔哩哔哩" placeholder="搜索歌曲、BV 号或视频链接">
    <button class="cm-search-go" type="submit">搜索</button>
  </form><p data-status role="status"></p><div class="cm-ulist" data-results></div>
  <button type="button" class="cm-search-go" data-more hidden>加载更多</button>`;
  const form = root.querySelector('form'), status = root.querySelector('[data-status]');
  const results = root.querySelector('[data-results]'), more = root.querySelector('[data-more]');
  let sequence = 0, query = '', offset = 0;
  async function load(append) {
    const request = ++sequence;
    status.textContent = '正在搜索…';
    more.hidden = true;
    if (!append) results.replaceChildren();
    try {
      const response = await bilibiliRequest('catalog.search', { query, offset, kind: 'song', limit: 20 });
      if (request !== sequence || !results.isConnected) return;
      for (const item of response.items || []) {
        const song = bilibiliSong(item);
        if (!song) continue;
        const row = document.createElement('article');
        row.className = 'cm-urow';
        row.innerHTML = `${item.artwork ? `<img class="cm-avatar" src="${esc(item.artwork)}" alt="" loading="lazy">` : ''}
          <div class="cm-umain"><strong>${esc(item.name)}</strong>
          <p class="cm-usub">${esc(item.uploader || '')} · ${fmtDur(item.durationMs)}</p></div>
          <button type="button" class="cm-search-go">听音频</button>
          <a class="cm-search-go" href="#/bilibili/mv/${esc(item.id)}">MV</a>`;
        row.querySelector('button').onclick = () => player.playSong(song);
        results.append(row);
      }
      offset = response.nextOffset;
      more.hidden = offset == null;
      status.textContent = results.children.length ? '' : '没有匹配的视频。';
    } catch (error) {
      if (request === sequence) status.textContent = error.message;
    }
  }
  form.onsubmit = event => {
    event.preventDefault();
    query = form.elements.query.value.trim();
    offset = 0;
    if (query) void load(false);
  };
  more.onclick = () => load(true);
}

export async function render(root, [id]) {
  if (!id) { renderSearch(root); return; }
  root.innerHTML = '<p data-status role="status">正在读取视频…</p>';
  const status = root.querySelector('[data-status]');
  try {
    const { resource, parts } = await bilibiliRequest('catalog.video', { id });
    if (!status.isConnected) return;
    const song = bilibiliSong(resource);
    root.innerHTML = `<div class="cm-mv-page"><div class="cm-mv-stage" data-stage></div>
      <header class="cm-mv-info"><div><h2>${esc(resource.name)}</h2><p>${esc(resource.uploader)}</p></div>
      <button class="cm-search-go" type="button">听音频</button>
      <a href="${esc(originalVideoUrl(id, resource.page))}" target="_blank" rel="noopener noreferrer">在哔哩哔哩打开</a></header>
      ${parts.length > 1 ? `<label>分 P <select aria-label="选择分 P">${parts.map(part => `<option value="${esc(part.id)}"${part.id === id ? ' selected' : ''}>P${part.page} ${esc(part.partTitle)}</option>`).join('')}</select></label>` : ''}</div>`;
    root.querySelector('button.cm-search-go').onclick = () => player.playSong(song);
    const select = root.querySelector('select');
    if (select) select.onchange = () => { location.hash = '#/bilibili/mv/' + select.value; };
    const stage = root.querySelector('[data-stage]');
    const dispose = mountMvPlayer(stage, { id, provider: 'bilibili', title: resource.name, cover: resource.artwork, original: originalVideoUrl(id, resource.page) });
    const observer = new MutationObserver(() => {
      if (stage.isConnected) return;
      observer.disconnect();
      dispose();
    });
    observer.observe(root.parentNode || root, { childList: true, subtree: true });
  } catch (error) {
    if (status.isConnected) status.textContent = error.message;
  }
}
