import argparse
import hashlib
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

spec = importlib.util.spec_from_file_location('pinned_engine', Path(__file__).resolve().parents[1] / 'codex_cli_image_gen.py')
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)


class OpenClawTests(unittest.TestCase):
    def invoke(self, mode='ok', reference=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / 'output.jpg'
            output.write_bytes(b'stale')
            ref = root / 'ref.png'
            Image.new('RGB', (32, 32), 'blue').save(ref)
            args = argparse.Namespace(filename=str(output), no_meta=False, meta_json=None,
                reference_receipt=root / 'receipt.json' if reference else None, reference_image=ref if reference else None,
                image_model='openai/gpt-image-2.6-sunburst', quality='medium', size='1024x1024',
                timeout_ms=9000, prompt='exact prompt', postprocess_transparent='off',
                max_width=None, max_size=40, jpeg_quality=90, flatten_to_jpeg=True)
            if mode == 'invalid-model':
                args.image_model = 'google/gemini'
            def fake(cmd, **kwargs):
                self.assertEqual(['openclaw', 'infer', 'image', 'edit' if reference else 'generate'], cmd[:4])
                for key, value in [('--agent', 'main'), ('--model', args.image_model), ('--quality', 'medium'),
                                   ('--timeout-ms', '9000'), ('--prompt', 'exact prompt'), ('--output-format', 'png')]:
                    self.assertEqual(value, cmd[cmd.index(key) + 1])
                self.assertTrue(kwargs['capture_output'])
                if reference:
                    snapshot = Path(cmd[cmd.index('--file') + 1])
                    self.assertNotEqual(ref, snapshot)
                    self.assertEqual(ref.read_bytes(), snapshot.read_bytes())
                raw = Path(cmd[cmd.index('--output') + 1])
                self.assertEqual(raw.resolve(), raw)
                self.assertFalse(raw.exists())
                if mode != 'missing':
                    Image.new('RGB', (80, 60), 'red').save(raw)
                data = dict(ok=True, provider='openai', model='gpt-image-2.6-sunburst', attempts=[],
                            ignoredOverrides=[], capability='image.edit' if reference else 'image.generate',
                            transport='local', outputs=[dict(path=str(raw), size=raw.stat().st_size if raw.exists() else 10)])
                if mode == 'model': data['model'] = 'gpt-image-2'
                if mode == 'provider': data['provider'] = 'google'
                if mode == 'fallback': data['attempts'] = [{'provider': 'openai', 'model': args.image_model}]
                if mode == 'ignored': data['ignoredOverrides'] = [{'key': 'quality', 'value': 'medium'}]
                if mode == 'stale': data['outputs'][0]['path'] = str(output)
                return subprocess.CompletedProcess(cmd, 7 if mode == 'failed' else 0,
                    'bad json' if mode == 'json' else json.dumps(data), '')
            with patch.object(engine.subprocess, 'run', side_effect=fake), patch.object(engine, 'ensure_auth', side_effect=AssertionError('must not read auth')):
                code = engine.run_openclaw(args)
            meta = json.loads(engine.default_meta_path(output).read_text())
            if mode != 'ok':
                self.assertNotEqual(0, code)
                self.assertFalse(output.exists())
                self.assertFalse(args.reference_receipt and args.reference_receipt.exists())
                self.assertEqual('failed', meta['status'])
                return
            self.assertEqual(0, code)
            self.assertEqual('success', meta['status'])
            self.assertEqual(args.image_model, meta['generation']['requested']['model'])
            self.assertEqual('gpt-image-2.6-sunburst', meta['generation']['result']['model'])
            with Image.open(output) as image:
                self.assertEqual(('JPEG', (40, 30)), (image.format, image.size))
            if reference:
                receipt = json.loads(args.reference_receipt.read_text())
                self.assertEqual(3, receipt['schemaVersion'])
                self.assertEqual(hashlib.sha256(ref.read_bytes()).hexdigest(), receipt['referenceSha256'])
                self.assertEqual(hashlib.sha256(output.read_bytes()).hexdigest(), receipt['outputSha256'])

    def test_generate_and_reference_edit(self):
        self.invoke()
        self.invoke(reference=True)

    def test_fail_closed(self):
        for mode in ['model', 'provider', 'fallback', 'ignored', 'stale', 'failed', 'missing', 'json', 'invalid-model']:
            with self.subTest(mode=mode):
                self.invoke(mode, reference=True)

    def test_reference_alias_rejected_before_any_writes(self):
        for destination in ('filename', 'meta_json', 'reference_receipt'):
            for alias in ('same', 'symlink', 'hardlink'):
                with self.subTest(destination=destination, alias=alias), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    reference = root / 'reference.png'
                    Image.new('RGB', (32, 32), 'blue').save(reference)
                    original = reference.read_bytes()
                    paths = dict(filename=root / 'output.png', meta_json=root / 'meta.json',
                                 reference_receipt=root / 'receipt.json')
                    for path in paths.values():
                        path.write_bytes(b'existing destination')
                    target = root / 'alias.png'
                    if alias == 'symlink':
                        target.symlink_to(reference)
                    elif alias == 'hardlink':
                        target.hardlink_to(reference)
                    else:
                        target = reference
                    paths[destination] = target
                    before = {path: path.read_bytes() for path in paths.values()}
                    args = argparse.Namespace(**paths, no_meta=False, reference_image=reference)
                    with patch.object(engine.subprocess, 'run') as run:
                        self.assertNotEqual(0, engine.run_openclaw(args))
                    run.assert_not_called()
                    self.assertEqual(original, reference.read_bytes())
                    for path, data in before.items():
                        self.assertEqual(data, path.read_bytes())

    def test_destination_overlap_rejected_before_cleanup(self):
        for first, second in (('filename', 'meta_json'), ('filename', 'reference_receipt'),
                              ('meta_json', 'reference_receipt')):
            with self.subTest(first=first, second=second), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                paths = dict(filename=root / 'output.png', meta_json=root / 'meta.json',
                             reference_receipt=root / 'receipt.json')
                for path in paths.values():
                    path.write_bytes(b'preserve')
                paths[second] = paths[first]
                args = argparse.Namespace(**paths, no_meta=False, reference_image=None)
                with patch.object(engine.subprocess, 'run') as run:
                    self.assertNotEqual(0, engine.run_openclaw(args))
                run.assert_not_called()
                for path in paths.values():
                    self.assertEqual(b'preserve', path.read_bytes())

    def test_symlink_temporary_root_succeeds(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real = root / 'real'
            real.mkdir()
            alias = root / 'alias'
            alias.symlink_to(real, target_is_directory=True)
            with patch.object(tempfile, 'tempdir', str(alias)):
                self.invoke()
                self.invoke(reference=True)

    def test_unknown_backend_and_legacy_default(self):
        script = str(Path(engine.__file__))
        result = subprocess.run(['python3', script, '--backend', 'unknown', '-p', 'p', '-f', '/tmp/unused'], capture_output=True)
        self.assertNotEqual(0, result.returncode)
        with patch.object(engine.sys, 'argv', [script, '-p', 'p', '-f', '/tmp/unused']), patch.object(engine, 'ensure_auth', return_value=(False, 'legacy auth called')) as auth:
            self.assertEqual(1, engine.main())
            auth.assert_called_once()


if __name__ == '__main__':
    unittest.main()
