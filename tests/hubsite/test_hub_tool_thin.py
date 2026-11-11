"""tools/hub.py holds no logic (ARCHITECTURE.md, Layers): the work of each verb is in
kitlib/hubsite/cli/, the parser in tools/_hub_args.py. The tool stays at most 500 lines, and
neither file imports a kitlib.hubsite module other than kitlib.hubsite.cli and its modules.

The import rule is deliberately stricter than "no logic": a tool that reached kitlib.hubsite
itself (its api, control or gitsync) is where logic grows back, so even a name it only passes
on is read through the cli module that uses it."""
import ast
import os
import unittest

from kitlib.modulemap.detectors.python import imports

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TOOL = os.path.join('tools', 'hub.py')
FILES = (TOOL, os.path.join('tools', '_hub_args.py'))
MAX_LINES = 500


def reached(source):
    """(line, dotted name) of each kitlib.hubsite name `source` imports outside
    kitlib.hubsite.cli: `from kitlib import hubsite` and `from kitlib.hubsite import api` too."""
    for line, level, parts, _text in imports(ast.parse(source)):
        if level == 0 and parts[:2] == ['kitlib', 'hubsite'] and parts[2:3] != ['cli']:
            yield line, '.'.join(parts)


def read(rel):
    with open(os.path.join(ROOT, rel), encoding='utf-8') as f:
        return f.read()


class HubToolThin(unittest.TestCase):
    def test_the_tool_is_at_most_500_lines(self):
        lines = len(read(TOOL).splitlines())
        self.assertLessEqual(lines, MAX_LINES, '%s has %d lines' % (TOOL, lines))

    def test_the_tool_and_its_parser_reach_kitlib_hubsite_only_through_cli(self):
        found = ['%s:%d %s' % (rel, line, name) for rel in FILES
                 for line, name in reached(read(rel))]
        self.assertEqual(found, [])

    def test_the_scan_sees_each_form_of_a_hubsite_import(self):
        source = ('from kitlib.hubsite.cli import common\nimport kitlib.hubsite.cli.server\n'
                  'from kitlib import hubsite\nfrom kitlib.hubsite import api\n'
                  'def f():\n    import kitlib.hubsite.control\n'
                  'from kitlib.hubsite.gitsync import scrub\nfrom kitlib.hubclient import client\n')
        self.assertEqual(sorted(name for _, name in reached(source)),
                         ['kitlib.hubsite', 'kitlib.hubsite.api', 'kitlib.hubsite.control',
                          'kitlib.hubsite.gitsync.scrub'])


if __name__ == '__main__':
    unittest.main()
