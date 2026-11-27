const MAX_IMAGE_PIXELS = 25000000;

export function bindBannerCrop(dialog, image, lossless) {
  const canvas = dialog.querySelector('canvas');
  const zoomInput = dialog.querySelector('[data-crop-zoom]');
  const reset = dialog.querySelector('[data-crop-reset]');
  const { naturalWidth: width, naturalHeight: height } = image;
  if (!width || !height || width * height > MAX_IMAGE_PIXELS) throw new Error('横幅图片需 ≤2500 万像素');
  if (lossless && (width < 3 || height < 2)) throw new Error('图片太小，无法无损裁切为 3∶2');
  const units = Math.max(1, Math.min(533, Math.floor(width / 3), Math.floor(height / 2)));
  canvas.width = units * 3;
  canvas.height = units * 2;
  const ctx = canvas.getContext('2d', { alpha: lossless });
  if (!ctx) throw new Error('当前设备无法裁切图片');
  const fitWidth = Math.min(width, height * 1.5);
  let x = width / 2;
  let y = height / 2;
  let cropWidth, cropHeight, frame, drag;

  function draw() {
    frame = undefined;
    cropWidth = fitWidth / Number(zoomInput.value);
    if (lossless) cropWidth = Math.max(1, Math.floor(cropWidth / 3)) * 3;
    cropHeight = cropWidth / 1.5;
    const halfWidth = cropWidth / 2;
    const halfHeight = cropHeight / 2;
    x = Math.max(halfWidth, Math.min(width - halfWidth, x));
    y = Math.max(halfHeight, Math.min(height - halfHeight, y));
    let left = x - halfWidth;
    let top = y - halfHeight;
    if (lossless) {
      left = Math.round(left);
      top = Math.round(top);
      ctx.clearRect(0, 0, canvas.width, canvas.height);
    } else {
      ctx.fillStyle = '#20222c';
      ctx.fillRect(0, 0, canvas.width, canvas.height);
    }
    ctx.drawImage(image, left, top, cropWidth, cropHeight, 0, 0, canvas.width, canvas.height);
    return [left, top, cropWidth, cropHeight];
  }

  function requestDraw() {
    if (frame === undefined) frame = requestAnimationFrame(draw);
  }

  zoomInput.oninput = requestDraw;
  reset.onclick = () => {
    zoomInput.value = '1';
    x = width / 2;
    y = height / 2;
    requestDraw();
  };
  canvas.onpointerdown = event => {
    if (!event.isPrimary || event.button !== 0) return;
    drag = event;
    canvas.setPointerCapture(event.pointerId);
  };
  canvas.onpointermove = event => {
    if (drag?.pointerId !== event.pointerId) return;
    const rect = canvas.getBoundingClientRect();
    x -= (event.clientX - drag.clientX) * cropWidth / rect.width;
    y -= (event.clientY - drag.clientY) * cropHeight / rect.height;
    drag = event;
    requestDraw();
  };
  canvas.onlostpointercapture = () => { drag = undefined; };
  canvas.onkeydown = event => {
    const directions = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
    const step = directions[event.key];
    if (!step) return;
    event.preventDefault();
    x += step[0] * cropWidth * .04;
    y += step[1] * cropHeight * .04;
    requestDraw();
  };
  draw();
  zoomInput.disabled = reset.disabled = false;

  function cropBox() {
    if (frame !== undefined) cancelAnimationFrame(frame);
    return draw();
  }

  async function exportCrop(limit) {
    cropBox();
    const toJpeg = quality => new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', quality));
    let cropped = await toJpeg(.9);
    if (cropped?.size > limit) cropped = await toJpeg(.78);
    if (cropped?.size > limit) cropped = await toJpeg(.65);
    if (!cropped) throw new Error('无法生成裁切后的图片');
    if (cropped.size > limit) throw new Error('裁切后的图片需 ≤1 MiB');
    return cropped;
  }

  return { cropBox, exportCrop };
}
