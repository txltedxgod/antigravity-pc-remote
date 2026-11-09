"""
Generates high-resolution minimalist monochrome app icons for Antigravity PC Remote.
Aesthetic: ChatGPT / Linear style - Deep Obsidian Black, crisp titanium white geometry.
"""

import sys
import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

def create_chatgpt_style_icon(output_path_512, output_path_192):
    # Render at 1024x1024 for supersampling
    size = 1024
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    cx, cy = size // 2, size // 2

    # 1. Background Squircle
    rect = [40, 40, size - 40, size - 40]
    corner_r = 220

    bg = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    bg_draw = ImageDraw.Draw(bg)
    # Deep obsidian black fill
    bg_draw.rounded_rectangle(rect, radius=corner_r, fill=(13, 13, 15, 255))

    # Subtle inner vignette / depth
    inner_vignette = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    v_draw = ImageDraw.Draw(inner_vignette)
    for r in range(480, 0, -8):
        alpha = int(45 * (1.0 - (r / 480) ** 1.8))
        v_draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255, alpha))
    bg.alpha_composite(inner_vignette)

    # Mask to squircle
    mask = Image.new("L", (size, size), 0)
    m_draw = ImageDraw.Draw(mask)
    m_draw.rounded_rectangle(rect, radius=corner_r, fill=255)

    final_bg = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    final_bg.paste(bg, (0, 0), mask)

    # Sleek precision outline
    outline = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    o_draw = ImageDraw.Draw(outline)
    o_draw.rounded_rectangle(rect, radius=corner_r, outline=(255, 255, 255, 38), width=4)
    o_draw.rounded_rectangle([rect[0] + 4, rect[1] + 4, rect[2] - 4, rect[3] - 4], radius=corner_r - 4, outline=(255, 255, 255, 15), width=2)
    final_bg.alpha_composite(outline)

    # 2. Geometric Antigravity Chevron / Emblem (Monochrome Titanium)
    # Stylized celestial chevron + orbital core
    icon_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    i_draw = ImageDraw.Draw(icon_layer)

    # Subtle soft drop shadow behind symbol
    shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    s_draw = ImageDraw.Draw(shadow)

    # Outer Chevron points
    top_y = cy - 200
    bottom_y = cy + 180
    wing_w = 230
    inner_wing_w = 140
    inner_top_y = cy - 80
    inner_bottom_y = cy + 90

    # Draw outer chevron / inverted delta wings
    # Left wing
    poly_left = [
        (cx, top_y),
        (cx - wing_w, bottom_y),
        (cx - inner_wing_w, bottom_y),
        (cx, inner_top_y)
    ]
    # Right wing
    poly_right = [
        (cx, top_y),
        (cx, inner_top_y),
        (cx + inner_wing_w, bottom_y),
        (cx + wing_w, bottom_y)
    ]

    s_draw.polygon(poly_left, fill=(0, 0, 0, 180))
    s_draw.polygon(poly_right, fill=(0, 0, 0, 180))
    # Soften shadow
    shadow_blur = shadow.filter(ImageFilter.GaussianBlur(25))
    final_bg.alpha_composite(shadow_blur)

    # Draw crisp titanium white facets
    # Left wing (slightly softer white for subtle 3D lighting, 235, 238, 242)
    i_draw.polygon(poly_left, fill=(235, 238, 245, 255))
    # Right wing (pure crisp white, 255, 255, 255)
    i_draw.polygon(poly_right, fill=(255, 255, 255, 255))

    # Floating gravitational core (sleek diamond / sphere)
    core_y = cy + 16
    core_r = 42
    # Floating core diamond
    diamond = [
        (cx, core_y - core_r),
        (cx + core_r, core_y),
        (cx, core_y + core_r),
        (cx - core_r, core_y)
    ]
    # Core drop shadow
    s_core = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    sc_draw = ImageDraw.Draw(s_core)
    sc_draw.polygon(diamond, fill=(0, 0, 0, 200))
    final_bg.alpha_composite(s_core.filter(ImageFilter.GaussianBlur(15)))

    # Core facets
    i_draw.polygon([(cx, core_y - core_r), (cx, core_y + core_r), (cx - core_r, core_y)], fill=(200, 205, 215, 255))
    i_draw.polygon([(cx, core_y - core_r), (cx + core_r, core_y), (cx, core_y + core_r)], fill=(255, 255, 255, 255))

    # Minimalist horizontal orbital ring
    ring_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    r_draw = ImageDraw.Draw(ring_layer)
    rx, ry = 280, 80
    r_draw.ellipse([cx - rx, core_y - ry, cx + rx, core_y + ry], outline=(255, 255, 255, 70), width=6)
    final_bg.alpha_composite(ring_layer)

    # Composite icon layer
    final_bg.alpha_composite(icon_layer)

    # Downsample with Lanczos filter for razor-sharp antialiasing
    icon_512 = final_bg.resize((512, 512), Image.Resampling.LANCZOS)
    icon_192 = final_bg.resize((192, 192), Image.Resampling.LANCZOS)

    Path(output_path_512).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path_192).parent.mkdir(parents=True, exist_ok=True)

    icon_512.save(output_path_512, "PNG")
    icon_192.save(output_path_192, "PNG")
    print(f"Generated 512x512 icon: {output_path_512}")
    print(f"Generated 192x192 icon: {output_path_192}")

    return final_bg

if __name__ == "__main__":
    base = Path(r"C:\Users\dev\.gemini\antigravity\scratch\pc_remote")
    p512 = base / "mobile_app" / "icon-512.png"
    p192 = base / "mobile_app" / "icon-192.png"
    bg = create_chatgpt_style_icon(p512, p192)

    # Also copy to android/assets and android/res
    android_assets = base / "android" / "assets"
    android_assets.mkdir(parents=True, exist_ok=True)
    bg.resize((512, 512), Image.Resampling.LANCZOS).save(android_assets / "icon-512.png", "PNG")
    bg.resize((192, 192), Image.Resampling.LANCZOS).save(android_assets / "icon-192.png", "PNG")

    # Mipmaps
    res_dir = base / "android" / "res"
    sizes = {
        "mipmap-mdpi": (48, 48),
        "mipmap-hdpi": (72, 72),
        "mipmap-xhdpi": (96, 96),
        "mipmap-xxhdpi": (144, 144),
        "mipmap-xxxhdpi": (192, 192),
    }
    for folder, sz in sizes.items():
        fp = res_dir / folder
        fp.mkdir(parents=True, exist_ok=True)
        bg.resize(sz, Image.Resampling.LANCZOS).save(fp / "ic_launcher.png", "PNG")
        print(f"Saved {folder}/ic_launcher.png {sz}")
