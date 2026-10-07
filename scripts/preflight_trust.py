"""Validate configured AI trust before restarting a deployed service."""

from pathlib import Path
import ssl
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def main() -> int:
    from integrations.ai import load_ai_settings, _ensure_gigachat_ca_bundle

    settings = load_ai_settings()
    if not settings.configured:
        print("AI trust preflight: skipped (AI disabled or unconfigured)")
        return 0
    try:
        bundle = _ensure_gigachat_ca_bundle(settings)
        ssl.create_default_context(cafile=bundle if isinstance(bundle, str) else None)
    except Exception as exc:
        # Do not print credentials, environment contents or exception payloads.
        print(
            "AI trust preflight failed: " + type(exc).__name__ + ". "
            "Configure a trusted GIGACHAT_CA_BUNDLE or independently verified "
            "GIGACHAT_ROOT_SHA256 before deployment.",
            file=sys.stderr,
        )
        return 1
    print("AI trust preflight: OK (no AI inference request made)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
