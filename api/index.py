"""
Vercel entry point. Vercel's Python runtime looks for a WSGI callable named
`app` in api/*.py; every request is rewritten here (see vercel.json) and
Django's normal URL routing takes over.
"""
import sys
from pathlib import Path

# Make the Backend/ folder importable (for `myproject` and `routeplanner`).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from myproject.wsgi import application  # noqa: E402

app = application
