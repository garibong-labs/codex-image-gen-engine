import importlib.util
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image


MODULE_PATH = Path(__file__).resolve().parents[1] / "codex_cli_image_gen.py"
SPEC = importlib.util.spec_from_file_location("codex_cli_image_gen", MODULE_PATH)
engine = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(engine)


class ImageProcessingTests(unittest.TestCase):
    def test_resize_preserves_png_format_by_default(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.png"
            Image.new("RGBA", (20, 10), (10, 20, 30, 128)).save(path)

            changed = engine.resize_and_convert_image(path)

            self.assertFalse(changed)
            with Image.open(path) as output:
                self.assertEqual(output.format, "PNG")
                self.assertEqual(output.mode, "RGBA")

    def test_resize_preserves_png_format_when_downscaled(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.png"
            Image.new("RGBA", (200, 100), (10, 20, 30, 128)).save(path)

            changed = engine.resize_and_convert_image(path, max_size=100)

            self.assertTrue(changed)
            with Image.open(path) as output:
                self.assertEqual(output.format, "PNG")
                self.assertEqual(output.mode, "RGBA")
                self.assertEqual(output.size, (100, 50))

    def test_flatten_to_jpeg_uses_white_background_for_alpha(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.png"
            image = Image.new("RGBA", (8, 8), (255, 0, 0, 0))
            image.putpixel((4, 4), (0, 255, 0, 255))
            image.save(path)

            changed = engine.resize_and_convert_image(path, jpeg_quality=95, flatten_to_jpeg=True)

            self.assertTrue(changed)
            with Image.open(path) as output:
                self.assertEqual(output.format, "JPEG")
                self.assertEqual(output.mode, "RGB")
                corner = output.getpixel((0, 0))
                self.assertGreater(corner[0], 240)
                self.assertGreater(corner[1], 240)
                self.assertGreater(corner[2], 240)

    def test_safe_temp_output_lives_under_tmp_generated_images(self):
        output = engine.make_safe_temp_output(Path("/tmp/example/out.png"))

        self.assertEqual(output.parent.name, "generated-images")
        self.assertEqual(output.parent.parent.name, "tmp")
        self.assertTrue(output.name.startswith("out-"))
        self.assertEqual(output.suffix, ".png")

    def test_build_prompt_keeps_raw_guardrail_opt_in(self):
        default_prompt = engine.build_prompt("draw a cat", Path("/tmp/out.png"))
        raw_prompt = engine.build_prompt("draw a cat", Path("/tmp/out.png"), prefer_raw_generated=True)

        self.assertNotIn("Do not alter it with Python", default_prompt)
        self.assertIn("Do not alter it with Python", raw_prompt)

    def test_extract_generated_image_paths_only_accepts_codex_generated_images(self):
        with TemporaryDirectory() as tmp:
            original_root = engine.CODEX_GENERATED_IMAGES
            root = Path(tmp) / ".codex" / "generated_images"
            raw = root / "session" / "raw.png"
            local = Path(tmp) / "workspace" / "edited.png"
            raw.parent.mkdir(parents=True)
            local.parent.mkdir(parents=True)
            Image.new("RGB", (4, 4), (1, 2, 3)).save(raw)
            Image.new("RGB", (4, 4), (4, 5, 6)).save(local)
            engine.CODEX_GENERATED_IMAGES = root
            try:
                paths = engine.extract_generated_image_paths(f"raw={raw}\nlocal={local}")
            finally:
                engine.CODEX_GENERATED_IMAGES = original_root

            self.assertEqual(paths, [raw.resolve()])

    def test_select_raw_generated_image_prefers_mentioned_raw_asset(self):
        with TemporaryDirectory() as tmp:
            original_root = engine.CODEX_GENERATED_IMAGES
            root = Path(tmp) / ".codex" / "generated_images"
            raw = root / "session" / "raw.png"
            raw.parent.mkdir(parents=True)
            Image.new("RGB", (4, 4), (1, 2, 3)).save(raw)
            engine.CODEX_GENERATED_IMAGES = root
            try:
                selected = engine.select_raw_generated_image(f"generated {raw}", before_paths=set())
            finally:
                engine.CODEX_GENERATED_IMAGES = original_root

            self.assertEqual(selected, raw.resolve())



class MetadataTests(unittest.TestCase):
    def test_default_meta_path_appends_meta_json_to_filename(self):
        output = Path('/tmp/example/out.jpg')

        self.assertEqual(engine.default_meta_path(output), Path('/tmp/example/out.jpg.meta.json'))

    def test_write_generation_meta_records_backend_and_model_labels(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            output = tmp_path / 'out.jpg'
            safe = tmp_path / 'safe.jpg'
            last = tmp_path / 'last.txt'
            meta = tmp_path / 'out.jpg.meta.json'
            Image.new('RGB', (12, 8), (1, 2, 3)).save(output)
            Image.new('RGB', (12, 8), (1, 2, 3)).save(safe)
            last.write_text('/tmp/final.jpg\n', encoding='utf-8')

            engine.write_generation_meta(
                meta,
                status='success',
                output_path=output,
                safe_output_path=safe,
                source_path=safe,
                last_message_path=last,
                returncode=0,
                prompt='prompt text',
                postprocess={'maxSize': 1280, 'flattenToJpeg': True},
            )

            data = json.loads(meta.read_text(encoding='utf-8'))
            self.assertEqual(data['status'], 'success')
            self.assertEqual(data['generation']['provider'], 'openai-codex')
            self.assertEqual(data['generation']['backend'], 'codex-cli-built-in-image-generation')
            self.assertEqual(data['generation']['modelLabel'], 'gpt-image-2')
            self.assertEqual(data['generation']['modelLabelSource'], 'openai-codex-cli-official-docs')
            self.assertEqual(data['generation']['modelConfidence'], 'docs-backed-not-runtime-verified')
            self.assertEqual(data['generation']['modelEvidenceUrl'], 'https://developers.openai.com/codex/cli/features#image-generation')
            self.assertFalse(data['generation']['fallbackProviderUsed'])
            self.assertEqual(data['codex']['returncode'], 0)
            self.assertEqual(data['output']['bytes'], output.stat().st_size)
            self.assertEqual(data['postprocess']['maxSize'], 1280)
            self.assertEqual(data['safety']['rawCodexGeneratedImagePreferred'], False)
            self.assertEqual(data['safety']['rawCodexGeneratedImageUsed'], False)
            self.assertEqual(data['safety']['codexLocalEditIgnored'], False)

    def test_write_generation_meta_records_ignored_codex_local_edit(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            output = tmp_path / 'out.jpg'
            safe = tmp_path / 'safe.jpg'
            raw = tmp_path / 'raw.png'
            meta = tmp_path / 'out.jpg.meta.json'
            Image.new('RGB', (12, 8), (1, 2, 3)).save(output)
            Image.new('RGB', (12, 8), (4, 5, 6)).save(safe)
            Image.new('RGB', (12, 8), (7, 8, 9)).save(raw)

            engine.write_generation_meta(
                meta,
                status='success',
                output_path=output,
                safe_output_path=safe,
                source_path=safe,
                last_message_path=None,
                returncode=0,
                prompt='prompt text',
                postprocess={},
                prefer_raw_generated=True,
                raw_generated_source_path=raw,
                ignored_local_output_path=safe,
                codex_local_edit_ignored=True,
            )

            data = json.loads(meta.read_text(encoding='utf-8'))
            self.assertEqual(data['output']['rawGeneratedSourcePath'], str(raw))
            self.assertEqual(data['output']['ignoredLocalOutputPath'], str(safe))
            self.assertEqual(data['safety']['rawCodexGeneratedImagePreferred'], True)
            self.assertEqual(data['safety']['rawCodexGeneratedImageUsed'], True)
            self.assertEqual(data['safety']['codexLocalEditIgnored'], True)


if __name__ == "__main__":
    unittest.main()
