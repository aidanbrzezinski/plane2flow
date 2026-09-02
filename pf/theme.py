"""Palette and state styling.

These are the EV27 workspace's own state colours, so a box on the board reads
the same as the item does in Plane. That is worth more day to day than the
ordinal ramp this used to carry, but it costs two things worth knowing:

  * Black-and-white printing no longer separates every state by shade. Blocked
    (91) sits beside Backlog (100), and Dropped (164) beside In Progress (167),
    on the 0-255 grey scale. The written state name and the glyph carry the
    meaning on paper; colour alone does not.
  * Several chips are below 3:1 against the page. The state name sits ON the
    chip in measured-contrast ink, and the node keeps its own hairline border,
    so nothing depends on the chip separating itself from the background.

Attention ("this is blocked by something unfinished") still rides a SEPARATE
channel -- the reserved critical red, plus a glyph and a label -- so it stays
distinct from the Blocked *state*, which is only what somebody set in Plane.
"""

# The workspace's own state colours, so the board and Plane agree at a glance.
# Backlog and Ready share a hex in Plane; they are kept apart here by lightness
# within the same grey, because two states that paint identically cannot be told
# apart on a wall chart. Set READY_MATCHES_PLANE = True to use the exact hex.
READY_MATCHES_PLANE = False

STATE_RAMP_LIGHT = {
    "Backlog":     "#60646C",
    "Ready":       "#60646C" if READY_MATCHES_PLANE else "#8D8F95",
    "In Progress": "#F59E0B",
    "Blocked":     "#EB144C",
    "Review":      "#8ED1FC",
    "Done":        "#46A758",
    "Dropped":     "#9AA4BC",
    "Cancelled":   "#9AA4BC",
}

# Same hues stepped for the dark surface. Only the greys move: #60646C sits at
# 2.93:1 on #1a1a19, under the 3:1 floor, so it is lightened until it clears.
STATE_RAMP_DARK = {
    "Backlog":     "#787B82",
    "Ready":       "#787B82" if READY_MATCHES_PLANE else "#A0A2A7",
    "In Progress": "#F59E0B",
    "Blocked":     "#EB144C",
    "Review":      "#8ED1FC",
    "Done":        "#46A758",
    "Dropped":     "#9AA4BC",
    "Cancelled":   "#9AA4BC",
}

# Ink chosen per chip by measured contrast, not by eye.
STATE_INK_LIGHT = {
    "Backlog": "#ffffff", "Ready": "#0b0b0b", "In Progress": "#0b0b0b",
    "Blocked": "#ffffff", "Review": "#0b0b0b", "Done": "#0b0b0b",
    "Dropped": "#0b0b0b", "Cancelled": "#0b0b0b",
}
STATE_INK_DARK = {
    "Backlog": "#0b0b0b", "Ready": "#0b0b0b", "In Progress": "#0b0b0b",
    "Blocked": "#ffffff", "Review": "#0b0b0b", "Done": "#0b0b0b",
    "Dropped": "#0b0b0b", "Cancelled": "#0b0b0b",
}

# Secondary encoding. These carry more weight than they used to: the state
# colours are categorical hues now rather than one ordinal ramp, so several
# pairs collapse to the same grey on a black-and-white printer. The glyph and
# the written state name are what keep a printed board readable.
STATE_GLYPH = {
    "Backlog": "○",      # hollow circle
    "Ready": "◔",        # quarter filled
    "In Progress": "◑",  # half filled
    "Blocked": "⛒",      # no entry
    "Review": "◕",       # three-quarter filled
    "Done": "●",         # solid
    "Dropped": "✕",      # x
    "Cancelled": "✕",
}


STATUS = {           # reserved status palette -- never reused as a series hue
    "good": "#0ca30c",
    "warning": "#fab219",
    "serious": "#ec835a",
    "critical": "#d03b3b",
}

SURFACE = {
    "light": {
        "surface": "#fcfcfb", "plane": "#f9f9f7", "ink": "#0b0b0b",
        "ink2": "#52514e", "muted": "#898781", "grid": "#e1e0d9",
        "axis": "#c3c2b7", "border": "rgba(11,11,11,0.10)",
    },
    "dark": {
        "surface": "#1a1a19", "plane": "#0d0d0d", "ink": "#ffffff",
        "ink2": "#c3c2b7", "muted": "#898781", "grid": "#2c2c2a",
        "axis": "#383835", "border": "rgba(255,255,255,0.10)",
    },
}

FONT = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif'


def theme_payload() -> dict:
    return {
        "rampLight": STATE_RAMP_LIGHT,
        "rampDark": STATE_RAMP_DARK,
        "inkLight": STATE_INK_LIGHT,
        "inkDark": STATE_INK_DARK,
        "glyph": STATE_GLYPH,
        "status": STATUS,
    }
