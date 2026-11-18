"""The rulings ledger's entries, for kitlib.backlog's readers.

No backlog write and no `check` reads a ledger file: the rulings ledger is the PM's private
record, and an owner gate on a write is a role (kitlib/backlog/roles.py), not a cited id. Only
`next` and `list --blocked` read it, through read_all, to tell a deliberate hold that a ruling it
cites has ended (docs/backlog.md "next: a short offer's causes").
`Entry` and `read_entries` are kitlib.ledger.read's, re-exported; `successor` follows a
tombstone's `see` chain over the entries that reader returns.

  read_all(config)          ({id: Entry}, None) of every readable ledger file, or (None, why)
  superseded_by(entries)    {id: [ids whose `Supersedes:` names it]}
  ended(entries, by, rid)   why ruling `rid` has ended (retired or superseded), or None
  later(entries, by, rid)   every id after `rid` in its supersede and tombstone chain
"""
import os

from kitlib.ledger import fmt, read

# The entry type and the reader are kitlib.ledger.read's: one reader for every consumer.
Entry = read.Entry
read_entries = read.read_entries
# The ruling ids a text names, as the ledger writes them (R-nnnn, H-<repo>-nnnn).
ruling_ids = fmt.RID_RE.findall


def successor(entries, rid):
    """The live id a tombstone's `see` chain ends at, or None."""
    seen = set()
    while rid in entries and entries[rid].retired:
        if rid in seen:
            return None
        seen.add(rid)
        rid = entries[rid].retired[1]
        if rid == '-':
            return None
    return rid


def read_all(config=None):
    """`({id: Entry}, None)` over every ledger file the machine config names (the frozen or
    one-file ledger, then each repo's file: kitlib.ledger.files.readable), or `(None, why)`. A repo
    file not written yet (missing, or no header in it) adds nothing, as files.read_views skips
    one. `config()` gives the machine config (default kitlib.config.user.user_config); any failure
    is a why, never a raise."""
    from kitlib.ledger import files as lfiles
    try:
        if config is None:
            from kitlib.config.user import user_config as config
        found = lfiles.of_config(config())
        if found is None:
            return None, 'no ledger_path or ledger_dir'
        out = {}
        for repo, path in lfiles.readable(found):
            if repo is not None and not os.path.exists(path):
                continue
            text, why = read.read_file(path)
            if text is None:
                return None, why
            out.update((e.id, e) for e in read.parse(text))
        return out, None
    except Exception as exc:            # noqa: BLE001 - any failure is the one note line
        return None, '%s: %s' % (type(exc).__name__, ' '.join(str(exc).split()))


def superseded_by(entries):
    """`{id: [ids]}`: for each id some entry's `Supersedes:` line names, the entries naming it, in
    id order. Any file's entry may name any id (an H- entry superseding an R- one)."""
    out = {}
    for e in sorted(entries.values(), key=lambda e: e.id):
        for old in e.supersedes:
            out.setdefault(old, []).append(e.id)
    return out


def ended(entries, by, rid):
    """Why ruling `rid` has ended, or None: `retired: <class>` for a tombstone, `superseded by
    <ids>` when an entry's `Supersedes:` names it (`by`, superseded_by). Nothing else ends one: an
    id the ledger does not hold, or a ruling whose end is only said in prose, has not ended."""
    e = entries.get(rid)
    if e is not None and e.retired:
        return 'retired: %s' % e.retired[0]
    if by.get(rid):
        return 'superseded by %s' % ', '.join(by[rid])
    return None


def later(entries, by, rid):
    """Every id after `rid` in its chain: the entries superseding it (`by`) and a tombstone's
    `see` id, followed on from each, `rid` itself left out."""
    out, todo = set(), [rid]
    while todo:
        cur = todo.pop()
        e = entries.get(cur)
        nxt = list(by.get(cur, ()))
        if e is not None and e.retired and e.retired[1] != '-':
            nxt.append(e.retired[1])
        for n in nxt:
            if n not in out and n != rid:
                out.add(n)
                todo.append(n)
    return out
