"""Canonical Kleinanzeiger CLI; existing mark_api module stays a compatibility backend."""
from __future__ import annotations

from importlib import import_module
import sys

_COMMANDS = {
    "launch": ("mark_api.launcher", "main"),
    "dashboard": ("mark_api.dashboard", "main"),
    "offline": ("mark_api.offline_launcher", "main"),
    "protected-frontend": ("mark_api.protected_frontend", "main"),
    "protected-owner": ("mark_api.protected_owner", "main"),
    "protected-actor": ("mark_api.protected_actor", "main"),
    "protected-witness": ("mark_api.protected_witness", "main"),
    "protected": ("mark_api.protected_product", "main"),
    "protected-pair": ("mark_api.protected_pair", "main"),
    "generate": ("mark_api.generation_cli", "main"),
    "backup": ("mark_api.backup_cli", "main"),
    "windows-restore": ("mark_api.windows_backup", "restore_main"),
    "sync": ("mark_api.periodic_sync", "main"),
    "import-mail": ("mark_api.email_import_cli", "main"),
    "import-articles": ("mark_api.portfolio_import_cli", "main"),
    "ki-auftraege": ("mark_api.portfolio_jobs_cli", "main"),
    "local-smoke": ("mark_api.local_smoke", "main"),
    "classify": ("mark_api.classification_cli", "main"),
    "migrate-legacy": ("mark_api.legacy_migration", "main"),
    "pair-restore-check": ("mark_api.paired_restore_preflight", "main"),
    "windows-choices": ("mark_api.windows_customer_choices", "main"),
    "inventory-sync": ("mark_api.owner_inventory_cli", "main"),
}


def _invoke(command: str) -> int:
    module_name, function_name = _COMMANDS[command]
    result = getattr(import_module(module_name), function_name)()
    return 0 if result is None else int(result)


def main() -> int:
    """`python -m kleinanzeiger [subcommand] ...`, default: start product."""
    command = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] in _COMMANDS else "launch"
    if command != "launch" or (len(sys.argv) > 1 and sys.argv[1] == "launch"):
        sys.argv.pop(1)
    return _invoke(command)


def launcher() -> int:
    return _invoke("launch")

def dashboard() -> int:
    return _invoke("dashboard")

def offline() -> int:
    return _invoke("offline")

def protected_frontend() -> int:
    return _invoke("protected-frontend")

def protected_owner() -> int:
    return _invoke("protected-owner")

def protected_actor() -> int:
    return _invoke("protected-actor")

def protected_witness() -> int:
    return _invoke("protected-witness")

def protected() -> int:
    return _invoke("protected")

def protected_pair() -> int:
    return _invoke("protected-pair")

def generate() -> int:
    return _invoke("generate")

def backup() -> int:
    return _invoke("backup")

def windows_restore() -> int:
    return _invoke("windows-restore")

def sync() -> int:
    return _invoke("sync")

def import_mail() -> int:
    return _invoke("import-mail")

def import_articles() -> int:
    return _invoke("import-articles")

def ki_auftraege() -> int:
    return _invoke("ki-auftraege")

def local_smoke() -> int:
    return _invoke("local-smoke")

def classify() -> int:
    return _invoke("classify")

def migrate_legacy() -> int:
    return _invoke("migrate-legacy")

def pair_restore_check() -> int:
    return _invoke("pair-restore-check")

def windows_choices() -> int:
    return _invoke("windows-choices")

def inventory_sync() -> int:
    return _invoke("inventory-sync")
