import { kugouRequest } from '../kugou.js';
import { kugouSongs } from '../music-platform.js';
import { player } from '../player.js';
import { esc, renderSongList, skelGrid, skelList, toast } from '../ui.js';

export async function renderSpokenDiscovery(el) {
  el.innerHTML = skelGrid(5);
  const pending = el.firstElementChild;
  try {
    const { items } = await kugouRequest('discover.podcasts', {});
    if (!pending.isConnected) return;
    el.innerHTML = `<div class="cm-sec-head"><h2>酷狗有声节目</h2></div><div class="cm-plgrid">${items.map(show => `
      <a class="cm-plcard" href="#/kugou/podcast/${esc(show.id)}"><div class="cm-plcover">
        ${show.artwork ? `<img src="${esc(show.artwork)}" loading="lazy" alt="">` : ''}<span class="material-icons-outlined">podcasts</span></div>
        <div class="cm-plname">${esc(show.name)}</div>${show.artists ? `<div class="cm-plsub">${esc(show.artists)}</div>` : ''}</a>`).join('') ||
      '<div class="cm-empty">暂无有声节目</div>'}</div>`;
  } catch (error) { if (pending.isConnected) el.textContent = error.message; }
}

export async function render(el, [id]) {
  el.innerHTML = skelList(6);
  const pending = el.firstElementChild;
  try {
    const result = await kugouRequest('catalog.podcast', { id });
    if (!pending.isConnected) return;
    const show = result.resource;
    const songs = kugouSongs(result.tracks);
    let offset = result.nextOffset;
    el.innerHTML = `<div class="cm-sec-head"><h2>${esc(show.name)}</h2></div>
      ${show.artists ? `<p class="cm-sec-sub">${esc(show.artists)}</p>` : ''}
      ${show.description ? `<p>${esc(show.description)}</p>` : ''}
      <div id="kgEpisodes"></div><mdui-button id="kgEpisodeMore" variant="text" ${offset == null ? 'hidden' : ''}>加载更多</mdui-button>`;
    const host = el.querySelector('#kgEpisodes'), more = el.querySelector('#kgEpisodeMore');
    const paint = from => renderSongList(host, songs, { from, onPlay: i => player.playList(songs, i), deferStatus: true });
    await paint(0);
    more.onclick = async () => {
      more.disabled = true;
      try {
        const next = await kugouRequest('catalog.podcast', { id, offset });
        if (!host.isConnected) return;
        const from = songs.length;
        songs.push(...kugouSongs(next.tracks));
        offset = next.nextOffset;
        more.hidden = offset == null;
        await paint(from);
      } catch (error) { toast(error.message); }
      finally { more.disabled = false; }
    };
  } catch (error) { if (pending.isConnected) el.textContent = error.message; }
}
