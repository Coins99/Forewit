"""Fluid motion: the easing glass glides on.

Liquid Glass moves "like a lightweight liquid" (WWDC25); the curve is monopo saigon's
expo-out, cubic-bezier(0.19, 1, 0.22, 1): a quick start that settles slowly, so elements
glide into place rather than snap.
"""

from typing import Final

from PySide6.QtCore import QEasingCurve, QPointF

GLIDE_MS: Final = 420  # A selection gliding from one place to the next.


def fluid_curve() -> QEasingCurve:
    """cubic-bezier(0.19, 1, 0.22, 1)."""
    curve = QEasingCurve(QEasingCurve.Type.BezierSpline)
    curve.addCubicBezierSegment(QPointF(0.19, 1.0), QPointF(0.22, 1.0), QPointF(1.0, 1.0))
    return curve
