"""The edge where a host's adapter is imported (ARCHITECTURE.md "Hosts"), kept out of kitlib.

kitlib never imports a host: kitlib.host.registry hands out the adapter's module name from
hosts.json, and this helper is the one place a shared tool turns that name into the module.
Not a command (no __main__ block; `kit` runs no tool whose name starts with _). A tool run as a
script has its own folder on sys.path, so `import _host` finds this file.

  adapter_module(name, environ, path)   the imported adapter module of `name`, else of PM_KIT_HOST
  plugin_copies(home, environ, path)    the plugin-copy step of each registered host whose adapter
                                        has one (tools/plugin_refresh.py --apply)
  model_maps(renderers)                 {host: (tier -> model, role -> model)} of each renderer
                                        (default: every registered host's): docs/ROSTER.md's
                                        `tiers` argument (kitlib.roster)
"""
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kitlib.host import registry                                    # noqa: E402


def adapter_module(name=None, environ=None, path=None):
    """The adapter module hosts.json names for `name` (None: registry.choose's pick). A registry
    fault is RegistryError; a module that will not import is ImportError, said by the caller."""
    return importlib.import_module(registry.host(registry.choose(name, environ, path), path).adapter)


def model_maps(renderers=None):
    """{host: (tier_models(), role_models())} of each ContentRenderer in `renderers` (default: every
    host hosts.json registers): the model maps docs/ROSTER.md's tables are rendered from."""
    if renderers is None:
        renderers = [adapter_module(h).ADAPTER.content().renderer() for h in sorted(registry.names())]
    return {r.host: (r.tier_models(), r.role_models()) for r in renderers}


def plugin_copies(home=None, environ=None, path=None):
    """[step] of each host hosts.json registers whose ADAPTER has the optional
    plugin_copy(home, environ): the step kitlib.pluginsource.refresh runs as step(export folder,
    out, apply) to mirror the export into that host's own plugin copy (docs/plugin-source.md "Host
    plugin copies"). A host without the hook gives none. A registry fault is RegistryError; an
    adapter that will not import is ImportError, said by the caller."""
    steps = []
    for name in registry.names(path):
        adapter = getattr(importlib.import_module(registry.host(name, path).adapter), 'ADAPTER', None)
        make = getattr(adapter, 'plugin_copy', None)
        if make is not None:
            steps.append(make(home, environ))
    return steps
