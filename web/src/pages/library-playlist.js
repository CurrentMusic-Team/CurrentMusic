import { mdui } from '../md.js';
import { player } from '../player.js';
import { libraryCall, libraryLink, libraryTrack, playlistPath, trackRef, writePlaylist } from '../library-v2.js';
import { musicPlatform } from '../music-platform.js';
import { esc, renderSongList, skelList, toast, promptDialog, confirmDialog } from '../ui.js';

export async function render(el, [kind, id] = []) {
  el.innerHTML = skelList(5);
  const platform = musicPlatform();
  const route = location.hash;
  const guard = el.firstElementChild;
  const current = () => guard?.isConnected && route === location.hash;
  try {
    if (kind === 'alias') {
      const { resource } = await libraryCall('GET', 'aliases/' + id);
      if (current()) location.replace(libraryLink(resource.id));
      return;
    }
    const favorites = kind === 'favorites';
    const endpoint = favorites ? 'favorites' : kind === 'shared' ? 'shared/' + id : playlistPath(id);
    let version, resource;
    const songs = [];
    const fetchPage = async offset => {
      const page = await libraryCall('GET', endpoint, { platform, offset });
      if (version !== undefined && page.resource?.version !== version) throw new Error('歌单已变化，请刷新后重试');
      resource = page.resource || resource;
      version = resource?.version;
      songs.push(...(page.items || page.tracks).map(libraryTrack).filter(Boolean));
      return page.nextOffset;
    };
    let next = await fetchPage(0);
    if (!current()) return;
    el.innerHTML = `<div class="cm-sec-head"><h2>${esc(resource?.name || '我喜欢')}</h2>${resource?.description ? `<span class="cm-sec-sub">${esc(resource.description)}</span>` : ''}</div>
      <div class="cm-quickrow"><mdui-button id="libraryPlay" variant="filled">播放全部</mdui-button>
      ${resource?.editable ? '<mdui-button id="libraryEdit" variant="tonal">编辑</mdui-button><mdui-button id="libraryReorder" variant="text">排序</mdui-button>' : ''}
      ${resource?.source === 'aggregate' && resource.editable ? `<mdui-button id="libraryShare" variant="text">${resource.shared ? '分享管理' : '开启分享'}</mdui-button>` : ''}</div>
      <div id="librarySongs"></div><mdui-button id="libraryMore" variant="text" ${next === null ? 'hidden' : ''}>加载更多</mdui-button>`;
    const list = el.querySelector('#librarySongs');
    const refresh = () => render(el, [kind, id]);
    const play = i => player.playList(songs, i);
    const remove = resource?.editable ? i => confirmDialog({ title: '移除此曲目？', onOk: async () => {
      await writePlaylist(resource, 'remove', { tracks: [trackRef(songs[i])] });
      refresh();
    }}) : undefined;
    const paint = from => renderSongList(list, songs, { from, onPlay: play, onRemove: remove, deferStatus: true,
      onLikeChange: favorites ? refresh : undefined });
    await paint(0);
    if (!list.isConnected) return;
    const all = el.querySelector('#libraryPlay');
    const more = el.querySelector('#libraryMore');
    all.onclick = async () => {
      if (more.disabled) return;
      all.disabled = more.disabled = true;
      const from = songs.length;
      try {
        while (next !== null) next = await fetchPage(next);
        if (!list.isConnected) return;
        if (songs.length > from) await paint(from);
        more.hidden = next === null;
        if (songs.length) play(0);
      } catch (error) { toast(error.message); }
      finally { all.disabled = more.disabled = false; }
    };
    more.onclick = async () => {
      more.disabled = true;
      try {
        const from = songs.length;
        next = await fetchPage(next);
        if (list.isConnected) await paint(from);
        more.hidden = next === null;
      } catch (error) { toast(error.message); }
      finally { more.disabled = false; }
    };
    el.querySelector('#libraryEdit')?.addEventListener('click', () => {
      const dialog = mdui.dialog({ headline: '编辑歌单', body: `
        <mdui-text-field id="libraryName" label="名称" maxlength="60" value="${esc(resource.name)}"></mdui-text-field>
        <mdui-text-field id="libraryDesc" label="简介" maxlength="300" value="${esc(resource.description || '')}"></mdui-text-field>`, actions: [
          { text: '删除', onClick: () => confirmDialog({ title: '删除歌单？', onOk: async () => {
            await writePlaylist(resource, 'delete'); location.hash = '#/library';
          }}) },
          { text: '取消' },
          { text: '保存', onClick: async () => {
            try {
              await writePlaylist(resource, 'edit', { name: dialog.querySelector('#libraryName').value,
                description: dialog.querySelector('#libraryDesc').value });
              refresh();
            } catch (error) { toast(error.message); return false; }
          } },
        ] });
    });
    el.querySelector('#libraryReorder')?.addEventListener('click', async () => {
      if (more.disabled) return;
      more.disabled = true;
      try {
        while (next !== null) next = await fetchPage(next);
        if (!list.isConnected) return;
        const { openPlaylistOrder } = await import('../library-order.js');
        openPlaylistOrder(resource, songs, refresh);
      } catch (error) { toast(error.message); }
      finally { more.disabled = false; }
    });
    el.querySelector('#libraryShare')?.addEventListener('click', () => confirmDialog({
      title: resource.shared ? '关闭此歌单的公开分享？' : '公开分享此歌单？',
      body: resource.shared ? '可以先复制已启用的分享链接。' : '持有链接的人都可以查看歌曲列表。',
      onOk: async () => {
        const shared = !resource.shared;
        await writePlaylist(resource, 'edit', { shared });
        if (shared) await copyLink(resource);
        refresh();
      },
    }));
    if (resource?.shared && resource.editable) el.querySelector('.cm-quickrow').insertAdjacentHTML('beforeend', '<mdui-button id="libraryCopy" variant="text">复制链接</mdui-button>');
    el.querySelector('#libraryCopy')?.addEventListener('click', () => copyLink(resource));
  } catch (error) { if (current()) el.innerHTML = `<div class="cm-empty">${esc(error.message)}</div>`; }
}

async function copyLink(resource) {
  const url = new URL(location.href);
  url.hash = '#/library/shared/' + resource.id.split(':')[1];
  try { await navigator.clipboard.writeText(url.href); toast('已复制歌单链接'); }
  catch { promptDialog({ title: '歌单链接', label: '链接', value: url.href, onOk: () => {} }); }
}
