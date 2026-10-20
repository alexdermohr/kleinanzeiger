"""User-triggered generation CLI with no implicit platform or account actions."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from .generation import (
    AdvertisementGenerator, GenerationError, _validated_model,
    reserve_generated_image,
)


_PROVIDER_PRICING_URL = "https://developers.openai.com/api/docs/pricing"


def _provider_cost_notice(mode: str, model: str) -> str:
    """Display the selected model and nonbinding pricing before any SDK call."""
    if mode == "text" and model == "gpt-5-mini":
        rates = (
            "Standard-Listenpreis vom 20.10.2026: 0.25 USD je 1 Mio "
            "Text-Eingabetokens und 2.00 USD je 1 Mio Text-Ausgabetokens."
        )
    elif mode == "image" and model == "gpt-image-2.5-flare":
        rates = (
            "Standard-Listenpreis vom 20.10.2026: 5.00 USD je 1 Mio "
            "Text-Eingabetokens und 30.00 USD je 1 Mio Bild-Ausgabetokens."
        )
    else:
        rates = (
            "Kein verifizierter Modelltarif in dieser Anwendung: den aktuell "
            "gewählten Modellpreis beim Anbieter vor der Bestätigung prüfen."
        )
    return (
        f"Externe KI-Anfrage: Modus {mode}, Modell {model}. "
        "Dein Prompt wird an OpenAI übertragen und kann Kosten auslösen. "
        f"{rates} Der tatsächliche Preis hängt vom Verbrauch ab und ist "
        "vor dem Aufruf nicht gedeckelt oder garantiert; weitere Eingabe-, "
        "Cache- oder Bildkosten sind modellabhängig möglich. "
        f"Aktuelle Preise: {_PROVIDER_PRICING_URL}"
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create advertisement draft text or a local PNG draft from a prompt. "
            "Requires optional mark-api[generation] and OPENAI_API_KEY; "
            "never publishes an advertisement. Each invocation requires "
            "separate --acknowledge-provider-cost after checking model prices."
        )
    )
    modes = parser.add_subparsers(dest="mode", required=True)
    for mode in ("text", "image"):
        sub = modes.add_parser(mode)
        source = sub.add_mutually_exclusive_group(required=True)
        source.add_argument(
            "--prompt",
            help="Prompt text (visible in process arguments; use --prompt-file for private prompts).",
        )
        source.add_argument(
            "--prompt-file",
            metavar="FILE",
            help="UTF-8 prompt file, or - for standard input; maximum 16 KiB.",
        )
        sub.add_argument(
            "--model",
            default="gpt-5-mini" if mode == "text" else "gpt-image-2.5-flare",
            help="Provider model (default: gpt-5-mini or gpt-image-2.5-flare).",
        )
        sub.add_argument(
            "--acknowledge-provider-cost", action="store_true",
            help="Explicit consent for this single provider request and its "
                 "variable cost; review model pricing before using this flag.",
        )
        if mode == "image":
            sub.add_argument(
                "--output", type=Path, required=True,
                help="Reserve and write a PNG in a private user-owned directory; no existing file is overwritten.",
            )
    return parser


def _prompt(args: argparse.Namespace) -> str:
    if args.prompt is not None:
        return args.prompt
    max_bytes = 16 * 1024
    try:
        if args.prompt_file == "-":
            raw = sys.stdin.buffer.read(max_bytes + 1)
        else:
            with Path(args.prompt_file).open("rb") as stream:
                raw = stream.read(max_bytes + 1)
    except OSError:
        raise GenerationError("prompt file is unavailable") from None
    if len(raw) > max_bytes:
        raise GenerationError("prompt file exceeds 16 KiB")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise GenerationError("prompt file must be UTF-8") from None


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    reserved_image = False
    try:
        model = _validated_model(args.model)
        notice = _provider_cost_notice(args.mode, model)
        if not args.acknowledge_provider_cost:
            raise GenerationError(
                notice + " Für genau diesen Aufruf erst nach Prüfung "
                "--acknowledge-provider-cost ausdrücklich angeben."
            )
        print(notice, file=sys.stderr)
        prompt = _prompt(args)
        generator = AdvertisementGenerator()
        if args.mode == "text":
            result = generator.generate_text(prompt, model=model)
            print(json.dumps(result.as_dict(), ensure_ascii=False))
        else:
            # Exclusive file reservation precedes the paid provider request.
            # Neither a missing parent nor an occupied path can incur a charge.
            output_path = Path(os.path.abspath(args.output))
            with reserve_generated_image(args.output) as save_image:
                reserved_image = True
                data = generator.generate_image(prompt, model=model)
                save_image(data)
            print(
                json.dumps(
                    {"status": "draft", "published": False,
                     "image_path": str(output_path), "format": "png",
                     "image_sha256": hashlib.sha256(data).hexdigest(),
                     "path_binding": "verified_at_write_only",
                     "persistent_path_guarantee": False},
                    ensure_ascii=False,
                )
            )
    except GenerationError as exc:
        print(f"mark-api-generate: {exc}", file=sys.stderr)
        if reserved_image:
            print(
                "mark-api-generate: output may contain an incomplete private "
                "reservation; inspect it before manual cleanup",
                file=sys.stderr,
            )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())