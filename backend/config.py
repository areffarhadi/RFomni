"""Paths and constants. Override the workspace with OMNI_STUDIO_WORKSPACE."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKSPACE = Path(os.environ.get("OMNI_STUDIO_WORKSPACE", ROOT / "workspace")).expanduser().resolve()
PROJECTS_DIR = WORKSPACE / "projects"
CUSTOM_TASKS_FILE = WORKSPACE / "custom_tasks.json"
TASK_OVERRIDES_FILE = WORKSPACE / "task_overrides.json"  # user changes to built-in tasks (classes, prompt, ...)
BUILTIN_TASKS_FILE = ROOT / "config" / "tasks.json"
FRONTEND_DIR = ROOT / "frontend"

# Analysis audio: everything is decoded once to 16 kHz mono and cached.
SR = 16000
HOP = 160          # 10 ms spectrogram hop for the cached full-file spectrogram
N_FFT = 512

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
AUDIO_EXT = {".wav", ".mp3", ".flac", ".ogg", ".opus", ".m4a", ".aac", ".wma", ".aif", ".aiff"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mpg", ".mpeg", ".wmv"}
MEDIA_EXT = IMAGE_EXT | AUDIO_EXT | VIDEO_EXT
