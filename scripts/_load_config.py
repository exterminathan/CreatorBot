"""Print KEY=VALUE lines from config.py so shell scripts can eval them.

Usage (from a shell script):
    eval "$(python scripts/_load_config.py)"

This exports a small set of deployment-time variables — enough for the
setup / deploy / update shell scripts. Values are shell-quoted so they
survive special characters safely.

Only non-secret values are printed here. Secrets stay in .env (sourced
directly by shell scripts).
"""
from __future__ import annotations

import shlex
import sys
from pathlib import Path


def _load_config_module():
    """Import the user's config.py, falling back to config.example.py."""
    repo_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo_root))
    try:
        import config as cfg  # noqa: WPS433
        return cfg
    except ImportError:
        import importlib.util
        example = repo_root / "config.example.py"
        if not example.exists():
            sys.stderr.write(
                "ERROR: neither config.py nor config.example.py found.\n"
            )
            sys.exit(1)
        spec = importlib.util.spec_from_file_location("_creatorbot_config", example)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        sys.stderr.write(
            "WARNING: config.py not found — using config.example.py. "
            "Copy config.example.py to config.py and fill in your values.\n"
        )
        return mod


def main() -> None:
    cfg = _load_config_module()

    exported = {
        "GCP_PROJECT_ID":          cfg.GCP_PROJECT_ID,
        "GCP_REGION":              cfg.GCP_REGION,
        "CLOUD_RUN_SERVICE":       cfg.CLOUD_RUN_SERVICE,
        "CONTAINER_IMAGE":         cfg.CONTAINER_IMAGE,
        "CLOUD_RUN_MIN_INSTANCES": str(cfg.CLOUD_RUN_MIN_INSTANCES),
        "CLOUD_RUN_MAX_INSTANCES": str(cfg.CLOUD_RUN_MAX_INSTANCES),
        "CLOUD_RUN_MEMORY":        cfg.CLOUD_RUN_MEMORY,
        "CLOUD_RUN_CPU":           cfg.CLOUD_RUN_CPU,
        "SERVICE_ACCOUNT":         cfg.SERVICE_ACCOUNT,
        "CONFIG_BUCKET":           cfg.CONFIG_BUCKET or "",
        "ADMIN_CHANNEL_ID":        str(cfg.ADMIN_CHANNEL_ID),
        "ADMIN_USER_ID":           str(cfg.ADMIN_USER_ID),
        "GEMINI_MODEL":            cfg.GEMINI_MODEL,
    }

    for key, value in exported.items():
        print(f"export {key}={shlex.quote(value)}")


if __name__ == "__main__":
    main()
