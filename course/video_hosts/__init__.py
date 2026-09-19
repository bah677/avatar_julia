from course.video_hosts.base import VideoHostAdapter
from course.video_hosts.disk_media import DiskMediaAdapter
from course.video_hosts.kinescope import KinescopeAdapter
from course.video_hosts.ytdlp import YtDlpAdapter, detect_host, extract_video_urls


def adapter_for(host: str) -> VideoHostAdapter:
    h = (host or "").strip().lower()
    if h == "vimeo":
        return YtDlpAdapter("vimeo")
    if h == "kinescope":
        return KinescopeAdapter()
    if h == "disk":
        return DiskMediaAdapter()
    return YtDlpAdapter("youtube")
