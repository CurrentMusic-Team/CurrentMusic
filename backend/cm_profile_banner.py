"""Validate and normalize public profile banner videos."""
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
VIDEO_OUTPUT_BYTES = 2 * 1024 ** 2
_VIDEO_JOBS = threading.BoundedSemaphore(2)


def upload_video(handler):
    length = handler.headers.get("Content-Length", "")
    if (handler.headers.get("Transfer-Encoding") or handler.headers.get("Content-Encoding")
            or not re.fullmatch(r"[0-9]{1,12}", length) or not int(length)):
        raise MediaError(400, "视频横幅须提供有效的 Content-Length")
    if int(length) > VIDEO_BYTES:
        raise MediaError(413, "横幅视频需 ≤16 MiB")
    probe, encoder = shutil.which("ffprobe"), shutil.which("ffmpeg")
    if not probe or not encoder:
        raise MediaError(503, "横幅视频处理暂不可用")
    if not _VIDEO_JOBS.acquire(blocking=False):
        raise MediaError(503, "横幅视频处理繁忙，请稍后重试")
    try:
        with tempfile.TemporaryDirectory(prefix="cm-banner-") as directory:
            work = Path(directory)
            source, target = work / "source", work / "banner.mp4"
            read_upload(handler, source, int(length))
            _encode(source, target, probe, encoder)
            return target.read_bytes()
    except OSError as exc:
        raise MediaError(503, "横幅视频处理暂不可用") from exc
    finally:
        _VIDEO_JOBS.release()


def _encode(source, target, probe, encoder):
    with source.open("rb") as handle:
        if handle.read(8)[4:] != b"ftyp":
            raise MediaError(400, "横幅视频须为 MP4 或 MOV 文件")
    with tempfile.TemporaryFile(dir=source.parent) as report:
        _run([probe, "-v", "error", *MOV_INPUT_ARGS, "-select_streams", "V:0", "-show_entries",
              "format=duration:stream=index,width,height,duration", "-of", "json", source],
             65536, 10, source.parent, report)
        report.seek(0)
        try:
            info = json.load(report)
            video = info["streams"][0]
            width, height = video["width"], video["height"]
            duration = float(video.get("duration") or info["format"]["duration"])
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            raise MediaError(400, "无法读取横幅视频的尺寸或时长") from exc
    if min(width, height) < 2 or max(width, height) > 3840 or width * height > 3840 * 2160:
        raise MediaError(400, "横幅视频最长边需 ≤3840 像素，总像素数需 ≤830 万")
    if not math.isfinite(duration) or not 0 < duration <= VIDEO_SECONDS:
        raise MediaError(400, "横幅视频最长 5 秒")

    filters = "crop=w='min(iw,ih*3/2/sar)':h='min(ih,iw*sar*2/3)',scale=720:480,setsar=1,fps=24"
    _run([encoder, "-nostdin", "-v", "error", "-xerror", "-err_detect", "explode",
          "-abort_on", "empty_output", *MOV_INPUT_ARGS, "-threads", "1", "-i", source,
          "-map", f"0:{video['index']}", "-an", "-sn", "-dn", "-map_metadata", "-1",
          "-map_metadata:s", "-1", "-map_chapters", "-1", "-t", str(VIDEO_SECONDS),
          "-filter_threads", "1", "-vf", filters, "-threads", "1", "-c:v", "libx264",
          "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "23", "-maxrate", "2M",
          "-bufsize", "2M", "-movflags", "+faststart", target], VIDEO_OUTPUT_BYTES, 20, source.parent)


def _run(args, size, timeout, work, stdout=subprocess.DEVNULL):
    try:
        process_media(["exec", *args], size, timeout, stdout, work=work)
    except MediaError as exc:
        if exc.status == 503:
            raise MediaError(503, "横幅视频处理暂不可用，请稍后重试") from exc
        if exc.status == 413:
            raise MediaError(413, "处理后的横幅视频超过 2 MiB") from exc
        raise MediaError(400, "横幅视频无法处理，请选择有效的 MP4 或 MOV 文件") from exc
