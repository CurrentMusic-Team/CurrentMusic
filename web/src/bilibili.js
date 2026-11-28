import { auth, call, settings } from './api.js';
import { kugouScope } from './kugou.js';

export const BILIBILI_ID = /^BV[0-9a-zA-Z]{10}:[1-9][0-9]{0,17}$/;
export const BILIBILI_QUALITIES = [
  { key: 'auto', label: '自动' }, { key: 'dolby', label: '杜比全景声' }, { key: 'standard', label: 'AAC' },
];

export const isBilibiliSong = song => song?.provider === 'bilibili';

export function bilibiliQuality(value) {
  const key = 'cm.bilibili.quality:' + kugouScope();
  if (BILIBILI_QUALITIES.some(item => item.key === value)) localStorage.setItem(key, value);
  const saved = localStorage.getItem(key);
  return BILIBILI_QUALITIES.some(item => item.key === saved) ? saved : 'auto';
}

export function supportsBilibiliDolby() {
  return !!new Audio().canPlayType('audio/mp4; codecs="ec-3"');
}

export async function bilibiliRequest(operation, body) {
  const base = settings.base, token = auth.token;
  const result = await call(operation === 'capabilities' ? 'GET' : 'POST',
    operation === 'capabilities' ? '/bilibili/capabilities' : '/bilibili/v1/' + operation,
    { base, body, auth: operation.startsWith('playback.'), redirect: 'error' });
  if (settings.base !== base || auth.token !== token) throw new Error('账号或服务器已切换，请重试');
  return result;
}

export function bilibiliMediaUrl(value) {
  const base = new URL(settings.base);
  const url = new URL(value);
  const prefix = base.pathname.replace(/\/$/, '') + '/bilibili/media/';
  if (url.protocol !== 'https:' || url.origin !== base.origin || url.username || url.password || url.search || url.hash ||
      !url.pathname.startsWith(prefix) || !/^[A-Za-z0-9_-]{32}$/.test(url.pathname.slice(prefix.length))) {
    throw new Error('哔哩哔哩未返回有效的流声引擎播放地址');
  }
  return url.href;
}

export function bilibiliSong(item) {
  if (!BILIBILI_ID.test(item?.id || '')) return null;
  return { ncm_id: 'bili:' + item.id, provider: 'bilibili', bilibili_id: item.id, name: item.name,
    artists: item.uploader || '', pic: item.artwork, duration: item.durationMs, mv: item.id };
}

export async function resolveBilibiliSong(song, quality, options = {}) {
  try {
    const result = await bilibiliRequest('playback.resolve', { id: song.bilibili_id, quality,
      supportsDolby: options.supportsDolby ?? supportsBilibiliDolby() });
    return { ...result, url: bilibiliMediaUrl(result.url), level: result.quality, type: result.format, br: result.bitrate,
      autoSpatial: quality === 'auto' && result.quality === 'dolby' ? 'dolby' : '' };
  } catch (error) {
    error.noAutoNext = true;
    throw error;
  }
}

export function originalVideoUrl(id, page = 1) {
  return BILIBILI_ID.test(id) ? `https://www.bilibili.com/video/${id.split(':')[0]}/?p=${Number(page) || 1}` : '';
}

let dashLoading;
export function loadDash() {
  if (window.dashjs?.MediaPlayer) return Promise.resolve(window.dashjs);
  if (!dashLoading) {
    dashLoading = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = new URL('dashjs-5.3.0.min.js', document.baseURI).href;
      script.onload = () => window.dashjs?.MediaPlayer ? resolve(window.dashjs) : reject(new Error('DASH 播放器加载失败'));
      script.onerror = () => reject(new Error('DASH 播放器加载失败'));
      document.head.appendChild(script);
    }).catch(error => { dashLoading = null; throw error; });
  }
  return dashLoading;
}
