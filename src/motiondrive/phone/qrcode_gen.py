"""Standards-compliant QR Code SVG generator for MotionDrive Phone Controller."""
from __future__ import annotations

import qrcode


def generate_qr_svg(data: str, size: int = 220) -> str:
    """Generates a standards-compliant, camera-scannable SVG string representing a QR code encoding `data`."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=1,
        border=4,
    )
    qr.add_data(data)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    n = len(matrix)

    rects = []
    for r in range(n):
        for c in range(n):
            if matrix[r][c]:
                rects.append(f'<rect x="{c}" y="{r}" width="1" height="1" fill="#0b0e14" />')

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {n}" '
        f'width="{size}" height="{size}" shape-rendering="crispEdges">\n'
        f'  <rect width="{n}" height="{n}" fill="#ffffff" rx="6" />\n'
        f'  {"".join(rects)}\n'
        f'</svg>'
    )
    return svg
