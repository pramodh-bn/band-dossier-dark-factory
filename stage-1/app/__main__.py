"""Entry point: listen on `PORT` (default 8080) on every interface (§3.1)."""
from __future__ import annotations

import os

from .server import serve


def main() -> None:
    """Serve until the container stops."""
    serve(int(os.environ.get("PORT", "8080")))


if __name__ == "__main__":
    main()
