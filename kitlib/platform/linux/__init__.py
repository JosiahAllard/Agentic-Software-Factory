"""The Linux backend of the seam: every primitive the contract names (kitlib.platform.api). An area
that is built takes its primitives from its module (BUILT); every other primitive is a stub that
raises PlatformUnsupported naming itself and its area. Never a primitive that silently does less
(docs/modules/base-design.md "Contract"). All three areas are built, so no stub is bound today."""
from kitlib.platform import PlatformUnsupported, api
from kitlib.platform.linux import cpu, hardware, process

# area -> the module that provides its primitives
BUILT = {'process': process, 'hardware': hardware, 'affinity': cpu}


def _stub(name, area, slice_number):
    def primitive(*args, **kwargs):
        raise PlatformUnsupported('%s is not built on Linux yet (the %s area, slice %d)'
                                    % (name, area, slice_number))
    primitive.__name__ = primitive.__qualname__ = name
    return primitive


for _area, (_names, _slice) in api.AREAS.items():
    for _name in _names:
        globals()[_name] = (getattr(BUILT[_area], _name) if _area in BUILT
                            else _stub(_name, _area, _slice))

__all__ = list(api.NAMES)
