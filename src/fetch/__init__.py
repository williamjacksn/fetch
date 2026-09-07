from __future__ import annotations

import os

import notch
import waitress

from .app import create_app

__all__ = ["create_app"]


def main() -> None:
    notch.configure()
    app = create_app()
    waitress.serve(app, port=int(os.environ.get("FETCH_PORT", "5000")), ident=None)
