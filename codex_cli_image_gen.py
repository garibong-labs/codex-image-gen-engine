#!/usr/bin/env python3
"""Drive Codex CLI built-in image generation using chatgpt auth-only login.

This wrapper does not use OPENAI_API_KEY. It relies on Codex CLI already being
logged in (for example, ~/.codex/auth.json with auth_mode=chatgpt).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from collections import deque
from pathlib import Path


WORKSPACE = Path.cwd()
CODEX_HOME = Path.home() / ".codex"
AUTH_JSON = CODEX_HOME / "auth.json"
SCRIPT_VERSION = "2026-04-24.2"


def fail(msg: str, code: int = 1) -> int:
    print(msg, file=sys.stderr)
    return code


def ensure_auth() -> tuple[bool, str]:
    if not AUTH_JSON.exists():
        return False, f"auth file not found: {AUTH_JSON}"
    try:
        data = json.loads(AUTH_JSON.read_text())
    except Exception as e:
        return False, f"failed to read auth file: {e}"
    if data.get("auth_mode") != "chatgpt":
        return False, f"unsupported auth_mode: {data.get('auth_mode')}"
    tokens = data.get("tokens") or {}
    if not tokens.get("access_token"):
        return False, "missing chatgpt access_token in auth.json"
    return True, "ok"


def build_prompt(user_prompt: str, output_path: Path) -> str:
    return (
        "Use Codex built-in image generation with the current logged-in account. "
        "Generate exactly one image from this prompt: "
        f"{user_prompt}. "
        "After generation, copy or save the final image into this exact absolute path: "
        f"{output_path}. "
        "If the generated image is initially stored elsewhere, copy it to that exact path. "
        "Reply with only the exact absolute output path when done."
    )


def run_codex(prompt: str, output_last: Path) -> subprocess.CompletedProcess[str]:
    cmd = [
        "codex",
        "exec",
        "--skip-git-repo-check",
        "--sandbox", "workspace-write",
        "-C", str(WORKSPACE),
        "-o", str(output_last),
        prompt,
    ]
    return subprocess.run(cmd, text=True, capture_output=True)


def extract_path(text: str) -> Path | None:
    s = text.strip()
    if not s:
        return None
    candidates = re.findall(r"(/[^\s`'\"]+\.(?:png|jpg|jpeg|webp))", s)
    if candidates:
        return Path(candidates[-1])
    if s.startswith("/"):
        return Path(s.splitlines()[-1].strip())
    return None


def colors_close(a: tuple[int, int, int], b: tuple[int, int, int], tolerance: int) -> bool:
    return all(abs(x - y) <= tolerance for x, y in zip(a, b))


def auto_make_background_transparent(image_path: Path, tolerance: int = 20) -> bool:
    try:
        from PIL import Image
    except Exception:
        return False

    image = Image.open(image_path).convert("RGBA")
    width, height = image.size
    if width == 0 or height == 0:
        return False

    corners = [
        image.getpixel((0, 0))[:3],
        image.getpixel((width - 1, 0))[:3],
        image.getpixel((0, height - 1))[:3],
        image.getpixel((width - 1, height - 1))[:3],
    ]
    base = corners[0]
    if not all(colors_close(base, c, tolerance) for c in corners[1:]):
        return False

    pixels = image.load()
    q: deque[tuple[int, int]] = deque()
    visited: set[tuple[int, int]] = set()
    changed = False

    for x in range(width):
        q.append((x, 0))
        q.append((x, height - 1))
    for y in range(height):
        q.append((0, y))
        q.append((width - 1, y))

    while q:
        x, y = q.popleft()
        if (x, y) in visited:
            continue
        visited.add((x, y))
        r, g, b, a = pixels[x, y]
        if a == 0:
            continue
        if not colors_close(base, (r, g, b), tolerance):
            continue
        pixels[x, y] = (r, g, b, 0)
        changed = True
        if x > 0:
            q.append((x - 1, y))
        if x + 1 < width:
            q.append((x + 1, y))
        if y > 0:
            q.append((x, y - 1))
        if y + 1 < height:
            q.append((x, y + 1))

    if changed:
        image.save(image_path)
    return changed


def resize_and_convert_image(image_path: Path, max_width: int | None = None, max_size: int | None = None, jpeg_quality: int | None = None) -> bool:
    try:
        from PIL import Image
    except Exception:
        return False

    changed = False
    image = Image.open(image_path)
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
        changed = True

    target_width = None
    if max_size:
        longest = max(image.width, image.height)
        if longest > max_size:
            if image.width >= image.height:
                target_width = max_size
            else:
                target_width = max(1, int(image.width * (max_size / float(image.height))))
    if max_width:
        target_width = min(target_width, max_width) if target_width else max_width

    if target_width and image.width > target_width:
        ratio = target_width / float(image.width)
        new_size = (target_width, max(1, int(image.height * ratio)))
        image = image.resize(new_size, Image.LANCZOS)
        changed = True

    save_kwargs = {}
    target_format = image.format or 'JPEG'
    if jpeg_quality is not None:
        target_format = 'JPEG'
        save_kwargs = {
            'format': 'JPEG',
            'quality': jpeg_quality,
            'optimize': True,
        }
        changed = True
    elif target_format == 'PNG' and image_path.suffix.lower() in ('.jpg', '.jpeg'):
        target_format = 'JPEG'
        save_kwargs = {
            'format': 'JPEG',
            'quality': 90,
            'optimize': True,
        }
        changed = True

    if changed:
        image.save(image_path, **save_kwargs)
    return changed


def main() -> int:
    if '--version' in sys.argv[1:] and len(sys.argv[1:]) == 1:
        print(SCRIPT_VERSION)
        return 0

    parser = argparse.ArgumentParser(description="Generate image via Codex CLI auth-only login")
    parser.add_argument("--prompt", "-p", required=True, help="Image prompt")
    parser.add_argument("--filename", "-f", required=True, help="Output filename/path")
    parser.add_argument(
        "--postprocess-transparent",
        choices=["off", "auto", "force"],
        default="auto",
        help="Postprocess background transparency",
    )
    parser.add_argument("--debug", action="store_true", help="Print Codex stdout/stderr on failure")
    parser.add_argument("--max-width", type=int, default=None, help="Downscale output if wider than this many pixels")
    parser.add_argument("--max-size", type=int, default=None, help="Downscale output if the longer edge exceeds this many pixels")
    parser.add_argument("--jpeg-quality", type=int, default=None, help="Re-save output as JPEG with this quality (1-100)")
    parser.add_argument("--version", action="store_true", help="Print script version and exit")
    args = parser.parse_args()

    if args.version:
        print(SCRIPT_VERSION)
        return 0

    ok, reason = ensure_auth()
    if not ok:
        return fail(f"Codex auth unavailable: {reason}")

    output_path = Path(args.filename).expanduser()
    if not output_path.is_absolute():
        output_path = (WORKSPACE / output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    output_last = WORKSPACE / "tmp" / "codex-cli-image-last-message.txt"
    output_last.parent.mkdir(parents=True, exist_ok=True)
    prompt = build_prompt(args.prompt, output_path)
    proc = run_codex(prompt, output_last)

    if args.debug and (proc.stdout or proc.stderr):
        print("--- codex stdout ---", file=sys.stderr)
        print(proc.stdout, file=sys.stderr)
        print("--- codex stderr ---", file=sys.stderr)
        print(proc.stderr, file=sys.stderr)

    if output_path.exists() and output_path.stat().st_size > 0:
        if args.postprocess_transparent != "off":
            auto_make_background_transparent(output_path, tolerance=20 if args.postprocess_transparent == "auto" else 40)
        resize_and_convert_image(output_path, max_width=args.max_width, max_size=args.max_size, jpeg_quality=args.jpeg_quality)
        print(str(output_path))
        return 0

    last_text = output_last.read_text(encoding="utf-8").strip() if output_last.exists() else ""
    search_text = "\n".join(part for part in [last_text, proc.stdout or "", proc.stderr or ""] if part)
    reported = extract_path(search_text)
    if reported and reported.exists() and reported != output_path:
        shutil.copy2(reported, output_path)
        if args.postprocess_transparent != "off":
            auto_make_background_transparent(output_path, tolerance=20 if args.postprocess_transparent == "auto" else 40)
        resize_and_convert_image(output_path, max_width=args.max_width, max_size=args.max_size, jpeg_quality=args.jpeg_quality)
        print(str(output_path))
        return 0

    msg = "Codex did not produce the requested output file"
    if proc.returncode != 0:
        msg += f" (exit={proc.returncode})"
    if last_text:
        msg += f"\nlast_message={last_text}"
    return fail(msg)


if __name__ == "__main__":
    raise SystemExit(main())
