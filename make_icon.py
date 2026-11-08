import sys
import math
from PIL import Image, ImageDraw, ImageFilter

def create_app_icon(output_path_512, output_path_192):
    # Render at 1024x1024 for supersampling
    size = 1024
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    cx, cy = size // 2, size // 2

    # 1. Background Squircle
    # Radius ~ 220px
    rect = [40, 40, size - 40, size - 40]
    corner_r = 220

    # Draw smooth squircle with radial gradient
    bg = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    bg_draw = ImageDraw.Draw(bg)
    bg_draw.rounded_rectangle(rect, radius=corner_r, fill=(10, 12, 20, 255))

    # Radial glow inside squircle
    radial = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    r_draw = ImageDraw.Draw(radial)
    for r in range(450, 0, -5):
        alpha = int(75 * (1.0 - (r / 450) ** 1.3))
        # deep indigo / violet glow
        r_draw.ellipse([cx - r, cy - r + 30, cx + r, cy + r + 30], fill=(99, 102, 241, alpha))

    # Composite background
    bg.alpha_composite(radial)
    # Mask to squircle
    mask = Image.new("L", (size, size), 0)
    m_draw = ImageDraw.Draw(mask)
    m_draw.rounded_rectangle(rect, radius=corner_r, fill=255)
    
    final_bg = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    final_bg.paste(bg, (0, 0), mask)

    # Subtle neon rim
    rim = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    rim_draw = ImageDraw.Draw(rim)
    rim_draw.rounded_rectangle(rect, radius=corner_r, outline=(99, 102, 241, 140), width=6)
    rim_draw.rounded_rectangle([rect[0]+3, rect[1]+3, rect[2]-3, rect[3]-3], radius=corner_r-3, outline=(0, 242, 254, 70), width=3)
    final_bg.alpha_composite(rim)

    # 2. Orbital Rings & Waves
    orbit_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    o_draw = ImageDraw.Draw(orbit_layer)

    # Outer dashed/segmented orbit
    orbit_r = 340
    o_draw.arc([cx - orbit_r, cy - orbit_r + 20, cx + orbit_r, cy + orbit_r + 20], start=30, end=150, fill=(0, 242, 254, 180), width=10)
    o_draw.arc([cx - orbit_r, cy - orbit_r + 20, cx + orbit_r, cy + orbit_r + 20], start=210, end=330, fill=(168, 85, 247, 180), width=10)

    # Little glowing orbital nodes
    for angle, col in [(30, (0, 242, 254, 255)), (150, (0, 242, 254, 255)), (210, (168, 85, 247, 255)), (330, (168, 85, 247, 255))]:
        rad = math.radians(angle)
        nx = cx + orbit_r * math.cos(rad)
        ny = cy + 20 + orbit_r * math.sin(rad)
        o_draw.ellipse([nx - 14, ny - 14, nx + 14, ny + 14], fill=col)

    # Remote Signal Waves at top (broadcasting pulse)
    for wave_r, w_width, w_alpha in [(160, 8, 220), (220, 10, 160), (280, 12, 100)]:
        o_draw.arc([cx - wave_r, cy - 220 - wave_r, cx + wave_r, cy - 220 + wave_r], start=220, end=320, fill=(0, 242, 254, w_alpha), width=w_width)

    final_bg.alpha_composite(orbit_layer)

    # 3. Antigravity Delta / Prism Logo
    delta_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d_draw = ImageDraw.Draw(delta_layer)

    # Outer Delta Triangle
    # Apex at top center, bottom-left, bottom-right
    top_y = cy - 140
    bot_y = cy + 180
    half_w = 190

    p_top = (cx, top_y)
    p_left = (cx - half_w, bot_y)
    p_right = (cx + half_w, bot_y)

    # Shadow/Glow behind delta
    glow_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    g_draw = ImageDraw.Draw(glow_layer)
    g_draw.polygon([p_top, p_left, p_right], fill=(0, 242, 254, 180))
    glow_blur = glow_layer.filter(ImageFilter.GaussianBlur(40))
    final_bg.alpha_composite(glow_blur)

    # Draw gradient-filled Delta
    delta_poly = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    dp_draw = ImageDraw.Draw(delta_poly)

    # Vertical gradient from Cyan (#00F2FE) to Violet (#7928CA)
    c1 = (0, 242, 254)   # Neon Cyan
    c2 = (147, 51, 234)  # Violet
    c3 = (79, 70, 229)   # Indigo

    # Render sliced horizontal lines inside triangle mask
    t_mask = Image.new("L", (size, size), 0)
    tm_draw = ImageDraw.Draw(t_mask)
    tm_draw.polygon([p_top, p_left, p_right], fill=255)

    grad = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    g_pixels = grad.load()
    h_total = bot_y - top_y

    for y in range(top_y, bot_y + 1):
        progress = (y - top_y) / h_total
        if progress < 0.5:
            f = progress / 0.5
            r = int(c1[0] * (1 - f) + c2[0] * f)
            g = int(c1[1] * (1 - f) + c2[1] * f)
            b = int(c1[2] * (1 - f) + c2[2] * f)
        else:
            f = (progress - 0.5) / 0.5
            r = int(c2[0] * (1 - f) + c3[0] * f)
            g = int(c2[1] * (1 - f) + c3[1] * f)
            b = int(c2[2] * (1 - f) + c3[2] * f)
        for x in range(cx - half_w - 10, cx + half_w + 10):
            g_pixels[x, y] = (r, g, b, 255)

    delta_img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    delta_img.paste(grad, (0, 0), t_mask)

    # Inner cut-out (Inverted Delta / Core Portal)
    cut_mask = Image.new("L", (size, size), 255)
    cm_draw = ImageDraw.Draw(cut_mask)
    # Inverted smaller triangle inside
    in_top_y = cy - 20
    in_bot_y = cy + 140
    in_half_w = 95
    p_in_bot = (cx, in_bot_y)
    p_in_left = (cx - in_half_w, in_top_y)
    p_in_right = (cx + in_half_w, in_top_y)
    cm_draw.polygon([p_in_bot, p_in_left, p_in_right], fill=0)

    delta_clean = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    delta_clean.paste(delta_img, (0, 0), cut_mask)

    # Central Glowing Antigravity Core / Sphere
    core_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    c_draw = ImageDraw.Draw(core_layer)
    core_cy = cy + 45
    for r in range(40, 0, -2):
        f = (40 - r) / 40.0
        # White center fading to cyan
        cr = int(255 * (1 - f) + 0 * f)
        cg = int(255 * (1 - f) + 242 * f)
        cb = int(255 * (1 - f) + 254 * f)
        ca = int(255 * (1 - (r / 40.0) ** 2))
        c_draw.ellipse([cx - r, core_cy - r, cx + r, core_cy + r], fill=(cr, cg, cb, ca))

    final_bg.alpha_composite(delta_clean)
    final_bg.alpha_composite(core_layer)

    # 4. Downscale with high quality Lanczos supersampling
    icon_512 = final_bg.resize((512, 512), Image.Resampling.LANCZOS)
    icon_512.save(output_path_512, "PNG")
    print(f"✅ Generated 512x512 icon at: {output_path_512}")

    icon_192 = final_bg.resize((192, 192), Image.Resampling.LANCZOS)
    icon_192.save(output_path_192, "PNG")
    print(f"✅ Generated 192x192 icon at: {output_path_192}")

if __name__ == "__main__":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    from pathlib import Path
    root = Path(r"C:\Users\dev\.gemini\antigravity\scratch\pc_remote")
    create_app_icon(
        root / "mobile_app" / "icon-512.png",
        root / "mobile_app" / "icon-192.png"
    )
    # Also save copy to android/assets
    create_app_icon(
        root / "android" / "assets" / "icon-512.png",
        root / "android" / "assets" / "icon-192.png"
    )
