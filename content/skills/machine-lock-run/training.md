# Machine lock: training workloads

- `--gpu-share training` only for model training on local-ai or render.
- Training, the pausable tier (not `run`'s detached form; that is `--detach`): `python <kit>/tools/machine_lock.py background --repo <repo> --slug <slug> -- <launcher>`; the trainer polls `should_pause()` from `kitlib.lock.bg_client` (`<kit>/kitlib/lock/bg_client.py`) and checkpoints and exits 75 when a due run needs its slots: it yields to due runs instead of holding its slots. In a container, mount the kitlib folder read-only and `import kitlib.lock.bg_client` (never copy bg_client.py into the image, and never put `kitlib/lock` itself on sys.path: its `site.py` and `queue.py` would shadow the stdlib); mount `<store>\background` read-only for control.json (module docstring).
