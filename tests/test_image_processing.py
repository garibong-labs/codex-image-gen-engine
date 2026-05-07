import importlib.util
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


if __name__ == "__main__":
    unittest.main()
