"""Every file under templates/ is copied by a setup step, or named by a role or docs/install.md's
hand steps that draft or copy from it (docs/modules/install-design.md "Adding to it"). A reader
names a file by its `templates/...` path, where `<stack>` stands for each stack and `{a,b}` for
each of a and b."""
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from kitlib import stackskill
from kitlib.ledger import fmt
from kitlib.machinesetup import repo
from tests.shipped import ROOT

READERS = sorted((ROOT / "content" / "roles").glob("*.md")) + [ROOT / "docs" / "install.md"]
NAMED = re.compile(r"templates/((?:[\w.<>/-]|\{[\w.,-]+\})+)")
BRACES = re.compile(r"\{([^{}]*)\}")


def copied(templates):
    """The templates-relative paths the setup steps copy: repo.py's templates, the ledger's
    starter, each stack's tools/ files (copied whole) and its skills (stackskill.plan)."""
    out = {"common/" + n for n in list(repo.LINE_TEMPLATES.values()) + [repo.INDEX_TEMPLATE]}
    out.add(Path(fmt.TEMPLATE).relative_to(ROOT / "templates").as_posix())
    for stack in (templates / "stacks").iterdir():
        base = "stacks/" + stack.name + "/"
        out |= {base + n for n in (repo.STACK_META, repo.STACK_GATE)}
        tools = stack / "tools"
        out |= {base + "tools/" + p.name for p in tools.iterdir() if p.is_file()}
        with tempfile.TemporaryDirectory() as tmp:
            for dst, _text, _kept in stackskill.plan(stack.name, tmp, kit_root=str(ROOT)):
                out.add(base + Path(dst).relative_to(tmp).as_posix())
    return out


def expand(path, stacks):
    """`path` with each `{a,b}` and `<stack>` spelled out: the paths it names."""
    m = BRACES.search(path)
    if m:
        return {p for alt in m.group(1).split(",")
                for p in expand(path[:m.start()] + alt + path[m.end():], stacks)}
    return {path.replace("<stack>", s) for s in stacks} if "<stack>" in path else {path}


def named(templates):
    """The templates-relative paths the readers name."""
    stacks = [p.name for p in (templates / "stacks").iterdir() if p.is_dir()]
    text = "".join(p.read_text(encoding="utf-8") for p in READERS)
    return {p for m in NAMED.finditer(text) for p in expand(m.group(1).rstrip("./"), stacks)}


def unnamed(templates):
    """The files under `templates` no copy list and no reader names."""
    done = copied(templates) | named(templates)
    return [rel for rel in (p.relative_to(templates).as_posix() for p in sorted(templates.rglob("*"))
                            if p.is_file() and "__pycache__" not in p.parts)
            if rel not in done]


class AllCopiedTest(unittest.TestCase):
    def test_every_template_is_copied_or_read(self):
        self.assertEqual(unnamed(ROOT / "templates"), [])

    def test_a_stray_file_is_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "templates"
            shutil.copytree(ROOT / "templates", copy)
            (copy / "common" / "stray.txt").write_text("x\n", encoding="utf-8")
            (copy / "stacks" / "python" / "tools" / "ok.py").write_text("x\n", encoding="utf-8")
            self.assertEqual(unnamed(copy), ["common/stray.txt"])


if __name__ == "__main__":
    unittest.main()
