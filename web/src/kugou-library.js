import { kugouRequest } from './kugou.js';

export const kugouWrite = (operation, body) => kugouRequest(operation, { ...body, confirm: true });

export const kugouPersonalLink = id => id
  ? '#/kugou/me/playlist/' + encodeURIComponent(id)
  : '#/kugou/me/favorites';

export async function kugouPlaylists() {
  const playlists = [];
  let offset = 0;
  do {
    const page = await kugouRequest('library.playlists', { offset, limit: 50 });
    playlists.push(...page.items.map(item => ({ ...item, cover: item.artwork, track_count: item.trackCount })));
    offset = page.nextOffset;
  } while (offset != null);
  return playlists;
}

export async function kugouAddTracks(id, tracks) {
  for (let offset = 0; offset < tracks.length; offset += 50) {
    const trackIds = tracks.slice(offset, offset + 50).map(track => track.kugou_id);
    await kugouWrite('library.playlist.tracks', { id, trackIds, selected: true });
  }
}
