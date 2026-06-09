#!/usr/bin/env python3
"""Drive Codex CLI built-in image generation using chatgpt auth-only login.

This wrapper does not use OPENAI_API_KEY. It relies on Codex CLI already being
logged in (for example, ~/.codex/auth.json with auth_mode=chatgpt).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from collections import deque
from pathlib import Path


WORKSPACE = Path.cwd()
EXEC_WORKSPACE = WORKSPACE / "tmp"
CODEX_HOME = Path.home() / ".codex"
AUTH_JSON = CODEX_HOME / "auth.json"
CODEX_GENERATED_IMAGES = CODEX_HOME / "generated_images"
SCRIPT_VERSION = "2026-05-13.1"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
IMAGE_PROVIDER_LABEL = os.environ.get("CODEX_IMAGE_PROVIDER_LABEL", "openai-codex")
IMAGE_BACKEND_LABEL = os.environ.get("CODEX_IMAGE_BACKEND_LABEL", "codex-cli-built-in-image-generation")
IMAGE_MODEL_LABEL = os.environ.get("CODEX_IMAGE_MODEL_LABEL", "gpt-image-2")
IMAGE_MODEL_LABEL_SOURCE = "env" if "CODEX_IMAGE_MODEL_LABEL" in os.environ else "openai-codex-cli-official-docs"
IMAGE_MODEL_CONFIDENCE = "operator-declared" if "CODEX_IMAGE_MODEL_LABEL" in os.environ else "docs-backed-not-runtime-verified"
IMAGE_MODEL_EVIDENCE_URL = os.environ.get("CODEX_IMAGE_MODEL_EVIDENCE_URL", "https://developers.openai.com/codex/cli/features#image-generation")


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



def default_meta_path(output_path: Path) -> Path:
    return output_path.with_name(f"{output_path.name}.meta.json")


def generation_backend_meta() -> dict:
    return {
        "provider": IMAGE_PROVIDER_LABEL,
        "backend": IMAGE_BACKEND_LABEL,
        "modelLabel": IMAGE_MODEL_LABEL,
        "modelLabelSource": IMAGE_MODEL_LABEL_SOURCE,
        "modelConfidence": IMAGE_MODEL_CONFIDENCE,
        "modelEvidenceUrl": IMAGE_MODEL_EVIDENCE_URL,
        "execution": "codex exec",
        "usesOpenClawImageGenerateTool": False,
        "fallbackProviderUsed": False,
        "note": "OpenAI Codex CLI official docs state that built-in image generation uses gpt-image-2. Codex CLI does not expose per-run structured provider/model telemetry here, so this is docs-backed rather than runtime-verified.",
    }


def write_generation_meta(
    meta_path: Path | None,
    *,
    status: str,
    output_path: Path,
    safe_output_path: Path | None,
    source_path: Path | None,
    last_message_path: Path | None,
    returncode: int | None,
    prompt: str,
    postprocess: dict,
    error: str | None = None,
    prefer_raw_generated: bool = False,
    raw_generated_source_path: Path | None = None,
    ignored_local_output_path: Path | None = None,
    codex_local_edit_ignored: bool = False,
) -> None:
    if meta_path is None:
        return
    output_exists = output_path.exists()
    safe_exists = bool(safe_output_path and safe_output_path.exists())
    payload = {
        "status": status,
        "engine": {
            "script": "codex_cli_image_gen.py",
            "scriptVersion": SCRIPT_VERSION,
        },
        "generation": generation_backend_meta(),
        "auth": {
            "mode": "chatgpt",
            "source": str(AUTH_JSON),
        },
        "output": {
            "path": str(output_path),
            "exists": output_exists,
            "bytes": output_path.stat().st_size if output_exists else 0,
            "safeOutputPath": str(safe_output_path) if safe_output_path else None,
            "safeOutputExists": safe_exists,
            "sourcePath": str(source_path) if source_path else None,
            "rawGeneratedSourcePath": str(raw_generated_source_path) if raw_generated_source_path else None,
            "ignoredLocalOutputPath": str(ignored_local_output_path) if ignored_local_output_path else None,
            "lastMessagePath": str(last_message_path) if last_message_path else None,
        },
        "codex": {
            "returncode": returncode,
            "command": "codex exec --skip-git-repo-check --ephemeral --ignore-rules --sandbox workspace-write",
        },
        "prompt": {
            "sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        },
        "postprocess": postprocess,
        "safety": {
            "rawCodexGeneratedImagePreferred": prefer_raw_generated,
            "rawCodexGeneratedImageUsed": bool(raw_generated_source_path),
            "codexLocalEditIgnored": codex_local_edit_ignored,
        },
    }
    if error:
        payload["error"] = error
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def build_prompt(user_prompt: str, output_path: Path, prefer_raw_generated: bool = False) -> str:
    raw_guardrail = ""
    if prefer_raw_generated:
        raw_guardrail = (
            "Use the generated bitmap as-is. Do not alter it with Python, PIL, ImageMagick, canvas, SVG, "
            "or any local drawing/post-processing step. Do not draw replacement text boxes, captions, "
            "speech bubbles, or overlays. If any generated text is imperfect, leave it imperfect and copy "
            "the raw generated image. "
        )

    return (
        "Use Codex built-in image generation with the current logged-in account. "
        "Generate exactly one image from this prompt: "
        f"{user_prompt}. "
        f"{raw_guardrail}"
        "After generation, copy or save the final image into this exact absolute path: "
        f"{output_path}. "
        "If the generated image is initially stored elsewhere, copy it to that exact path. "
        "Reply with only the exact absolute output path when done."
    )


def make_safe_temp_output(output_path: Path) -> Path:
    safe_dir = WORKSPACE / "tmp" / "generated-images"
    safe_dir.mkdir(parents=True, exist_ok=True)
    suffix = output_path.suffix if output_path.suffix else ".png"
    stem = output_path.stem or "image"
    digest = hashlib.sha1(str(output_path).encode("utf-8")).hexdigest()[:12]
    return (safe_dir / f"{stem}-{digest}{suffix}").resolve()


def _append_log(log_path: Path | None, line: str) -> None:
    if not log_path:
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log_file:
        log_file.write(line)


def _stream_pipe(pipe, label: str, chunks: list[str], log_path: Path | None) -> None:
    try:
        for line in iter(pipe.readline, ""):
            chunks.append(line)
            rendered = f"[codex {label}] {line}"
            print(rendered, end="", file=sys.stderr, flush=True)
            _append_log(log_path, rendered)
    finally:
        pipe.close()


def run_codex(prompt: str, output_last: Path, *, stream: bool = False, log_path: Path | None = None) -> subprocess.CompletedProcess[str]:
    EXEC_WORKSPACE.mkdir(parents=True, exist_ok=True)
    cmd = [
        "codex",
        "exec",
        "--skip-git-repo-check",
        "--ephemeral",
        "--ignore-rules",
        "--sandbox", "workspace-write",
        "-C", str(EXEC_WORKSPACE),
        "-o", str(output_last),
        prompt,
    ]
    if not stream:
        return subprocess.run(cmd, text=True, capture_output=True)

    proc = subprocess.Popen(
        cmd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=1,
    )
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    threads = [
        threading.Thread(target=_stream_pipe, args=(proc.stdout, "stdout", stdout_chunks, log_path), daemon=True),
        threading.Thread(target=_stream_pipe, args=(proc.stderr, "stderr", stderr_chunks, log_path), daemon=True),
    ]
    for thread in threads:
        thread.start()
    returncode = proc.wait()
    for thread in threads:
        thread.join()
    return subprocess.CompletedProcess(cmd, returncode, "".join(stdout_chunks), "".join(stderr_chunks))


def list_codex_generated_images() -> set[Path]:
    if not CODEX_GENERATED_IMAGES.exists():
        return set()
    paths: set[Path] = set()
    try:
        for path in CODEX_GENERATED_IMAGES.rglob("*"):
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS:
                paths.add(path.resolve())
    except OSError:
        return paths
    return paths


def is_codex_generated_image(path: Path) -> bool:
    try:
        resolved = path.resolve()
        generated_root = CODEX_GENERATED_IMAGES.resolve()
        return resolved.is_file() and resolved.suffix.lower() in IMAGE_EXTENSIONS and generated_root in resolved.parents
    except OSError:
        return False


def extract_generated_image_paths(text: str) -> list[Path]:
    candidates = re.findall(r"(/[^\s`'\"<>]+\.(?:png|jpg|jpeg|webp))", text, flags=re.IGNORECASE)
    paths: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        path = Path(candidate.rstrip(".,);]"))
        if is_codex_generated_image(path):
            resolved = path.resolve()
            if resolved not in seen:
                paths.append(resolved)
                seen.add(resolved)
    return paths


def newest_file(paths: list[Path] | set[Path]) -> Path | None:
    existing = [path for path in paths if path.exists()]
    if not existing:
        return None
    return max(existing, key=lambda path: path.stat().st_mtime_ns)


def select_raw_generated_image(search_text: str, before_paths: set[Path]) -> Path | None:
    """Return the raw Codex image asset, ignoring any later local edits.

    Codex CLI can autonomously run Python/PIL or other local tools after image
    generation. For this wrapper the authoritative image is the bitmap produced
    by Codex's built-in image generator under ~/.codex/generated_images, not a
    workspace file the agent may have redrawn before returning.
    """
    mentioned = extract_generated_image_paths(search_text)
    if mentioned:
        return newest_file(mentioned)

    after_paths = list_codex_generated_images()
    new_paths = after_paths - before_paths
    return newest_file(new_paths)


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

    original = Image.open(image_path)
    original_format = original.format
    image = original.convert("RGBA")
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
        save_kwargs = {}
        if original_format:
            save_kwargs['format'] = original_format
        image.save(image_path, **save_kwargs)
    return changed


def flatten_image_on_white(image):
    from PIL import Image

    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        background.alpha_composite(rgba)
        return background.convert("RGB")
    if image.mode not in ("RGB", "L"):
        return image.convert("RGB")
    return image


def resize_and_convert_image(
    image_path: Path,
    max_width: int | None = None,
    max_size: int | None = None,
    jpeg_quality: int | None = None,
    flatten_to_jpeg: bool = False,
) -> bool:
    try:
        from PIL import Image
    except Exception:
        return False

    changed = False
    original = Image.open(image_path)
    original_format = original.format
    image = original.copy()

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
    if flatten_to_jpeg or jpeg_quality is not None:
        flattened = flatten_image_on_white(image)
        if flattened is not image:
            image = flattened
            changed = True
        save_kwargs = {
            'format': 'JPEG',
            'quality': jpeg_quality if jpeg_quality is not None else 90,
            'optimize': True,
        }
        changed = True
    elif image.mode not in ("RGB", "RGBA", "L"):
        image = image.convert("RGB")
        changed = True
        if original_format:
            save_kwargs['format'] = original_format
    elif original_format:
        save_kwargs['format'] = original_format

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
    parser.add_argument("--meta-json", type=Path, default=None, help="Generation metadata JSON path; default is <filename>.meta.json")
    parser.add_argument("--no-meta", action="store_true", help="Do not write generation metadata JSON")
    parser.add_argument(
        "--postprocess-transparent",
        choices=["off", "auto", "force"],
        default="off",
        help="Postprocess background transparency",
    )
    parser.add_argument("--debug", action="store_true", help="Print Codex stdout/stderr on failure")
    parser.add_argument(
        "--stream-codex-output",
        action="store_true",
        help="Stream child Codex stdout/stderr to this wrapper's stderr while still keeping final stdout path-only",
    )
    parser.add_argument(
        "--codex-log",
        type=Path,
        default=None,
        help="Optional log file that receives streamed child Codex stdout/stderr",
    )
    parser.add_argument("--max-width", type=int, default=None, help="Downscale output if wider than this many pixels")
    parser.add_argument("--max-size", type=int, default=None, help="Downscale output if the longer edge exceeds this many pixels")
    parser.add_argument("--jpeg-quality", type=int, default=None, help="Re-save output as JPEG with this quality (1-100)")
    parser.add_argument("--flatten-to-jpeg", action="store_true", help="Flatten/convert final image to JPEG after generation")
    parser.add_argument(
        "--prefer-raw-generated",
        action="store_true",
        help=(
            "Prefer the raw Codex built-in image output under ~/.codex/generated_images "
            "over any workspace file Codex may create after local edits. Default is off."
        ),
    )
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
    meta_path = None if args.no_meta else (args.meta_json.resolve() if args.meta_json else default_meta_path(output_path))
    postprocess_meta = {
        "transparent": args.postprocess_transparent,
        "maxWidth": args.max_width,
        "maxSize": args.max_size,
        "jpegQuality": args.jpeg_quality,
        "flattenToJpeg": args.flatten_to_jpeg,
        "preferRawGenerated": args.prefer_raw_generated,
    }

    output_digest = hashlib.sha1(str(output_path).encode("utf-8")).hexdigest()[:12]
    output_last = EXEC_WORKSPACE / f"codex-cli-image-last-message-{output_digest}.txt"
    output_last.parent.mkdir(parents=True, exist_ok=True)
    if output_last.exists():
        output_last.unlink()
    safe_output_path = make_safe_temp_output(output_path)
    if safe_output_path.exists():
        safe_output_path.unlink()
    prompt = build_prompt(args.prompt, safe_output_path, prefer_raw_generated=args.prefer_raw_generated)
    generated_before = list_codex_generated_images() if args.prefer_raw_generated else set()
    proc = run_codex(prompt, output_last, stream=args.stream_codex_output, log_path=args.codex_log)

    if args.debug and (proc.stdout or proc.stderr):
        print("--- codex stdout ---", file=sys.stderr)
        print(proc.stdout, file=sys.stderr)
        print("--- codex stderr ---", file=sys.stderr)
        print(proc.stderr, file=sys.stderr)

    last_text = output_last.read_text(encoding="utf-8").strip() if output_last.exists() else ""
    search_text = "\n".join(part for part in [last_text, proc.stdout or "", proc.stderr or ""] if part)
    raw_generated = select_raw_generated_image(search_text, generated_before) if args.prefer_raw_generated else None

    if raw_generated and raw_generated.exists() and raw_generated.stat().st_size > 0:
        safe_output_preexisted = safe_output_path.exists()
        if raw_generated != safe_output_path:
            shutil.copy2(raw_generated, safe_output_path)
        if args.postprocess_transparent != "off":
            auto_make_background_transparent(safe_output_path, tolerance=20 if args.postprocess_transparent == "auto" else 40)
        resize_and_convert_image(
            safe_output_path,
            max_width=args.max_width,
            max_size=args.max_size,
            jpeg_quality=args.jpeg_quality,
            flatten_to_jpeg=args.flatten_to_jpeg,
        )
        shutil.copy2(safe_output_path, output_path)
        write_generation_meta(
            meta_path,
            status="success",
            output_path=output_path,
            safe_output_path=safe_output_path,
            source_path=safe_output_path,
            last_message_path=output_last,
            returncode=proc.returncode,
            prompt=prompt,
            postprocess=postprocess_meta,
            prefer_raw_generated=args.prefer_raw_generated,
            raw_generated_source_path=raw_generated,
            ignored_local_output_path=safe_output_path if safe_output_preexisted and raw_generated != safe_output_path else None,
            codex_local_edit_ignored=safe_output_preexisted and raw_generated != safe_output_path,
        )
        print(str(output_path))
        return 0

    if safe_output_path.exists() and safe_output_path.stat().st_size > 0:
        if args.postprocess_transparent != "off":
            auto_make_background_transparent(safe_output_path, tolerance=20 if args.postprocess_transparent == "auto" else 40)
        resize_and_convert_image(
            safe_output_path,
            max_width=args.max_width,
            max_size=args.max_size,
            jpeg_quality=args.jpeg_quality,
            flatten_to_jpeg=args.flatten_to_jpeg,
        )
        shutil.copy2(safe_output_path, output_path)
        write_generation_meta(
            meta_path,
            status="success",
            output_path=output_path,
            safe_output_path=safe_output_path,
            source_path=safe_output_path,
            last_message_path=output_last,
            returncode=proc.returncode,
            prompt=prompt,
            postprocess=postprocess_meta,
            prefer_raw_generated=args.prefer_raw_generated,
        )
        print(str(output_path))
        return 0

    reported = extract_path(search_text)
    if reported and reported.exists():
        source_path = reported
        if source_path != safe_output_path and not safe_output_path.exists():
            shutil.copy2(source_path, safe_output_path)
            source_path = safe_output_path
        if source_path.exists():
            if args.postprocess_transparent != "off":
                auto_make_background_transparent(source_path, tolerance=20 if args.postprocess_transparent == "auto" else 40)
            resize_and_convert_image(
                source_path,
                max_width=args.max_width,
                max_size=args.max_size,
                jpeg_quality=args.jpeg_quality,
                flatten_to_jpeg=args.flatten_to_jpeg,
            )
            shutil.copy2(source_path, output_path)
            write_generation_meta(
                meta_path,
                status="success",
                output_path=output_path,
                safe_output_path=safe_output_path,
                source_path=source_path,
                last_message_path=output_last,
                returncode=proc.returncode,
                prompt=prompt,
                postprocess=postprocess_meta,
                prefer_raw_generated=args.prefer_raw_generated,
            )
            print(str(output_path))
            return 0

    msg = "Codex did not produce the requested output file"
    if proc.returncode != 0:
        msg += f" (exit={proc.returncode})"
    if last_text:
        msg += f"\nlast_message={last_text}"
    write_generation_meta(
        meta_path,
        status="failure",
        output_path=output_path,
        safe_output_path=safe_output_path,
        source_path=reported,
        last_message_path=output_last,
        returncode=proc.returncode,
        prompt=prompt,
        postprocess=postprocess_meta,
        prefer_raw_generated=args.prefer_raw_generated,
        error=msg,
    )
    return fail(msg)


if __name__ == "__main__":
    raise SystemExit(main())
