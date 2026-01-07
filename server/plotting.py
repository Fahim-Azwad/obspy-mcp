"""Plotting helpers.

These utilities create artifacts (PNG) that can be referenced by the agent.
"""

from __future__ import annotations

import matplotlib.pyplot as plt


def plot_stream(stream, out) -> None:
    """Render an ObsPy Stream plot to disk."""
    # ObsPy returns a matplotlib Figure when show=False.
    fig = stream.plot(show=False)
    fig.savefig(out, dpi=150)
    plt.close(fig)
