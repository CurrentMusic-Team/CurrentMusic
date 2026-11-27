import { avatarHTML, esc, fmtListen, userTags } from './ui.js';
import { profileBannerHTML } from './profile-banner.js';

function editButton(id, label) {
  return `<button type="button" class="cm-profile-card-edit" id="${id}" aria-label="${label}" title="${label}">
    <span class="material-icons-outlined" aria-hidden="true">edit</span></button>`;
}

export function profileCardHTML(user, stat = {}, editable = false) {
  const avatar = avatarHTML(user, 90);
  const tags = userTags(user);
  const bio = user.bio ? esc(user.bio) : '';
  return `<section class="cm-community-profile cm-profile-card">
    ${profileBannerHTML(user, editable)}
    <div class="cm-profile-card-body">
      ${editable ? `<button type="button" id="avaWrap" class="cm-ava-wrap cm-profile-card-portrait" aria-label="更换头像">
        ${avatar}<span class="cm-ava-cam"><span class="material-icons-outlined" aria-hidden="true">photo_camera</span></span></button>`
        : `<div class="cm-profile-card-portrait" aria-hidden="true">${avatar}</div>`}
      <div class="cm-profile-card-heading">
        <h2>${esc(user.nickname || user.username || '')}</h2>${editable ? editButton('editNick', '修改昵称') : ''}
      </div>
      ${tags ? `<div class="cm-profile-card-tags">${tags}</div>` : ''}
      ${user.ipRegion ? `<div class="cm-profile-card-meta"><span class="material-icons-outlined" aria-hidden="true">location_on</span>IP 属地 ${esc(user.ipRegion)}</div>` : ''}
      ${editable && user.phone ? `<div class="cm-profile-card-meta"><span class="material-icons-outlined" aria-hidden="true">smartphone</span>${esc(user.phone)}</div>` : ''}
      ${!editable && user.created_at ? `<div class="cm-profile-card-meta">注册于 ${new Date(user.created_at * 1000).toLocaleDateString('zh-CN')}</div>` : ''}
      ${bio || editable ? `<div class="cm-profile-card-bio">
        ${bio ? `<span>${bio}</span>${editable ? editButton('editBio', '修改简介') : ''}` : '<button type="button" class="cm-profile-card-edit" id="editBio">添加简介</button>'}
      </div>` : ''}
      <dl class="cm-profile-card-counts">
        <div><dt>点赞</dt><dd>${esc(stat.likes ?? '—')}</dd></div>
        <div><dt>收藏</dt><dd>${esc(stat.favs ?? '—')}</dd></div>
        <div><dt>歌单</dt><dd>${esc(stat.playlists ?? '—')}</dd></div>
      </dl>
      <dl class="cm-profile-card-listening">
        <div><dt>听歌天数</dt><dd>${stat.playDays == null ? '—' : `${esc(stat.playDays)} 天`}</dd></div>
        <div><dt>听歌时长</dt><dd>${stat.listenMs == null ? '—' : fmtListen(stat.listenMs)}</dd></div>
      </dl>
      ${editable ? (user.id ? `<a class="cm-community-link cm-my-public-profile" href="#/u/${esc(user.id)}">查看公开主页</a>` : '')
        : '<div class="cm-social-actions" data-profile-social></div>'}
    </div>
  </section>`;
}
