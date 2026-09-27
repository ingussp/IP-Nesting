"""Detect the video cards (GPUs) installed in the current machine.

The IP-Nesting workbench lets the user pick an OpenCL GPU for the nesting
CLI.  This module provides that list independently of the CLI executable, so
the available cards can be shown even when the bundled ``clinesting.exe`` is
not present (it is intentionally git-ignored).

Detection tries several backends in order and degrades gracefully: if no
optional GPU library is installed, the operating system is queried through
the standard library only.  The result is always a list of ``(index, label)``
tuples, possibly empty, and the public function never raises.
"""

import os
import subprocess
import sys


def detect_gpu_devices():
    """Return ``[(index, label), ...]`` for the GPUs found on this machine.

    ``index`` is a stable integer and ``label`` a human-readable device name.
    An empty list means no GPU could be identified.
    """
    for backend in (_opencl_devices, _nvidia_devices, _system_devices):
        try:
            devices = backend()
        except Exception:
            devices = []
        if devices:
            return devices
    return []


# Enumerate OpenCL GPU devices through pyopencl (cross-vendor: NVIDIA, AMD,
# Intel).  This mirrors the device ordering the nesting CLI uses.
def _opencl_devices():
    try:
        import pyopencl as cl  # type: ignore
    except Exception:
        return []
    devices = []
    try:
        for platform in cl.get_platforms():
            try:
                for device in platform.get_devices(device_type=cl.device_type.GPU):
                    devices.append((len(devices), device.name))
            except Exception:
                continue
    except Exception:
        return []
    return devices


# Enumerate NVIDIA GPUs through GPUtil (a thin wrapper around nvidia-smi).
def _nvidia_devices():
    try:
        import GPUtil  # type: ignore
    except Exception:
        return []
    devices = []
    for gpu in GPUtil.getGPUs():
        try:
            devices.append((int(gpu.id), str(gpu.name)))
        except Exception:
            continue
    return devices


# Fall back to the operating system's video-controller list using only the
# standard library, so the feature works without any optional dependency.
def _system_devices():
    if os.name == "nt":
        return _windows_devices()
    if sys.platform == "darwin":
        return _macos_devices()
    return _linux_devices()


def _windows_devices():
    output = _run_command([
        "powershell", "-NoProfile", "-Command",
        "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name",
    ])
    return _parse_lines(output)


def _linux_devices():
    output = _run_command(
        ["sh", "-c", "lspci | grep -Ei 'vga|3d|display'"]
    )
    return _parse_lines(output)


def _macos_devices():
    output = _run_command(["system_profiler", "SPDisplaysDataType"])
    devices = []
    for line in (output or "").splitlines():
        line = line.strip()
        if line.startswith("Chipset Model:"):
            name = line.split(":", 1)[1].strip()
            if name:
                devices.append((len(devices), name))
    return devices


def _run_command(arguments):
    try:
        output = subprocess.check_output(
            arguments, stderr=subprocess.DEVNULL, timeout=15
        )
    except Exception:
        return ""
    if isinstance(output, bytes):
        output = output.decode("utf-8", errors="replace")
    return output


def _parse_lines(output):
    devices = []
    for line in (output or "").splitlines():
        line = line.strip()
        if line:
            devices.append((len(devices), line))
    return devices
