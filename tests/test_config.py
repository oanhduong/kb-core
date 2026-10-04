"""Test F1: config.yaml load/validate, sha256 prompt chặn sửa prompt im lặng, chọn snapshot theo version
(rollback), nội suy biến môi trường, GET /config không lộ secret."""
import shutil
from pathlib import Path

import pytest

from core.config import ConfigError, config_path_for_version, load_config, main as config_cli

ROOT = Path(__file__).resolve().parent.parent


def _copy_repo_config(tmp_path: Path) -> Path:
    shutil.copytree(ROOT / "prompts", tmp_path / "prompts")
    text = (ROOT / "config.yaml").read_text(encoding="utf-8").replace("path: prompts/", f"path: {tmp_path}/prompts/")
    p = tmp_path / "config.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_repo_config_loads_with_version_hash_and_prompt_hashes():
    cfg = load_config(ROOT / "config.yaml")
    assert cfg.config_version and len(cfg.config_hash) == 16
    assert set(cfg.prompt_hashes) == {"answer_system", "router_classify", "entity_extract", "graph_extract",
                                      "chat_compaction", "doc_classify"}
    assert cfg.models.answer.startswith("claude-")


def test_editing_prompt_without_updating_sha_fails_fast(tmp_path):
    p = _copy_repo_config(tmp_path)
    (tmp_path / "prompts" / "answer_system.v1.md").write_text("prompt bị sửa lén", encoding="utf-8")
    with pytest.raises(ConfigError, match="answer_system"):
        load_config(p)
    assert load_config(p, strict_prompts=False).prompt_hashes["answer_system"]  # CLI hash-prompts vẫn đọc được


def test_env_interpolation_with_default_and_override(tmp_path, monkeypatch):
    p = _copy_repo_config(tmp_path)
    monkeypatch.delenv("GRAPH_ENABLED", raising=False)
    assert load_config(p).graph.enabled is True
    monkeypatch.setenv("GRAPH_ENABLED", "false")
    cfg = load_config(p)
    assert cfg.graph.enabled is False and cfg.router.graph_enabled is False  # tắt graph -> router cũng tắt


def test_config_hash_changes_when_parameter_changes(tmp_path):
    p = _copy_repo_config(tmp_path)
    h1 = load_config(p).config_hash
    p.write_text(p.read_text(encoding="utf-8").replace("top_k: 5 ", "top_k: 6 "), encoding="utf-8")
    assert load_config(p).config_hash != h1


def test_version_selection_for_rollback(monkeypatch):
    versions = sorted((ROOT / "config" / "versions").glob("*.yaml"))
    assert versions, "phải có ít nhất 1 snapshot để rollback"
    v = versions[0].stem
    assert config_path_for_version(v) == versions[0]
    monkeypatch.setenv("RETAILKB_CONFIG_VERSION", v)
    assert load_config().config_version == v
    with pytest.raises(ConfigError, match="Không có snapshot"):
        config_path_for_version("1999.01.01-9")


def test_snapshot_refuses_to_overwrite_different_content_of_same_version(tmp_path, monkeypatch):
    import core.config as cc

    monkeypatch.setattr(cc, "VERSIONS_DIR", tmp_path / "versions")
    p = _copy_repo_config(tmp_path)
    assert config_cli(["snapshot", "--path", str(p)]) == 0
    assert config_cli(["snapshot", "--path", str(p)]) == 0  # cùng nội dung -> idempotent
    p.write_text(p.read_text(encoding="utf-8") + "\n# đổi tham số nhưng quên bump version\n", encoding="utf-8")
    assert config_cli(["snapshot", "--path", str(p)]) == 1


def test_public_view_has_no_secret_fields():
    view = load_config(ROOT / "config.yaml").public_view()
    flat = str(view).lower()
    for secret in ("api_key", "jwt_secret", "password", "database_url"):
        assert secret not in flat
