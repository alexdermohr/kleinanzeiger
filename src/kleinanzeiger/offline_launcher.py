"""Run Kleinanzeiger in its local work mode."""
from .cli import offline as main

if __name__ == "__main__":
    raise SystemExit(main())
