"""Run the full Kleinanzeiger product: python -m kleinanzeiger.launcher."""
from .cli import launcher as main

if __name__ == "__main__":
    raise SystemExit(main())
