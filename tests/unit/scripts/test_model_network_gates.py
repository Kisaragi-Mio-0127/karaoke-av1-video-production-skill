"""Network opt-in gates for public ASR and MMS model loading."""

from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = ROOT / "integration" / "strangeutagame" / "scripts"
ASR_SOURCE = SCRIPT_DIR / "audit_karaoke_asr_recognition.py"
MMS_SOURCE = SCRIPT_DIR / "audit_karaoke_mms_alignment.py"


def load_function(path: Path, name: str):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    namespace = {"Path": Path}
    exec(  # noqa: S102 - execute one selected local function definition
        compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"),
        namespace,
    )
    return namespace[name]


class ModelNetworkGateTests(unittest.TestCase):
    def test_asr_defaults_offline_and_accepts_only_explicit_access(self) -> None:
        validate = load_function(ASR_SOURCE, "_validate_model_access")
        with self.assertRaisesRegex(RuntimeError, "offline by default"):
            validate(None, allow_network=False, model_loading_required=True)
        self.assertIsNone(
            validate(None, allow_network=False, model_loading_required=False)
        )
        self.assertIsNone(
            validate(None, allow_network=True, model_loading_required=True)
        )

        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            checkpoint = Path(temporary) / "fixture.pt"
            with self.assertRaises(FileNotFoundError):
                validate(
                    checkpoint,
                    allow_network=True,
                    model_loading_required=True,
                )
            checkpoint.write_bytes(b"fixture checkpoint")
            self.assertEqual(
                validate(
                    checkpoint,
                    allow_network=False,
                    model_loading_required=True,
                ),
                checkpoint.resolve(),
            )

    def test_mms_defaults_offline_and_accepts_only_explicit_access(self) -> None:
        validate = load_function(MMS_SOURCE, "_validate_mms_model_access")
        with self.assertRaisesRegex(RuntimeError, "offline by default"):
            validate(None, allow_network=False)
        self.assertIsNone(validate(None, allow_network=True))

        with tempfile.TemporaryDirectory(dir=ROOT) as temporary:
            checkpoint = Path(temporary) / "fixture.pt"
            with self.assertRaises(FileNotFoundError):
                validate(checkpoint, allow_network=True)
            checkpoint.write_bytes(b"fixture checkpoint")
            self.assertEqual(
                validate(checkpoint, allow_network=False),
                checkpoint.resolve(),
            )

    def test_guards_dominate_model_loader_calls_and_cli_propagates_opt_in(self) -> None:
        cases = (
            (
                ASR_SOURCE,
                "run_recognition_audit",
                "_validate_model_access",
                "load_model",
            ),
            (
                MMS_SOURCE,
                "load_mms_runtime",
                "_validate_mms_model_access",
                "get_model",
            ),
        )
        for path, function_name, validator_name, loader_name in cases:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            function = next(
                node
                for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == function_name
            )
            validator_line = min(
                node.lineno
                for node in ast.walk(function)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == validator_name
            )
            loader_line = min(
                node.lineno
                for node in ast.walk(function)
                if isinstance(node, ast.Attribute) and node.attr == loader_name
            )
            with self.subTest(script=path.name):
                self.assertLess(validator_line, loader_line)
                self.assertIn('"--allow-network"', source)
                self.assertIn("allow_network=args.allow_network", source)

        mms_source = MMS_SOURCE.read_text(encoding="utf-8")
        self.assertIn("MMS_FA.get_model(dl_kwargs=download_options)", mms_source)
        self.assertIn('"file_name": local_model_path.name', mms_source)


if __name__ == "__main__":
    unittest.main()
