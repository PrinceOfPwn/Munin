from pathlib import Path


def _skills_contract() -> str:
    return (Path(__file__).resolve().parents[1] / "soul" / "skills.md").read_text(encoding="utf-8")


def test_presence_contract_probes_only_core_meshes() -> None:
    text = _skills_contract()
    assert "startup presence" in text
    assert 'munin_diagnostics(mode="quick")' in text
    assert 'valravn_status(probe=false)' in text
    assert 'valravn_talons_status(refresh=true)' in text
    assert "munin_capabilities" in text


def test_presence_contract_is_lightweight_and_non_repairing() -> None:
    text = _skills_contract()
    section = text.split("## Discord 启动自检与自我介绍", 1)[1].split("## 对话与运行时入口", 1)[0]
    assert "不要 `hugin_refresh`" in section
    assert "不要在 presence 回合修复" in section
    assert "不要逐个执行" in section
    assert "不要打开每个 `SKILL.md`" in section
    assert "send_discord_message" in section


def test_presence_contract_requires_operational_intro() -> None:
    text = _skills_contract()
    section = text.split("## Discord 启动自检与自我介绍", 1)[1].split("## 对话与运行时入口", 1)[0]
    for required in ("Hugin", "Valravn", "Talons", "skills", "tools", "/tools"):
        assert required in section
    assert "3–5" in section
    assert "一条" in section


def test_presence_contract_offers_operator_governed_repair_follow_up() -> None:
    text = _skills_contract()
    section = text.split("## Discord 启动自检与自我介绍", 1)[1].split("## 对话与运行时入口", 1)[0]
    assert "Si así desea, se puede trabajar en solucionarlo." in section
    assert "tool_forge" in section
    assert "subagent" in section
    assert "workflow" in section
    assert "presence 本身只提出这个选项，不执行修复" in section
