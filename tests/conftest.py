"""Shared fixtures: tool discovery and real MKV generation via ffmpeg."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

SANDBOX_TOOLS = Path.home() / "opt" / "mkvtoolnix" / "bin"

# User-space mkvtoolnix (sandbox bootstrap); on a normal system they are on PATH
if shutil.which("mkvmerge") is None and SANDBOX_TOOLS.is_dir():
    os.environ["PATH"] = f"{SANDBOX_TOOLS}{os.pathsep}{os.environ.get('PATH', '')}"


def have_tools() -> bool:
    return bool(shutil.which("mkvmerge") and shutil.which("mkvpropedit") and shutil.which("ffmpeg"))


requires_tools = pytest.mark.skipif(not have_tools(), reason="mkvtoolnix/ffmpeg not available")


@pytest.fixture
def tools():
    from mkvtune.scan import find_tools
    return find_tools()


@pytest.fixture
def xdg_env(tmp_path, monkeypatch):
    """Point all mkvtune state into the test's tmp dir."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
    return tmp_path


SRT = "1\n00:00:00,000 --> 00:00:01,000\nhello\n"


@pytest.fixture
def mkv_factory(tmp_path):
    """Build real MKV files with ffmpeg: configurable audio/subtitle layout."""
    def make(name: str = "ep.mkv", audio_langs: tuple[str, ...] = ("jpn", "eng"),
             subs: tuple[tuple[str, str], ...] = (("eng", "Signs/Songs"), ("eng", "Full"))):
        out = tmp_path / name
        inputs = ["-f", "lavfi", "-i", "color=c=blue:s=160x120:r=5"]
        freqs = (330, 550, 770, 990)
        for i in range(len(audio_langs)):
            inputs += ["-f", "lavfi", "-i", f"sine=f={freqs[i % len(freqs)]}:d=2"]
        for i in range(len(subs)):
            srt = tmp_path / f"{name}.{i}.srt"
            srt.write_text(SRT)
            inputs += ["-i", str(srt)]

        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs, "-map", "0:v"]
        for i in range(len(audio_langs)):
            cmd += ["-map", f"{1 + i}:a"]
        for i in range(len(subs)):
            cmd += ["-map", f"{1 + len(audio_langs) + i}:s"]
        cmd += ["-c:v", "ffv1", "-c:a", "aac", "-c:s", "srt", "-t", "2"]
        for i, lang in enumerate(audio_langs):
            cmd += [f"-metadata:s:a:{i}", f"language={lang}"]
        for i, (lang, title) in enumerate(subs):
            cmd += [f"-metadata:s:s:{i}", f"language={lang}"]
            if title:
                cmd += [f"-metadata:s:s:{i}", f"title={title}"]
        cmd += [str(out)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        assert proc.returncode == 0, f"ffmpeg failed: {proc.stderr}"
        return out

    return make
