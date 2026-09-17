"""Chart palette and Plotly layout for the demo UI.

Colour is assigned by the job it does, not by taste:

* The cost matrix encodes **magnitude**, so it uses one hue, light to dark.
* The component chart encodes **polarity** -- money spent against money saved --
  so it uses a warm/cool pair, blue for cost and red for the unpaid-suspension saving.
* Suppressed cells are not a value at all, so they are drawn in surface grey and
  labelled, never given a place on the colour scale.

Both modes are selected, not flipped: the dark steps are chosen for the dark surface.
The blue/red pair clears every colourblind-safety gate in both modes.
"""

from __future__ import annotations

PALETTES = {
    "light": {
        "surface": "#fcfcfb",
        "text_primary": "#0b0b0b",
        "text_secondary": "#52514e",
        "grid": "#e6e5e1",
        "cost": "#2a78d6",
        "saving": "#e34948",
        "suppressed": "#e6e5e1",
        # Sequential blue ramp, light -> dark. Near-zero is allowed to recede.
        "ramp": ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec",
                 "#5598e7", "#3987e5", "#2a78d6", "#256abf", "#1c5cab",
                 "#184f95", "#104281", "#0d366b"],
    },
    "dark": {
        "surface": "#1a1a19",
        "text_primary": "#ffffff",
        "text_secondary": "#c3c2b7",
        "grid": "#383835",
        "cost": "#3987e5",
        "saving": "#e66767",
        "suppressed": "#383835",
        "ramp": ["#0d366b", "#104281", "#184f95", "#1c5cab", "#256abf",
                 "#2a78d6", "#3987e5", "#5598e7", "#6da7ec", "#86b6ef",
                 "#9ec5f4", "#b7d3f6", "#cde2fb"],
    },
}


def palette(mode: str) -> dict:
    return PALETTES.get(mode, PALETTES["light"])


def colorscale(mode: str) -> list[list]:
    """Plotly colourscale from the sequential ramp.

    Each mode's ramp is already ordered so that its first step is the one nearest that
    mode's surface: near-zero recedes into the background and high values come forward. On
    light that means light -> dark; on dark it means dark -> light. Do not reverse either
    one -- a dark mode is selected for its surface, not flipped from the light one.
    """
    ramp = palette(mode)["ramp"]
    steps = len(ramp) - 1
    return [[i / steps, hex_code] for i, hex_code in enumerate(ramp)]


def style(fig, mode: str, *, height: int | None = None, showlegend: bool = False):
    """Recessive axes and grid, generous margins, surface that matches the page."""
    colours = palette(mode)
    fig.update_layout(
        paper_bgcolor=colours["surface"],
        plot_bgcolor=colours["surface"],
        font=dict(
            family="ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif",
            size=13,
            color=colours["text_primary"],
        ),
        margin=dict(l=8, r=8, t=8, b=8),
        showlegend=showlegend,
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
            font=dict(color=colours["text_secondary"]),
        ),
        hoverlabel=dict(font_size=13),
    )
    if height:
        fig.update_layout(height=height)
    fig.update_xaxes(
        showgrid=False, zeroline=False, linecolor=colours["grid"],
        tickfont=dict(color=colours["text_secondary"]),
    )
    fig.update_yaxes(
        showgrid=True, gridcolor=colours["grid"], gridwidth=1, zeroline=False,
        linecolor=colours["grid"], tickfont=dict(color=colours["text_secondary"]),
    )
    return fig
