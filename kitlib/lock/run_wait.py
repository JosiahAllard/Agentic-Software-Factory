"""A run's wait for its turn in the machine lock's queue (kitlib.lock.run, whose docstring gives
the whole run): acquire joins the queue, looks again until the run holds what it needs, reaps
stale runs, and gives up at the run's wait, a strong hold, a cancel or Ctrl+C.

Nothing here prints: progress lines go to store.say. A refusal raises LockError with the CLI's
exit code.
"""
import contextlib
import json
import math
import time

from kitlib.lock import game
from kitlib.lock import holds as hl
from kitlib.lock import layout as lay
from kitlib.lock import queue as qm
from kitlib.lock import stale
from kitlib.lock.store import (EXIT_BUSY, EXIT_REQUIRE, EXIT_RESERVED, EXIT_TIMEOUT, LockError, iso,
                               local_text, utc_now)
from kitlib.lock.run_end import request_stop
from kitlib.lock.layout import POOLS
from kitlib.lock.queue import PICK


def wait_text(wait_s):
    return "no limit" if wait_s is None else "%d s" % wait_s


# ------------------------------------------------------------------ waiting for a turn
def acquire(store, run, L, need, wait_s, no_wait, prefer=None, within=None):
    """Waits for run's turn in the queue and returns {pool: [slot]} once it holds `need`
    (its entry and record name them). Raises LockError with its entry gone: 11 when a strong
    hold (owner or game) leaves it too few for its wait, or its own whole hold is over or has
    not started; 6 (--no-wait) or 8 when agent holds or runs do; 10 when the queue or the holds
    cannot be read before it joined; 1 when stop cancelled it. It never starts on a slot a hold but its own
    covers within its expected time; one they leave too few of keeps its place, parked. With
    no_wait it waits only for background runs to yield. prefer: its hold's slots. within: {pool:
    the only slots it may take} (book's within): its entry carries it (kitlib.lock.queue.plan)
    and only holds over those slots park it. After its first look at the queue it moves dead
    runs' records to ended/ (Store.live_runs prune), whether or not it then gets its slots.
    Memory: with its slots free it still waits, in its place, while its need (need_mem_mb) does
    not fit the commit headroom read at that look (kitlib.lock.queue.mem_fits; exempt nested in a
    held run, or with nothing else held); its entry's mem_wait says so. Nothing is paused for it."""
    s, say = store.settings, store.say
    deadline = time.monotonic() + (0 if no_wait else math.inf if wait_s is None else wait_s)
    span, hid = run.rec["expect_s"], run.rec.get("hold")
    whole = run.rec.get("hold_scope") == "whole"
    need_mem = run.rec.get("need_mem_mb") or s.mem_default_mb
    mem_said = False
    told = said = seen = yielding = ypool = qbad = hbad = stale_at = None
    joined = back = reaped = False
    tally = qm.WaitTally()      # its wait by cause, for its record and history line (wait_by)
    while True:
        now = utc_now()
        game.sync(store, L)
        try:
            hs = hl.records(store, L)
        except hl.HoldsUnreadable as err:
            # Never planned on as no holds (that frees every slot a hold covers). A run not yet
            # in the queue exits; one already waiting keeps its place, takes nothing, and looks
            # again.
            if not joined:
                raise LockError(EXIT_REQUIRE, "require-lock: %s; the command was not run" % err)
            if hbad is None:
                hbad = str(err)
                say("run %s: %s; it keeps its place and looks again" % (run.id, err))
            why = ("cancelled" if run.stop_asked() else "--no-wait" if no_wait else
                   "timeout" if time.monotonic() >= deadline else None)
            if why:
                qm.leave(store, run.id)
            if why == "cancelled":
                raise LockError(1, "run %s was cancelled by stop while it waited; the command "
                                   "was not run" % run.id)
            if why == "--no-wait":
                raise LockError(EXIT_BUSY, "NOT ACQUIRED: the holds are unreadable; --no-wait")
            if why == "timeout":
                raise LockError(EXIT_TIMEOUT, "NOT ACQUIRED: the holds are unreadable; waited %s "
                                              "(its --max-wait)" % wait_text(wait_s))
            time.sleep(s.queue_poll_s)
            continue
        if hbad is not None:
            say("run %s: the holds read again" % run.id)
            hbad = None
        h = next((x for x in hs if x["id"] == hid), None) if hid else None
        if whole and h is None:
            qm.leave(store, run.id)
            raise LockError(EXIT_RESERVED, "refused: owner hold %s (quiet hours) is over or was "
                            "ended, and run %s runs only inside it (--hold). Exit 11."
                            % (hid, run.id))
        cover, blocking, ablocking = hl.cover_plan(store, L, need, span, own=hid, hs=hs,
                                                   within=within)
        park, clear, why_kind = [], None, None
        if whole and h["_start"] > now:
            park, clear, why_kind = [h], h["_start"], "start"
        elif blocking:
            park, clear, why_kind = blocking, max(x["_end"] for x in blocking), "strong"
        elif ablocking:
            park, clear, why_kind = ablocking, max(hl.clear_at(b) for b in ablocking), "agent"
        ids = [x["id"] for x in park]
        key = {p: sorted(cover.get(p, {})) for p in POOLS}
        if seen is not None and key != seen and not park:
            say("run %s: the holds changed while it waited; it plans again" % run.id)
        seen = key
        if park and (no_wait or (clear - now).total_seconds() > deadline - time.monotonic()):
            qm.leave(store, run.id)
            why = "--no-wait" if no_wait else "its wait ran out" if told is not None else (
                {"start": "it starts at %s", "strong": "it ends at %s",
                 "agent": "it keeps them until %s"}[why_kind] % local_text(clear)
                + ", after this run's wait (%s)" % wait_text(wait_s))
            if why_kind == "start":
                raise LockError(EXIT_RESERVED, "refused: owner hold %s (quiet hours) has not "
                                "started, and run %s runs only inside it (--hold); %s. Exit 11."
                                % (hid, run.id, why))
            if why_kind == "strong":
                raise LockError(EXIT_RESERVED, hl.refusal_text(run.id, need, span, park, why))
            raise LockError(EXIT_BUSY if no_wait else EXIT_TIMEOUT, "NOT ACQUIRED: %s; %s" % (
                hl.agent_wait_text(run.id, need, span, park), why))
        if park and told != ids:
            say({"start": "run %s waits for %s to start (at %s); it runs only inside it",
                 "strong": "run %s waits for %s to end (by %s) before it takes a slot",
                 "agent": "run %s waits for %s (until %s) before it takes a slot"}[why_kind] % (
                run.id, "; ".join(hl.text(x) for x in park), local_text(clear)))
            told = ids
            with contextlib.suppress(OSError):
                run.save(blocked_by=ids)
        elif not park and told is not None:
            say("run %s: the hold in its way is over; it takes its slots" % run.id)
            told = None
            with contextlib.suppress(OSError):
                run.save(blocked_by=None)
        stop = run.stop_asked()
        result, text, got, moved = None, None, None, None
        try:
            with store.lock("queue", s.queue_lock_s if not joined else 2):
                q = qm.load(store)
                before = json.dumps(q, sort_keys=True)
                qm.prune(store, L, q, run.id)
                # Its entry gone while it waited (an unreadable queue.json was set aside): it
                # joins again, at the back.
                back = joined and qm.entry_of(q, run.id) is None
                e = qm.entry_of(q, run.id) or qm.join(q, run.rec, need)
                # A parked entry is planned from clear_at, around what else covers its slots
                # (avoid_after); plan holds later runs to it.
                e.update(avoid={p: sorted(cover[p]) for p in POOLS if cover.get(p)},
                         parked=bool(park), blocked_by=ids or None, prefer=prefer or {},
                         clear_at=clear.timestamp() if park else None,
                         avoid_after={p: sorted(i for i, x in cover[p].items() if x["id"] not in ids)
                                      for p in POOLS if cover.get(p)} if park else {})
                if within:
                    e["within"] = within
                planned = qm.plan(L, q)[0]
                info = planned[run.id]
                held = qm.holders(L, q, run.id)
                in_way = {p: [held[p][i] for i in info[PICK[p]] if i in held[p]] for p in POOLS}
                # A parked run takes nothing, even planned now: it looks again with the holds
                # read anew. cause: (None, None) when its slots are free to take (wait_cause).
                cause = qm.wait_cause(info, ids, in_way)
                bg_only = info["now"] and cause[0] is not None
                # Its slots free, its memory need must fit the headroom read now; short, it keeps
                # its entry, ticket and plan, takes nothing, and looks again.
                fits, head = (qm.mem_fits(store, q, e, need_mem) if cause[0] is None
                              else (True, None))
                cause = cause if fits else ("mem", None)
                ready = cause[0] is None
                tally.note(*cause)
                if not fits:
                    e["mem_wait"] = need_mem
                else:
                    e.pop("mem_wait", None)
                if mem_said != (not fits):
                    mem_said, said = not fits, None     # its next wait line names the change
                if ready:
                    got = {p: info[PICK[p]] for p in POOLS}
                    moved = e.get("moved")
                    e.update(state="held", cores=got["cpu"], gpus=got["gpu"], held_at=time.time(),
                             parked=False, blocked_by=None)
                elif stop:
                    result = 1
                elif no_wait and bg_only and yielding is not None and time.monotonic() > yielding:
                    result, text = EXIT_BUSY, ("NOT ACQUIRED: %s; --no-wait, and the background "
                                               "run did not yield in time"
                                               % qm.busy_text(L, q, e, planned, head))
                elif no_wait and bg_only:
                    if yielding is None:
                        ypool = next(p for p in ("gpu", "cpu") if in_way[p])
                        wait_y = s.bg_yield_s + s.bg_yield_margin_s
                        yielding = time.monotonic() + wait_y
                        text = ("run %s: --no-wait, but only a background run holds the %s it "
                                "needs; it asks it to pause and waits up to %d s for that" % (
                                    run.id, "cores" if ypool == "cpu" else "gpu slot", wait_y))
                elif no_wait:
                    short = qm.short_pools(L, q, e)
                    result = EXIT_BUSY
                    if yielding is not None and short:
                        text = "NOT ACQUIRED: foreground work took the %s it %s: %s; --no-wait" % (
                            ypool if ypool in short else short[0],
                            "waited for" if ypool in short else "needs next",
                            qm.busy_text(L, q, e, planned, head))
                    else:
                        text = "NOT ACQUIRED: %s; --no-wait" % qm.busy_text(L, q, e, planned, head)
                elif time.monotonic() >= deadline:
                    result, text = EXIT_TIMEOUT, "NOT ACQUIRED: %s; waited %s (its --max-wait)" % (
                        qm.busy_text(L, q, e, planned, head), wait_text(wait_s))
                elif not park and said != info["position"]:
                    said = info["position"]
                    text = "run %s waits in the queue: position %d of %d, expected to start %s%s; %s" % (
                        run.id, info["position"], len(planned), qm.est_text(info),
                        qm.BACKFILL_TEXT if info["backfill"] else "",
                        qm.busy_text(L, q, e, planned, head))
                if result is not None:
                    q["entries"].remove(e)
                if json.dumps(q, sort_keys=True) != before:
                    qm.save(store, q)
                joined = True
        except qm.QueueUnreadable as err:
            # Never planned on as empty (its save would wipe every entry). A run not yet in the
            # queue exits; one already waiting keeps its place, writes nothing, and looks again.
            if not joined:
                raise LockError(EXIT_REQUIRE, "require-lock: %s; the command was not run" % err)
            if qbad is None:
                qbad, said = str(err), None
                say("run %s: %s; it keeps its place and looks again" % (run.id, err))
            if stop:
                raise LockError(1, "run %s was cancelled by stop while it waited; the command "
                                   "was not run" % run.id)
            if no_wait and (yielding is None or time.monotonic() > yielding):
                raise LockError(EXIT_BUSY, "NOT ACQUIRED: the queue is unreadable; --no-wait")
            if not no_wait and time.monotonic() >= deadline:
                raise LockError(EXIT_TIMEOUT, "NOT ACQUIRED: the queue is unreadable; waited %s "
                                              "(its --max-wait)" % wait_text(wait_s))
            time.sleep(s.queue_poll_s)
            continue
        except OSError as err:
            if not joined:
                raise LockError(EXIT_REQUIRE, "require-lock: the queue could not be read or "
                                              "written (%s); the command was not run" % err)
            time.sleep(s.queue_poll_s)
            continue
        if not reaped:
            # Once, after the first prune of the queue said which dead runs it dropped: records
            # in active/ whose wrapper is surely gone (Store.run_live) move to ended/, how
            # "reaped". Nothing else takes them out, and every read of the store walks them. An
            # ended record makes a later prune drop the run's entry quietly (queue.prune).
            reaped = True
            with contextlib.suppress(OSError):
                store.live_runs(prune=True)
        if stale_at is None or time.monotonic() >= stale_at:
            # Then, and every stale_check_s while it waits: a held run whose booking sessions
            # are gone and that has held far past its guide is asked to stop (kitlib.lock.stale).
            # Outside the queue mutex; a reap that fails never fails this booking.
            stale_at = time.monotonic() + s.stale_check_s
            try:
                stale.reap(store, request_stop)
            except Exception as err:        # noqa: BLE001
                say("run %s: the stale-run look failed (%s: %s)" % (run.id, type(err).__name__, err))
        if qbad is not None:
            say("run %s: the queue reads again%s" % (
                run.id, "; its entry was gone, so it joined again at the back" if back else ""))
        elif back:
            say("run %s: its queue entry was gone; it joined again at the back" % run.id)
        qbad = None
        if result == 1:
            raise LockError(1, "run %s was cancelled by stop while it waited; the command was "
                               "not run" % run.id)
        if result is not None:
            raise LockError(result, text)
        if text:
            say(text)
        if got is not None:
            # A hold made between this look at them and the take: the run gives back what it
            # covers and plans again (it then waits, takes others, or exits 11, 6, 8).
            try:
                now_cover = hl.cover_plan(store, L, need, span, own=hid)[0]
            except hl.HoldsUnreadable:
                qm.give_back(store, run.id)     # it looks again; the loop's top waits on them
                tally.note("hold", None)        # its wait from here counts, as a hold's
                continue
            hit = [now_cover[p][i] for p in POOLS for i in got[p] if i in now_cover.get(p, {})]
            if hit and qm.give_back(store, run.id):
                tally.note("hold", hit[0].get("id"))
                say("run %s: %s now covers %s; it gives it back and plans again" % (
                    run.id, hl.text(hit[0]),
                    " + ".join(lay.slots_text(p, got[p]) for p in POOLS if got[p])))
                continue
            with contextlib.suppress(OSError):
                run.save(state="held", held_since=iso(), blocked_by=None, cores=got["cpu"],
                         gpus=got["gpu"], slots={p: got[p] for p in POOLS if got[p]},
                         holding=[p for p in POOLS if got[p]], **tally.fields(),
                         **({"moved": moved} if moved else {}))
            return got
        time.sleep(s.queue_poll_s)
