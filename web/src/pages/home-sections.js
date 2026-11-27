import { esc } from '../ui.js';
import './home-sections.css';

const dateFormat = new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric', weekday: 'long' });
const icon = name => `<span class="material-icons-outlined" aria-hidden="true">${name}</span>`;

export function homeIntro(platformControl) {
  return `<header class="cm-home-intro">
    <div class="cm-home-date">${dateFormat.format(new Date())}</div>
    <h1>今天想听点什么？</h1>
    <div class="cm-home-finder">
      <a class="cm-home-search" href="#/search">${icon('search')}<span>搜索歌曲、歌手或歌单</span></a>
      ${platformControl}
    </div>
  </header>`;
}

export function homeDaily(songs = [], { loading = false, compact = false } = {}) {
  const items = songs.slice(0, compact ? 3 : 12);
  const art = items.find(song => song.pic)?.pic;
  return `<section class="cm-home-daily${compact ? ' cm-home-daily-compact' : ''}" aria-labelledby="homeDailyTitle"
      ${loading ? 'aria-busy="true"' : ''}>
    ${art ? `<img class="cm-home-daily-backdrop" src="${esc(art)}" alt="" onerror="this.remove()">` : ''}
    <div class="cm-home-daily-head">
      <div><h2 id="homeDailyTitle">每日推荐</h2>${items.length ? `<span class="cm-home-daily-count">${songs.length} 首</span>` : ''}</div>
      ${items.length ? `<div class="cm-home-daily-actions">
        ${compact ? '' : `<div class="cm-home-rail-controls">
          <button type="button" data-daily-shift="-1" aria-label="上一组推荐" disabled>${icon('chevron_left')}</button>
          <button type="button" data-daily-shift="1" aria-label="下一组推荐" disabled>${icon('chevron_right')}</button>
        </div>`}
        <button type="button" class="cm-home-play-all" id="playDaily">${icon('play_arrow')}播放全部</button>
      </div>` : ''}
    </div>
    ${loading ? `<div class="cm-home-daily-rail" aria-label="正在加载每日推荐">
      ${Array.from({ length: compact ? 3 : 4 }, () => '<div class="cm-card"><div class="cm-home-art sk"></div><div class="sk cm-home-skel-name"></div></div>').join('')}
    </div>` : items.length ? `<div class="cm-home-daily-rail" id="dailyRow" aria-label="每日推荐歌曲">
      ${items.map((song, index) => `<article class="cm-card" data-i="${index}">
        <button type="button" class="cm-home-track" data-daily-song="${index}">
          <span class="cm-home-art">
            ${icon('music_note')}
            ${song.pic ? `<img src="${esc(song.pic)}" alt=""${index > 3 ? ' loading="lazy"' : ''} onerror="this.remove()">` : ''}
            <span class="cm-home-track-play">${icon('play_arrow')}</span>
          </span>
          <span class="cm-card-name">${esc(song.name)}</span>
          <span class="cm-card-sub">${esc(song.artists)}</span>
        </button>
      </article>`).join('')}
    </div>` : `<div class="cm-home-daily-empty">
      ${icon('library_music')}<span>今日推荐暂不可用</span><button type="button" data-daily-retry>重新加载</button>
    </div>`}
  </section>`;
}

export function homeShortcuts(chartUrl) {
  return `<div class="cm-home-shortcuts">
    <div class="cm-home-listen">
      <a href="#/podcast" class="cm-home-listen-card">
        <span class="cm-home-listen-icon">${icon('radio')}</span>
        <span><b>私人 FM</b><small>连续听歌</small></span>${icon('chevron_right')}
      </a>
      <button type="button" class="cm-home-listen-card" id="quickHeart">
        <span class="cm-home-listen-icon">${icon('auto_awesome')}</span>
        <span><b>心动模式</b><small>个性化续播</small></span>${icon('chevron_right')}
      </button>
    </div>
    <nav class="cm-home-library" aria-label="音乐快捷入口">
      <a href="#/library/favorites">${icon('favorite_border')}<span>我喜欢</span></a>
      <a href="#/library">${icon('queue_music')}<span>我的歌单</span></a>
      <a href="${chartUrl}">${icon('leaderboard')}<span>排行榜</span></a>
      <a href="#/rooms">${icon('headphones')}<span>一起听</span></a>
    </nav>
  </div>`;
}

export function bindDailyShelf(el, play, retry) {
  el.querySelector('[data-daily-retry]')?.addEventListener('click', retry);
  el.querySelector('#playDaily')?.addEventListener('click', () => play(0));
  const row = el.querySelector('#dailyRow');
  if (!row) return;
  for (const button of row.querySelectorAll('[data-daily-song]')) {
    button.onclick = () => play(Number(button.dataset.dailySong));
  }
  const controls = [...el.querySelectorAll('[data-daily-shift]')];
  if (!controls.length) return;
  const [back, forward] = controls;
  const update = () => {
    back.disabled = row.scrollLeft <= 3;
    forward.disabled = row.scrollWidth - row.clientWidth - row.scrollLeft <= 3;
  };
  for (const button of controls) {
    button.onclick = () => { row.scrollLeft += Number(button.dataset.dailyShift) * row.clientWidth; };
  }
  row.addEventListener('scroll', update, { passive: true });
  if (typeof ResizeObserver !== 'undefined') {
    const observer = new ResizeObserver(() => {
      if (row.isConnected) update();
      else observer.disconnect();
    });
    observer.observe(row);
  }
  update();
}
