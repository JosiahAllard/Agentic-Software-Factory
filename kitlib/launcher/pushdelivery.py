"""The host adapter's push delivery over the launcher's settings, for the entry points that send a
push (tools/launch.py pm-relaunch and pm-crossover; the hub's push notifications).

No printing: a caller that wants the reason a delivery is off passes `note`. kitlib never imports a
host (ARCHITECTURE.md "Hosts"), so the caller passes `adapter_module`, the tools/_host.py function
that imports the adapter."""
from kitlib.launcher import settings as launcher_settings


def push_delivery(adapter_module, settings=None, note=None):
    """`(delivery, state folder)` of the host adapter's push over `settings` (default: the
    launcher's loaded settings), or `(None, None)` when there is none: no adapter, an adapter
    without push, or any fault loading either. `note(text)` is told why, when given."""
    try:
        launch = launcher_settings.load() if settings is None else settings
        folder = launch.state_dir()
        make = getattr(adapter_module().ADAPTER, 'push', None)
        if make is None:
            if note:
                note('push notifications off: this host has no delivery')
            return None, None
        return make(launch), folder
    except Exception as e:              # noqa: BLE001 - the caller goes on without push
        if note:
            note('push notifications off: %s: %s' % (type(e).__name__, e))
        return None, None
