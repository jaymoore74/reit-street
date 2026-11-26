"""LoRA fine-tuning with mlx-lm on Apple silicon: `reitstreet-ai train --recipe extract-qa`.

A recipe (src/reitstreet/ai/recipes/*.toml) fixes everything that decides a result: base
model, dataset folder, LoRA rank and layers, learning rate, iterations, sequence length.
Each run gets its own folder under data/ai/runs/<UTC time>-<recipe>/ holding the recipe,
the generated mlx-lm config, the dataset manifest it trained on, the git commit, the
command line, the training log and the adapter. A run can always be explained and
repeated; nothing is overwritten.

Training itself is mlx-lm's (`python -m mlx_lm lora`), which runs only on Apple silicon.
On any other machine `--dry-run` writes the folder and prints the command.
"""

from __future__ import annotations

import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

from ..core.errors import ConfigError
from . import models

# recipe [lora] keys -> mlx-lm config keys. Anything else in [mlx] is passed through as is.
TRAIN_KEYS = (
    "fine_tune_type",
    "num_layers",
    "batch_size",
    "iters",
    "learning_rate",
    "max_seq_length",
    "grad_checkpoint",
    "mask_prompt",
    "steps_per_report",
    "steps_per_eval",
    "val_batches",
    "save_every",
    "seed",
    "grad_accumulation_steps",
    "optimizer",
)


def is_apple_silicon() -> bool:
    return sys.platform == "darwin" and platform.machine() == "arm64"


def recipe_path(name: str) -> Path:
    p = Path(name)
    if p.suffix == ".toml" and p.exists():
        return p
    res = resources.files("reitstreet.ai").joinpath("recipes", f"{name}.toml")
    if not res.is_file():
        raise ConfigError(f"recipe {name!r} not found (built-in recipes: {', '.join(list_recipes())})")
    return Path(str(res))


def list_recipes() -> list[str]:
    d = resources.files("reitstreet.ai").joinpath("recipes")
    return sorted(p.name[:-5] for p in d.iterdir() if p.name.endswith(".toml"))


def load_recipe(name: str) -> dict[str, Any]:
    with recipe_path(name).open("rb") as f:
        r = tomllib.load(f)
    for k in ("name", "model", "train"):
        if k not in r:
            raise ConfigError(f"recipe {name}: missing [{k}]" if k == "train" else f"recipe {name}: missing {k}")
    return r


def _yaml_scalar(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    return json.dumps(str(v))  # JSON strings are valid YAML


def to_yaml(d: dict[str, Any], indent: int = 0) -> str:
    """Enough YAML for mlx-lm configs (nested maps, scalars, lists of scalars); avoids a PyYAML dependency."""
    pad = "  " * indent
    out = []
    for k, v in d.items():
        if isinstance(v, dict):
            out.append(f"{pad}{k}:")
            out.append(to_yaml(v, indent + 1))
        elif isinstance(v, list):
            out.append(f"{pad}{k}: [{', '.join(_yaml_scalar(x) for x in v)}]")
        else:
            out.append(f"{pad}{k}: {_yaml_scalar(v)}")
    return "\n".join(x for x in out if x != "")


def mlx_config(recipe: dict[str, Any], data_dir: Path, adapter_dir: Path) -> dict[str, Any]:
    t = recipe["train"]
    cfg: dict[str, Any] = {
        "model": recipe["model"],
        "train": True,
        "data": str(data_dir),
        "adapter_path": str(adapter_dir),
    }
    for k in TRAIN_KEYS:
        if k in t:
            cfg[k] = t[k]
    if "lora" in recipe:
        cfg["lora_parameters"] = dict(recipe["lora"])
    cfg.update(recipe.get("mlx", {}))
    return cfg


def git_commit(repo: Path) -> str:
    try:
        return subprocess.run(  # noqa: S603
            ["git", "-C", str(repo), "rev-parse", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


@dataclass
class Run:
    path: Path
    command: list[str]
    config: dict[str, Any]


def prepare(recipe_name: str, ai_root: Path, data_dir: Path | None = None, *, repo: Path | None = None) -> Run:
    recipe = load_recipe(recipe_name)
    data = Path(data_dir or ai_root / "datasets" / recipe.get("dataset", "current"))
    for f in ("train.jsonl", "valid.jsonl"):
        if not (data / f).exists():
            raise ConfigError(f"{data / f} not found: run `reitstreet-ai build-dataset` first")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run = ai_root / "runs" / f"{stamp}-{recipe['name']}"
    (run / "adapter").mkdir(parents=True)
    base = models.resolve(recipe["model"], ai_root)
    cfg = mlx_config({**recipe, "model": base}, data.resolve(), (run / "adapter").resolve())
    (run / "lora_config.yaml").write_text(to_yaml(cfg) + "\n", encoding="utf-8")
    shutil.copy(recipe_path(recipe_name), run / "recipe.toml")
    if (data / "manifest.json").exists():
        shutil.copy(data / "manifest.json", run / "dataset_manifest.json")
    cmd = [sys.executable, "-m", "mlx_lm", "lora", "-c", str((run / "lora_config.yaml").resolve())]
    meta = {
        "recipe": recipe["name"],
        "base_model": base,
        "base_preset": recipe["model"] if models.by_key(recipe["model"]) else None,
        "env": dict(recipe.get("env", {})),
        "dataset": str(data.resolve()),
        "git_commit": git_commit(repo or Path.cwd()),
        "created_at": stamp,
        "command": shlex.join(cmd),
        "host": {"platform": sys.platform, "machine": platform.machine(), "python": platform.python_version()},
    }
    (run / "run.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return Run(run, cmd, cfg)


def execute(run: Run) -> int:
    if not is_apple_silicon():
        raise ConfigError("mlx-lm training needs a Mac with Apple silicon; use --dry-run here")
    meta = json.loads((run.path / "run.json").read_text(encoding="utf-8"))
    base = Path(meta["base_model"])
    if base.is_absolute() and not (base / "config.json").exists():
        raise ConfigError(f"{base} is missing: run `reitstreet-ai models --convert {meta.get('base_preset')}` first")
    env = {**os.environ, **{k: str(v) for k, v in meta.get("env", {}).items()}}
    with (run.path / "train.log").open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(run.command, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)  # noqa: S603
        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stdout.write(line)
            log.write(line)
        rc = proc.wait()
    meta = json.loads((run.path / "run.json").read_text(encoding="utf-8"))
    meta["finished_at"] = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    meta["exit_code"] = rc
    (run.path / "run.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return rc


def fuse_command(run_dir: Path, *, out: Path | None = None) -> list[str]:
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    save = out or (run_dir / "fused")
    return [
        sys.executable,
        "-m",
        "mlx_lm",
        "fuse",
        "--model",
        meta["base_model"],
        "--adapter-path",
        str((run_dir / "adapter").resolve()),
        "--save-path",
        str(save.resolve()),
    ]


def serve_command(model: str, *, adapter: Path | None = None, port: int = 8080) -> list[str]:
    cmd = [sys.executable, "-m", "mlx_lm", "server", "--model", model, "--host", "127.0.0.1", "--port", str(port)]
    if adapter is not None:
        cmd += ["--adapter-path", str(adapter.resolve())]
    return cmd
