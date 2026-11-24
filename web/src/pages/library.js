import { api, auth, settings } from '../api.js';
import { allLibraryItems, libraryCall, libraryLink, libraryPreferences, platformLabels } from '../library-v2.js';
import { musicPlatform } from '../music-platform.js';
import { esc, promptDialog, skelGrid, toast } from '../ui.js';

export async function render(el) {
  const platform = musicPlatform();
  if (!auth.token) {
    el.innerHTML = `<div class="cm-login-tip page"><span class="material-icons-outlined">library_music</span>
      <div>登录后即可创建歌单和收藏歌曲</div><mdui-button variant="filled" href="#/user">去登录</mdui-button></div>`;
    return;
  }
  el.innerHTML = skelGrid(6);
  const guard = el.firstElementChild;
  const current = () => guard?.isConnected && musicPlatform() === platform;
  const token = auth.token, base = settings.base;
  try {
    const [{ providers }, snapshots] = await Promise.all([
      libraryPreferences(),
      platform === 'netease' ? api.myPlaylists().then(data => data.playlists.filter(row => row.source === 'ncm')).catch(() => []) : [],
    ]);
    const selection = providers[platform];
    const sourceName = selection.source === 'native' ? platformLabels[platform] + ' App' : '流声聚合歌单';
    const result = await allLibraryItems('playlists', { platform, source: selection.source }).catch(error => error);
    if (!current() || token !== auth.token || base !== settings.base) return;
    const cards = Array.isArray(result) ? result.filter(playlist => !playlist.liked).map(playlist => `
      <a class="cm-plcard" href="${libraryLink(esc(playlist.id))}">
        <div class="cm-plcover">${playlist.artwork ? `<img src="${esc(playlist.artwork)}" loading="lazy" alt="">` : ''}<span class="material-icons-outlined">queue_music</span></div>
        <div class="cm-plname">${esc(playlist.name)}</div><div class="cm-plsub">${playlist.trackCount} 首</div>
      </a>`).join('') : `<div class="cm-empty small">${esc(result.message)} <a href="#/user">管理绑定</a></div>`;
    el.innerHTML = `
      <div class="cm-quickrow"><a class="cm-quick" href="#/library/favorites"><span class="material-icons-outlined">favorite</span><b>我喜欢</b><i>${sourceName}</i></a></div>
      <div class="cm-sec-head"><h2>我的歌单</h2><a href="#/settings" class="cm-sec-more">${sourceName}</a>
        <a class="cm-sec-more" href="#/plaza">歌单广场</a>
        ${selection.writable ? '<button type="button" class="cm-sec-more" id="newPl">新建</button>' : ''}</div>
      <div class="cm-plgrid" id="plGrid">${cards || '<div class="cm-empty small">还没有歌单</div>'}</div>
      ${!selection.writable && selection.binding === 'bound' ? '<p class="cm-empty small">当前可以浏览酷狗 App 歌单。可在设置中切换为流声聚合歌单进行编辑。</p>' : ''}
      ${snapshots.length ? `<details><summary>网易云旧同步快照</summary><div class="cm-setting-list">${snapshots.map(p => `
        <div class="cm-setting"><a href="#/pl/${esc(p.id)}">${esc(p.name)}</a>
          <button type="button" class="cm-sec-more" data-import="${esc(p.id)}">复制到聚合歌单</button></div>`).join('')}</div></details>` : ''}`;
    el.querySelector('#newPl')?.addEventListener('click', () => promptDialog({ title: '新建歌单', label: '歌单名称', onOk: async name => {
      await libraryCall('POST', 'playlists', { platform, source: selection.source, body: { name } });
      toast('歌单已创建');
      render(el);
    }}));
    el.querySelectorAll('[data-import]').forEach(button => button.onclick = async () => {
      button.disabled = true;
      try {
        const { resource } = await libraryCall('POST', 'playlists/import', { body: { snapshotId: button.dataset.import } });
        location.hash = libraryLink(resource.id);
      } catch (error) { toast(error.message); button.disabled = false; }
    });
  } catch (error) {
    if (current()) el.innerHTML = `<div class="cm-empty">${esc(error.message)}</div>`;
  }
}
