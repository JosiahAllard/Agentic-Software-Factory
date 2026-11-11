"""Every file under templates/ is copied by a setup step, or named by a role or docs/install.md's
hand steps that draft or copy from it (docs/modules/install-design.md "Adding to it")."""
import shutil
import tempfile
import unittest
from pathlib import Path

from kitlib import stackskill
from kitlib.ledger import fmt
from kitlib.machinesetup import repo
from tests.shipped import ROOT

READERS = sorted((ROOT / "content" / "roles").glob("*.md")) + [ROOT / "docs" / "install.md"]


def copied(templates):
    """The templates-relative paths the setup steps copy: repo.py's lists, the ledger's starter,
    each stack's tools/ files (copied whole) and its skills (stackskill.plan)."""
    out = {"common/" + n for n in repo.COMMON_TEMPLATES}
    out.add(Path(fmt.TEMPLATE).relative_to(ROOT / "templates").as_posix())
    for stack in (templates / "stacks").iterdir():
        base = "stacks/" + stack.name + "/"
        out |= {base + n for n in repo.STACK_TEMPLATES}
        tools = stack / "tools"
        out |= {base + "tools/" + p.name for p in tools.iterdir() if p.is_file()}
        with tempfile.TemporaryDirectory() as tmp:
            for dst, _text, _kept in stackskill.plan(stack.name, tmp, kit_root=str(ROOT)):
                out.add(base + Path(dst).relative_to(tmp).as_posix())
    return out


def unnamed(templates):
    """The files under `templates` no copy list and no reader names."""
    text = "".join(p.read_text(encoding="utf-8") for p in READERS)
    done = copied(templates)
    bad = []
    for p in sorted(templates.rglob("*")):
        rel = p.relative_to(templates).as_posix()
        if not p.is_file() or "__pycache__" in p.parts or rel in done or "templates/" + rel in text:
            continue
        inner = rel.split("/", 2)[-1] if rel.startswith("stacks/") else rel
        if inner.startswith(".github/workflows/") and ".github/workflows/{quick,full}.yml" in text:
            continue
        bad.append(rel)
    return bad


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
