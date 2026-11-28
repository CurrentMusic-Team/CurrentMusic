import { auth } from './api.js';
import { kugouRequest, kugouScope, kugouSong } from './kugou.js';

export function musicPlatform() {
  const value = localStorage.getItem('cm.music.platform:' + kugouScope());
  return ['kugou', 'bilibili'].includes(value) ? value : 'netease';
}

export function platformSelector(platform) {
  return `<select class="cm-platform-select" aria-label="音乐平台">
    <option value="netease"${platform === 'netease' ? ' selected' : ''}>网易云音乐</option>
    <option value="kugou"${platform === 'kugou' ? ' selected' : ''}>酷狗概念版</option>
    <option value="bilibili"${platform === 'bilibili' ? ' selected' : ''}>哔哩哔哩</option>
  </select>`;
}

export function bindPlatformSelector(el, onChange) {
  const select = el.querySelector('.cm-platform-select');
  if (!select) return;
  select.onchange = () => {
    localStorage.setItem('cm.music.platform:' + kugouScope(), select.value);
    onChange();
    el.querySelector('.cm-platform-select')?.focus({ preventScroll: true });
  };
}

export function kugouDetailLink(kind, id) {
  return '#/kugou/' + kind + '/' + encodeURIComponent(id);
}

export const kugouSongs = items => (items || []).map(kugouSong).filter(Boolean);

export async function kugouDaily(charts) {
  if (auth.token) {
    const recommended = await kugouRequest('discover.recommendations', {}).catch(() => null);
    if (recommended?.items.length) return kugouSongs(recommended.items);
  }
  const first = (await (charts || kugouRequest('discover.charts', {})))?.items[0];
  if (!first) return [];
  const data = await kugouRequest('catalog.playlist', { id: first.id });
  return kugouSongs(data.tracks);
}

export async function kugouHome() {
  const charts = kugouRequest('discover.charts', {}).catch(() => null);
  const [daily, ranks, playlists, recent] = await Promise.all([
    kugouDaily(charts).catch(() => []),
    charts,
    kugouRequest('discover.playlists', {}).catch(() => null),
    auth.token ? kugouRequest('history.recent', { limit: 20 }).catch(() => null) : null,
  ]);
  return { daily, charts: ranks?.items || [], playlists: playlists?.items || [], recent: kugouSongs(recent?.items) };
}
