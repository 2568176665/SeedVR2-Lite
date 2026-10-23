"""模型下载计划的离线校验。

覆盖 build_file_plan 的仓库/路径拼装与哈希绑定：这两处一旦写错，
表现为下载 404 或下载完校验失败，是最容易静默失效的地方。
"""

from __future__ import annotations

import pytest

from app.integrated_app.services.model_download import build_file_plan


@pytest.fixture(scope="module")
def model_config() -> dict:
    import yaml

    with open("config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)["model"]


def _by_name(plan, filename):
    return next(p for p in plan if p.filename == filename)


@pytest.mark.parametrize("precision", ["fp16", "fp8"])
def test_numz_repo_for_unquantized(model_config, precision):
    plan = build_file_plan(model_config, "3b", precision)
    dit = _by_name(plan, model_config["models"]["3b"][f"checkpoint_{precision}"])
    assert dit.url.startswith("numz/SeedVR2_comfyUI/resolve/main/")
    assert "/diffusion_models/" not in dit.url
    assert dit.sha256_candidates


@pytest.mark.parametrize("precision", ["mxfp8", "int8_convrot", "nvfp4"])
def test_comfy_org_repo_for_quantized(model_config, precision):
    plan = build_file_plan(model_config, "3b", precision)
    dit = _by_name(plan, model_config["models"]["3b"][f"checkpoint_{precision}"])
    assert dit.url.startswith("Comfy-Org/SeedVR2/resolve/main/diffusion_models/")
    assert dit.sha256_candidates


def test_vae_and_embeddings_always_included(model_config):
    plan = build_file_plan(model_config, "7b", "fp8")
    names = [p.filename for p in plan]
    assert names == [
        model_config["models"]["7b"]["checkpoint_fp8"],
        model_config["models"]["7b"]["vae_checkpoint"],
        model_config["models"]["7b"]["pos_emb"],
        model_config["models"]["7b"]["neg_emb"],
    ]
    assert [p.url.split("/resolve/main/")[0] for p in plan] == [
        "numz/SeedVR2_comfyUI",
        "numz/SeedVR2_comfyUI",
        "ByteDance-Seed/SeedVR2-3B",
        "ByteDance-Seed/SeedVR2-3B",
    ]


def test_unknown_size_and_precision_rejected(model_config):
    with pytest.raises(ValueError):
        build_file_plan(model_config, "99b", "fp8")
    with pytest.raises(ValueError):
        build_file_plan(model_config, "3b", "bf16")
