"""A set of logical processors as one Python int, on every platform. A thread is named by one
global id, group * 64 + its bit in the group's mask (Windows processor groups; on Linux the cpu
id), so a mask can be of any width and group 0 keeps the ids it always had.
"""
GROUP_BITS = 64


def gid(group, bit):
    return group * GROUP_BITS + bit


def split(mask):
    """{group: 64-bit mask} of a global mask."""
    out, g = {}, 0
    while mask:
        m = mask & ((1 << GROUP_BITS) - 1)
        if m:
            out[g] = m
        mask >>= GROUP_BITS
        g += 1
    return out


def bits(mask):
    """The global ids set in mask, ascending."""
    out, i = [], 0
    while mask:
        if mask & 1:
            out.append(i)
        mask >>= 1
        i += 1
    return out


def bits_mask(ids):
    m = 0
    for i in ids:
        m |= 1 << i
    return m


def mask_text(mask):
    """A Linux cpuset list of a global mask: '0-3,8', '' for none."""
    ids, parts, i = bits(mask), [], 0
    while i < len(ids):
        j = i
        while j + 1 < len(ids) and ids[j + 1] == ids[j] + 1:
            j += 1
        parts.append(str(ids[i]) if i == j else "%d-%d" % (ids[i], ids[j]))
        i = j + 1
    return ",".join(parts)


def cores_text(mask):
    if mask is None:
        return "all cores"
    return "cores " + mask_text(mask) if mask else "no cores"
