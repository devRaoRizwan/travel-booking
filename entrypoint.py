#!/usr/bin/env python3
# python entrypoint.py [--test]: create .venv, install requirements if changed, then run the app or the tests.

import hashlib
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / ".venv"
REQUIREMENTS = ROOT / "requirements.txt"
INSTALL_STAMP = VENV_DIR / ".requirements.sha256"


def venv_python() -> Path:
    return VENV_DIR / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def running_inside_venv() -> bool:
    return Path(sys.prefix).resolve() == VENV_DIR.resolve()


def ensure_venv() -> None:
    if not venv_python().exists():
        print(f"Creating virtualenv in {VENV_DIR} ...", flush=True)
        try:
            venv.create(VENV_DIR, with_pip=True)
        except Exception as error:
            sys.exit(f"Could not create the virtualenv ({error}).\n"
                     "On Debian/Ubuntu install it first: sudo apt install python3-venv")

    requirements_digest = hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()
    if not INSTALL_STAMP.exists() or INSTALL_STAMP.read_text().strip() != requirements_digest:
        print("Installing requirements.txt ...", flush=True)
        subprocess.check_call([str(venv_python()), "-m", "pip", "install", "-q", "--disable-pip-version-check",
                               "-r", str(REQUIREMENTS)])
        INSTALL_STAMP.write_text(requirements_digest)


def main() -> int:
    cli_args = sys.argv[1:]
    ensure_venv()

    if not running_inside_venv():
        try:
            return subprocess.call([str(venv_python()), str(Path(__file__).resolve()), *cli_args], cwd=ROOT)
        except KeyboardInterrupt:
            return 130

    sys.path.insert(0, str(ROOT))
    if "--test" in cli_args:
        import pytest
        return pytest.main(["-q", "-p", "no:cacheprovider", str(ROOT / "tests")])

    from app.server import run
    try:
        run()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
