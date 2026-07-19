"""Best-effort local accelerator detection → Ollama model recommendation.

Used by the setup wizard to suggest an editing model that matches the
machine (GPU memory / unified memory / plain RAM) instead of one that
swamps it. All probes are cheap external commands or ctypes calls with
short timeouts; call `detect()` from a worker thread.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass

_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


@dataclass(frozen=True, slots=True)
class Hardware:
    kind: str  # nvidia | amd | apple | cpu
    name: str  # human-readable device name
    vram_gb: float | None  # dedicated GPU memory (Apple: unified = RAM)
    ram_gb: float | None


def _run(cmd: list[str]) -> str:
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=5,
        creationflags=_CREATE_NO_WINDOW,
    ).stdout


def _system_ram_gb() -> float | None:
    try:
        if sys.platform == "win32":
            import ctypes

            class MemoryStatusEx(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_uint32),
                    ("dwMemoryLoad", ctypes.c_uint32),
                    ("ullTotalPhys", ctypes.c_uint64),
                    ("ullAvailPhys", ctypes.c_uint64),
                    ("ullTotalPageFile", ctypes.c_uint64),
                    ("ullAvailPageFile", ctypes.c_uint64),
                    ("ullTotalVirtual", ctypes.c_uint64),
                    ("ullAvailVirtual", ctypes.c_uint64),
                    ("ullAvailExtendedVirtual", ctypes.c_uint64),
                ]

            status = MemoryStatusEx()
            status.dwLength = ctypes.sizeof(MemoryStatusEx)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
            return status.ullTotalPhys / 1024**3
        if sys.platform == "darwin":
            return int(_run(["sysctl", "-n", "hw.memsize"]).strip()) / 1024**3
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) / 1024**2  # kB → GB
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return None


def _nvidia() -> Hardware | None:
    try:
        out = _run([
            "nvidia-smi", "--query-gpu=name,memory.total",
            "--format=csv,noheader,nounits",
        ])
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = [line for line in out.strip().splitlines() if "," in line]
    if not lines:
        return None
    name, _, memory = lines[0].partition(",")
    try:
        vram = float(memory.strip()) / 1024  # MiB → GB
    except ValueError:
        vram = None
    return Hardware("nvidia", name.strip(), vram, _system_ram_gb())


def _apple() -> Hardware | None:
    if sys.platform != "darwin":
        return None
    import platform

    if platform.machine() != "arm64":
        return None
    ram = _system_ram_gb()
    try:
        chip = _run(["sysctl", "-n", "machdep.cpu.brand_string"]).strip() or "Apple Silicon"
    except (OSError, subprocess.TimeoutExpired):
        chip = "Apple Silicon"
    # unified memory: the GPU can use (most of) system RAM
    return Hardware("apple", chip, ram, ram)


def _amd() -> Hardware | None:
    names: list[str] = []
    try:
        if sys.platform == "win32":
            out = _run([
                "powershell", "-NoProfile", "-Command",
                "(Get-CimInstance Win32_VideoController).Name",
            ])
            names = [line.strip() for line in out.splitlines() if line.strip()]
        elif sys.platform.startswith("linux"):
            out = _run(["lspci"])
            names = [line for line in out.splitlines() if "VGA" in line or "3D" in line]
    except (OSError, subprocess.TimeoutExpired):
        return None
    for name in names:
        upper = name.upper()
        if "AMD" in upper or "RADEON" in upper:
            return Hardware("amd", name.strip()[:64], None, _system_ram_gb())
    return None


def detect() -> Hardware:
    """Blocking (up to a few seconds) — run on a worker thread."""
    for probe in (_nvidia, _apple, _amd):
        hardware = probe()
        if hardware:
            return hardware
    return Hardware("cpu", "CPU (no supported GPU found)", None, _system_ram_gb())


def recommended_ollama_model(hardware: Hardware) -> tuple[str, str]:
    """Pick a Gemma size that fits the machine → (ollama tag, reason).

    Strong GPUs get Gemma 4 (12B/26B/31B — no smaller sizes exist);
    modest hardware gets a small Gemma 3 so the machine stays responsive.
    """
    if hardware.kind == "nvidia" and hardware.vram_gb:
        # thresholds sit just under marketed sizes: a "24 GB" card reports
        # ~23.99 GB, a "16 GB" card ~15.99, and must still hit its tier
        vram = hardware.vram_gb
        if vram >= 23:
            return "gemma4:31b", f"fits your {vram:.0f} GB of GPU memory with room to spare"
        if vram >= 15:
            return "gemma4:26b", f"a strong fit for your {vram:.0f} GB of GPU memory"
        if vram >= 9.5:
            return "gemma4:12b", f"a great fit for your {vram:.0f} GB of GPU memory"
        if vram >= 4.5:
            return "gemma3:4b", (
                f"sized for your {vram:.0f} GB of GPU memory — fast, no spill into RAM"
            )
        return "gemma3:1b", (
            "small enough for your GPU memory; larger models would fall back to slow CPU"
        )
    if hardware.kind == "apple":
        ram = hardware.ram_gb or 8
        if ram >= 64:
            return "gemma4:31b", f"your {ram:.0f} GB of unified memory handles it comfortably"
        if ram >= 48:
            return "gemma4:26b", f"a strong fit for {ram:.0f} GB of unified memory (GPU via Metal)"
        if ram >= 24:
            return "gemma4:12b", f"a good fit for {ram:.0f} GB of unified memory (GPU via Metal)"
        if ram >= 16:
            return "gemma3:4b", (
                f"leaves plenty of your {ram:.0f} GB of unified memory free for other apps"
            )
        return "gemma3:1b", "keeps memory pressure low on this Mac"
    if hardware.kind == "amd":
        return "gemma3:4b", (
            "Ollama uses AMD GPUs via ROCm where supported and falls back to "
            "CPU otherwise — 4B stays responsive either way"
        )
    ram = hardware.ram_gb or 8
    if ram >= 16:
        return "gemma3:4b", "runs well on CPU with your amount of RAM"
    return "gemma3:1b", "small enough to run on CPU without slowing your PC down"
