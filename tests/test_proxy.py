import subprocess

import pytest

from subtitle_translator.core import proxy
from subtitle_translator.core.media import tool_path


@pytest.fixture
def av1_video(tmp_path):
    v = tmp_path / "ep.mkv"
    res = subprocess.run([tool_path("ffmpeg"), "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=s=320x240:d=2:r=24",
                          "-f", "lavfi", "-i", "sine=d=2", "-c:v", "libsvtav1", "-preset", "12",
                          "-c:a", "aac", str(v)], capture_output=True)
    if res.returncode != 0:
        pytest.skip("bundled ffmpeg cannot encode AV1 here")
    return v


def test_av1_needs_proxy_and_proxy_is_playable_h264(av1_video, tmp_path, monkeypatch):
    monkeypatch.setattr(proxy, "proxy_dir", lambda: tmp_path / "proxies")
    (tmp_path / "proxies").mkdir()
    assert proxy.needs_proxy(av1_video)
    job = proxy.ensure_proxy(av1_video)
    assert proxy.ensure_proxy(av1_video, start=False) is job          # one shared job per file
    job._thread.join(300)                                    # slow when the CPU is busy
    assert job.ready and job.error is None and job.fraction == 1.0
    assert proxy.video_codec(job.out) == "h264"
    assert abs(proxy.duration_of(job.out) - 2.0) < 0.3                # same timeline as the source
    assert not list((tmp_path / "proxies").glob("*.part.mp4"))


def test_proxy_key_changes_when_file_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(proxy, "proxy_dir", lambda: tmp_path)
    v = tmp_path / "a.mkv"
    v.write_bytes(b"1")
    first = proxy.proxy_path(v)
    v.write_bytes(b"22")
    assert proxy.proxy_path(v) != first
