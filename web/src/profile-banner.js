import { mdui } from './md.js';
import { api, auth, settings } from './api.js';
import { confirmDialog, esc, toast } from './ui.js';
import { bindBannerCrop } from './profile-banner-crop.js';

const MAX_IMAGE_BYTES = 1024 * 1024;
const MAX_VIDEO_BYTES = 16 * 1024 * 1024;

function videoType(file) {
  if (file.type === 'video/mp4' || /\.mp4$/i.test(file.name)) return 'video/mp4';
  if (file.type === 'video/quicktime' || /\.mov$/i.test(file.name)) return 'video/quicktime';
  return '';
}

export function profileBannerHTML({ bannerUrl, bannerType }, editable = false) {
  const url = api.profileBannerUrl(bannerUrl);
  const video = bannerType === 'video';
  const autoplay = !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const src = esc(url);
  const media = video
    ? `<video src="${src}" muted loop playsinline ${autoplay ? 'autoplay' : 'preload="metadata"'} aria-hidden="true"></video>`
    : `<img src="${src}" alt="" onerror="this.remove()">`;
  return `<div class="cm-profile-banner${url ? ' cm-profile-banner-custom' : ''}">
    ${url ? media : ''}
    ${editable ? `<div class="cm-banner-actions">
      <button type="button" data-banner="select">
        <span class="material-icons-outlined" aria-hidden="true">image</span>${url ? '更换横幅' : '上传横幅'}
      </button>
      ${url ? '<button type="button" data-banner="remove" aria-label="移除横幅" title="移除横幅"><span class="material-icons-outlined" aria-hidden="true">delete_outline</span></button>' : ''}
    </div><input type="file" accept="image/jpeg,image/png,image/webp,video/mp4,video/quicktime,.mp4,.mov" hidden>` : ''}
  </div>`;
}

function readImage(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error('无法读取图片'));
    reader.readAsDataURL(file);
  });
}

export function bindProfileBanner(container) {
  const banner = container.querySelector('.cm-profile-banner');
  const input = banner.querySelector('input');
  const select = banner.querySelector('[data-banner="select"]');
  const remove = banner.querySelector('[data-banner="remove"]');
  const token = auth.token;
  const base = settings.base;
  const isCurrent = () => banner.isConnected && token === auth.token && base === settings.base;
  let busy = false;

  function setBusy(value) {
    busy = select.disabled = value;
    if (remove) remove.disabled = value;
  }

  function update(result) {
    if (!isCurrent()) return;
    banner.outerHTML = profileBannerHTML(result, true);
    bindProfileBanner(container);
  }

  select.onclick = () => input.click();
  input.onchange = () => {
    const file = input.files[0];
    input.value = '';
    if (!file || busy || !isCurrent()) return;
    const type = videoType(file);
    if (type && file.size > MAX_VIDEO_BYTES) return toast('横幅视频需 ≤16 MiB');
    if (!type && file.size > MAX_IMAGE_BYTES) return toast('横幅图片需 ≤1 MiB');
    if (!type && file.type && !/^image\/(jpeg|png|webp)$/.test(file.type)) {
      return toast('请选择 JPEG、PNG、WebP 图片或 MP4、MOV 视频');
    }

    const dialog = mdui.dialog({
      headline: type ? '预览主页横幅' : '裁切主页横幅',
      body: `<div class="cm-banner-preview">
        <div class="cm-profile-banner cm-profile-banner-custom">${type ? '<video muted loop playsinline controls></video>'
          : '<canvas tabindex="0" aria-label="3 比 2 横幅裁切预览，可拖动或使用方向键取景"></canvas>'}</div>
        ${type ? '<p>视频会居中裁剪为 3∶2，最长 5 秒，发布后公开静音循环播放。</p>' : `
          <p>拖动图片调整取景，保存后会公开展示。</p>
          <div class="cm-banner-crop-controls">
            <label>缩放 <input type="range" min="1" max="3" step=".01" value="1" data-crop-zoom disabled></label>
            <button type="button" data-crop-reset disabled>重置</button>
          </div>`}
        <p data-preview-status role="status" hidden></p>
        <div class="cm-banner-preview-actions">
          <mdui-button variant="text" data-cancel>取消</mdui-button>
          <mdui-button variant="filled" data-save disabled>保存横幅</mdui-button>
        </div>
      </div>`,
    });
    const save = dialog.querySelector('[data-save]');
    const media = type ? dialog.querySelector('video') : new Image();
    const status = dialog.querySelector('[data-preview-status]');
    const previewUrl = URL.createObjectURL(file);
    function showStatus(message) {
      status.textContent = message;
      status.hidden = false;
    }
    let exportCrop;
    media.onload = () => {
      try {
        exportCrop = bindBannerCrop(dialog, media);
        save.disabled = false;
      } catch (e) { showStatus(e.message); }
    };
    media.onloadedmetadata = () => {
      if (media.duration > 5.05) showStatus('横幅视频最长 5 秒，请选择更短的视频。');
      else save.disabled = false;
    };
    media.onerror = () => {
      showStatus(type ? '当前设备无法预览此视频，可以提交给服务器转码。' : '无法预览这张图片。');
      if (type) save.disabled = false;
    };
    if (type) media.muted = true;
    media.src = previewUrl;
    dialog.addEventListener('closed', () => URL.revokeObjectURL(previewUrl), { once: true });
    dialog.querySelector('[data-cancel]').onclick = () => { dialog.open = false; };
    save.onclick = async () => {
      if (busy || save.disabled || !isCurrent()) return;
      setBusy(true);
      save.loading = true;
      try {
        const imageData = type ? '' : (await readImage(await exportCrop(MAX_IMAGE_BYTES))).split(',')[1];
        if (!isCurrent()) return;
        const result = type
          ? await api.uploadProfileBannerVideo(file.slice(0, file.size, type))
          : await api.uploadProfileBanner(imageData);
        dialog.open = false;
        update(result);
        toast('横幅已更新');
      } catch (e) { toast('上传失败：' + e.message); }
      finally { save.loading = false; setBusy(false); }
    };
  };

  if (remove) remove.onclick = () => confirmDialog({
    title: '移除主页横幅',
    body: '移除后，主页会显示默认横幅。',
    onOk: async () => {
      if (busy || !isCurrent()) return;
      setBusy(true);
      try {
        const result = await api.removeProfileBanner();
        update(result);
        toast('横幅已移除');
      } catch (e) { toast('移除失败：' + e.message); }
      finally { setBusy(false); }
    },
  });
}
