import { mdui } from './md.js';
import { trackRef, writePlaylist } from './library-v2.js';
import { esc, toast } from './ui.js';

export function openPlaylistOrder(playlist, songs, onSave) {
  const ordered = songs.slice();
  const dialog = mdui.dialog({ headline: '调整曲序', body: '<div class="cm-qm" id="libraryOrder"></div>', actions: [
    { text: '取消' },
    { text: '保存', onClick: async () => {
      try { await writePlaylist(playlist, 'order', { tracks: ordered.map(trackRef) }); onSave(); }
      catch (error) { toast(error.message); return false; }
    } },
  ] });
  const list = dialog.querySelector('#libraryOrder');
  const paint = () => {
    list.innerHTML = ordered.map((song, i) => `<div class="cm-qm-item">
      <div>${i + 1}. ${esc(song.name)}<div class="sub">${esc(song.artists || '')}</div></div>
      <button type="button" data-index="${i}" data-step="-1" ${i === 0 ? 'disabled' : ''} aria-label="上移">↑</button>
      <button type="button" data-index="${i}" data-step="1" ${i + 1 === ordered.length ? 'disabled' : ''} aria-label="下移">↓</button></div>`).join('');
  };
  list.onclick = event => {
    const button = event.target.closest('[data-index]');
    if (!button) return;
    const from = Number(button.dataset.index), to = from + Number(button.dataset.step);
    [ordered[from], ordered[to]] = [ordered[to], ordered[from]];
    paint();
    list.querySelector(`[data-index="${to}"][data-step="${button.dataset.step}"]:not(:disabled)`)?.focus();
  };
  paint();
}
