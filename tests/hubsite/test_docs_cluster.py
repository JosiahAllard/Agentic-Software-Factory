"""The Docs section's cluster map (kitlib/hubsite/web/index.html renderDocs): a treemap above the cards that shows the
selected tree node's children (repos, then kinds, then months), kept in sync with the tree. Run against the stand-in
DOM of tests/hubsite/pagedom.py."""
import unittest

from tests.hubsite.test_docs_tree import SEE, card, snap
from tests.hubsite.test_notices_docs import dom, snapshot
from tests.hubsite.test_page import NODE

MAP = """
    const cells = () => $$("#list .cmap .cell").map(c => [c.getAttribute("title"), c.classList.contains("on")]);
    const cell = t => $$("#list .cmap .cell").find(c => c.getAttribute("title").startsWith(t + " "));
    const lit = () => nodes().filter(n => n[2]).map(n => n[0]);
"""


@unittest.skipUnless(NODE, "node is needed to run the page's logic")
class DocsClusterTest(unittest.TestCase):
    def test_the_map_is_an_image_with_a_label_and_a_cell_per_child_with_titles(self):
        got = dom(SEE + MAP + """
            await start();
            const m = $("#list .cmap");
            return { role: m.getAttribute("role"), label: m.getAttribute("aria-label"), cells: cells(),
                     tone: cell("alpha").className };
        """, snap(), hash="#docs")
        self.assertEqual(got["role"], "img")
        self.assertIn("All documents", got["label"])
        self.assertEqual(got["cells"], [["alpha \u00b7 4 docs", False], ["Unsorted \u00b7 1 doc", False], ["beta \u00b7 1 doc", False]])
        self.assertIn("k-", got["tone"])

    def test_a_cell_click_selects_the_node_in_the_tree_and_cards_and_redraws_the_map(self):
        got = dom(SEE + MAP + """
            await start();
            await click(cell("alpha"));
            const repo = { lit: lit(), cards: cards(), cells: cells(), head: head() };
            await click(cell("Plan"));
            const kind = { lit: lit(), cards: cards(), kept: localStorage.getItem("oq.docnode"), cells: cells() };
            return { repo, kind };
        """, snap(), hash="#docs")
        self.assertEqual(got["repo"]["lit"], ["alpha"])
        self.assertEqual(got["repo"]["cards"], ["row-a-bare", "row-a-plan-2", "row-a-design", "row-a-plan-1"])
        self.assertEqual([c[0] for c in got["repo"]["cells"]], ["Plan \u00b7 2 docs", "Design \u00b7 1 doc", "Unsorted \u00b7 1 doc"])
        self.assertEqual(got["kind"]["lit"], ["Plan"])
        self.assertEqual(got["kind"]["cards"], ["row-a-plan-2", "row-a-plan-1"])
        self.assertEqual(got["kind"]["kept"], '["alpha","plan"]')
        self.assertEqual(got["kind"]["cells"], [["2026-10 \u00b7 2 docs", False]])

    def test_a_tree_click_redraws_the_map_and_the_cell_of_the_selected_child_is_marked(self):
        got = dom(SEE + MAP + """
            await start();
            await click(node("beta"));
            const beta = cells();
            await click(node("All documents"));
            return { beta, all: cells().length };
        """, snap(), hash="#docs")
        self.assertEqual(got["beta"], [["Research \u00b7 1 doc", False]])
        self.assertEqual(got["all"], 3)

    def test_the_map_follows_search_and_is_absent_in_the_empty_state(self):
        got = dom(SEE + MAP + """
            await start();
            await type($("#idq"), "research");
            const one = cells();
            await type($("#idq"), "zzz");
            return { one, none: $$("#list .cmap").length };
        """, snap(), hash="#docs")
        self.assertEqual(got["one"], [["beta \u00b7 1 doc", False]])
        self.assertEqual(got["none"], 0)

    def test_markup_in_a_repo_name_is_drawn_as_text_and_300_cards_draw_a_small_map(self):
        bad = "<img src=x onerror=alert(1)>"
        pages = {"p%d" % i: card("t%d" % i, "repo%d" % (i % 7), ["plan", "audit", "design"][i % 3], "2026-%02d-05T00:00:00Z" % (i % 12 + 1)) for i in range(300)}
        pages["m"] = card("m", bad, "plan", "2026-10-01T00:00:00Z")
        got = dom(SEE + MAP + """
            await start();
            const all = { cards: cards().length, cells: cells().map(c => c[0]) };
            await click(cell("repo1"));
            return { all, repo: cells().length, sinks, tags: [...new Set(created)] };
        """, snap(pages), hash="#docs")
        self.assertEqual(got["all"]["cards"], 301)
        self.assertEqual(len(got["all"]["cells"]), 8)
        self.assertIn(bad + " \u00b7 1 doc", got["all"]["cells"])
        self.assertEqual(got["repo"], 3)
        self.assertEqual(got["sinks"], [])
        self.assertNotIn("img", got["tags"])


if __name__ == "__main__":
    unittest.main()
