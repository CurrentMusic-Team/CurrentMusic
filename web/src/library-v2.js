import { auth, call, settings } from './api.js';
import { isKugouSong, kugouSong } from './kugou.js';

export const platformLabels = { netease: '网易云音乐', kugou: '酷狗概念版' };
export const sourceLabels = { auto: '自动', native: 'App 歌单', aggregate: '流声聚合歌单' };

export async function libraryCall(method, path, { body, platform, source, offset } = {}) {
  const base = settings.base, token = auth.token;
  const params = new URLSearchParams();
  if (platform) params.set('platform', platform);
  if (source) params.set('source', source);
  if (offset) params.set('offset', offset);
  const query = params.size ? '?' + params : '';
  const result = await call(method, '/library/v2/' + path + query, {
    auth: !path.startsWith('shared/'), base, body, redirect: 'error',
  });
  if (base !== settings.base || token !== auth.token) throw new Error('账号或服务器已切换，请重试');
  return result;
}

export const libraryPreferences = () => libraryCall('GET', 'preferences');
export const playlistPath = id => 'playlists/' + id;
export const libraryLink = id => '#/library/playlist/' + id;
export const trackRef = track => isKugouSong(track)
  ? { provider: 'kugou', id: track.kugou_id }
  : { provider: 'netease', id: String(track.ncm_id) };

export function libraryTrack(row) {
  if (row.provider === 'kugou') return kugouSong(row);
  if (row.provider !== 'netease' || !/^[1-9][0-9]*$/.test(row.id)) return null;
  return { ncm_id: Number(row.id), provider: 'netease', name: row.name, artists: row.artists, album: row.album,
    pic: row.artwork, duration: row.durationMs, mv: Number(row.videoId) || 0 };
}

export async function allLibraryItems(path, request) {
  const items = [];
  let offset = 0;
  do {
    const page = await libraryCall('GET', path, { ...request, offset });
    items.push(...page.items);
    offset = page.nextOffset;
  } while (offset !== null);
  return items;
}

export async function writePlaylist(playlist, action, data = {}) {
  const paths = { edit: '', delete: '', add: '/tracks', remove: '/tracks', order: '/order' };
  const methods = { edit: 'PUT', delete: 'DELETE', add: 'POST', remove: 'DELETE', order: 'PUT' };
  return libraryCall(methods[action], playlistPath(playlist.id) + paths[action],
    { body: { ...data, version: playlist.version } });
}

export async function favoriteStates(songs, sources = {}) {
  const groups = new Map();
  for (const song of songs) {
    const ref = trackRef(song);
    const key = ref.provider;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(ref);
  }
  const answers = await Promise.all([...groups].map(async ([platform, tracks]) => {
    const selected = [];
    for (let i = 0; i < tracks.length; i += 100) {
      const result = await libraryCall('POST', 'favorites/status', { platform, source: sources[platform],
        body: { tracks: tracks.slice(i, i + 100) } });
      selected.push(...result.selected.map(track => track.provider + ':' + track.id));
    }
    return selected;
  }));
  return new Set(answers.flat());
}

export const favoriteKey = song => {
  const { provider, id } = trackRef(song);
  return provider + ':' + id;
};
