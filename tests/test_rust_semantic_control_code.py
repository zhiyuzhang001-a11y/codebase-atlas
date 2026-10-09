"""Source/AST-only bootstrap tests. Never eval, exec, spawn or signal."""
import ast
import hashlib
from pathlib import Path
import unittest

from scripts.rust_semantic_control_code import (ROOT_SOURCE, MODULE_ORDER,
                                               prepare_module_bundle, prepare_sources)
from scripts.rust_semantic_source_file import SOURCE_NAMES


class ControlCodeTests(unittest.TestCase):
    def modules(self):
        return {name + '.py': b'# measured fixed definition\n' for name in MODULE_ORDER}

    def test_complete_module_bundle_is_deterministic_syntax_only(self):
        sources = self.modules()
        sources['rust_semantic_journal_pipe.py'] = (
            b'from scripts.rust_semantic_control_journal import FRAME\n')
        raw, receipt = prepare_module_bundle(sources)
        again, other = prepare_module_bundle(dict(reversed(list(sources.items()))))
        self.assertEqual((raw, receipt), (again, other))
        self.assertEqual(set(sources), SOURCE_NAMES)
        self.assertEqual(receipt['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertFalse(receipt['source_authenticated'])
        self.assertFalse(receipt['source_policy_audited'])
        tree = ast.parse(raw)
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
        self.assertEqual(sum(isinstance(n.func, ast.Name) and n.func.id == 'exec'
                             for n in calls), 23)
        self.assertIn(b'_package.__path__ = []', raw)
        self.assertIn(b'foreign scripts package already loaded', raw)
        self.assertNotIn(b'sys.path', raw)

    def test_unknown_relative_and_unprepared_imports_refused(self):
        for raw in (b'import project_plugin\n', b'from scripts.evil import x\n',
                    b'from . import evil\n', b'from scripts.rust_semantic_journal_pipe import x\n'):
            sources = self.modules()
            sources['rust_semantic_linux_resources.py'] = raw
            with self.assertRaises(ValueError): prepare_module_bundle(sources)

    def test_module_set_size_utf8_syntax_and_total_bounds(self):
        sources = self.modules()
        sources.pop(next(iter(sources)))
        with self.assertRaises(ValueError): prepare_module_bundle(sources)
        sources = self.modules()
        sources['evil.py'] = b'# evil'
        with self.assertRaises(ValueError): prepare_module_bundle(sources)
        for raw in (b'', b'x'*65537, b'\xff', b'('):
            sources = self.modules()
            sources[next(iter(sources))] = raw
            with self.assertRaises((ValueError, UnicodeError, SyntaxError)):
                prepare_module_bundle(sources)
        sources = {name + '.py': b'#' + b'x'*65535 for name in MODULE_ORDER}
        with self.assertRaises(ValueError): prepare_module_bundle(sources)

    def test_current_fixed_checkout_modules_all_fit_and_import_in_order(self):
        # Bounded read-only test inputs; never import/eval the generated artifact.
        folder = Path(__file__).resolve().parents[1] / 'scripts'
        sources = {name + '.py': (folder / (name + '.py')).read_bytes()
                   for name in MODULE_ORDER}
        raw, receipt = prepare_module_bundle(sources)
        self.assertEqual(len(receipt['modules']), 23)
        self.assertLessEqual(receipt['source_bytes'], 192*1024)
        self.assertLessEqual(len(raw), 512*1024)
        self.assertEqual(receipt['delivery'], 'owned-regular-artifact-required')

    def test_exact_measured_bytes_embedded_and_receipted(self):
        resource = b'# measured resource\n'
        ptrace = b'# measured ptrace\n'
        source, receipt = prepare_sources(resource, ptrace)
        self.assertIn(repr(resource.hex()), source)
        self.assertIn(repr(ptrace.hex()), source)
        self.assertEqual(receipt['resources']['sha256'], hashlib.sha256(resource).hexdigest())
        self.assertEqual(receipt['assembled']['bytes'], len(source.encode()))
        self.assertEqual(receipt['assembled']['sha256'], hashlib.sha256(source.encode()).hexdigest())
        self.assertNotIn('sys.path', source)
        self.assertNotIn('from scripts', source)

    def test_bounds_and_invalid_utf8_refused(self):
        for raw in (None, '', bytearray(b'x'), b'', b'x'*65537, b'\xff'):
            with self.assertRaises((ValueError, UnicodeDecodeError)):
                prepare_sources(raw, b'# good\n')
        with self.assertRaises(SyntaxError):
            prepare_sources(b'# good\n', b'(')
        with self.assertRaises(ValueError):
            prepare_sources(b'#' + b'x'*65535, b'#' + b'x'*65535)

    def test_bootstrap_has_fixed_exec_and_no_top_level_control_call(self):
        tree = ast.parse(ROOT_SOURCE)
        self.assertEqual(len(tree.body), 1)
        self.assertIsInstance(tree.body[0], ast.FunctionDef)
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
        path = [node for node in calls if isinstance(node.func, ast.Attribute)
                and node.func.attr == 'execve']
        self.assertEqual(len(path), 1)
        self.assertEqual(ast.literal_eval(path[0].args[0]), '/usr/bin/true')
        loop = next(node for node in ast.walk(tree) if isinstance(node, ast.For))
        self.assertEqual(ast.literal_eval(loop.iter), ('path', 'fd'))
        self.assertEqual([arg.arg for arg in tree.body[0].args.args],
                         ['libc', 'true_fd', 'control_env'])


if __name__ == '__main__':
    unittest.main()
