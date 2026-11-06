# codex-image-gen-engine

Shared Codex-based image generation engine for Garibong Labs pipelines.

## What it does
- Generates one image through Codex CLI using ChatGPT auth
- Copies the result into a requested output path
- Preserves original format by default
- Optionally downsizes output with `--max-size`
- Optionally converts/re-saves as JPEG with `--jpeg-quality`
- Optionally flattens alpha onto white for JPEG output with `--flatten-to-jpeg`
- Can auto-remove flat background edges for transparent asset workflows
- Writes backend/model metadata sidecars by default (`<filename>.meta.json`)

## Format policy
- **기본은 포맷 보존**이다.
- 엔진은 특별한 옵션이 없는 한 생성된 이미지의 원래 포맷을 유지한다.
- PNG/RGBA 같은 결과를 JPEG로 강제 변환하지 않는다.

## Blog / upload optimization policy
- **블로그/업로드용 경량화는 profile/옵션으로 처리**한다.
- 예를 들어 Daum 같은 파이프라인은 `--max-size`, `--jpeg-quality`, `--flatten-to-jpeg` 같은 옵션이나 profile 설정으로 경량화한다.
- 이런 업로드 최적화 정책은 엔진의 전역 기본값이 아니라 호출자 책임으로 분리한다.

## Main script
- `codex_cli_image_gen.py`

## Example
```bash
# generic usage: keep original format unless options request conversion
python3 codex_cli_image_gen.py \
  --prompt "editorial illustration of a shipping route map" \
  --filename ./out.png

# blog/upload profile style: downscale + flatten + JPEG
python3 codex_cli_image_gen.py \
  --prompt "editorial illustration of a shipping route map" \
  --filename ./out.jpg \
  --max-size 1280 \
  --jpeg-quality 85 \
  --flatten-to-jpeg
```

## Metadata
- By default the engine writes `<filename>.meta.json` next to the requested output.
- The metadata records the declared generation path used by this engine:
  - `generation.provider`: `openai-codex`
  - `generation.backend`: `codex-cli-built-in-image-generation`
  - `generation.modelLabel`: `gpt-image-2` by default
  - `generation.modelLabelSource`: `openai-codex-cli-official-docs` by default
  - `generation.modelConfidence`: `docs-backed-not-runtime-verified` by default
  - `generation.modelEvidenceUrl`: `https://developers.openai.com/codex/cli/features#image-generation` by default
- OpenAI Codex CLI official docs state that built-in image generation uses `gpt-image-2`.
- Codex CLI does not expose per-run structured provider/model telemetry here, so this is docs-backed rather than runtime-verified.
- Override labels with `CODEX_IMAGE_PROVIDER_LABEL`, `CODEX_IMAGE_BACKEND_LABEL`, `CODEX_IMAGE_MODEL_LABEL`, or `CODEX_IMAGE_MODEL_EVIDENCE_URL` only when you have a better source for the run.
- Use `--meta-json <path>` to choose another metadata path, or `--no-meta` to disable sidecar output.

## Notes
- Uses Codex CLI login state from `~/.codex/auth.json`
- Does not require `OPENAI_API_KEY`
- Intended as a reusable engine; prompt/style profiles should live separately under `profiles/`
- Engine default should stay neutral; pipeline-specific optimization (for example Daum blog JPEG slimming) should be expressed via profile/flags, not hardcoded as the global default.
- In short: **기본은 포맷 보존**, **블로그/업로드용 경량화는 profile/옵션으로**.

### Explicit image model backend (OpenClaw 2026.10.4)

The default remains the legacy Codex backend. `codex -m` and
`CODEX_IMAGE_MODEL_LABEL` do not select an image model. For actual selection:

```sh
python3 codex_cli_image_gen.py --backend openclaw-infer \
  --image-model openai/gpt-image-2.6-flare --quality medium \
  --prompt 'A single editorial scene without text' --filename /tmp/body.jpg \
  --size 1536x1024 --max-size 1280 --jpeg-quality 90 --flatten-to-jpeg
```

Only `openai/gpt-image-2.6-flare` and `openai/gpt-image-2.6-sunburst` are
accepted. `--reference-image PATH --reference-receipt PATH` switches to
`openclaw infer image edit --file SNAPSHOT`; the snapshot contains the exact
approved bytes. Credentials remain with OpenClaw (`--agent main`); this backend
never reads Codex auth. Conversion is limited to resizing and format conversion.

The installed CLI's `image-CNpAuh-9.mjs` forwards `modelOverride` into
`runtime-B5Vk7EEi.mjs`. In `runtime-shared-0B7S8h9l.mjs`,
`resolveCapabilityModelCandidates` returns `[override]` before configured and
automatic fallback candidates. `image-generation-provider-Dnv6lkDG.mjs` forwards
the requested image model into `tools[].model` on the managed OAuth Responses
transport (or the direct Image API request). The Responses *text* model can
retry internally on model-unavailable errors; that does not change the pinned
image tool model. No config mutation is needed.

JSON is a top-level envelope: `ok`, `capability`, `transport`, `provider`,
`model`, `attempts` (failed candidates), `outputs` (`path`, `size`, `mimeType`),
and `ignoredOverrides`. The wrapper rejects unexpected values, any failed
candidate, ignored overrides, missing bytes, or a path outside its fresh exact
output target. Failed requests remove stale final output and receipt and write
failure metadata. Unknown backends are rejected by argparse.

Metadata separates `generation.requested` from `generation.result`. OpenClaw
can default the reported model to the requested candidate; this is observed CLI
result evidence, **not underlying model attestation**. Schema 3 reference receipts
record original path/SHA/byte count, submitted snapshot path, requested model,
CLI-reported provider/model, output hash, and unreviewed semantic status.
Snapshots are temporary and are removed after the request. No inner-tool
self-attestation or historical Codex receipt relabelling is performed.

Offline checks: `python3 -m unittest discover -s tests -q`.

## License

This project is licensed under the [MIT License](LICENSE).
