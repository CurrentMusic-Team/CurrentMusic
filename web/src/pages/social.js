import { auth, settings } from '../api.js';
import { closeChat, mountChat } from '../chat.js';
import { esc, avatarHTML, confirmDialog, promptDialog, toast } from '../ui.js';
import { openDirect, socialCall, socialURL } from '../social-api.js';
import './social.css';

const WORLD = { id: 1, kind: 'world', name: '世界聊天', description: '流声音乐官方公共聊天室' };
const ROLES = { owner: '群主', admin: '管理员', member: '成员' };
let disposeSocial = null;

export function closeSocial() {
  if (disposeSocial) {
    disposeSocial();
    disposeSocial = null;
  }
  closeChat();
}

const button = (action, label, id = '', variant = 'text') =>
  '<mdui-button compact variant="' + variant + '" data-action="' + action + '" data-id="' + id + '">' + label + '</mdui-button>';
const icon = name => '<span class="material-icons-outlined" aria-hidden="true">' + name + '</span>';
const empty = message => '<div class="cm-empty small" role="status">' + esc(message) + '</div>';
const formatTime = timestamp => {
  const d = new Date(timestamp * 1000);
  return d.toDateString() === new Date().toDateString()
    ? d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false })
    : d.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' });
};

const contactAction = (id, action) => socialCall('PUT', '/contacts/' + id, { action });
const reportFriend = id => promptDialog({
  title: '举报好友', label: '举报原因', maxlength: 500,
  onOk: async reason => {
    await socialCall('POST', '/contacts/' + id + '/report', { reason });
    toast('举报已提交');
  },
});

function contactButtons(id, state, canReport = false) {
  if (state === 'accepted') {
    return button('direct', '私聊', id, 'tonal') + button('remove-friend', '删除', id) + button('report', '举报', id);
  }
  if (state === 'incoming') return button('accept', '接受', id, 'tonal') + button('decline', '拒绝', id);
  if (state === 'outgoing') return button('cancel', '撤回申请', id) + (canReport ? button('report', '举报', id) : '');
  return button('request', '加好友', id, 'tonal') + (canReport ? button('report', '举报', id) : '');
}

function bindContactActions(host, update) {
  host.onclick = async event => {
    const action = event.target.closest('[data-action]');
    if (!action || action.loading || action.disabled) return;
    const id = Number(action.dataset.id);
    if (!id) return;
    const kind = action.dataset.action;
    if (kind === 'report') { reportFriend(id); return; }
    if (kind === 'remove-friend') {
      confirmDialog({ title: '删除好友？', body: '双方将暂停私聊，聊天记录仍可查看。',
        onOk: async () => { await contactAction(id, 'remove'); await update(); } });
      return;
    }
    action.loading = true;
    try {
      if (kind === 'direct') await openDirect(id);
      else if (kind === 'accept-invite' || kind === 'decline-invite') {
        await socialCall('PUT', '/conversations/' + id + '/invites', {
          status: kind === 'accept-invite' ? 'accepted' : 'declined',
        });
        await update();
      } else {
        const result = await contactAction(id, kind);
        if (kind === 'request' && result.state === 'outgoing') toast('好友申请已发送');
        await update();
      }
    } catch (error) { toast(error.message); }
    finally { action.loading = false; }
  };
}

function dialog(title) {
  const panel = document.createElement('mdui-dialog');
  panel.headline = title;
  panel.className = 'cm-social-dialog';
  const host = document.createElement('section');
  host.className = 'cm-social-panel';
  const close = document.createElement('mdui-button');
  close.slot = 'action';
  close.variant = 'text';
  close.textContent = '关闭';
  close.onclick = () => { panel.open = false; };
  panel.append(host, close);
  panel.addEventListener('closed', () => panel.remove(), { once: true });
  document.body.append(panel);
  panel.open = true;
  return { panel, host };
}

function userRow(user, sub, operations) {
  return '<div class="cm-social-person"><a class="cm-social-person-link" href="#/u/' + user.id + '">' +
    avatarHTML(user, 34) + '<span><strong>' + esc(user.nickname) + '</strong>' +
    (sub ? '<small>' + esc(sub) + '</small>' : '') + '</span></a>' +
    '<div class="cm-social-actions">' + operations + '</div></div>';
}

export function mountProfileSocial(host, userId) {
  if (!auth.token || Number(auth.user?.id) === userId) return;
  const here = location.hash;
  const token = auth.token;
  const refresh = async () => {
    const data = await socialCall('GET', '/contacts/' + userId);
    if (!host.isConnected || location.hash !== here || token !== auth.token) return;
    host.innerHTML = contactButtons(userId, data.state, data.canReport);
  };
  bindContactActions(host, refresh);
  refresh().catch(() => {});
}

function openPeople(inboxOnly, userId, onChange) {
  const { panel, host } = dialog(inboxOnly ? '申请与邀请' : '联系人');
  let pending = 0;
  let search = '';
  let offset = 0;
  host.innerHTML = inboxOnly ? '<div data-people-results></div>'
    : '<form class="cm-social-search"><input aria-label="公开昵称" placeholder="搜索公开昵称" maxlength="50">' +
      '<mdui-button type="submit" variant="tonal">搜索</mdui-button></form><div data-people-results></div>';
  const results = host.querySelector('[data-people-results]');
  const draw = async (append = false) => {
    const seq = ++pending;
    try {
      const data = search ? await socialCall('GET', '/users?q=' + encodeURIComponent(search) + '&offset=' + offset)
        : await socialCall('GET', '/contacts');
      if (seq !== pending || !panel.open) return;
      let markup;
      if (search) {
        markup = data.users.filter(u => u.id !== userId)
          .map(u => userRow(u, '', contactButtons(u.id, u.friendship, u.canReport))).join('');
      } else if (inboxOnly) {
        markup = data.contacts.filter(item => item.state === 'incoming' || item.state === 'outgoing').map(item =>
          userRow(item.user, item.state === 'incoming' ? '好友申请' : '已发送好友申请',
            contactButtons(item.user.id, item.state, item.canReport))).join('');
        markup += data.invites.map(item => userRow(item.sender, '邀请你加入「' + item.name + '」',
          button('accept-invite', '接受', item.id, 'tonal') + button('decline-invite', '拒绝', item.id))).join('');
      } else {
        markup = data.contacts.filter(item => item.state === 'accepted')
          .map(item => userRow(item.user, '', contactButtons(item.user.id, item.state, item.canReport))).join('');
        const former = data.contacts.filter(item => item.canReport && !['accepted', 'incoming', 'outgoing'].includes(item.state));
        if (former.length) markup += '<details><summary>曾经的好友</summary>' + former.map(item =>
          userRow(item.user, '', contactButtons(item.user.id, item.state, true))).join('') + '</details>';
      }
      results.querySelector('[data-people-more]')?.remove();
      if (!append) results.innerHTML = '';
      results.insertAdjacentHTML('beforeend', markup || (append ? '' : empty(
        search ? '没有找到用户' : inboxOnly ? '暂无待处理的申请或邀请' : '还没有好友，可搜索公开昵称添加')));
      if (search && data.more) {
        results.insertAdjacentHTML('beforeend', '<mdui-button data-people-more variant="text">加载更多</mdui-button>');
        results.querySelector('[data-people-more]').onclick = () => { offset += 30; draw(true); };
      }
    } catch (error) { if (panel.open && seq === pending) results.innerHTML = empty(error.message); }
  };
  const searchForm = host.querySelector('form');
  if (searchForm) searchForm.onsubmit = event => {
    event.preventDefault();
    search = searchForm.querySelector('input').value.trim();
    offset = 0;
    draw();
  };
  bindContactActions(results, async () => { offset = 0; await draw(); onChange(); });
  draw();
  return panel;
}

function openGroup(room, userId, onSettings, onLeave) {
  const { panel, host } = dialog(room.name + ' · 群资料');
  const path = '/conversations/' + room.id;
  const manages = room.role === 'owner' || room.role === 'admin';
  const fields = '<div class="cm-social-note" data-description></div><div class="cm-social-note" data-announcement></div>';
  const controls = manages ? button('description', '编辑简介') + button('announcement', '编辑公告') + button('invite', room.kind === 'world' ? '恢复成员' : '邀请好友', '', 'tonal') : '';
  host.innerHTML = fields + '<div class="cm-social-actions">' + controls +
    (room.kind === 'group' && room.role !== 'owner' ? button('leave', '退出群组') : '') +
    '</div><form class="cm-social-search"><input aria-label="搜索成员昵称" placeholder="搜索成员昵称" maxlength="50">' +
    '<mdui-button type="submit" variant="tonal">搜索</mdui-button></form>' +
    (manages && room.kind === 'world' ? '<label class="cm-social-removed"><input type="checkbox">查看已移出的成员</label>' : '') +
    '<div data-group-members></div>';
  const setRoom = fresh => {
    room = fresh;
    host.querySelector('[data-description]').textContent = room.description || '暂无简介';
    host.querySelector('[data-announcement]').textContent = room.announcement ? '公告 · ' + room.announcement : '暂无公告';
    onSettings(room);
  };
  setRoom(room);
  const memberList = host.querySelector('[data-group-members]');
  const form = host.querySelector('form');
  const checkbox = host.querySelector('input[type=checkbox]');
  let offset = 0;
  let seq = 0;
  const draw = async (append = false) => {
    const turn = ++seq;
    const query = encodeURIComponent(form.querySelector('input').value.trim());
    const removed = !!checkbox?.checked;
    try {
      const data = await socialCall('GET', path + '/members?q=' + query + '&offset=' + offset + '&removed=' + Number(removed));
      if (seq !== turn || !panel.open) return;
      const content = data.members.map(person => {
        let actions = '';
        if (removed) actions = button('restore', '发出恢复邀请', person.id, 'tonal');
        else if (manages && person.id !== userId && person.role !== 'owner' && (person.role !== 'admin' || room.role === 'owner')) {
          actions = (person.mutedUntil * 1000 > Date.now()
            ? button('unmute', '解除禁言', person.id) : button('mute', '禁言', person.id)) + button('remove', '移出', person.id);
          if (room.role === 'owner') actions += person.role === 'admin'
            ? button('demote', '撤销管理员', person.id) : button('promote', '设为管理员', person.id);
        }
        return userRow(person, removed ? '已移出' : ROLES[person.role] || '', actions);
      }).join('');
      memberList.querySelector('[data-group-more]')?.remove();
      if (!append) memberList.innerHTML = '';
      memberList.insertAdjacentHTML('beforeend', content || (append ? '' : empty('没有符合条件的成员')));
      if (data.more) {
        memberList.insertAdjacentHTML('beforeend', '<mdui-button data-group-more variant="text">加载更多</mdui-button>');
        memberList.querySelector('[data-group-more]').onclick = () => { offset += 30; draw(true); };
      }
    } catch (error) { if (panel.open && seq === turn) memberList.innerHTML = empty(error.message); }
  };
  const reload = () => { offset = 0; return draw(); };
  form.onsubmit = event => { event.preventDefault(); reload(); };
  if (checkbox) checkbox.onchange = reload;
  const changeMember = (id, action, extra = {}) =>
    socialCall('PUT', path + '/members/' + id, { action, ...extra }).then(reload);

  host.onclick = async event => {
    const action = event.target.closest('[data-action]');
    if (!action || action.loading) return;
    const id = Number(action.dataset.id);
    const kind = action.dataset.action;
    if (kind === 'description' || kind === 'announcement') {
      const label = kind === 'description' ? '群简介' : '群公告';
      promptDialog({ title: '编辑' + label, label, value: room[kind], allowEmpty: true,
        maxlength: kind === 'description' ? 300 : 1000, onOk: async value => {
          const data = await socialCall('PUT', path, { [kind]: value });
          setRoom(data.conversation);
        } });
    } else if (kind === 'invite') {
      if (room.kind === 'world') {
        if (checkbox) checkbox.checked = true;
        await reload();
        return;
      }
      const picker = dialog('邀请好友');
      picker.host.innerHTML = empty('正在加载…');
      try {
        const data = await socialCall('GET', '/contacts');
        if (!picker.panel.open) return;
        picker.host.innerHTML = data.contacts.filter(item => item.state === 'accepted')
          .map(item => userRow(item.user, '', button('invite-user', '邀请', item.user.id, 'tonal'))).join('') || empty('暂无可邀请的好友');
        picker.host.onclick = async ev => {
          const target = ev.target.closest('[data-action="invite-user"]');
          if (!target || target.disabled || target.loading) return;
          target.loading = true;
          try {
            await socialCall('POST', path + '/invites', { userId: Number(target.dataset.id) });
            target.disabled = true;
            target.textContent = '已邀请';
          } catch (error) { toast(error.message); }
          finally { target.loading = false; }
        };
      } catch (error) { picker.host.innerHTML = empty(error.message); }
    } else if (kind === 'mute') {
      promptDialog({ title: '禁言成员', label: '时长（分钟，1–43200）', value: '60', maxlength: 5, onOk: async raw => {
        if (!/^[1-9][0-9]*$/.test(raw) || Number(raw) > 43200) throw new Error('请输入 1–43200 分钟');
        return changeMember(id, 'mute', { seconds: Number(raw) * 60 });
      } });
    } else if (kind === 'remove' || kind === 'leave') {
      confirmDialog({ title: kind === 'remove' ? '移出该成员？' : '退出群组？', onOk: async () => {
        if (kind === 'remove') await changeMember(id, 'remove');
        else { await socialCall('DELETE', path); panel.open = false; onLeave(); }
      } });
    } else if (kind === 'restore') {
      action.loading = true;
      try {
        await socialCall('POST', path + '/invites', { userId: id });
        action.textContent = '已发送邀请';
        action.disabled = true;
      } catch (error) { toast(error.message); }
      finally { action.loading = false; }
    } else if (id) {
      action.loading = true;
      try { await changeMember(id, kind); } catch (error) { toast(error.message); }
      finally { action.loading = false; }
    }
  };
  reload();
  return panel;
}

function createGroup() {
  const { panel, host } = dialog('创建群组');
  host.innerHTML = '<form class="cm-social-form"><label>群名称<input required maxlength="50" name="name"></label>' +
    '<label>群简介<textarea maxlength="300" name="description" rows="3"></textarea></label>' +
    '<mdui-button type="submit" variant="filled">创建</mdui-button></form>';
  host.querySelector('form').onsubmit = async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const submit = form.querySelector('mdui-button');
    if (submit.loading) return;
    const values = new FormData(form);
    submit.loading = true;
    try {
      const { conversation } = await socialCall('POST', '/conversations', {
        kind: 'group', name: values.get('name'), description: values.get('description'),
      });
      panel.open = false;
      location.hash = socialURL(conversation.id);
    } catch (error) { toast(error.message); }
    finally { submit.loading = false; }
  };
  return panel;
}

function rowHTML(item) {
  const last = item.last;
  const glyph = item.kind === 'world' ? 'public' : item.kind === 'group' ? 'groups' : 'person';
  const avatar = item.peer ? avatarHTML(item.peer, 42) : '<span class="cm-social-glyph">' + icon(glyph) + '</span>';
  return '<a class="cm-social-row" href="' + socialURL(item.id) + '">' + avatar + '<span class="cm-social-text"><strong>' +
    esc(item.name) + '</strong><span>' + (item.mentioned ? '<b>有人 @ 你 · </b>' : '') +
    esc(last?.preview || item.description || '暂无消息') + '</span></span>' +
    '<span class="cm-social-meta">' + (last ? '<time>' + formatTime(last.createdAt) + '</time>' : '') +
    (item.unread ? '<span class="cm-social-badge" aria-label="' + item.unread + ' 条未读">' +
      (item.unread > 99 ? '99+' : item.unread) + '</span>' : '') + '</span></a>';
}

export async function mountSocial(root, selected) {
  closeSocial();
  const hash = location.hash;
  const token = auth.token;
  const base = settings.base;
  const signedIn = !!token;
  let disposed = false;
  let timer = 0;
  let requestId = 0;
  let dialogPanel = null;
  const active = () => !disposed && root.isConnected && location.hash === hash && token === auth.token && base === settings.base;
  const navigateAway = () => { if (!active()) closeSocial(); };
  window.addEventListener('hashchange', navigateAway);
  disposeSocial = () => {
    disposed = true;
    clearInterval(timer);
    window.removeEventListener('hashchange', navigateAway);
    if (dialogPanel) dialogPanel.open = false;
  };
  const cid = /^[1-9][0-9]*$/.test(selected || '') ? Number(selected) : 0;
  root.classList.add('cm-social');
  if (cid) {
    root.innerHTML = '<header class="cm-social-thread"><a href="' + socialURL() +
      '" aria-label="返回对话列表">' + icon('arrow_back') + '</a><div class="cm-social-thread-text"><h2></h2><p></p></div>' +
      button('details', '群资料', '', 'tonal') + '</header><div data-social-announcement></div><div data-social-chat>' + empty('正在加载…') + '</div>';
    const chatBox = root.querySelector('[data-social-chat]');
    const detail = root.querySelector('[data-action="details"]');
    detail.hidden = true;
    const showRoom = room => {
      if (!active()) return;
      root.querySelector('h2').textContent = room.name;
      root.querySelector('.cm-social-thread p').textContent = room.description || '';
      const announcement = root.querySelector('[data-social-announcement]');
      announcement.textContent = room.announcement ? '公告 · ' + room.announcement : '';
      announcement.hidden = !room.announcement;
      detail.hidden = !signedIn || (room.kind === 'world' && !room.role);
      detail.classList.toggle('cm-social-hidden', detail.hidden);
      detail.textContent = room.kind === 'direct' ? '聊天对象' : '群资料';
      detail.onclick = () => {
        if (room.kind === 'direct') location.hash = '#/u/' + room.peer.id;
        else dialogPanel = openGroup(room, viewer, showRoom, () => { location.hash = socialURL(); });
      };
    };
    const fail = error => {
      if (!active()) return;
      closeChat();
      if (dialogPanel) dialogPanel.open = false;
      detail.hidden = true;
      detail.classList.add('cm-social-hidden');
      chatBox.innerHTML = empty(error.message || '无法打开对话');
    };
    let viewer = 0;
    try {
      const { conversation } = await socialCall('GET', '/conversations/' + cid);
      if (!active()) return;
      viewer = conversation.userId;
      showRoom(conversation);
      mountChat(chatBox, { room: conversation, userId: viewer, onRights: showRoom, onLost: fail });
    } catch (error) {
      if (!active()) return;
      if (cid === WORLD.id && error.status === 404) {
        showRoom(WORLD);
        detail.hidden = true;
        detail.classList.add('cm-social-hidden');
        mountChat(chatBox);
      } else fail(error);
    }
    return;
  }

  root.innerHTML = '<div class="cm-social-head"><h2>对话</h2><div class="cm-social-actions">' +
    (signedIn ? button('contacts', '联系人') + button('inbox', '申请与邀请') + button('new-group', '创建群组', '', 'tonal')
      : '<mdui-button href="#/user" compact variant="tonal">登录</mdui-button>') +
    '</div></div><div class="cm-social-list" data-conversations>' + empty('正在加载…') + '</div>';
  const list = root.querySelector('[data-conversations]');
  let viewer = 0;
  let previous = '';
  const refresh = async () => {
    const seq = ++requestId;
    try {
      const data = await socialCall('GET', '/conversations');
      if (!active() || seq !== requestId) return;
      viewer = data.userId;
      const inboxButton = root.querySelector('[data-action="inbox"]');
      if (inboxButton) inboxButton.textContent = '申请与邀请' + (data.pending ? ' (' + data.pending + ')' : '');
      const markup = data.conversations.map(rowHTML).join('');
      if (markup !== previous) {
        const focused = list.contains(document.activeElement) ? document.activeElement.getAttribute('href') : '';
        list.innerHTML = markup;
        previous = markup;
        if (focused) Array.from(list.querySelectorAll('a')).find(link => link.getAttribute('href') === focused)?.focus();
      }
    } catch (error) {
      if (!active() || seq !== requestId) return;
      if (!previous) list.innerHTML = error.status === 404 ? rowHTML(WORLD) : empty(error.message) + button('retry', '重试');
    }
  };
  root.onclick = event => {
    const action = event.target.closest('[data-action]')?.dataset.action;
    if (!action || !active()) return;
    if (action === 'contacts' || action === 'inbox') dialogPanel = openPeople(action === 'inbox', viewer, refresh);
    else if (action === 'new-group') dialogPanel = createGroup();
    else if (action === 'retry') refresh();
  };
  refresh();
  timer = setInterval(() => { if (active() && !document.hidden) refresh(); }, 6000);
}
