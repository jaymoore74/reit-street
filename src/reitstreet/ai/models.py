"""Recommended open-weights models for a Mac, and where each lives on disk.

Checked 2026-11-26 against the model cards and mlx-community repos (sources in
docs/adr/0012-local-model.md). Re-check before buying hardware or switching models.

A preset key (e.g. "qwen3.8-27b-8bit") can stand anywhere a model is expected: the
[llm] model setting, a training recipe, `reitstreet-ai serve --model`. It resolves to
data/ai/models/<key>, which `reitstreet-ai models --convert <key>` builds from the
original Hugging Face repo with mlx_lm.convert. We convert ourselves instead of using
the mlx-community builds because those were made with mlx-vlm for these multimodal
models, and mlx-lm LoRA wants an mlx-lm conversion.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from ..core.errors import ConfigError


@dataclass(frozen=True)
class Preset:
    key: str
    hf_repo: str  # original weights, the source for mlx_lm.convert
    mlx_repo: str  # ready-made MLX build, fine for serving and a first try
    q_bits: int
    license: str
    weights_gb: float  # size of the converted weights
    min_memory_gb: int  # smallest Mac that serves it with MySQL and the OS beside it
    train_memory_gb: int  # smallest Mac that LoRA-trains it with the recipe settings
    role: str
    note: str


PRESETS: tuple[Preset, ...] = (
    Preset(
        key="qwen3.8-27b-8bit",
        hf_repo="Qwen/Qwen3.8-27B",
        mlx_repo="mlx-community/Qwen3.8-27B-8bit",
        q_bits=8,
        license="Apache-2.0",
        weights_gb=29.0,
        min_memory_gb=48,
        # LoRA rank 32 on 16 of 64 layers peaked at 57.3 GB (M3 Ultra, published run)
        train_memory_gb=96,
        role="primary",
        note="dense 27B; best instruction following that fits (IFBench 79.5); extraction, SQL, training",
    ),
    Preset(
        key="qwen3.8-27b-4bit",
        hf_repo="Qwen/Qwen3.8-27B",
        mlx_repo="mlx-community/Qwen3.8-27B-4bit",
        q_bits=4,
        license="Apache-2.0",
        weights_gb=16.1,
        min_memory_gb=32,
        train_memory_gb=64,
        role="fast",
        note="same model, about twice as fast, slightly less exact; smoke tests and QLoRA",
    ),
    Preset(
        key="qwen3.6-35b-a3b-6bit",
        hf_repo="Qwen/Qwen3.6-35B-A3B",
        mlx_repo="mlx-community/Qwen3.6-35B-A3B-6bit",
        q_bits=6,
        license="Apache-2.0",
        weights_gb=29.1,
        min_memory_gb=48,
        train_memory_gb=96,
        role="bulk",
        note="mixture of experts, 3B active: fastest for bulk table extraction; LoRA yes, DoRA no",
    ),
    Preset(
        key="muse-glimmer-30b-4bit",
        hf_repo="meta-models/Muse-Glimmer-30B",
        mlx_repo="mlx-community/Muse-Glimmer-30B-4bit",
        q_bits=4,
        license="Apache-2.0",
        weights_gb=19.4,
        min_memory_gb=32,
        train_memory_gb=64,
        role="challenger",
        note="dense 30B, strongest tool use (MCP Atlas 75.5); A/B it against the primary on Q&A",
    ),
)


def by_key(key: str) -> Preset | None:
    return next((p for p in PRESETS if p.key == key), None)


def by_role(role: str) -> Preset:
    for p in PRESETS:
        if p.role == role:
            return p
    raise KeyError(role)


def fits(p: Preset, mem_gb: float | None, *, training: bool = False) -> bool:
    if mem_gb is None:
        return False
    return mem_gb >= (p.train_memory_gb if training else p.min_memory_gb)


def local_dir(p: Preset, ai_root: Path) -> Path:
    return ai_root / "models" / p.key


def resolve(model: str, ai_root: Path, *, must_exist: bool = False) -> str:
    """A preset key becomes its local folder; anything else (a path, a repo id) is returned as is."""
    p = by_key(model)
    if p is None:
        return model
    d = local_dir(p, ai_root)
    if must_exist and not (d / "config.json").exists():
        raise ConfigError(f"{d} is missing: run `reitstreet-ai models --convert {p.key}` first")
    return str(d.resolve())


def convert_command(p: Preset, ai_root: Path) -> list[str]:
    return [
        sys.executable,
        "-m",
        "mlx_lm",
        "convert",
        "--hf-path",
        p.hf_repo,
        "-q",
        "--q-bits",
        str(p.q_bits),
        "--mlx-path",
        str(local_dir(p, ai_root).resolve()),
    ]


def machine_memory_gb() -> float | None:
    try:
        if sys.platform == "darwin":
            out = subprocess.run(["/usr/sbin/sysctl", "-n", "hw.memsize"], capture_output=True, text=True, check=True)
            return int(out.stdout.strip()) / 1024**3
        if os.path.exists("/proc/meminfo"):
            with open("/proc/meminfo", encoding="ascii") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        return int(line.split()[1]) / 1024**2
    except (OSError, ValueError, subprocess.CalledProcessError):
        return None
    return None


def table(mem_gb: float | None, ai_root: Path | None = None) -> str:
    head = f"{'key':<24} {'role':<11} {'GB':>5} {'serve':>6} {'train':>6} {'here':<6} license"
    lines = [f"this machine: {mem_gb:.0f} GB" if mem_gb else "this machine: memory unknown", head, "-" * len(head)]
    for p in PRESETS:
        here = "train" if fits(p, mem_gb, training=True) else "serve" if fits(p, mem_gb) else "no"
        lines.append(
            f"{p.key:<24} {p.role:<11} {p.weights_gb:>5.1f} {p.min_memory_gb:>6} "
            f"{p.train_memory_gb:>6} {here:<6} {p.license}"
        )
        status = ""
        if ai_root is not None:
            status = " [converted]" if (local_dir(p, ai_root) / "config.json").exists() else " [not converted]"
        lines.append(f"    {p.hf_repo}{status}: {p.note}")
    return "\n".join(lines)
