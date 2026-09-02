"""Palette and state styling.

State is an ORDINAL variable (Backlog -> Ready -> In Progress -> Review -> Done),
so it gets a single-hue ramp with monotone lightness rather than categorical
hues. That choice is what makes the chart survive a black-and-white printer:
the steps stay distinguishable as grays. Validated with the dataviz palette
validator in --ordinal mode, light and dark, all checks pass.

Attention ("this is blocked") rides a SEPARATE channel -- a critical-red edge
bar plus a glyph and a label -- so no meaning is ever carried by hue alone.
"""

# Ordinal blue ramp. Light: steps 250..650. Dark: 600..150 (inverted so
# lightness still increases with progress against a dark surface).
STATE_RAMP_LIGHT = {
    "Backlog":     "#86b6ef",
    "Ready":       "#5598e7",
    "In Progress": "#2a78d6",
    "Review":      "#1c5cab",
    "Done":        "#104281",
    "Cancelled":   "#a9a7a0",
}
STATE_RAMP_DARK = {
    "Backlog":     "#184f95",
    "Ready":       "#2a78d6",
    "In Progress": "#5598e7",
    "Review":      "#86b6ef",
    "Done":        "#b7d3f6",
    "Cancelled":   "#6a6862",
}

# Ink that sits ON a state chip, chosen for >= 4.5:1 against that chip.
STATE_INK_LIGHT = {
    "Backlog": "#0b0b0b", "Ready": "#0b0b0b", "In Progress": "#ffffff",
    "Review": "#ffffff", "Done": "#ffffff", "Cancelled": "#0b0b0b",
}
STATE_INK_DARK = {
    "Backlog": "#ffffff", "Ready": "#ffffff", "In Progress": "#0b0b0b",
    "Review": "#0b0b0b", "Done": "#0b0b0b", "Cancelled": "#ffffff",
}

# Secondary encoding: a per-state glyph, so state is never color-only.
STATE_GLYPH = {
    "Backlog": "○",      # hollow circle
    "Ready": "◔",        # quarter filled
    "In Progress": "◑",  # half filled
    "Review": "◕",       # three-quarter filled
    "Done": "●",         # solid
    "Cancelled": "✕",    # x
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
