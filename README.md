# codex-image-gen-engine

Shared Codex-based image generation engine for Garibong Labs pipelines.

## What it does
- Generates one image through Codex CLI using ChatGPT auth
- Copies the result into a requested output path
- Optionally downsizes output with `--max-size`
- Optionally converts/re-saves as JPEG with `--jpeg-quality`
- Can auto-remove flat background edges for transparent asset workflows

## Main script
- `codex_cli_image_gen.py`

## Example
```bash
python3 codex_cli_image_gen.py \
  --prompt "editorial illustration of a shipping route map" \
  --filename ./out.jpg \
  --max-size 1280 \
  --jpeg-quality 85
```

## Notes
- Uses Codex CLI login state from `~/.codex/auth.json`
- Does not require `OPENAI_API_KEY`
- Intended as a reusable engine; prompt/style profiles should live separately under `profiles/`
