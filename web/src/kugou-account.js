import { mdui } from './md.js';
import { esc, toast, confirmDialog } from './ui.js';
import { kugouAccountRequest } from './kugou.js';

export async function openKugouAccount(onChange = () => {}) {
  let state;
  try {
    state = await kugouAccountRequest();
    if (!state.enabled) return toast('此服务暂未开启酷狗个人账号');
  } catch (error) {
    return toast(error.message);
  }

  let timer;
  const dialog = mdui.dialog({
    headline: state.bound ? '管理酷狗概念版账号' : '绑定酷狗概念版账号',
    body: `
      <div class="cm-phonebind">
        <div class="cm-phone-row"><mdui-text-field label="酷狗手机号" id="kgMobile" inputmode="tel" maxlength="11" variant="outlined"></mdui-text-field></div>
        <div class="cm-phone-row">
          <mdui-text-field label="验证码" id="kgCode" inputmode="numeric" maxlength="8" autocomplete="one-time-code" variant="outlined"></mdui-text-field>
          <mdui-button variant="tonal" id="kgCodeSend">获取验证码</mdui-button>
        </div>
        <div class="cm-pe-hint" id="kgBindHint" role="status">${state.bound ? '输入手机号可重新绑定' : '绑定后可读取个人云盘和发表歌曲评论'}</div>
        ${state.bound ? '<mdui-button variant="text" id="kgUnbind">解除绑定</mdui-button>' : ''}
      </div>`,
    onClose: () => clearInterval(timer),
    actions: [
      { text: '取消' },
      { text: '绑定', onClick: async () => {
        const code = dialog.querySelector('#kgCode').value.trim();
        if (!/^[0-9]{4,8}$/.test(code)) { toast('请填写短信验证码'); return false; }
        try {
          await kugouAccountRequest('POST', '/verify', { code });
          toast('酷狗概念版账号已绑定');
          onChange();
          return true;
        } catch (error) {
          toast(error.message);
          return false;
        }
      } },
    ],
  });

  const send = dialog.querySelector('#kgCodeSend');
  const mobileField = dialog.querySelector('#kgMobile');
  const hint = dialog.querySelector('#kgBindHint');
  send.onclick = async () => {
    const mobile = mobileField.value.trim();
    if (!/^1[0-9]{10}$/.test(mobile)) return toast('请填写有效的中国大陆手机号');
    send.disabled = true;
    try {
      const result = await kugouAccountRequest('POST', '/code', { mobile });
      if (!dialog.open) return;
      hint.textContent = '验证码已发送';
      mobileField.disabled = true;
      let left = Number(result.retryAfter) || 60;
      send.textContent = `${left} 秒后可重发`;
      timer = setInterval(() => {
        if (--left > 0) send.textContent = `${left} 秒后可重发`;
        else {
          clearInterval(timer);
          mobileField.disabled = false;
          send.disabled = false;
          send.textContent = '获取验证码';
        }
      }, 1000);
    } catch (error) {
      send.disabled = false;
      toast(error.message);
    }
  };

  dialog.querySelector('#kgUnbind')?.addEventListener('click', () => {
    confirmDialog({
      title: '解除酷狗概念版账号绑定？',
      body: esc('解除后可重新绑定以访问个人云盘和评论。'),
      onOk: async () => {
        await kugouAccountRequest('DELETE');
        dialog.open = false;
        toast('已解除酷狗绑定');
        onChange();
      },
    });
  });
}
