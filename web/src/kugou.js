import { auth, call, settings } from './api.js';

const TRACK_ID = /^[a-f0-9]{32}-[1-9][0-9]{0,14}$/;
export const KUGOU_QUALITIES = [
  { key: 'auto', label: '自动' }, { key: 'standard', label: '标准' },
  { key: 'exhigh', label: '高品质' }, { key: 'lossless', label: '无损' },
];

export function isKugouSong(song) { return song?.provider === 'kugou'; }

export function kugouScope() { return JSON.stringify([settings.base, auth.user?.id ?? '']); }

export function kugouQuality(value) {
  const key = 'cm.kugou.quality:' + kugouScope();
  if (KUGOU_QUALITIES.some(item => item.key === value)) localStorage.setItem(key, value);
  const saved = localStorage.getItem(key);
  return KUGOU_QUALITIES.some(item => item.key === saved) ? saved : 'auto';
}

export async function kugouRequest(operation, body) {
  const base = settings.base, token = auth.token;
  const needsAuth = /^(playback|library|history)\./.test(operation) || operation === 'discover.recommendations';
  const response = await call(operation === 'capabilities' ? 'GET' : 'POST',
    operation === 'capabilities' ? '/kugou/capabilities' : '/kugou/v1/' + operation,
    { base, body, auth: needsAuth, keepalive: operation === 'history.record', redirect: 'error' });
  if (base !== settings.base || token !== auth.token) throw new Error('账号或服务器已切换，请重试');
  return response;
}

export function kugouSong(item) {
  if (!TRACK_ID.test(item?.id || '')) return null;
  return { ncm_id: 'kg:' + item.id, provider: 'kugou', kugou_id: item.id, name: item.name,
    artists: item.artists, album: item.album, pic: item.artwork, duration: item.durationMs, lyricMatch: item.snippet };
}

export async function resolveKugouSong(song, level) {
  try {
    const quality = KUGOU_QUALITIES.some(item => item.key === level) ? level : kugouQuality();
    const result = await kugouRequest('playback.resolve', { id: song.kugou_id, quality });
    const base = new URL(settings.base);
    const url = new URL(result.url);
    if (url.protocol !== 'https:' || url.origin !== base.origin || url.username || url.password ||
        !url.pathname.startsWith(base.pathname.replace(/\/$/, '') + '/kugou/media/')) {
      throw new Error('酷狗未返回有效的流声引擎播放地址');
    }
    return { url: url.href, provider: 'kugou', level: result.quality, type: result.format, br: result.bitrate };
  } catch (error) {
    error.noAutoNext = true;
    throw error;
  }
}
