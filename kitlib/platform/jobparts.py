"""The parts of a job every platform's Job shares (kitlib.platform.windows.job's Job on Windows,
kitlib.platform.linux.contain's on Linux): JobError, and
_Held, which holds Ctrl+C and Ctrl+Break off while a start makes its child and puts it in the job.
"""
import signal
import sys

HELD_SIGNALS = tuple(s for s in (signal.SIGINT, getattr(signal, "SIGBREAK", None))
                     if s is not None)


class _HeldDefault(KeyboardInterrupt):
    """A held signal whose handler was SIG_DFL: Job.start ends the child, then _Held.close puts
    SIG_DFL back and raises the signal, which ends this process as it would have at once."""


class _Held:
    """Ctrl+C and Ctrl+Break held off while Job.start makes a suspended child and puts it in the
    job. A KeyboardInterrupt between CreateProcess and the assign would leave the child suspended
    in no job, never resumed or ended; so would SIGBREAK under SIG_DFL (Python's own default for
    it: Ctrl+Break, or the console closing, ends the process there and then). Meanwhile each held
    signal runs a handler that only queues it. kitlib.lock.run_end.settle masks SIGINT the same way
    (on the main thread, put back in a finally), but with SIG_IGN; here a signal is late, never
    lost.

    deliver() hands each signal queued so far to the handler it replaced, still held, with the
    caller's frame: one that raises (Python's SIGINT default raises KeyboardInterrupt) raises
    where Job.start still ends the child; SIG_IGN drops it; SIG_DFL raises _HeldDefault, so the
    child is ended before close() lets the default act. close() puts every handler back, then
    raises again with signal.raise_signal each signal still queued: one that came after the last
    deliver(), or while a failed start ended its child. Nothing is held off the main thread
    (signal.signal raises ValueError there, and a signal's handler runs only on the main
    thread), and a signal whose handler was not set from Python (getsignal None: it could not be
    put back) is not held.

    signal.signal runs any pending handler before it swaps, and one may raise there: a Ctrl+C
    while the next handler is set back, or a Ctrl+Break whose own handler raises while it is
    held. Either way every handler is still put back (close() sets each again until it takes,
    up to RETRIES tries: every failed try ran a handler, so the signal behind it is not lost),
    the queue is still raised again, and the first error is raised after. A _Held whose
    __init__ raises has put back what it held, so Job.start makes it before its try."""

    RETRIES = 32        # failed sets of one handler in close(), each a signal's handler raising

    def __init__(self, signals=HELD_SIGNALS):
        self.before, self.queue = {}, []
        try:
            for s in signals:
                before = signal.getsignal(s)
                if before is None:
                    continue
                try:
                    signal.signal(s, self._note)
                except ValueError:              # not the main thread
                    return
                self.before[s] = before
        except BaseException:
            self.close()
            raise

    def _note(self, signum, frame):
        self.queue.append(signum)

    def deliver(self):
        frame = sys._getframe(1)
        while self.queue:
            s = self.queue[0]
            before = self.before[s]
            if before is signal.SIG_DFL:
                raise _HeldDefault("signal %d held while a child started" % s)
            self.queue.pop(0)
            if callable(before):
                before(s, frame)

    def close(self):
        first = None
        for s, handler in list(self.before.items()):
            for _ in range(self.RETRIES):
                try:
                    signal.signal(s, handler)
                    break
                except BaseException as e:      # a pending signal's handler ran and raised
                    first = e if first is None else first
            del self.before[s]
        while self.queue:                       # read after the sets: _note may add to it
            s = self.queue.pop(0)
            try:
                signal.raise_signal(s)
            except BaseException as e:
                first = e if first is None else first
        if first is not None:
            raise first


class JobError(OSError):
    """The job could not be made, or a process could not be put in it. Nothing runs outside a
    job (fail closed)."""
