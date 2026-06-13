#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys
from pathlib import Path


def _project_venv_python():
    """Return the project-local virtualenv interpreter when available."""
    base_dir = Path(__file__).resolve().parent
    candidates = [
        base_dir / 'venv' / 'Scripts' / 'python.exe',
        base_dir / '.venv' / 'Scripts' / 'python.exe',
        base_dir / 'venv' / 'bin' / 'python',
        base_dir / '.venv' / 'bin' / 'python',
    ]
    current_python = Path(sys.executable).resolve() if sys.executable else None

    for candidate in candidates:
        if not candidate.exists():
            continue
        resolved_candidate = candidate.resolve()
        if current_python and resolved_candidate == current_python:
            return None
        return str(resolved_candidate)

    return None


def main():
    """Run administrative tasks."""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'lms_project.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        venv_python = _project_venv_python()
        if venv_python:
            os.execv(venv_python, [venv_python, __file__, *sys.argv[1:]])
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
