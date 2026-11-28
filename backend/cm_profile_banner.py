"""Validate and normalize public profile banner media."""
import json
import math
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from cm_feedback_media import MOV_INPUT_ARGS, MediaError, process_media, read_upload

VIDEO_TYPES = ("video/mp4", "video/quicktime")
VIDEO_BYTES = 16 * 1024 ** 2
VIDEO_SECONDS = 5
EXTENDED_VIDEO_SECONDS = 10
VIDEO_OUTPUT_BYTES = 2 * 1024 ** 2
IMAGE_FORMATS = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP", "application/octet-stream": ""}
IMAGE_BYTES = 32 * 1024 ** 2
IMAGE_OUTPUT_BYTES = 48 * 1024 ** 2
_MEDIA_JOBS = threading.BoundedSemaphore(2)


def _upload_length(handler, media, limit):
    length = handler.headers.get("Content-Length", "")
    if (handler.headers.get("Transfer-Encoding") or handler.headers.get("Content-Encoding")
            or not re.fullmatch(r"[0-9]{1,12}", length) or not int(length)):
        raise MediaError(400, f"{media}横幅须提供有效的 Content-Length")
    if int(length) > limit:
        raise MediaError(413, f"横幅{media}需 ≤{limit // 1024 ** 2} MiB")
    return int(length)


def upload_lossless_image(handler, content_type, crop):
    if not re.fullmatch(r"[0-9]{1,8}(?:,[0-9]{1,8}){3}", crop or ""):
        raise MediaError(400, "请提供裁切区域 crop=x,y,width,height")
    left, top, width, height = map(int, crop.split(","))
    if width < 3 or height < 2 or width * 2 != height * 3:
        raise MediaError(400, "横幅裁切比例须为 3∶2")
    length = _upload_length(handler, "原图", IMAGE_BYTES)
    if not _MEDIA_JOBS.acquire(blocking=False):
        raise MediaError(503, "横幅图片处理繁忙，请稍后重试")
    try:
        with tempfile.TemporaryDirectory(prefix="cm-banner-") as directory:
            work = Path(directory)
            source, target = work / "source", work / "banner.png"
            read_upload(handler, source, length)
            try:
                process_media(["banner", source, target, IMAGE_FORMATS[content_type], left, top, width, height],
                              IMAGE_OUTPUT_BYTES, 30, work=work)
            except MediaError as exc:
                if exc.status == 413:
                    raise MediaError(413, "裁切后的无损横幅需 ≤48 MiB") from exc
                if exc.status == 503:
                    raise MediaError(503, "横幅图片处理暂不可用，请稍后重试") from exc
                raise MediaError(400, "横幅图片无效、裁切区域越界或图像超过 2500 万像素") from exc
            return target.read_bytes()
    except OSError as exc:
        raise MediaError(503, "横幅图片处理暂不可用") from exc
    finally:
        _MEDIA_JOBS.release()


def upload_video(handler, *, extended=False):
    length = _upload_length(handler, "视频", VIDEO_BYTES)
    max_seconds = EXTENDED_VIDEO_SECONDS if extended else VIDEO_SECONDS
    probe, encoder = shutil.which("ffprobe"), shutil.which("ffmpeg")
    if not probe or not encoder:
        raise MediaError(503, "横幅视频处理暂不可用")
    if not _MEDIA_JOBS.acquire(blocking=False):
        raise MediaError(503, "横幅视频处理繁忙，请稍后重试")
    try:
        with tempfile.TemporaryDirectory(prefix="cm-banner-") as directory:
            work = Path(directory)
            source, target = work / "source", work / "banner.mp4"
            read_upload(handler, source, length)
            _encode(source, target, probe, encoder, max_seconds)
            return target.read_bytes()
    except OSError as exc:
        raise MediaError(503, "横幅视频处理暂不可用") from exc
    finally:
        _MEDIA_JOBS.release()


def _encode(source, target, probe, encoder, max_seconds):
    with source.open("rb") as handle:
        if handle.read(8)[4:] != b"ftyp":
            raise MediaError(400, "横幅视频须为 MP4 或 MOV 文件")
    with tempfile.TemporaryFile(dir=source.parent) as report:
        _run([probe, "-v", "error", *MOV_INPUT_ARGS, "-select_streams", "V:0", "-show_entries",
              "format=start_time,duration:stream=index,width,height,start_time,duration", "-of", "json", source],
             65536, 10, source.parent, report)
        report.seek(0)
        try:
            info = json.load(report)
            video = info["streams"][0]
            width, height = video["width"], video["height"]
            container = info["format"]
            duration = float(video.get("duration") or container["duration"])
            input_start = float(container.get("start_time", 0))
            video_start = float(video.get("start_time", input_start))
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            raise MediaError(400, "无法读取横幅视频的尺寸或时长") from exc
    if min(width, height) < 2 or max(width, height) > 3840 or width * height > 3840 * 2160:
        raise MediaError(400, "横幅视频最长边需 ≤3840 像素，总像素数需 ≤830 万")
    if not math.isfinite(duration) or not 0 < duration <= max_seconds:
        raise MediaError(400, f"横幅视频最长 {max_seconds} 秒")
    if not math.isfinite(video_start - input_start):
        raise MediaError(400, "无法读取横幅视频的起始时间")

    # FFmpeg shifts the input start to zero. Stop at the picture's end on that timeline.
    end = min(max_seconds, duration + max(0, video_start - input_start))
    output_limit = VIDEO_OUTPUT_BYTES * max_seconds // VIDEO_SECONDS
    filters = "crop=w='min(iw,ih*3/2/sar)':h='min(ih,iw*sar*2/3)',scale=720:480,setsar=1,fps=24"
    _run([encoder, "-nostdin", "-v", "error", "-xerror", "-err_detect", "explode",
          "-abort_on", "empty_output", *MOV_INPUT_ARGS, "-threads", "1", "-i", source,
          "-map", f"0:{video['index']}", "-map", "0:a:0?", "-sn", "-dn", "-map_metadata", "-1",
          "-map_metadata:s", "-1", "-map_chapters", "-1", "-t", str(end),
          "-filter_threads", "1", "-vf", filters, "-threads", "1", "-c:v", "libx264",
          "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "23", "-maxrate", "2M",
          "-bufsize", "2M", "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-ar", "48000",
          "-movflags", "+faststart", target], output_limit, max_seconds * 4, source.parent)


def _run(args, size, timeout, work, stdout=subprocess.DEVNULL):
    try:
        process_media(["exec", *args], size, timeout, stdout, work=work)
    except MediaError as exc:
        if exc.status == 503:
            raise MediaError(503, "横幅视频处理暂不可用，请稍后重试") from exc
        if exc.status == 413:
            raise MediaError(413, "处理后的横幅视频超过大小限制") from exc
        raise MediaError(400, "横幅视频无法处理，请选择有效的 MP4 或 MOV 文件") from exc
