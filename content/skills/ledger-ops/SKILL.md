---
name: ledger-ops
description: Use as the PM when you record an owner standing rule or a delegated call with <kit>/tools/ledger.py add, especially one that supersedes an earlier entry. Gives the one bare call per entry and the --supersedes rules.
---

# Ledger operations

PM only. Format, verbs: `<kit>/docs/ledger.md`. An owner standing rule goes through `add --rule` (design rules: the design docs); an entry without `--rule` is only for delegated calls; one-off answers (a go, a hold, a one-case no) get no entry (the hub answer or the record it concerns keeps it).

1. One entry = one bare call per shell call: `python <kit>/tools/ledger.py add --repo <repo> --prov <typed|selected|relayed-from-terminal|delegated> --q "<question>" --a "<answer>" --decides "<one line>" --source "<where given>" [--supersedes <id>] [--rule <key> --topic <T> --checked <hash>]`. Run `conflicts` first for the hash (else exit 2). No heredoc, script, `cd`, pipe, chaining, second `add`. Decides over 400 chars warns: shorten.
2. Never guess an id: from `add` output, `grep "<text>"` or `show <id>`.
3. `--supersedes <id>`: earlier entry, same repo file (with `ledger_dir`, also the frozen file, read only). Else `REFUSED: ... not an earlier entry` (exit 1, nothing written). Fix the id once; another repo's file: record there (`--repo`), or drop `--supersedes`, say so in Decides.
4. Old entry stays (append-only). `retire <id> --class superseded --why "<why>" --by <new id>` tombstones a repo-file entry; a frozen-file retire is refused.
5. `--repo all` refused with `ledger_dir` set: use `--repo pm`. Provenance = owner's own words, or `delegated`, which needs Source `under <id>` or `under hub:A-nnn`. `REFUSED:` on substance is final (skill backlog-ops "Refusals"); a usage error (exit 2) is yours: fix once.
