# ADR 0012: A local model on a Mac, fine-tuned on our own warehouse

Status: accepted, 2026-11-26

## Context
Two jobs need a language model: reading supplemental pages into numbers the parser
missed, and answering "give me everything on NNN" from the warehouse. Sending
supplementals and the warehouse to a hosted API every day is a running cost and puts
our data outside our machines. A Mac with 128 GB of unified memory can hold a 27-30B
open-weights model, serve it, and LoRA-train it, next to a local MySQL.

The risk with any model reading financials is a confident wrong number. Whatever we
build has to make a wrong number detectable, not just unlikely.

## Decision
1. **Model:** Qwen3.8-27B (Qwen/Qwen3.8-27B), Apache-2.0, at 8-bit for production and
   training, 4-bit for smoke tests. It had the best instruction-following score of the
   open models that fit (IFBench 79.5) and a published LoRA run on Apple silicon (rank 32,
   16 of 64 layers, 57.3 GB peak). Fallback for bulk extraction: Qwen3.6-35B-A3B (MoE,
   3B active, Apache-2.0). Challenger for Q&A: Muse-Glimmer-30B (Apache-2.0). Models with
   use or revenue conditions in their license (Qwen3.8-Flash-Next's Qwen Community
   License, Llama 4) are out, because this is a commercial product.
2. **Runtime:** mlx-lm (0.32.x) for converting, LoRA training, fusing and serving
   (`mlx_lm.server`, 127.0.0.1:8080). Ollama is supported as a backend because it enforces
   a JSON schema during decoding; mlx_lm.server does not. llama.cpp works through the
   generic OpenAI-compatible backend.
3. **Our code talks to one interface:** an OpenAI-compatible chat endpoint. It refuses any
   host that is not this machine unless `[llm] allow_remote = true`.
4. **The model never does arithmetic or units.** Extraction returns each number exactly as
   printed plus the page's stated scale. Code applies the scale. Any number not found on
   the page is dropped and counted (the grounding check).
5. **Q&A reads, never writes.** The model writes SQL; a guard allows one SELECT over the
   client views (`dl_*`) and a few reference tables, adds a row limit, and runs it in a
   READ ONLY transaction with a 15-second limit, as a MySQL user with SELECT only.
6. **Training data comes from the warehouse, not from the model.** Extraction examples
   are parser output with confidence >= 0.9 whose numbers appear on the page. Q&A examples
   run real SQL and put the real rows in the conversation. Companies are split by a hash
   of the ticker (10% test, 10% valid), so a test company is never seen in training.
7. **A model is promoted only through gates:** extraction recall >= 95%, precision >= 97%,
   ungrounded numbers <= 0.5%, Q&A accuracy >= 90%, and every hand-checked gold value
   right. Every eval report is saved and compared with the previous model.
8. **Every training run is a folder** (data/ai/runs/...) with the recipe, the dataset
   manifest and hashes, the git commit, the command and the log. Nothing is overwritten.

## Consequences
- Day-to-day extraction and Q&A cost electricity only, and the data stays on the Mac.
- A 128 GB Mac is the floor for training the primary model; 64 GB can serve it and run
  the smoke recipe only.
- mlx-lm support for Qwen3.8's hybrid architecture is new. A published run needed
  MLX_DISABLE_COMPILE=1 (set by the recipes) and was slow before an upstream fix. Plan
  for some friction on the first run.
- Model choice is data (`src/reitstreet/ai/models.py`), so switching later is one line
  plus an eval.

## Sources (read 2026-11-26)
- https://huggingface.co/Qwen/Qwen3.8-27B (license, architecture, IFBench)
- https://huggingface.co/collections/mlx-community/qwen38 (MLX builds and sizes)
- https://huggingface.co/mlx-community/Qwen3.6-35B-A3B-6bit
- https://huggingface.co/meta-models/Muse-Glimmer-30B and https://huggingface.co/blog/muse-glimmer
- https://huggingface.co/Qwen/Qwen3.8-Flash-Next (Qwen Community License terms)
- https://pypi.org/project/mlx_lm and https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/LORA.md
- https://raw.githubusercontent.com/ml-explore/mlx-lm/main/mlx_lm/SERVER.md
- https://docs.ollama.com/api/openai-compatibility and https://docs.ollama.com/capabilities/structured-outputs
- https://dev.to/mihai_leanzero/how-to-fine-tune-qwen38-27b-with-lora-on-a-mac-mlx-fine-tuning-from-scratch-2kl4 (LoRA memory and speed, M3 Ultra)
- https://support.apple.com/126319 (MacBook Pro M5 Max memory options)
