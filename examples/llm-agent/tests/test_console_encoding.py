import locale
import os
import subprocess
import sys
from pathlib import Path

import yaml

RUN_PY = Path(__file__).resolve().parent.parent / "run.py"


def test_errors_are_readable_in_the_console_encoding_and_unencodable_text_does_not_crash(tmp_path):
    task = tmp_path / "task.yaml"
    task.write_text(
        yaml.safe_dump(
            {
                "goal": "g",
                "exe": str(tmp_path / "gone—\U0001f600.exe"),
                "permission_level": 3,
                "success": {"require": [{"role": "switch"}]},
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}

    completed = subprocess.run(
        [sys.executable, "-X", "utf8=0", str(RUN_PY), str(task)],
        capture_output=True,
        env=env,
    )

    assert completed.returncode == 2
    assert "실행 파일이 없습니다" in completed.stderr.decode(locale.getencoding())
