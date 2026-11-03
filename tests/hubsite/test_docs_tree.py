"""The Docs section's tree (kitlib/hubsite/web/index.html renderDocs): All documents > repo > kind with a count
on each node, the cards of the selected node, search, the Unsorted node and the empty and no-match states.
Run against the stand-in DOM of tests/hubsite/pagedom.py."""
import json
import unittest

from tests.hubsite.test_notices_docs import dom, snapshot
from tests.hubsite.test_page import NODE, js


def card(title, repo, kind, at, **kw):
    d = {"title": title, "repo": repo, "kind": kind, "at": at, "version": 1, "bytes": 2048}
    d.update(kw)
    return d


PAGES = {
    "a-plan-1": card("Alpha plan one", "alpha", "plan", "2026-10-01T00:00:00Z", summary="first plan", refs=["a-design"]),
    "a-plan-2": card("Alpha plan two", "alpha", "Plan", "2026-10-03T00:00:00Z"),
    "a-design": card("Alpha design", "alpha", "design", "2026-10-02T00:00:00Z"),
    "a-bare": card("Alpha no kind", "alpha", "", "2026-10-04T00:00:00Z"),
    "b-res": card("Beta research", "beta", "research", "2026-10-05T00:00:00Z"),
    "loose": card("Loose page", "", "", "2026-10-06T00:00:00Z", bytes=None),
}

BODIES = {"a-design": "<p>design</p>", "a-plan-1": "<p>plan</p>"}
ROUTE = """
    const BODIES = %s;
    server.route = c => (c.method === "GET" && c.path.startsWith("/pages/"))
      ? { status: 200, body: { ok: true, id: c.path.slice(7), html: BODIES[c.path.slice(7)] || "<p>x</p>", version: 1 } } : undefined;
""" % json.dumps(BODIES)

# The tree as [label, count, selected] for each node, the cards and the heading.
SEE = """
    const nodes = () => $$("#list .doctree .tn").map(b => [text(b.children[0]), text(b.children[1]), b.classList.contains("on")]);
    const cards = () => $$("#list article").map(a => a.id);
    const head = () => text($("#list .dochead"));
    const toggle = repo => $$("#list .doctree .tt").find(b => b.getAttribute("aria-label").endsWith(" " + repo));
    const node = label => $$("#list .doctree .tn").find(b => text(b.children[0]) === label);
"""


def snap(pages=PAGES):
    return snapshot(collections={"pages": pages})


@unittest.skipUnless(NODE, "node is needed to run the page's logic")
class DocsTreeTest(unittest.TestCase):
    def test_the_tree_counts_repos_and_expands_and_collapses(self):
        got = dom(SEE + """
            await start();
            const closed = nodes();
            await click(toggle("alpha"));
            const open = nodes();
            const flag = toggle("alpha").getAttribute("aria-expanded");
            await click(toggle("alpha"));
            return { closed, open, flag, after: nodes(), stored: localStorage.getItem("oq.docopen") };
        """, snap(), hash="#docs")
        self.assertEqual(got["closed"], [["All documents", "6", True], ["alpha", "4", False], ["beta", "1", False], ["Unsorted", "1", False]])
        self.assertEqual(got["open"], [["All documents", "6", True], ["alpha", "4", False], ["Design", "1", False], ["Plan", "2", False],
                                       ["Unsorted", "1", False], ["beta", "1", False], ["Unsorted", "1", False]])
        self.assertEqual(got["flag"], "true")
        self.assertEqual(got["after"], got["closed"])
        self.assertEqual(got["stored"], "[]")

    def test_a_node_shows_its_cards_newest_first_with_a_heading_and_keeps_the_choice(self):
        got = dom(SEE + """
            await start();
            const all = cards();
            await click(toggle("alpha"));
            await click(node("Plan"));
            const plan = { cards: cards(), head: head(), on: nodes().filter(n => n[2]).map(n => n[0]), kept: localStorage.getItem("oq.docnode") };
            await click(node("alpha"));
            const repo = { cards: cards(), head: head() };
            await click(node("Unsorted"));                                 // the first Unsorted: alpha's pages with no kind
            const nokind = cards();
            return { all, plan, repo, nokind, heads: $$("#list h3").map(h => text(h)) };
        """, snap(), hash="#docs")
        self.assertEqual(got["all"], ["row-loose", "row-b-res", "row-a-bare", "row-a-plan-2", "row-a-design", "row-a-plan-1"])
        self.assertEqual(got["plan"]["cards"], ["row-a-plan-2", "row-a-plan-1"])
        self.assertEqual(got["plan"]["head"], "alpha / Plan2 docs · 4 KB")
        self.assertEqual(got["plan"]["on"], ["Plan"])
        self.assertEqual(got["plan"]["kept"], '["alpha","plan"]')
        self.assertEqual(got["repo"]["cards"], ["row-a-bare", "row-a-plan-2", "row-a-design", "row-a-plan-1"])
        self.assertEqual(got["nokind"], ["row-a-bare"])
        self.assertEqual(sum(int(h.split(" · ")[1]) for h in got["heads"]), 1)

    def test_a_card_with_no_repo_is_unsorted_and_has_no_size_when_unknown(self):
        got = dom(SEE + """
            await start();
            await click(node("Unsorted"));
            return { cards: cards(), head: head(), nodes: nodes().filter(n => n[2]) };
        """, snap(), hash="#docs")
        self.assertEqual(got["cards"], ["row-loose"])
        self.assertEqual(got["head"], "Unsorted1 doc")
        self.assertEqual(got["nodes"], [["Unsorted", "1", True]])

    def test_search_filters_the_tree_and_the_cards_and_opens_every_repo(self):
        got = dom(SEE + """
            await start();
            await type($("#idq"), "plan");
            const hit = { nodes: nodes(), cards: cards() };
            await type($("#idq"), "research");
            const one = { nodes: nodes().map(n => n[0]), cards: cards() };
            await type($("#idq"), "zzz");
            return { hit, one, none: text($("#list")), tree: $$("#list .doctree").length };
        """, snap(), hash="#docs")
        self.assertEqual(got["hit"]["cards"], ["row-a-plan-2", "row-a-plan-1"])
        self.assertEqual([n[:2] for n in got["hit"]["nodes"]], [["All documents", "2"], ["alpha", "2"], ["Plan", "2"]])
        self.assertEqual(got["one"], {"nodes": ["All documents", "beta", "Research"], "cards": ["row-b-res"]})
        self.assertIn('No match for "zzz"', got["none"])
        self.assertEqual(got["tree"], 0)

    def test_a_selected_node_that_a_search_removes_falls_back_to_all(self):
        got = dom(SEE + """
            await start();
            await click(node("beta"));
            await type($("#idq"), "alpha");
            return { cards: cards(), head: head() };
        """, snap(), hash="#docs")
        self.assertEqual(got["cards"], ["row-a-bare", "row-a-plan-2", "row-a-design", "row-a-plan-1"])
        self.assertTrue(got["head"].startswith("All documents4 docs"))

    def test_a_document_opens_by_a_click_on_its_card_and_by_a_ref(self):
        got = dom(ROUTE + SEE + """
            await start();
            await click(toggle("alpha")); await click(node("Plan"));
            await click(document.getElementById("row-a-plan-1"));
            const shown = $$("#pane-detail article").map(a => a.id);
            const ref = $("#doc-a-plan-1 .refs button");
            await click(ref);
            return { shown, title: text($("#reader-title")), srcdoc: all().filter(e => e.tag === "iframe").map(e => e.srcdoc.slice(-13)),
                     reads: calls.filter(c => c.path.startsWith("/pages/")).map(c => c.path), sinks };
        """, snap(), hash="#docs")
        self.assertEqual(got["shown"], ["doc-a-plan-1"])
        self.assertEqual(got["title"], "Alpha design")                      # a ref to a card outside the node opens too
        self.assertEqual(got["reads"], ["/pages/a-design"])
        self.assertEqual(got["srcdoc"], ["<p>design</p>"])
        self.assertEqual(got["sinks"], [])

    def test_the_repo_menu_narrows_the_tree(self):
        got = dom(SEE + """
            await start();
            const sel = $("#repo"); sel.value = "beta"; await fire(sel, "change");
            return nodes().map(n => n[0]);
        """, snapshot(collections={"pages": PAGES}, views={"repos": [{"name": "alpha", "aliases": []}, {"name": "beta", "aliases": []}]}), hash="#docs")
        self.assertEqual(got, ["All documents", "beta"])

    def test_no_documents_is_the_empty_state_with_no_tree(self):
        got = dom("""await start(); return { shown: text($("#list")), tree: $$("#list .doctree").length };""", snapshot(), hash="#docs")
        self.assertIn("No documents yet", got["shown"])
        self.assertEqual(got["tree"], 0)

    def test_kind_and_folder_are_read_as_text(self):
        got = js("hubState(items).pages.map(p => [p.id, p.kind, p.folder, p.bytes])", snap(
            {"x": {"title": "t", "kind": ["no"], "folder": 5, "bytes": "9"}, "y": {"title": "t", "kind": "plan", "folder": "f/g", "bytes": 12}}))
        self.assertEqual(sorted(got), [["x", "", "5", None], ["y", "plan", "f/g", 12]])


if __name__ == "__main__":
    unittest.main()
