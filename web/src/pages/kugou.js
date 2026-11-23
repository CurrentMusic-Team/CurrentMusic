import { auth } from '../api.js';
import { kugouRequest, kugouScope, kugouSong } from '../kugou.js';
import { player } from '../player.js';
import { esc, fmtDur, toast } from '../ui.js';
import '../kugou.css';

export async function render(el, [id] = []) {
  const scope = kugouScope();
  el.innerHTML = `<section class="cm-kugou-page">
    <div class="cm-sec-head"><h2>酷狗音乐</h2><a class="cm-sec-more" href="#/search">返回搜索</a></div>
    <form class="cm-kugou-form"><label>搜索酷狗单曲 <input type="search" maxlength="60" required></label><mdui-button type="submit" variant="filled">搜索</mdui-button></form>
    <div data-kg-status role="status"></div><div data-kg-results></div></section>`;
  const host = el.firstElementChild;
  const active = () => host.isConnected && kugouScope() === scope;
  const status = host.querySelector('[data-kg-status]');
  const result = host.querySelector('[data-kg-results]');
  const form = host.querySelector('form');
  let sequence = 0;
  let playable = false;

  function fill(items, continuation) {
    const tracks = items.map(kugouSong).filter(Boolean);
    result.innerHTML = tracks.length ? `<div class="cm-songs">${tracks.map((track, i) => `
      <div class="cm-song">
        ${track.pic ? `<img class="cm-song-pic" src="${esc(track.pic)}" alt="" loading="lazy">` : ''}
        <button type="button" class="cm-song-main cm-kugou-track" data-kg-play="${i}"><span class="cm-song-name">${esc(track.name)}</span>
          <span class="cm-song-sub">${esc([track.artists, track.album].filter(Boolean).join(' · '))}</span></button>
        <span class="cm-song-dur">${fmtDur(track.duration)}</span>
        ${id ? '' : `<a class="cm-song-mv" href="#/kugou/${track.kugou_id}" aria-label="歌曲详情">详情</a>`}
        <button class="cm-song-mv" type="button" data-kg-enqueue="${i}" aria-label="加入播放队列"><span class="material-icons-outlined" aria-hidden="true">queue</span></button>
      </div>`).join('')}</div>` : '<div class="cm-empty">暂无曲目</div>';
    for (const node of result.querySelectorAll('[data-kg-play], [data-kg-enqueue]')) node.onclick = () => {
      if (!auth.token || !playable) {
        toast(!auth.token ? '请登录 CurrentMusic 后播放' : '此服务器暂未开通酷狗播放');
        return;
      }
      if (node.dataset.kgEnqueue !== undefined) player.enqueue(tracks[Number(node.dataset.kgEnqueue)]);
      else player.playList(tracks, Number(node.dataset.kgPlay), { shuffleStart: false });
    };
    if (continuation) {
      const button = document.createElement('button');
      button.textContent = '加载更多';
      button.onclick = continuation;
      result.append(button);
    }
  }

  async function search(query, offset, earlier = []) {
    const current = ++sequence;
    status.textContent = '正在搜索';
    try {
      const data = await kugouRequest('catalog.search', { query, offset, limit: 20 });
      if (!active() || current !== sequence) return;
      const items = earlier.concat(data.items || []);
      status.textContent = '';
      fill(items, Number.isInteger(data.nextOffset) ? () => search(query, data.nextOffset, items) : null);
    } catch (error) {
      if (active() && current === sequence) status.textContent = error.message;
    }
  }

  form.onsubmit = event => {
    event.preventDefault();
    const query = form.querySelector('input').value.trim();
    if (query) search(query, 0);
  };
  try {
    const availability = await kugouRequest('capabilities');
    if (!active()) return;
    if (!availability?.catalog) {
      form.hidden = true;
      status.textContent = '此服务器暂未开通酷狗音乐';
      return;
    }
    playable = availability.playback;
    if (id && !sequence) {
      const data = await kugouRequest('catalog.track', { id });
      if (active() && !sequence) fill([data.resource]);
    }
  } catch (error) {
    if (active()) status.textContent = error.message;
  }
}
