import { kugouRequest } from '../kugou.js';
import { kugouDetailLink, kugouSongs, musicPlatform } from '../music-platform.js';
import { player } from '../player.js';
import { esc, skelGrid, renderSongList } from '../ui.js';

function cards(items, kind) {
  return items.map(item => `<a class="cm-plcard" href="${esc(kugouDetailLink(kind, item.id))}">
    <div class="cm-plcover">${item.artwork ? `<img src="${esc(item.artwork)}" loading="lazy" alt="">` : ''}
    <span class="material-icons-outlined">${kind === 'artist' ? 'person' : 'queue_music'}</span></div>
    <div class="cm-plname">${esc(item.name)}</div>${item.trackCount ? `<div class="cm-plsub">${item.trackCount} 首</div>` : ''}</a>`).join('');
}

export async function mountKugouDiscover(el) {
  el.innerHTML = `<div class="cm-disc"><section class="cm-sec"><div class="cm-sec-head"><h2>推荐新歌</h2></div><div id="kgDiscoverTracks">${skelGrid(3)}</div></section>
    <section class="cm-sec"><div class="cm-sec-head"><h2>推荐歌单</h2><a class="cm-sec-more" href="#/plaza">歌单广场</a></div><div class="cm-plgrid" id="kgDiscoverPlaylists">${skelGrid(3)}</div></section>
    <section class="cm-sec"><div class="cm-sec-head"><h2>排行榜</h2><a class="cm-sec-more" href="#/kugou/charts">查看全部</a></div><div class="cm-plgrid" id="kgDiscoverCharts">${skelGrid(3)}</div></section>
    <section class="cm-sec"><div class="cm-sec-head"><h2>热门歌手</h2><a class="cm-sec-more" href="#/artists">歌手分类</a></div><div class="cm-plgrid" id="kgDiscoverArtists">${skelGrid(3)}</div></section></div>`;
  const sections = [
    ['#kgDiscoverTracks', 'discover.new.songs', null], ['#kgDiscoverPlaylists', 'discover.playlists', 'playlist'],
    ['#kgDiscoverCharts', 'discover.charts', 'playlist'], ['#kgDiscoverArtists', 'discover.artists', 'artist'],
  ];
  await Promise.all(sections.map(async ([selector, operation, kind]) => {
    const node = el.querySelector(selector);
    try {
      const { items } = await kugouRequest(operation, { limit: 9 });
      if (!node.isConnected || musicPlatform() !== 'kugou') return;
      if (kind) node.innerHTML = cards(items.slice(0, 9), kind);
      else {
        const songs = kugouSongs(items);
        await renderSongList(node, songs, { onPlay: i => player.playList(songs, i), deferStatus: true });
      }
      if (!items.length) node.textContent = '暂无内容';
    } catch (error) { if (node.isConnected) node.textContent = error.message; }
  }));
}

export async function renderKugouDirectory(el, kind) {
  const artist = kind === 'artist';
  el.innerHTML = `<div class="cm-sec-head"><h2>${artist ? '歌手分类' : '歌单广场'}</h2>
    <label>分类 <select id="kgCategory"><option value="">${artist ? '热门' : '全部'}</option></select></label></div>
    <div class="cm-plgrid" id="kgDirectory">${skelGrid(6)}</div><mdui-button id="kgMore" variant="text" hidden>加载更多</mdui-button>`;
  const grid = el.querySelector('#kgDirectory'), select = el.querySelector('#kgCategory'), more = el.querySelector('#kgMore');
  let cursor = 0, revision = 0;
  const load = async (reset = false) => {
    const request = ++revision;
    more.disabled = true;
    if (reset) cursor = 0;
    try {
      const answer = await kugouRequest(artist ? 'discover.artists' : 'discover.playlist.directory',
        { [artist ? 'group' : 'category']: select.value, offset: cursor, limit: 36 });
      if (request !== revision || !grid.isConnected || musicPlatform() !== 'kugou') return;
      if (artist && select.options.length === 1) answer.groups.filter(group => group !== '热门').forEach(group => select.add(new Option(group, group)));
      const html = cards(answer.items, kind);
      if (reset) grid.innerHTML = html || '<div class="cm-empty">此分类暂无内容</div>';
      else grid.insertAdjacentHTML('beforeend', html);
      cursor = answer.nextOffset;
      more.hidden = cursor == null;
    } catch (error) { if (request === revision && grid.isConnected) grid.textContent = error.message; }
    finally { if (request === revision) more.disabled = false; }
  };
  if (!artist) kugouRequest('discover.playlist.categories', {}).then(({ items }) => {
    if (select.isConnected) items.forEach(item => select.add(new Option(`${item.group} · ${item.name}`, item.id)));
  }).catch(() => {});
  more.onclick = () => load();
  select.onchange = () => load(true);
  return load(true);
}
