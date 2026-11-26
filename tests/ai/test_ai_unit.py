"""Local model layer: everything that runs without a model, a Mac or a database."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from reitstreet.ai import evaluate, extract, models, sqlguard, train
from reitstreet.ai.llm import LLMError, OpenAICompatClient, ScriptedClient, client_from_settings, parse_json
from reitstreet.core import config as cfgmod
from reitstreet.core.errors import ConfigError
from tests.ai.pdfmini import SUPPLEMENTAL_PAGE, make_pdf


# ------------------------------------------------------------------------- sql guard
@pytest.mark.parametrize(
    "sql",
    [
        "select * from dl_metrics where ticker = 'ESS'",
        "SELECT value FROM dl_metrics WHERE ticker='O' ORDER BY period_end DESC LIMIT 1",
        "with a as (select * from dl_metrics) select * from a join ref_company c on c.ticker = a.ticker",
        "select extract(year from period_end) y, count(*) from dl_metrics group by y",
        "select * from dl_metrics m, ref_company c where m.ticker = c.ticker",
        "select * from (select ticker from dl_properties) x",
        "select 'drop table x; delete' as s from dl_companies",  # keywords inside a string are data
    ],
)
def test_sqlguard_allows_reads_of_client_views(sql: str) -> None:
    out = sqlguard.check(sql)
    assert "limit" in out.lower()


@pytest.mark.parametrize(
    ("sql", "why"),
    [
        ("delete from dl_metrics", "SELECT"),
        ("select * from wh_metric_value", "not allowed"),
        ("select * from dl_metrics; drop table ref_company", "one statement"),
        ("select load_file('/etc/passwd')", "LOAD_FILE"),
        ("select * from mysql.user", "MYSQL"),
        ("select * from dl_metrics m, wh_property p", "wh_property"),
        ("select * from dl_metrics into outfile '/tmp/x'", "INTO"),
        ("select sleep(100)", "SLEEP"),
        ("select * from information_schema.tables", "INFORMATION_SCHEMA"),
        ("select * from reitstreet.dl_metrics", "schema-qualified"),
        ("/* hi */ update dl_metrics set value = 1", "SELECT"),
        ("", "empty"),
    ],
)
def test_sqlguard_refuses(sql: str, why: str) -> None:
    with pytest.raises(sqlguard.UnsafeSql, match=why):
        sqlguard.check(sql)


def test_sqlguard_keeps_an_existing_limit() -> None:
    assert sqlguard.check("select * from dl_metrics limit 5") == "select * from dl_metrics limit 5"


# ------------------------------------------------------------------------- client
def test_parse_json_handles_fences_thinking_and_prose() -> None:
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('<think>maybe {"x": 0}</think>Here: {"a": [1, 2]} done') == {"a": [1, 2]}
    assert parse_json("[1, 2]") == [1, 2]
    with pytest.raises(LLMError):
        parse_json("no json here")


class FakeResponse:
    def __init__(self, status: int, data: dict[str, Any]):
        self.status_code = status
        self._data = data
        self.text = json.dumps(data)

    def json(self) -> dict[str, Any]:
        return self._data


class FakeSession:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def post(self, url: str, json: dict[str, Any], headers: dict[str, str], timeout: float) -> FakeResponse:
        self.sent.append({"url": url, "json": json, "headers": headers})
        return FakeResponse(
            200, {"model": "m", "choices": [{"message": {"content": "hi"}}], "usage": {"prompt_tokens": 3}}
        )


def test_client_posts_openai_chat_and_reads_reply() -> None:
    sess = FakeSession()
    c = OpenAICompatClient("http://127.0.0.1:11434", "qwen", backend="ollama", session=sess)  # type: ignore[arg-type]
    r = c.chat([{"role": "user", "content": "x"}], json_schema={"type": "object"})
    assert r.text == "hi" and r.prompt_tokens == 3
    body = sess.sent[0]["json"]
    assert sess.sent[0]["url"] == "http://127.0.0.1:11434/v1/chat/completions"
    assert body["model"] == "qwen" and body["temperature"] == 0.0
    assert body["response_format"]["type"] == "json_schema"


def test_client_refuses_remote_servers_unless_allowed() -> None:
    with pytest.raises(ConfigError, match="not on this machine"):
        OpenAICompatClient("https://api.example.com", "m")
    OpenAICompatClient("https://api.example.com", "m", allow_remote=True)


def test_client_from_settings_defaults_per_backend() -> None:
    s = cfgmod.Settings(llm_backend="ollama")
    assert client_from_settings(s).base_url == "http://127.0.0.1:11434"
    with pytest.raises(ConfigError):
        client_from_settings(cfgmod.Settings(llm_backend="cloud"))
    assert client_from_settings(cfgmod.Settings(llm_backend="ollama", llm_model="qwen3.8")).model == "qwen3.8"


def test_mlx_client_names_a_model_only_when_asked(tmp_path: Path) -> None:
    s = cfgmod.Settings(llm_backend="mlx", llm_model="qwen3.8-27b-8bit", data_dir=tmp_path)
    assert client_from_settings(s).model == ""  # whatever the server was started with
    assert client_from_settings(s, model="qwen3.8-27b-4bit").model.endswith("models/qwen3.8-27b-4bit")


# ------------------------------------------------------------------------- config profiles
def test_profile_selects_its_config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.local.ini").write_text("[db]\nhost = localhost\n[llm]\nmodel = m1\n", encoding="utf-8")
    (tmp_path / "config.ini").write_text("[db]\nhost = aurora.example\n", encoding="utf-8")
    s = cfgmod.load(environ={"REITSTREET_PROFILE": "local"})
    assert (s.db_host, s.llm_model, s.profile) == ("localhost", "m1", "local")
    assert cfgmod.load(environ={}).db_host == "aurora.example"
    with pytest.raises(ConfigError, match=r"config\.aurora\.ini"):
        cfgmod.load(environ={"REITSTREET_PROFILE": "aurora"})
    with pytest.raises(ConfigError):
        cfgmod.load(environ={"REITSTREET_PROFILE": "../etc"})
    s = cfgmod.load(environ={"REITSTREET_PROFILE": "local", "REITSTREET_AI_DB_URL": "mysql+pymysql://ro:pw@x/y"})
    assert s.redacted()["ai_db_url"] == "***"


# ------------------------------------------------------------------------- extraction
def test_parse_printed() -> None:
    assert extract.parse_printed("(1,234.5)") == Decimal("-1234.5")
    assert extract.parse_printed("$12.00") == Decimal("12.00")
    assert extract.parse_printed("4.5%") == Decimal("4.5")
    assert extract.parse_printed("5.2x") == Decimal("5.2")
    assert extract.parse_printed("—") is None
    assert extract.parse_printed("abc") is None


def test_grounding_catches_numbers_not_on_the_page() -> None:
    page = "Rental revenues   125,400   (41,200)   5.2%"
    assert extract.grounded("125,400", page)
    assert extract.grounded("(41,200)", page)
    assert extract.grounded("5.2%", page)
    assert not extract.grounded("125,401", page)
    assert not extract.grounded("12,540", page)


def page_text() -> str:
    return extract.pdf_pages(make_pdf([SUPPLEMENTAL_PAGE]))[1]


def model_reply(*, invent: bool = False) -> str:
    vals = [
        {
            "label": "Rental revenues",
            "column": "Q3 2024",
            "period_end": "2024-09-30",
            "printed": "125,400",
            "unit": "USD",
        },
        {"label": "Property operating expenses", "column": "Q3 2024", "printed": "(41,200)", "unit": "USD"},
        {"label": "NOI growth", "column": "Q3 2024", "printed": "5.2%", "unit": "pct"},
    ]
    if invent:
        vals.append({"label": "FFO per share", "printed": "1.87", "unit": "USD/shares"})
    return (
        "```json\n" + json.dumps({"table_title": "Same-Property NOI", "scale": "thousands", "values": vals}) + "\n```"
    )


def test_extract_page_scales_money_not_percentages_and_drops_invented_numbers() -> None:
    client = ScriptedClient([model_reply(invent=True)])
    out = extract.extract_page(client, page_text(), ticker="ACME", period_end="2024-09-30", page_no=1)
    assert out.error is None and out.ungrounded == 1
    got = {v.label: v.value for v in out.values}
    assert got == {
        "Rental revenues": Decimal("125400000"),
        "Property operating expenses": Decimal("-41200000"),
        "NOI growth": Decimal("5.2"),
    }
    prompt = client.calls[0][1]["content"]
    assert "Company: ACME" in prompt and "125,400" in prompt


def test_extract_page_reports_unreadable_replies() -> None:
    out = extract.extract_page(ScriptedClient(["sorry, cannot"]), page_text())
    assert out.error and not out.values


def test_label_rules_load_from_the_metric_map() -> None:
    rules = extract.load_label_rules()
    assert rules
    assert all(code for _, _, code in rules)


# ------------------------------------------------------------------------- evaluation
def test_score_page_counts_matches_label_hits_and_wrong_numbers() -> None:
    target = json.loads(model_reply().split("```json\n")[1].split("\n```")[0])
    pred = extract.read_reply(target, page_text())
    assert evaluate.score_page(pred, target) == (3, 3, 3, 3, 3)
    wrong = json.loads(json.dumps(target))
    wrong["values"][0]["printed"] = "120,100"  # right label, the other column's number
    pred = extract.read_reply(wrong, page_text())
    t, p, m, lm, nc = evaluate.score_page(pred, target)
    assert (t, p, m, lm, nc) == (3, 3, 2, 3, 2)


def test_eval_extract_end_to_end_with_a_scripted_model() -> None:
    target = json.loads(model_reply().split("```json\n")[1].split("\n```")[0])
    case = {"ticker": "ACME", "page_no": 1, "page_text": page_text(), "expected": target}
    sc = evaluate.eval_extract(ScriptedClient([model_reply(invent=True)]), [case])
    s = sc.summary()
    assert s["recall"] == 1.0 and s["precision"] == 1.0 and s["ungrounded_rate"] == 0.25


def test_states_value_accepts_common_formats() -> None:
    assert evaluate.states_value("ESS total revenue was $467.8 million in 3Q26.", Decimal("467812000"))
    assert evaluate.states_value("Occupancy 96.10%", Decimal("96.1"))
    assert evaluate.states_value("AFFO per share of $3.99", Decimal("3.99"))
    assert not evaluate.states_value("Revenue was $470.0 million", Decimal("467812000"))


def test_gates() -> None:
    good = {
        "extract": {"recall": 0.97, "precision": 0.99, "ungrounded_rate": 0.0},
        "qa": {"accuracy": 0.95, "gold_accuracy": 1.0},
    }
    assert all(evaluate.gates(good).values())
    bad = {"extract": {"recall": 0.5, "precision": 0.99, "ungrounded_rate": 0.02}}
    g = evaluate.gates(bad)
    assert g == {"extract.recall": False, "extract.precision": True, "extract.ungrounded_rate": False}


# ------------------------------------------------------------------------- training recipes
def test_builtin_recipes_load_and_render_an_mlx_config(tmp_path: Path) -> None:
    names = train.list_recipes()
    assert names
    for n in names:
        r = train.load_recipe(n)
        cfg = train.mlx_config(r, tmp_path / "data", tmp_path / "adapter")
        assert cfg["model"] == r["model"] and cfg["train"] is True
        assert models.by_key(r["model"]) is not None, "recipes name a preset key"
        assert cfg["fine_tune_type"] in ("lora", "dora", "full")
        y = train.to_yaml(cfg)
        assert "lora_parameters:" in y and f'model: "{r["model"]}"' in y


def test_prepare_writes_a_reproducible_run_folder(tmp_path: Path) -> None:
    data = tmp_path / "ai" / "datasets" / "current"
    data.mkdir(parents=True)
    for f in ("train.jsonl", "valid.jsonl"):
        (data / f).write_text('{"messages": []}\n', encoding="utf-8")
    (data / "manifest.json").write_text("{}", encoding="utf-8")
    run = train.prepare(train.list_recipes()[0], tmp_path / "ai")
    assert (run.path / "lora_config.yaml").exists() and (run.path / "recipe.toml").exists()
    assert (run.path / "dataset_manifest.json").exists()
    meta = json.loads((run.path / "run.json").read_text())
    assert meta["base_model"].endswith("/models/" + meta["base_preset"]) and meta["env"]
    assert meta["command"].endswith("lora_config.yaml") and "-m mlx_lm lora -c" in meta["command"]
    if not train.is_apple_silicon():
        with pytest.raises(ConfigError, match="Apple silicon"):
            train.execute(run)


def test_prepare_needs_a_dataset(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="build-dataset"):
        train.prepare(train.list_recipes()[0], tmp_path)


# ------------------------------------------------------------------------- model presets
def test_presets_are_complete_and_fit_is_judged_by_memory() -> None:
    assert models.PRESETS
    for p in models.PRESETS:
        assert p.mlx_repo and p.hf_repo and p.license and p.weights_gb > 0 and p.min_memory_gb >= p.weights_gb
        assert p.train_memory_gb >= p.min_memory_gb
    primary = models.by_role("primary")
    assert models.fits(primary, 128) and not models.fits(primary, 16)
    assert "primary" in models.table(128)
    assert not models.fits(primary, 64, training=True) and models.fits(primary, 128, training=True)
    assert models.resolve("org/some-model", Path("/x")) == "org/some-model"
    assert models.resolve(primary.key, Path("/x")).endswith(f"models/{primary.key}")
    with pytest.raises(ConfigError, match="--convert"):
        models.resolve(primary.key, Path("/nonexistent"), must_exist=True)
    cmd = models.convert_command(primary, Path("/x"))
    assert cmd[2:4] == ["mlx_lm", "convert"] and primary.hf_repo in cmd and "--q-bits" in cmd
