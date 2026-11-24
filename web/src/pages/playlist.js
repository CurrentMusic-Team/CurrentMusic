// 列表详情页：自己的歌单 / likes / favs / 每日推荐 / NCM 排行榜（#/ncmpl/:pid）
import { api, auth } from '../api.js';
import { esc, renderSongList, toast, confirmDialog, setStatus, skelList, skelDetail } from '../ui.js';
import { player } from '../player.js';
import { kugouRequest } from '../kugou.js';
import { kugouPersonalLink, kugouWrite } from '../kugou-library.js';
import { kugouDaily, kugouDetailLink, kugouSongs, musicPlatform } from '../music-platform.js';
import { addToPlaylist } from '../player-ui.js';
import { openShare } from '../share.js';
import { getAimoChartCover } from '../aimo-charts.js';

export async function render(el, params) {
  const kind = params[0]; // pl | ncmpl | special
  const id = params[1];
  if (kind === 'kugou-charts') return renderKugouCharts(el);
  const favorites = kind === 'kugou-favorites' || (kind === 'pl' && id === 'likes' && musicPlatform() === 'kugou');
  const personal = kind === 'kugou-personal';
  const catalogKind = !personal && !favorites && kind.startsWith('kugou-') ? kind.slice(6) : '';
  const playlist = favorites || personal ? kugouPersonalLink(personal ? id : null)
    : catalogKind ? kugouDetailLink(catalogKind, id) : kind === 'daily' ? '#/daily' : '#/' + kind + '/' + id;

  el.innerHTML = skelDetail();
  let title = '', desc = '', songs = [], removable = false, owner = false, cover = '', badge = '';
  let shareKind = '', shareSub = '';
  let catalogAlbums = [], nextOffset = null, loadPage;
  if (favorites) loadPage = offset => kugouRequest('library.favorites', { offset });
  else if (personal) loadPage = offset => kugouRequest('library.playlist', { id, offset });
  else if (catalogKind) loadPage = offset => kugouRequest('catalog.' + catalogKind, { id, offset });

  try {
    if (loadPage) {
      const result = await loadPage(0);
      const resource = result.resource;
      title = favorites ? '我喜欢的音乐' : resource.name;
      cover = resource?.artwork || '';
      const count = favorites ? result.total : resource.trackCount;
      desc = [resource?.artists, resource?.description, count == null ? '' : `共 ${count} 首`].filter(Boolean).join(' · ');
      songs = kugouSongs(result.tracks || result.items);
      nextOffset = result.nextOffset;
      catalogAlbums = result.albums || [];
      if (catalogKind) shareKind = catalogKind;
      owner = personal;
      removable = favorites || personal;
    } else if (kind === 'pl' && id === 'likes') {
      if (!auth.token) throw new Error('请先登录');
      const d = await api.likedSongs();
      songs = d.songs || [];
      title = '我喜欢的音乐';
      desc = `共 ${songs.length} 首`;
      removable = true;
    } else if (kind === 'pl') {
      const p = await api.playlist(id);
      const isNcm = p.source === 'ncm';
      title = p.name;
      badge = isNcm ? '<span class="cm-ncmbadge">网易云</span>' : '';
      songs = p.tracks || [];
      desc = (p.description ? p.description + ' · ' : '') + `共 ${songs.length} 首`;
      cover = p.cover || '';
      shareKind = 'local-playlist';
      shareSub = 'CurrentMusic 歌单';
      owner = !isNcm && p.user_id === (auth.user && auth.user.id);
      removable = owner;
    } else if (kind === 'daily') {
      songs = musicPlatform() === 'kugou' ? await kugouDaily() : (await api.daily()).daily || [];
      title = '每日推荐'; desc = `今天为你准备了 ${songs.length} 首`;
    } else if (kind === 'ncmpl') {
      const p = await api.ncmPlaylist(id);
      title = p.name || '排行榜'; desc = `网易云 · 共 ${p.trackCount || (p.songs || []).length} 首`;
      songs = p.songs || [];
      cover = p.cover || '';
      shareKind = 'playlist';
      shareSub = p.creator || '网易云歌单';
    } else {
      throw new Error('未知列表');
    }
  } catch (e) {
    el.innerHTML = `<div class="cm-empty">${esc(e.message)}</div>`;
    return;
  }

  // 排序：网易云导入歌单默认倒序，其余默认正序；四种方式会话内记忆
  const SORTS = [
    { key: 'asc', label: '正序' },
    { key: 'desc', label: '倒序' },
    { key: 'name', label: '歌名' },
    { key: 'artist', label: '歌手' },
  ];
  const isNcm = kind === 'pl' && badge;
  let sortKey = sessionStorage.getItem('cm.plSort') || (isNcm ? 'desc' : 'asc');
  const originalSongs = songs;
  const applySort = key => {
    if (key === 'desc') return [...originalSongs].reverse();
    if (key === 'name') return [...originalSongs].sort((a, b) => (a.name || '').localeCompare(b.name || '', 'zh'));
    if (key === 'artist') return [...originalSongs].sort((a, b) => (a.artists || '').localeCompare(b.artists || '', 'zh'));
    return originalSongs;
  };
  songs = applySort(sortKey);
  const displayCover = (kind === 'ncmpl' && getAimoChartCover(id)) || cover;

  el.innerHTML = `
    <div class="cm-plsort" id="plSort">
      <span class="cm-plsort-l"><span class="material-icons-outlined">sort</span>排序</span>
      ${SORTS.map(s => `<mdui-chip ${s.key === sortKey ? 'selected' : ''} data-k="${s.key}">${s.label}</mdui-chip>`).join('')}
    </div>
    <div class="cm-detail-head">
      <div class="cm-detail-cover">${displayCover ? `<img src="${esc(displayCover)}" onerror="this.remove()">` : ''}<span class="material-icons-outlined">music_note</span></div>
      <div>
        <h2>${esc(title)} ${badge}</h2>
        <div class="cm-detail-sub">${esc(desc)}</div>
        <div class="cm-detail-actions">
          <mdui-button variant="filled" id="playAll"><span class="material-icons-outlined">play_arrow</span>播放全部</mdui-button>
          ${shareKind ? `<mdui-button variant="tonal" id="sharePl"><span class="material-icons-outlined">share</span>分享</mdui-button>` : ''}
          ${!owner ? '' : `<mdui-button variant="tonal" id="addSongs"><span class="material-icons-outlined">add</span>添加歌曲</mdui-button>`}
          ${kind === 'pl' || personal ? `<mdui-button variant="tonal" id="refreshPl"><span class="material-icons-outlined">refresh</span>刷新</mdui-button>` : ''}
          ${kind === 'ncmpl' ? `<mdui-button variant="tonal" id="plComments"><span class="material-icons-outlined">comment</span>评论</mdui-button>` : ''}
          ${kind === 'ncmpl' && auth.token ? `<mdui-button variant="tonal" id="editPl"><span class="material-icons-outlined">edit</span>编辑歌单</mdui-button>` : ''}
          ${kind === 'ncmpl' ? `<mdui-button variant="tonal" id="saveOrder"><span class="material-icons-outlined">save</span>保存排序</mdui-button>` : ''}
          ${owner ? `<mdui-button variant="tonal" id="delPl"><span class="material-icons-outlined">delete</span>删除</mdui-button>` : ''}
        </div>
      </div>
    </div>
    <div id="list"></div>
    ${nextOffset != null ? '<div class="cm-more-row"><mdui-button id="kgLoadMore" variant="text">加载更多</mdui-button></div>' : ''}
    ${catalogAlbums.length ? `<section class="cm-sec"><div class="cm-sec-head"><h2>专辑</h2></div>
      <div class="cm-hscroll">${catalogAlbums.map(album => `<a class="cm-card" href="${esc(kugouDetailLink('album', album.id))}">
        <img src="${esc(album.artwork)}" alt="" loading="lazy"><div class="cm-card-name">${esc(album.name)}</div></a>`).join('')}</div>
    </section>` : ''}
    <section class="cm-sec" id="plSimiSec" hidden><div class="cm-sec-head"><h2>相似歌单</h2></div><div class="cm-plgrid" id="plSimi"></div></section>`;

  let list = el.querySelector('#list');
  const attachSongList = (tracks) =>
    renderSongList(list, tracks, {
      deferStatus: true,
      likeSource: id === 'likes' ? 'local' : undefined,
      onLikeChange: favorites ? () => render(el, params) : undefined,
      onPlay: i => player.playList(tracks, i),
      onRemove: removable ? async i => {
        const s = tracks[i];
        try {
          if (favorites) {
            await kugouWrite('library.favorite.set', { id: s.kugou_id, selected: false });
            setStatus(s.ncm_id, { liked: false, faved: false });
          } else if (personal) {
            await kugouWrite('library.playlist.tracks', { id, trackIds: [s.kugou_id], selected: false });
          } else if (id === 'likes') await api.toggleLike(s);
          else await api.delPlaylistTracks(+id, [s.ncm_id]);
          render(el, params);
        } catch (e) { toast(e.message); }
      } : undefined,
    });

  el.querySelectorAll('#plSort mdui-chip').forEach(ch => {
    ch.onclick = () => {
      const k = ch.dataset.k;
      sessionStorage.setItem('cm.plSort', k);
      const isAimo = document.documentElement.getAttribute('data-ui-preset') === 'aimo';
      if (isAimo) {
        sortKey = k;
        el.querySelectorAll('#plSort mdui-chip').forEach(c => {
          if (c === ch) c.setAttribute('selected', '');
          else c.removeAttribute('selected');
        });
        songs = applySort(sortKey);
        // 旧列表上的红心状态更新或重绘只能写入已移出的节点。
        const next = list.cloneNode(false);
        list.replaceWith(next);
        list = next;
        attachSongList(songs);
        return;
      }
      render(el, params);
    };
  });

  // 把当前列表顺序写回网易云歌单（T2 写操作，需绑定 + confirm=1）
  const saveOrderBtn = el.querySelector('#saveOrder');
  if (saveOrderBtn) saveOrderBtn.onclick = async () => {
    const ids = (songs || []).map(s2 => s2.ncm_id).filter(Boolean);
    if (!ids.length) return toast('列表为空');
    if (!await confirmDialog(`把当前顺序保存到网易云歌单？\n（共 ${ids.length} 首，会覆盖云端顺序）`)) return;
    try {
      await api.ncm('/song/order/update', { pid: id, ids: ids.join(','), confirm: 1 });
      toast('已保存到网易云');
    } catch (e) { toast(e.message.includes('绑定') || /401/.test(e.message) ? '需先绑定网易云账号' : e.message); }
  };

  const plCmtBtn = el.querySelector('#plComments');
  if (plCmtBtn) plCmtBtn.onclick = () => {
    import('../comments.js').then(m => m.openComments({ type: 2, id, title })).catch(() => {});
  };

  // 编辑网易云歌单（阶段三）：改信息 / 收藏 / 增删曲目 / 排序 / 导入，全部走 T2 写接口
  const editBtn = el.querySelector('#editPl');
  if (editBtn) editBtn.onclick = () => {
    import('../playlist-edit.js')
      .then(m => m.openPlaylistEditor({ id, name: title, onChanged: () => render(el, params) }))
      .catch(e => toast('编辑器加载失败：' + e.message));
  };

  await attachSongList(songs);

  const more = el.querySelector('#kgLoadMore');
  if (more) more.onclick = async () => {
    more.disabled = true;
    try {
      const result = await loadPage(nextOffset);
      if (!more.isConnected) return;
      const known = new Set(originalSongs.map(song => song.kugou_id));
      originalSongs.push(...kugouSongs(result.tracks || result.items).filter(song => !known.has(song.kugou_id)));
      songs = applySort(sortKey);
      nextOffset = result.nextOffset;
      await attachSongList(songs);
      if (nextOffset == null) more.parentElement.remove();
    } catch (error) {
      toast(error.message);
    } finally {
      more.disabled = false;
    }
  };
  el.querySelector('#playAll').onclick = () => songs.length ? player.playList(songs, 0) : toast('列表为空');
  const sharePl = el.querySelector('#sharePl');
  if (sharePl) sharePl.onclick = () => openShare({
    provider: catalogKind ? 'kugou' : 'netease', type: shareKind, id, name: title, pic: cover, sub: shareSub,
    sub2: `共 ${songs.length} 首`,
  });

  // 相似歌单（/simi/playlist；上游该维度常返回空，空则不显示区块）
  if (kind === 'ncmpl') {
    api.ncm('/simi/playlist', { id, limit: 6, offset: 0 }).then(r => {
      const list = (r.playlists || []).slice(0, 6);
      if (!list.length) return;
      const sec = el.querySelector('#plSimiSec');
      sec.hidden = false;
      el.querySelector('#plSimi').innerHTML = list.map(pl => `
        <div class="cm-plcard" data-pid="${pl.id}">
          <div class="cm-plcover">${pl.coverImgUrl ? `<img src="${esc(pl.coverImgUrl)}?param=300y300" loading="lazy" onerror="this.remove()">` : ''}<span class="material-icons-outlined">queue_music</span></div>
          <div class="cm-plname">${esc(pl.name)}</div>
          <div class="cm-plsub">${pl.trackCount || 0} 首</div>
        </div>`).join('');
      el.querySelectorAll('#plSimi .cm-plcard').forEach(c => {
        c.onclick = () => { location.hash = `#/ncmpl/${c.dataset.pid}`; };
      });
    }).catch(() => {});
  }
  const refreshBtn = el.querySelector('#refreshPl');
  if (refreshBtn) refreshBtn.onclick = async () => {
    if (refreshBtn.dataset.loading) return;
    refreshBtn.dataset.loading = '1';
    refreshBtn.innerHTML = '<mdui-circular-progress style="--mdui-circular-progress-size:18px"></mdui-circular-progress> 刷新中…';
    try {
      // 重新进入当前歌单路由：render 顶部会重新调用 /playlists/:id API
      await render(el, params);
    } catch (e) {
      delete refreshBtn.dataset.loading;
      refreshBtn.innerHTML = '<span class="material-icons-outlined">refresh</span>刷新';
      toast('刷新歌单失败：' + e.message);
    }
  };
  const del = el.querySelector('#delPl');
  if (del) del.onclick = () => confirmDialog({
    title: `删除歌单「${title}」？`, body: '删除后不可恢复。',
    onOk: async () => {
      if (personal) await kugouWrite('library.playlist.delete', { id });
      else await api.deletePlaylist(+id);
      toast('已删除');
      location.hash = '#/library';
    },
  });
  const addBtn = el.querySelector('#addSongs');
  if (addBtn) addBtn.onclick = () => {
    location.hash = '#/search';
    toast('在搜索结果用「全部收入歌单」，或在播放页加入歌单');
  };
}


async function renderKugouCharts(el) {
  el.innerHTML = skelList(5);
  try {
    const { items } = await kugouRequest('discover.charts', {});
    el.innerHTML = `<section class="cm-sec"><div class="cm-sec-head"><h2>排行榜</h2></div>
      <div class="cm-plgrid">${items.map(item => `<a class="cm-plcard" href="${esc(kugouDetailLink('playlist', item.id))}">
        <div class="cm-plcover">${item.artwork ? `<img src="${esc(item.artwork)}" alt="" loading="lazy">` : ''}
          <span class="material-icons-outlined">trending_up</span></div>
        <div class="cm-plname">${esc(item.name)}</div></a>`).join('')}</div></section>`;
  } catch (error) {
    el.innerHTML = `<div class="cm-empty">${esc(error.message)}</div>`;
  }
}
