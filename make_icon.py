"""
Generate a .icns icon for the Laser Spectrometer app.
Draws a spectrum curve on a dark background with spectral colours.
"""
import os
import subprocess
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

SIZE = 1024

def make_icon(size):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    s = size / SIZE  # scale factor

    # Rounded background
    pad = int(60 * s)
    r = int(180 * s)
    draw.rounded_rectangle([pad, pad, size - pad, size - pad],
                            radius=r, fill=(28, 28, 36, 255))

    # Spectrum x-axis spans the width (with margins)
    margin_x = int(90 * s)
    margin_top = int(200 * s)
    margin_bot = int(280 * s)
    plot_w = size - 2 * margin_x
    plot_h = size - margin_top - margin_bot

    # Gaussian peak centred near 550 nm across a 380–750 nm range
    wl = np.linspace(380, 750, 500)
    spectrum = (
        0.85 * np.exp(-((wl - 532) ** 2) / (2 * 28 ** 2))   # main peak
        + 0.18 * np.exp(-((wl - 630) ** 2) / (2 * 18 ** 2)) # red shoulder
        + 0.08 * np.exp(-((wl - 445) ** 2) / (2 * 15 ** 2)) # blue wing
    )
    spectrum /= spectrum.max()

    # Wavelength → spectral RGB (simple approximation)
    def wl_to_rgb(nm):
        nm = float(nm)
        if   380 <= nm < 440: r, g, b = (440-nm)/60,        0.0,            1.0
        elif 440 <= nm < 490: r, g, b = 0.0,                (nm-440)/50,    1.0
        elif 490 <= nm < 510: r, g, b = 0.0,                1.0,            (510-nm)/20
        elif 510 <= nm < 580: r, g, b = (nm-510)/70,        1.0,            0.0
        elif 580 <= nm < 645: r, g, b = 1.0,                (645-nm)/65,    0.0
        elif 645 <= nm <=750: r, g, b = 1.0,                0.0,            0.0
        else:                  r, g, b = 0.0,                0.0,            0.0
        gamma = 0.9
        return (int((r**gamma) * 255), int((g**gamma) * 255), int((b**gamma) * 255))

    # Draw filled spectrum as vertical colour slices
    n = len(wl)
    for i in range(n - 1):
        x0 = int(margin_x + (i / n) * plot_w)
        x1 = int(margin_x + ((i + 1) / n) * plot_w)
        y_top = int(margin_top + plot_h * (1 - spectrum[i]))
        y_bot = margin_top + plot_h

        r, g, b = wl_to_rgb(wl[i])
        alpha = int(60 + 170 * spectrum[i])
        draw.rectangle([x0, y_top, x1, y_bot], fill=(r, g, b, alpha))

    # Draw the curve line on top
    pts = []
    for i in range(n):
        x = margin_x + (i / (n - 1)) * plot_w
        y = margin_top + plot_h * (1 - spectrum[i])
        pts.append((x, y))

    line_w = max(2, int(8 * s))
    for i in range(len(pts) - 1):
        x0, y0 = pts[i]
        x1, y1 = pts[i + 1]
        r, g, b = wl_to_rgb(wl[i])
        draw.line([x0, y0, x1, y1], fill=(r, g, b, 255), width=line_w)

    # Subtle glow on the curve
    glow = img.filter(ImageFilter.GaussianBlur(radius=int(10 * s)))
    img = Image.alpha_composite(glow, img)

    # Horizontal axis line
    axis_y = margin_top + plot_h + int(12 * s)
    draw = ImageDraw.Draw(img)
    draw.line([margin_x, axis_y, size - margin_x, axis_y],
              fill=(180, 180, 200, 160), width=max(1, int(3 * s)))

    return img


# Build iconset
iconset_dir = "/tmp/LaserSpectrometer.iconset"
os.makedirs(iconset_dir, exist_ok=True)

sizes = [16, 32, 64, 128, 256, 512, 1024]
for sz in sizes:
    icon = make_icon(sz)
    # @1x
    if sz <= 512:
        icon.save(f"{iconset_dir}/icon_{sz}x{sz}.png")
    # @2x (retina) — file named at half the pixel size
    if sz >= 32:
        icon.save(f"{iconset_dir}/icon_{sz//2}x{sz//2}@2x.png")

# Convert to .icns
icns_path = "/Applications/Laser Lab.app/Contents/Resources/AppIcon.icns"
os.makedirs(os.path.dirname(icns_path), exist_ok=True)
subprocess.run(["iconutil", "-c", "icns", iconset_dir, "-o", icns_path], check=True)
print(f"Icon written to {icns_path}")
