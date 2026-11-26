"""Local AI layer: an open-weights model served on the same machine as the warehouse.

Everything here talks to a model through one small interface (`llm.ChatClient`) so the
backend can be mlx_lm.server, Ollama or llama.cpp without code changes, and so tests run
against a scripted fake with no model at all.

  llm.py       client for OpenAI-compatible local servers, JSON output, a scripted fake
  extract.py   read the numbers in a supplemental page into checked JSON
  ask.py       answer questions from the warehouse with read-only SQL and cited sources
  sqlguard.py  the read-only rules every model-written query must pass
  dataset.py   build train/valid/test sets (mlx-lm chat format) from the warehouse
  evaluate.py  score a model against held-out REITs and a hand-checked gold file
  train.py     mlx-lm LoRA training, fusing and serving, driven from one YAML recipe
  models.py    the model presets we recommend, with memory needs and licenses
  cli.py       `reitstreet-ai <command>`

Design: docs/adr/0012-local-model.md. Setup: docs/mac/README.md.
"""
