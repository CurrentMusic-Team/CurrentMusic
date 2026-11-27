// 首页：每日推荐 + 排行榜 + 猜你喜欢（登录后） + 最近播放（登录后）
import { kugouWrite } from '../kugou-library.js';
import { api, auth, ncmSongs, settings } from '../api.js';
import { esc, renderSongList, toast } from '../ui.js';
import { player } from '../player.js';
import { bindPlatformSelector, kugouDetailLink, kugouHome, musicPlatform, platformSelector } from '../music-platform.js';
import { levelBadge } from '../level-badges.js';
import { bindDailyShelf, homeDaily, homeIntro, homeShortcuts } from './home-sections.js';

// CurrentMusicX 推广横幅：第三方 Android 客户端，由本站超级管理员 bileizhen 开发维护。
// 下载走服务端镜像（/cm/x/download/latest，始终指向最新版），不依赖 GitHub 直连。
function cmxBannerHTML() {
  return `
    <a class="cm-cmx-banner" href="${esc(settings.base)}/x/download/latest">
      <span class="material-icons-outlined">smartphone</span>
      <span class="cm-cmx-t">
        <b>CurrentMusicX · 第三方 Android 客户端</b>
        <span class="cm-cmx-sub">由本站超级管理员 bileizhen 开发维护</span>
      </span>
      <span class="cm-cmx-go">下载<span class="material-icons-outlined">download</span></span>
    </a>`;
}

// 头像挂件更新横幅：仅在「已登录 + 听歌时长达标 + 尚未设置挂件」时出现，设好后自动消失
// （永久占位会打扰已设置的用户；未达标者由「我的」页设置行引导，首页不做催促）
function decorBannerFromMe(me) {
  if (!me || me.avatarDecoration || !me.decorUnlocked) return '';
  return `
    <a class="cm-decor-banner" href="#/user?decor=1">
      <span class="material-icons-outlined">face_retouching_natural</span>
      <span class="cm-decor-banner-t">
        <b>重大更新：听歌时长 2 小时及以上的用户现已支持设置动态头像挂件！</b>
        <span class="cm-decor-banner-sub">挂件会在个人主页、发现页等位置公开展示</span>
      </span>
      <span class="cm-decor-banner-go">点此设置<span class="material-icons-outlined">chevron_right</span></span>
    </a>`;
}

function accountBannersFromMe(me) {
  const trial = me?.level === 6 ? `
    <a class="cm-decor-banner" href="#/levels/trial">
      ${levelBadge(7)}
      <span class="cm-decor-banner-t">
        <b>通过试炼</b>
        <span class="cm-decor-banner-sub">答题及格即可解锁终阶徽章</span>
      </span>
      <span class="cm-decor-banner-go">开始答题<span class="material-icons-outlined">chevron_right</span></span>
    </a>` : '';
  return trial + decorBannerFromMe(me);
}

async function accountBannersHTML() {
  if (!auth.token) return '';
  try { return accountBannersFromMe(await api.me()); } catch (e) { return ''; }   // 未登录/限流/断网都静默不显示
}

// 首页大横幅：移植「翱翔鸟Music / Apple Music」首页的双列 hero
// （封面大图 + 从左侧压暗的遮罩 + 大标题，点击进歌单或直接播放）。
const HERO_TINT = [['#5B4BFF', '#FF3D71'], ['#00C2FF', '#FF6B4A']];
function heroHTML(daily, recPls, chartHref) {
  const items = [];
  (recPls || []).slice(0, 2).forEach((p, i) => {
    if (!p || !p.picUrl) return;
    items.push({
      kind: 'pl', href: chartHref(p.id), pic: p.picUrl, tint: HERO_TINT[i % 2],
      eyebrow: '精选歌单', title: p.name, sub: p.trackCount ? `${p.trackCount} 首` : '',
    });
  });
  (daily || []).forEach((s, idx) => {
    if (items.length >= 2 || !s || !s.pic) return;
    items.push({
      kind: 'song', songIdx: idx, pic: s.pic, tint: HERO_TINT[items.length % 2],
      eyebrow: '每日推荐', title: s.name, sub: s.artists || '',
    });
  });
  if (!items.length) return '';
  return renderHeroItems(items);
}

function renderHeroItems(items) {
  return `<section class="cm-hero">${items.map(it => {
    const inner = `<img class="cm-hero__art" src="${esc(it.pic)}" alt="" loading="lazy">
      <span class="cm-hero__veil" aria-hidden="true"></span>
      <span class="cm-hero__text">
        <span class="cm-hero__eyebrow">${esc(it.eyebrow)}</span>
        <span class="cm-hero__title">${esc(it.title)}</span>
        ${it.sub ? `<span class="cm-hero__sub">${esc(it.sub)}</span>` : ''}
      </span>`;
    return it.kind === 'pl'
      ? `<a class="cm-hero__card" href="${esc(it.href)}" style="--h1:${it.tint[0]};--h2:${it.tint[1]}">${inner}</a>`
      : `<button type="button" class="cm-hero__card" data-hero-song="${it.songIdx}" style="--h1:${it.tint[0]};--h2:${it.tint[1]}">${inner}</button>`;
  }).join('')}</section>`;
}

export async function render(el) {
  const platform = musicPlatform();
  const kugou = platform === 'kugou';
  const compact = localStorage.getItem('cm-home-style') === 'cmx-simple';
  const intro = homeIntro(platformSelector(platform));
  const bindSelector = () => bindPlatformSelector(el, () => render(el));
  el.innerHTML = `<div class="cm-home-layout">${intro}${homeDaily([], { loading: true, compact })}</div>`;
  bindSelector();
  const initialSelector = el.querySelector('.cm-platform-select');
  let charts = [
    { id: '3778678', name: '热歌榜' }, { id: '19723756', name: '飙升榜' }, { id: '3779629', name: '新歌榜' },
  ];
  const chartHref = id => kugou ? kugouDetailLink('playlist', id) : `#/ncmpl/${id}`;

  // 横幅不阻塞首屏：与每日推荐并行请求；结果先存起来，整页重渲染后再插入
  let bannerHTML = '';
  let daily = [], forYou = [], recent = [], artists = [];
  let recPls = [], ncmBound = false;
  // 首屏聚合（v1.28.3）：me/daily/bind/recent 一次请求取回，服务端并行查好。
  // 原先这四条要各自建立连接（移动网络下每次 TLS 握手都要付出代价）。
  // 旧服务端没有 /bootstrap → boot 为 null，走下面的回退分支，行为与之前完全一致。
  if (kugou) {
    const [data, banner] = await Promise.all([kugouHome(), accountBannersHTML()]);
    daily = data.daily;
    recent = data.recent;
    const mainRanks = ['TOP500', '飙升榜', '新歌榜'];
    charts = mainRanks.map(name => data.charts.find(item => item.name === name)).filter(Boolean);
    recPls = data.playlists.map(item => ({ ...item, picUrl: item.artwork }));
    bannerHTML = banner;
  } else {
    const boot = await api.bootstrap().catch(() => null);
    if (boot && boot.daily) {
      daily = boot.daily.daily || [];
      forYou = boot.daily.forYou || [];
      artists = boot.daily.artists || [];
      bannerHTML = accountBannersFromMe(boot.me);
      ncmBound = !!(boot.bind && boot.bind.bound);
      recent = (boot.recent && boot.recent.songs) || [];
    } else {
      const bannerJob = accountBannersHTML().then(html => { bannerHTML = html; });
      const jobs = [api.daily().then(d => { daily = d.daily || []; forYou = d.forYou || []; artists = d.artists || []; }).catch(() => {})];
      if (auth.token) jobs.push(api.bindStatus().then(b => { if (b && b.bound) ncmBound = true; }).catch(() => {}));
      if (auth.token) jobs.push(api.recentPlays(20).then(d => { recent = d.songs || []; }).catch(() => {}));
      await Promise.allSettled(jobs);
      await bannerJob;   // 横幅请求通常更快，这里几乎立即返回
    }
    // 每日推荐歌单/每日推荐歌曲是账号维度接口（T1）：必须用用户自己的网易云 cookie，
    // 且是**上游**接口，所以仍单独取（放进聚合会让整个首屏等上游）。未绑定则整体跳过。
    if (auth.token && ncmBound) {
      await Promise.allSettled([
        api.ncm('/recommend/resource').then(d => { recPls = (d.recommend || []).filter(p => /^[1-9][0-9]*$/.test(String(p?.id))); }),
        // T1：账号维度的每日推荐。拿到就用它覆盖服务端 SVIP 档（对已绑定用户更准确）
        api.ncm('/recommend/songs').then(d => {
          const own = ncmSongs(((d.data || {}).dailySongs) || []);
          if (own.length) daily = own;
        }),
      ]);
    }
  }

  if (!el.isConnected || el.querySelector('.cm-platform-select') !== initialSelector) return;
  const keepFocus = document.activeElement === initialSelector;
  const chartsUrl = kugou ? '#/kugou/charts' : '#/square';
  const hero = !compact && document.documentElement.dataset.uiPreset === 'soaringbird' ? heroHTML(daily, recPls, chartHref) : '';
  const login = !auth.token ? `<section class="cm-sec">
    <div class="cm-login-tip"><div>登录后同步点赞、收藏与歌单</div>
      <mdui-button variant="filled" href="#/user">去登录</mdui-button></div>
  </section>` : '';

  const sections = compact ? '' : `
    ${homeShortcuts(chartsUrl)}
    ${hero}
    <div class="cm-home-columns${recent.length && forYou.length ? ' has-pair' : ''}">
      ${forYou.length ? `<section class="cm-sec">
        <div class="cm-sec-head"><h2>猜你喜欢</h2>${artists.length ? `<span class="cm-sec-sub">${esc(artists.join('、'))}</span>` : ''}</div>
        <div id="forYouList"></div>
        <button type="button" class="cm-more-row" id="forYouMore" hidden></button>
      </section>` : ''}
      ${recent.length ? `<section class="cm-sec">
        <div class="cm-sec-head"><h2>最近播放</h2>
          <button type="button" class="cm-sec-more" id="playRecent"><span class="material-icons-outlined" aria-hidden="true">play_circle</span>播放全部</button>
        </div>
        <div id="recentList"></div>
        <button type="button" class="cm-more-row" id="recentMore" hidden></button>
      </section>` : ''}
    </div>
    ${recPls.length ? `<section class="cm-sec">
      <div class="cm-sec-head"><h2>每日推荐歌单</h2></div>
      <div class="cm-plgrid" id="recPlsGrid">${recPls.slice(0, 6).map(pl => `
        <a class="cm-plcard" href="${esc(chartHref(pl.id))}">
          <div class="cm-plcover">${pl.picUrl ? `<img src="${esc(pl.picUrl)}" loading="lazy" alt="" onerror="this.remove()">` : ''}<span class="material-icons-outlined" aria-hidden="true">queue_music</span></div>
          <div class="cm-plname">${esc(pl.name)}</div>
          ${pl.trackCount ? `<div class="cm-plsub">${esc(pl.trackCount)} 首</div>` : ''}
        </a>`).join('')}</div>
    </section>` : ''}
    ${charts.length ? `<section class="cm-sec">
      <div class="cm-sec-head"><h2>排行榜</h2><a class="cm-sec-more" href="${chartsUrl}">更多榜单</a></div>
      <div class="cm-quickrow">
        ${charts.map((chart, i) => `<a class="cm-quick" href="${esc(chartHref(chart.id))}">
          <span class="material-icons-outlined" aria-hidden="true">${['local_fire_department', 'trending_up', 'star'][i]}</span><b>${esc(chart.name)}</b></a>`).join('')}
      </div>
    </section>` : ''}
    ${forYou.length ? `<section class="cm-sec">
      <div class="cm-sec-head"><h2>一起听</h2><a class="cm-sec-more" href="#/rooms">房间广场</a></div>
      <div class="cm-quickrow">
        <a class="cm-quick" href="#/rooms"><span class="material-icons-outlined" aria-hidden="true">meeting_room</span><b>加入房间</b></a>
        <button type="button" class="cm-quick" id="quickCreateRoom"><span class="material-icons-outlined" aria-hidden="true">add_circle</span><b>创建房间</b></button>
      </div>
    </section>` : ''}
    <div class="cm-home-footer">${bannerHTML}${cmxBannerHTML()}</div>`;
  el.innerHTML = `<div class="cm-home-layout">${intro}${homeDaily(daily, { compact })}${sections}${login}</div>`;
  bindSelector();
  if (keepFocus) el.querySelector('.cm-platform-select')?.focus({ preventScroll: true });
  const playDaily = index => player.playList(daily, index);
  bindDailyShelf(el, playDaily, () => render(el));
  if (compact) return;

  el.querySelectorAll('.cm-hero__card[data-hero-song]').forEach(button => {
    button.onclick = () => playDaily(Number(button.dataset.heroSong));
  });
  const cmxBanner = el.querySelector('.cm-cmx-banner');
  if (cmxBanner && typeof window.NativeApi?.downloadApk === 'function') {
    cmxBanner.addEventListener('click', event => {
      event.preventDefault();
      try {
        window.NativeApi.downloadApk(cmxBanner.href, 'CurrentMusicX.apk');
        toast('开始下载 CurrentMusicX…');
      } catch {
        location.href = cmxBanner.href;
      }
    });
  }
  el.querySelector('#quickHeart')?.addEventListener('click', () => {
    const seed = recent[0] || daily[0];
    if (!seed) return toast('需要先有播放记录或每日推荐才能起播心动模式');
    player.startHeart(seed);
  });
  el.querySelector('#quickCreateRoom')?.addEventListener('click', () => {
    import('./rooms.js').then(module => module.createRoomDialog());
  });
  el.querySelector('#playRecent')?.addEventListener('click', () => player.playList(recent, 0));

  if (kugou ? auth.token : ncmBound) {
    el.querySelectorAll('#dailyRow .cm-card').forEach(card => {
      const song = daily[Number(card.dataset.i)];
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'cm-home-track-dismiss material-icons-outlined';
      button.textContent = 'block';
      button.setAttribute('aria-label', `对《${song.name}》标记不感兴趣`);
      button.title = '不感兴趣';
      button.onclick = async () => {
        button.disabled = true;
        try {
          if (kugou) await kugouWrite('roaming.skip', { id: song.kugou_id });
          else await api.ncm('/recommend/songs/dislike', { id: song.ncm_id, confirm: 1 });
          toast('已标记不感兴趣');
          card.remove();
        } catch (error) {
          toast('操作失败：' + error.message);
          button.disabled = false;
        }
      };
      card.appendChild(button);
    });
  }

  const previewCount = 5;
  async function bindCollapsible(boxId, moreId, songs) {
    const box = el.querySelector('#' + boxId);
    const more = el.querySelector('#' + moreId);
    if (!box) return;
    let expanded = false;
    const rest = songs.length - previewCount;
    async function draw() {
      await renderSongList(box, expanded ? songs : songs.slice(0, previewCount), {
        onPlay: index => player.playList(songs, index),
      });
      more.hidden = rest <= 0;
      more.setAttribute('aria-expanded', String(expanded));
      more.innerHTML = expanded
        ? '<span class="material-icons-outlined cm-rot180" aria-hidden="true">keyboard_arrow_down</span> 收起'
        : `查看全部（还有 ${rest} 首）<span class="material-icons-outlined" aria-hidden="true">keyboard_arrow_down</span>`;
    }
    more.setAttribute('aria-controls', boxId);
    more.onclick = async () => { expanded = !expanded; await draw(); };
    await draw();
  }
  await Promise.all([
    bindCollapsible('forYouList', 'forYouMore', forYou),
    bindCollapsible('recentList', 'recentMore', recent),
  ]);
}
