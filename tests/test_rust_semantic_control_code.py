"""Source/AST-only bootstrap tests. Never eval, exec, spawn or signal."""
import ast
import hashlib
import unittest

from scripts.rust_semantic_control_code import ROOT_SOURCE, prepare_sources


class ControlCodeTests(unittest.TestCase):
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
