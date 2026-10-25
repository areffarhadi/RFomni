"""Convert Open WebUI uploaded audio/video/image files into Omni multimodal parts."""
from __future__ import annotations

import base64
import logging
import mimetypes
import os
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

AUDIO_EXTS = {'.wav', '.mp3', '.flac', '.ogg', '.m4a', '.aac', '.wma', '.opus'}
VIDEO_EXTS = {'.mp4', '.mov', '.mkv', '.avi', '.webm', '.mpeg', '.mpg', '.m4v'}
IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tif', '.tiff'}

# Keep uploads bounded for the Omni thinker context.
OMNI_VIDEO_MAX_SECONDS = float(os.environ.get('OMNI_VIDEO_MAX_SECONDS', '90'))
OMNI_VIDEO_MAX_BYTES = int(os.environ.get('OMNI_VIDEO_MAX_BYTES', str(80 * 1024 * 1024)))


def _file_content_type(file_item: dict) -> str:
    if not isinstance(file_item, dict):
        return ''
    ct = file_item.get('content_type') or ''
    if ct:
        return str(ct).lower()
    meta = file_item.get('meta') or {}
    if isinstance(meta, dict) and meta.get('content_type'):
        return str(meta['content_type']).lower()
    nested = file_item.get('file')
    if isinstance(nested, dict):
        nmeta = nested.get('meta') or {}
        if isinstance(nmeta, dict) and nmeta.get('content_type'):
            return str(nmeta['content_type']).lower()
        if nested.get('content_type'):
            return str(nested['content_type']).lower()
    return ''


def _file_name(file_item: dict) -> str:
    if not isinstance(file_item, dict):
        return ''
    nested = file_item.get('file') if isinstance(file_item.get('file'), dict) else {}
    return str(
        file_item.get('name')
        or file_item.get('filename')
        or (file_item.get('meta') or {}).get('name')
        or nested.get('filename')
        or ''
    )


def is_video_file(file_item: dict) -> bool:
    if not isinstance(file_item, dict):
        return False
    if file_item.get('type') == 'video':
        return True
    ct = _file_content_type(file_item)
    if ct.startswith('video/'):
        return True
    # browser webm can be video even when labeled oddly
    name = _file_name(file_item)
    ext = Path(name).suffix.lower() if name else ''
    if ext in VIDEO_EXTS:
        return True
    if ext == '.webm' and not ct.startswith('audio/'):
        return True
    return False


def is_audio_file(file_item: dict) -> bool:
    if not isinstance(file_item, dict):
        return False
    if is_video_file(file_item):
        return False
    if file_item.get('type') == 'audio':
        return True
    ct = _file_content_type(file_item)
    if ct.startswith('audio/'):
        return True
    name = _file_name(file_item)
    if name and Path(name).suffix.lower() in AUDIO_EXTS:
        return True
    return False


def is_audio_or_video_file(file_item: dict) -> bool:
    return is_audio_file(file_item) or is_video_file(file_item)


def is_image_file(file_item: dict) -> bool:
    if not isinstance(file_item, dict):
        return False
    if file_item.get('type') == 'image':
        return True
    ct = _file_content_type(file_item)
    if ct.startswith('image/'):
        return True
    name = _file_name(file_item)
    if name and Path(name).suffix.lower() in IMAGE_EXTS:
        return True
    url = str(file_item.get('url') or '')
    return url.startswith('data:image/')


def _ffmpeg_to_wav(src: Path, max_seconds: float = 60.0) -> Path:
    out = Path(tempfile.mkstemp(prefix='omni_av_', suffix='.wav')[1])
    cmd = [
        'ffmpeg', '-y', '-i', str(src),
        '-vn', '-ac', '1', '-ar', '16000', '-f', 'wav',
        '-t', str(max_seconds),
        str(out),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out


def _ffmpeg_to_mp4(src: Path, max_seconds: float | None = None) -> Path:
    """Remux/transcode to H.264+AAC mp4 for Omni video_url compatibility."""
    max_seconds = OMNI_VIDEO_MAX_SECONDS if max_seconds is None else max_seconds
    out = Path(tempfile.mkstemp(prefix='omni_vid_', suffix='.mp4')[1])
    cmd = [
        'ffmpeg', '-y', '-i', str(src),
        '-t', str(max_seconds),
        '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '28',
        '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-ac', '1', '-ar', '16000', '-b:a', '64k',
        '-movflags', '+faststart',
        str(out),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out


def file_path_to_audio_data_url(path: str | Path, content_type: str | None = None, name: str | None = None) -> str | None:
    path = Path(path)
    if not path.is_file():
        log.warning('audio file missing: %s', path)
        return None

    ext = path.suffix.lower()
    ct = (content_type or '').lower()
    name = name or path.name
    try:
        if ext != '.wav' and 'wav' not in ct:
            wav = _ffmpeg_to_wav(path)
            try:
                data = wav.read_bytes()
            finally:
                wav.unlink(missing_ok=True)
            return 'data:audio/wav;base64,' + base64.b64encode(data).decode('ascii')

        data = path.read_bytes()
        mime = ct if ct.startswith('audio/') else (mimetypes.guess_type(name)[0] or 'audio/wav')
        return f'data:{mime};base64,' + base64.b64encode(data).decode('ascii')
    except Exception:
        log.exception('failed converting audio %s', path)
        return None


def file_path_to_image_data_url(path: str | Path, content_type: str | None = None, name: str | None = None) -> str | None:
    path = Path(path)
    if not path.is_file():
        log.warning('image file missing: %s', path)
        return None
    try:
        data = path.read_bytes()
        ct = (content_type or '').lower()
        name = name or path.name
        mime = ct if ct.startswith('image/') else (mimetypes.guess_type(name)[0] or 'image/jpeg')
        return f'data:{mime};base64,' + base64.b64encode(data).decode('ascii')
    except Exception:
        log.exception('failed converting image %s', path)
        return None


def file_path_to_video_data_url(path: str | Path, content_type: str | None = None, name: str | None = None) -> str | None:
    """Encode video as data:video/mp4 for Omni video_url (matches finetuner eval clients)."""
    path = Path(path)
    if not path.is_file():
        log.warning('video file missing: %s', path)
        return None

    ext = path.suffix.lower()
    ct = (content_type or '').lower()
    name = name or path.name
    tmp: Path | None = None
    try:
        size = path.stat().st_size
        # Prefer mp4; remux/transcode other containers or oversized files.
        needs_transcode = (
            ext not in {'.mp4', '.m4v'}
            or 'mp4' not in ct
            or size > OMNI_VIDEO_MAX_BYTES
        )
        if needs_transcode:
            log.info('transcoding video for Omni: %s (%s bytes)', name, size)
            tmp = _ffmpeg_to_mp4(path)
            data = tmp.read_bytes()
            mime = 'video/mp4'
        else:
            # Still trim long clips so context stays manageable
            try:
                tmp = _ffmpeg_to_mp4(path)
                data = tmp.read_bytes()
                mime = 'video/mp4'
            except Exception:
                log.warning('mp4 remux failed for %s; sending original', name, exc_info=True)
                data = path.read_bytes()
                mime = ct if ct.startswith('video/') else (mimetypes.guess_type(name)[0] or 'video/mp4')

        if len(data) > OMNI_VIDEO_MAX_BYTES:
            log.warning(
                'video too large after encode (%s > %s); refusing',
                len(data),
                OMNI_VIDEO_MAX_BYTES,
            )
            return None

        return f'data:{mime};base64,' + base64.b64encode(data).decode('ascii')
    except Exception:
        log.exception('failed converting video %s', path)
        return None
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


async def resolve_upload_path(file_item: dict) -> str | None:
    """Resolve an Open WebUI file attachment to a local path."""
    for key in ('path', 'file_path'):
        p = file_item.get(key)
        if p and os.path.isfile(p):
            return p

    nested = file_item.get('file')
    if isinstance(nested, dict):
        for key in ('path', 'file_path'):
            p = nested.get(key)
            if p and os.path.isfile(p):
                return p

    file_id = file_item.get('id') or file_item.get('url')
    if isinstance(file_id, str) and file_id.startswith(('http://', 'https://', 'data:')):
        return None
    if not file_id:
        return None

    file_id = str(file_id).split('/')[0].split('?')[0]

    try:
        from open_webui.models.files import Files

        rec = await Files.get_file_by_id(file_id)
        if rec and getattr(rec, 'path', None) and os.path.isfile(rec.path):
            return rec.path
    except Exception:
        log.exception('Files.get_file_by_id failed for %s', file_id)

    try:
        from open_webui.config import UPLOAD_DIR

        matches = list(Path(UPLOAD_DIR).glob(f'{file_id}_*'))
        if matches:
            return str(matches[0])
    except Exception:
        log.exception('upload dir lookup failed for %s', file_id)
    return None


async def build_audio_url_parts(file_items: list[dict]) -> list[dict]:
    parts = []
    for f in file_items:
        if not is_audio_file(f):
            continue
        path = await resolve_upload_path(f)
        if not path:
            log.warning('could not resolve audio file: %s', f.get('id') or f.get('name'))
            continue
        ct = _file_content_type(f)
        data_url = file_path_to_audio_data_url(path, ct, _file_name(f))
        if data_url:
            parts.append({'type': 'audio_url', 'audio_url': {'url': data_url}})
    return parts


async def build_video_url_parts(file_items: list[dict]) -> list[dict]:
    parts = []
    for f in file_items:
        if not is_video_file(f):
            continue
        path = await resolve_upload_path(f)
        if not path:
            log.warning('could not resolve video file: %s', f.get('id') or f.get('name'))
            continue
        ct = _file_content_type(f)
        data_url = file_path_to_video_data_url(path, ct, _file_name(f))
        if data_url:
            parts.append({'type': 'video_url', 'video_url': {'url': data_url}})
        else:
            log.warning('failed to build video_url for %s', f.get('name') or path)
    return parts


async def build_image_url_parts(file_items: list[dict]) -> list[dict]:
    parts = []
    for f in file_items:
        if not is_image_file(f):
            continue
        url = str(f.get('url') or '')
        if url.startswith('data:image/'):
            parts.append({'type': 'image_url', 'image_url': {'url': url}})
            continue
        path = await resolve_upload_path(f)
        if not path:
            log.warning('could not resolve image file: %s', f.get('id') or f.get('name'))
            continue
        ct = _file_content_type(f)
        data_url = file_path_to_image_data_url(path, ct, _file_name(f))
        if data_url:
            parts.append({'type': 'image_url', 'image_url': {'url': data_url}})
    return parts
