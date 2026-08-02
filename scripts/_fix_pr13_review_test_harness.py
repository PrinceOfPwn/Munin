#!/usr/bin/env python3
from pathlib import Path

root = Path(__file__).resolve().parents[1]

live_path = root / "tests/test_live_process_output.py"
live = live_path.read_text(encoding="utf-8")
if "import shlex\n" not in live:
    live = live.replace("import subprocess\n", "import shlex\nimport subprocess\n", 1)
old = 'command=subprocess.list2cmdline([sys.executable, "-c", f"print({secret!r})"]),'
new = 'command=shlex.join([sys.executable, "-c", f"print({secret!r})"]),'
if live.count(old) != 1:
    raise SystemExit(f"expected one secret command fixture, found {live.count(old)}")
live_path.write_text(live.replace(old, new, 1), encoding="utf-8")

skills_path = root / "tests/test_deepagents_skills.py"
skills = skills_path.read_text(encoding="utf-8")
old = "from munin.core.autonomy.skill_library import SkillLibrary\n"
new = "from munin.core.autonomy.skill_library import BundledSkillLibrary\n"
if skills.count(old) != 1:
    raise SystemExit(f"expected one SkillLibrary import, found {skills.count(old)}")
skills = skills.replace(old, new, 1)
skills = skills.replace("library = SkillLibrary(tmp_path)", "library = BundledSkillLibrary(tmp_path)", 1)
skills_path.write_text(skills, encoding="utf-8")
