// 公开用户主页：资料 + 统计（点赞/歌单/听歌天数/累计时长）+ 当前在听 + 最近听过 + TA 的歌单
import { api, auth } from '../api.js';
import { mountProfileSocial } from './social.js';
import { esc, skelProfile } from '../ui.js';
import { player } from '../player.js';
import { mountPosts } from '../posts.js';
import { profileCardHTML } from '../profile-card.js';

export async function render(el, params) {
  const uid = params[0];
  const showPosts = params.query?.tab === 'posts';
  el.innerHTML = skelProfile();
  let d;
  try {
    d = await api.userProfile(uid);
  } catch (e) {
    el.innerHTML = `<div class="cm-empty">${esc(e.message)}</div>`;
    return;
  }
  const u = d.user, st = d.stat;
  const songRow = (s, i) => `
    <div class="cm-song" data-i="${i}">
      <div class="cm-song-cover">${s.pic ? `<img src="${esc(s.pic)}" loading="lazy">` : '<span class="material-icons-outlined">music_note</span>'}</div>
      <div class="cm-song-main">
        <div class="cm-song-name">${esc(s.name)}</div>
        <div class="cm-song-sub">${esc(s.artists)}</div>
      </div>
    </div>`;

  el.innerHTML = `
    ${profileCardHTML(u, st)}
    <nav class="cm-profile-tabs" aria-label="个人主页">
      <a href="#/u/${uid}" ${showPosts ? '' : 'aria-current="page"'}>音乐</a>
      <a href="#/u/${uid}?tab=posts" ${showPosts ? 'aria-current="page"' : ''}>帖子</a>
    </nav>
    ${showPosts ? '<div class="cm-profile-posts"></div>' : `
    ${d.current ? `
    <div class="cm-sec-head"><h2>当前在听</h2></div>
    <div class="cm-up-now">
      <span class="material-icons-outlined graphic_eq">graphic_eq</span>
      <div class="cm-up-now-main">
        <div class="cm-up-now-name">${esc(d.current.name)}</div>
        <div class="cm-up-now-sub">${esc(d.current.artists)}</div>
      </div>
      <mdui-button variant="tonal" compact id="nowPlay"><span class="material-icons-outlined">play_arrow</span>一起听</mdui-button>
    </div>` : ''}
    ${(d.followed || []).length ? `
    <div class="cm-sec-head"><h2>关注的歌手</h2><span class="cm-sec-sub">${d.followedCount || d.followed.length} 位 · 来自网易云</span></div>
    <div class="cm-artist-grid cm-artist-grid-compact" id="upFollow">
      ${d.followed.map(a => `
        <div class="cm-artist-card" data-aid="${a.artist_id}">
          <div class="cm-artist-ava">${a.pic ? `<img src="${esc(a.pic)}?param=160y160" loading="lazy">` : '<span class="material-icons-outlined">person</span>'}</div>
          <div class="cm-artist-name">${esc(a.name || '歌手')}</div>
        </div>`).join('')}
    </div>` : ''}
    ${d.recent.length ? `
    <div class="cm-sec-head"><h2>最近听过</h2></div>
    <div id="upRecent">${d.recent.map((s, i) => songRow(s, i)).join('')}</div>` : ''}
    ${d.playlists.length ? `
    <div class="cm-sec-head"><h2>TA 的歌单</h2></div>
    <div class="cm-plgrid">
      ${d.playlists.map(p => `
        <div class="cm-plcard" data-id="${p.id}">
          <div class="cm-plcover">${p.cover ? `<img src="${esc(p.cover)}" loading="lazy">` : ''}<span class="material-icons-outlined">queue_music</span>${p.source === 'ncm' ? '<span class="cm-ncmbadge">网易云</span>' : ''}</div>
          <div class="cm-plname">${esc(p.name)}</div>
          <div class="cm-plsub">${p.track_count} 首</div>
        </div>`).join('')}
    </div>` : ''}`}
  `;

  if (auth.token && Number(uid) !== Number(auth.user?.id)) {
    mountProfileSocial(el.querySelector('[data-profile-social]'), Number(uid));
  }
  if (showPosts) {
    mountPosts(el.querySelector('.cm-profile-posts'), { author: uid });
    return;
  }

  if (d.current) {
    el.querySelector('#nowPlay').onclick = () => player.playList([d.current], 0);
  }
  el.querySelectorAll('#upRecent .cm-song').forEach(r => {
    r.onclick = () => player.playList(d.recent, +r.dataset.i);
  });
  el.querySelectorAll('#upFollow .cm-artist-card').forEach(c => {
    c.onclick = () => { location.hash = `#/artist/${c.dataset.aid}`; };
  });
  el.querySelectorAll('.cm-plcard').forEach(c => {
    c.onclick = () => { location.hash = `#/pl/${c.dataset.id}`; };
  });
}
