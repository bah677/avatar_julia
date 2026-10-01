from course.video_hosts.base import VideoHostAdapter
from course.video_hosts.disk_media import DiskMediaAdapter
from course.video_hosts.kinescope import KinescopeAdapter
from course.video_hosts.vimeo import VimeoAdapter
from course.video_hosts.ytdlp import YtDlpAdapter, detect_host, extract_video_urls
from course.video_hosts.zoom import ZoomAdapter


def adapter_for(host: str, *, video_password: str = "") -> VideoHostAdapter:
    h = (host or "").strip().lower()
    if h == "vimeo":
        return VimeoAdapter()
    if h == "kinescope":
        return KinescopeAdapter()
    if h == "zoom":
        return ZoomAdapter(video_password=video_password)
    if h == "disk":
        return DiskMediaAdapter()
    return YtDlpAdapter("youtube")
