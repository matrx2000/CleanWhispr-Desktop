"""Best-effort local accelerator detection → model recommendation.

Used by the setup wizard and editor settings to suggest an editing model that
matches the machine (GPU memory / unified memory / plain RAM) instead of one
that swamps it. All probes are cheap external commands or ctypes calls with
short timeouts; call `detect()` from a worker thread.

The recommendation is driven by a provider-supplied catalog of
`InstallableModel`s (see llm.base), so it works for any LLM backend — not just
Ollama — as long as the backend advertises models with memory metadata.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass

from cleanwispr.llm.base import InstallableModel

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


# A big model can "fit" in system RAM yet crawl without a GPU, so on CPU-only
# (and AMD, where Ollama's ROCm support is uneven) we cap what we'll recommend.
_CPU_CEILING_GB = 6.0
# "Smallest usable" targets a model at least this heavy — below it, editing
# quality drops off — falling back to the absolute smallest only if nothing
# bigger fits.
_USABLE_FLOOR_GB = 5.0
# When we can't measure memory at all, stay safe rather than assume plenty: a
# small model always runs, a big one might swap or OOM.
_UNKNOWN_BUDGET_GB = 3.0


def usable_memory_gb(hardware: Hardware) -> float | None:
    """How much memory we can realistically give a model on this machine.

    - NVIDIA: dedicated VRAM (models spilling to RAM run far slower).
    - Apple: unified memory, discounted to leave the OS and apps headroom.
    - AMD / CPU: RAM-bound *and* compute-bound — capped so we never suggest a
      giant model that technically fits but runs at a crawl on the CPU.
    """
    if hardware.kind == "nvidia" and hardware.vram_gb:
        return hardware.vram_gb
    if hardware.kind == "apple" and hardware.ram_gb:
        return hardware.ram_gb * 0.7
    if hardware.ram_gb:  # amd + cpu
        return min(hardware.ram_gb * 0.6, _CPU_CEILING_GB)
    return None


def _fit_reason(model: InstallableModel, hardware: Hardware, *, fits: bool) -> str:
    if not fits:
        return (
            "the smallest option — bigger models would exceed this machine's "
            "memory and fall back to slow processing"
        )
    if hardware.kind == "nvidia" and hardware.vram_gb:
        return f"fits your {hardware.vram_gb:.0f} GB of GPU memory"
    if hardware.kind == "apple" and hardware.ram_gb:
        return f"a good fit for {hardware.ram_gb:.0f} GB of unified memory (GPU via Metal)"
    if hardware.kind == "amd":
        return "responsive on AMD GPUs (ROCm) or CPU fallback"
    if hardware.ram_gb:
        return f"runs on CPU within your {hardware.ram_gb:.0f} GB of RAM"
    return "a safe fit for this machine"


def recommend_from_catalog(
    catalog: list[InstallableModel], hardware: Hardware, prefer: str = "quality"
) -> tuple[InstallableModel, str]:
    """Pick a model from a provider catalog for this machine → (model, reason).

    prefer="quality": the most capable model that fits (best output).
    prefer="small":   the smallest model that still edits well (fastest, lightest).
    Provider-agnostic: any backend whose catalog carries memory metadata works.
    """
    if not catalog:
        raise ValueError("catalog is empty")
    # recommend only from vetted defaults; the rest of the catalog is for search
    pool = [m for m in catalog if m.recommended] or list(catalog)
    budget = usable_memory_gb(hardware)
    if budget is None:
        budget = _UNKNOWN_BUDGET_GB
    fitting = [m for m in pool if m.min_memory_gb <= budget]
    if not fitting:
        pick = min(pool, key=lambda m: m.min_memory_gb)
        return pick, _fit_reason(pick, hardware, fits=False)
    if prefer == "small":
        usable = [m for m in fitting if m.min_memory_gb >= _USABLE_FLOOR_GB]
        pick = min(usable or fitting, key=lambda m: m.min_memory_gb)
    else:
        pick = max(fitting, key=lambda m: m.min_memory_gb)
    return pick, _fit_reason(pick, hardware, fits=True)
