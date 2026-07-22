"""Entry point: `python -m cockpit`."""
from .daemon import main

if __name__ == "__main__":
    raise SystemExit(main())
