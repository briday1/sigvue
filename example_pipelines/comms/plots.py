"""Pure Plotly figure builders for communications products."""

import numpy as np
import plotly.graph_objects as go

from ..style import ORANGE, TEAL
from .models import CommsProducts


def constellation_figure(products: CommsProducts) -> go.Figure:
    constellation = go.Figure(go.Scattergl(
        x=products.symbols.real,
        y=products.symbols.imag,
        mode="markers",
        marker={"color": TEAL, "size": 6, "opacity": 0.58},
        name=products.modulation,
    ))
    constellation.update_xaxes(
        title_text="In-phase",
        range=[-products.constellation_limit, products.constellation_limit],
        autorange=False,
        scaleanchor="y",
        scaleratio=1,
    )
    constellation.update_yaxes(
        title_text="Quadrature",
        range=[-products.constellation_limit, products.constellation_limit],
        autorange=False,
    )

    return constellation


def eye_figure(products: CommsProducts) -> go.Figure:
    """Build the eye-diagram figure without interacting with the UI."""
    eye = go.Figure()
    if products.eye_segments.size:
        separators = np.full((products.eye_segments.shape[0], 1), np.nan)
        eye_x = np.concatenate((
            np.tile(products.eye_time, (products.eye_segments.shape[0], 1)),
            separators,
        ), axis=1).reshape(-1)
        for label, values, color in (
            ("I", products.eye_segments.real, TEAL),
            ("Q", products.eye_segments.imag, ORANGE),
        ):
            eye_y = np.concatenate((values, separators), axis=1).reshape(-1)
            eye.add_trace(go.Scattergl(
                x=eye_x,
                y=eye_y,
                mode="lines",
                line={"color": color, "width": 0.8},
                opacity=0.35,
                name=label,
            ))
    eye.update_xaxes(title_text="Symbol periods", range=[0, 2], autorange=False)
    eye.update_yaxes(
        title_text="Amplitude",
        range=[-products.eye_limit, products.eye_limit],
        autorange=False,
    )

    return eye
