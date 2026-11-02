"""Canonical Windows launch command shared by shortcuts and auto-start."""
from dataclasses import dataclass
from pathlib import Path
import subprocess


@dataclass(frozen=True)
class LaunchCommand:
    executable: Path
    script: Path
    cwd: Path

    @property
    def command_line(self) -> str:
        return subprocess.list2cmdline([str(self.executable), str(self.script)])


def windows_launch_command(base: Path | None = None) -> LaunchCommand:
    directory = (base or Path(__file__).resolve().parent.parent).resolve()
    executable = directory / ".venv" / "Scripts" / "pythonw.exe"
    script = directory / "launch_chloe.py"
    if not executable.is_file() or not script.is_file():
        raise OSError("Configure o ambiente .venv e mantenha o inicializador na pasta do projeto.")
    return LaunchCommand(executable, script, directory)
