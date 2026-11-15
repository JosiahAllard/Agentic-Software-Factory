---
name: ledger-ops
description: Use as the PM when you record an owner ruling with <kit>/tools/ledger.py add, especially one that supersedes an earlier entry. Gives the one bare call per entry and the --supersedes rules.
---

# Ledger operations

PM only. Format, verbs: `<kit>/docs/ledger.md`. An owner standing rule goes through `add --rule` (design rules go in the design docs); an entry without `--rule` is only for delegated calls; a one-off go gets no entry.

1. One entry = one bare call per shell call: `python <kit>/tools/ledger.py add --repo <repo> --prov <typed|selected|relayed-from-terminal|delegated> --q "<question>" --a "<answer>" --decides "<one line>" --source "<where given>" [--supersedes <id>] [--rule <key> --topic <T> --checked <hash from conflicts>]`. No heredoc, script, `cd`, pipe, chaining, second `add`; prints new id. No file flag: long text in quoted values, whole answer in `--a`. Decides over 400 chars warns: shorten.
2. Never guess an id: from `add` output; look up with `python <kit>/tools/ledger.py grep "<text>"` or `show <id>`.
3. `--supersedes <id>`: earlier entry, same repo file; with `ledger_dir` set, also frozen `ledger_path` file (read, never written). In neither, or not earlier: `REFUSED: the new entry is invalid: ... supersedes <id>, which is not an earlier entry ...` (exit 1, nothing written). Fix id once from `grep`/`show`; entry in another repo's file: record there (`--repo`), or drop `--supersedes`, say so in Decides.
4. Superseding leaves old entry: append-only, frozen file never written. `retire <id> --class superseded --why "<why>" --by <new id>` tombstones a repo-file entry; frozen-file retire refused.
5. `--repo all` refused with `ledger_dir` set: all-repo ruling in `--repo pm`. Provenance = owner's own words (`typed`, `selected`, `relayed-from-terminal`); `delegated` needs Source `under <id>` or `under hub:A-nnn`. `REFUSED:` is final on substance (skill backlog-ops "Refusals"); a usage error (exit 2) is yours: fix once.
