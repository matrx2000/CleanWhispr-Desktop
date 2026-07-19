"""Downloads for whisper models (HuggingFace) and the whisper-server binary
(GitHub releases). All functions are blocking — call from worker threads;
progress callbacks fire on the calling thread.
"""

from __future__ import annotations

import logging
import os
import stat
import sys
import tarfile
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path
from threading import Event

import httpx

from cleanwispr.stt import registry

log = logging.getLogger(__name__)

ProgressFn = Callable[[int, int | None], None]  # (received_bytes, total_bytes|None)


class DownloadError(RuntimeError):
    pass


class DownloadCancelled(DownloadError):
    pass


def download_file(
    url: str,
    dest: Path,
    *,
    progress: ProgressFn | None = None,
    cancel: Event | None = None,
) -> Path:
    """Stream url to dest atomically (tmp file + rename)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=dest.parent, suffix=".part")
    tmp = Path(tmp_name)
    try:
        with (
            os.fdopen(fd, "wb") as out,
            httpx.stream("GET", url, follow_redirects=True, timeout=60) as response,
        ):
            response.raise_for_status()
            total = int(response.headers.get("content-length", 0)) or None
            received = 0
            for chunk in response.iter_bytes(chunk_size=1024 * 256):
                if cancel is not None and cancel.is_set():
                    raise DownloadCancelled("download cancelled")
                out.write(chunk)
                received += len(chunk)
                if progress:
                    progress(received, total)
        os.replace(tmp, dest)
        return dest
    except httpx.HTTPError as exc:
        raise DownloadError(f"Download failed: {exc}") from exc
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def download_model(
    model_id: str, *, progress: ProgressFn | None = None, cancel: Event | None = None
) -> Path:
    model = registry.WHISPER_MODELS[model_id]
    dest = registry.model_path(model_id)
    log.info("downloading whisper model %s from %s", model_id, model.download_url)
    return download_file(model.download_url, dest, progress=progress, cancel=cancel)


def delete_model(model_id: str) -> None:
    registry.model_path(model_id).unlink(missing_ok=True)


# --- Parakeet models (tar.bz2 archives) ---


def extract_tar_bz2(archive: Path, dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:bz2") as tar:
        try:
            tar.extractall(dest_dir, filter="data")  # blocks path traversal
        except TypeError:  # Python < 3.12 without the filter param
            tar.extractall(dest_dir)


def download_parakeet_model(
    model_id: str, *, progress: ProgressFn | None = None, cancel: Event | None = None
) -> Path:
    model = registry.PARAKEET_MODELS[model_id]
    target_dir = registry.parakeet_model_dir(model_id)
    with tempfile.TemporaryDirectory() as tmp_dir:
        archive = Path(tmp_dir) / model.archive
        log.info("downloading parakeet model %s from %s", model_id, model.download_url)
        download_file(model.download_url, archive, progress=progress, cancel=cancel)
        extract_tar_bz2(archive, target_dir.parent)
    if not registry.is_parakeet_model_installed(model_id):
        raise DownloadError(f"Archive for {model_id} did not contain the expected files")
    return target_dir


def delete_parakeet_model(model_id: str) -> None:
    import shutil

    shutil.rmtree(registry.parakeet_model_dir(model_id), ignore_errors=True)


# --- whisper-server binary ---


def _latest_release_asset_url(repo: str, asset_name: str) -> str:
    try:
        response = httpx.get(
            f"https://api.github.com/repos/{repo}/releases/latest",
            follow_redirects=True,
            timeout=30,
            headers={"Accept": "application/vnd.github+json"},
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise DownloadError(f"Could not query {repo} releases: {exc}") from exc
    for asset in response.json().get("assets", []):
        if asset.get("name") == asset_name:
            return asset["browser_download_url"]
    raise DownloadError(f"Asset {asset_name} not found in the latest {repo} release")


def extract_binary_from_zip(zip_path: Path, binary_name: str, dest: Path) -> Path:
    """Find binary_name anywhere inside the zip and extract it to dest."""
    with zipfile.ZipFile(zip_path) as zf:
        member = next(
            (n for n in zf.namelist() if n.rsplit("/", 1)[-1] == binary_name),
            None,
        )
        if member is None:
            raise DownloadError(f"{binary_name} not found in {zip_path.name}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(member) as src, open(dest, "wb") as out:
            while chunk := src.read(1024 * 256):
                out.write(chunk)
    if sys.platform != "win32":
        dest.chmod(dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return dest


def download_server_binary(
    variant: str = "cpu", *, progress: ProgressFn | None = None, cancel: Event | None = None
) -> Path:
    """Fetch the prebuilt whisper-server (cpu/cuda/vulkan) for this platform.
    GPU zips bundle their runtime libs (cuBLAS etc.) — extract everything next
    to the binary, not just the exe."""
    asset = registry.server_binary_asset_name(variant)
    url = _latest_release_asset_url(registry.WHISPER_SERVER_REPO, asset)
    dest = registry.server_binary_path(variant)
    with tempfile.TemporaryDirectory() as tmp_dir:
        zip_path = Path(tmp_dir) / asset
        log.info("downloading whisper-server (%s) from %s", variant, url)
        download_file(url, zip_path, progress=progress, cancel=cancel)
        return _extract_server_zip(zip_path, variant, dest)


def _extract_server_zip(zip_path: Path, variant: str, dest: Path) -> Path:
    """Extract the server binary to dest and any companion files (DLLs/.so)
    alongside it, flattening directories."""
    binary_name = registry.server_binary_name_in_zip(variant)
    found = False
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.namelist():
            if member.endswith("/"):
                continue
            file_name = member.rsplit("/", 1)[-1]
            if file_name == binary_name:
                target, found = dest, True
            elif file_name.lower().endswith((".dll", ".so")) or ".so." in file_name.lower():
                target = dest.parent / file_name
            else:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, open(target, "wb") as out:
                while chunk := src.read(1024 * 256):
                    out.write(chunk)
    if not found:
        raise DownloadError(f"{binary_name} not found in {zip_path.name}")
    if sys.platform != "win32":
        dest.chmod(dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return dest
