import os
from PIL import Image, ImageDraw

def generate_favicon():
    # Colors
    bg = (0, 0, 0, 0)
    brand_gradient_start = (255, 77, 109) # #FF4D6D
    brand_gradient_end = (59, 130, 246)   # #3B82F6
    white = (255, 255, 255)
    
    # Create 32x32 and 16x16 icons
    sizes = [16, 32, 48]
    images = []
    
    for size in sizes:
        img = Image.new('RGBA', (size, size), bg)
        draw = ImageDraw.Draw(img)
        
        # Scale coordinates based on size
        scale = size / 32.0
        
        cx, cy = size / 2.0, size / 2.0
        r_outer = 13.0 * scale
        r_inner = 3.5 * scale
        stroke_w = max(1, int(1.5 * scale))
        bracket_w = max(1, int(1.5 * scale))
        
        # Draw camera lens ring (outer circle)
        draw.ellipse([cx - r_outer, cy - r_outer, cx + r_outer, cy + r_outer], 
                     outline=brand_gradient_end, width=stroke_w)
        
        # Draw brackets
        # Top-left
        draw.line([9*scale, 12*scale, 9*scale, 9*scale], fill=brand_gradient_start, width=bracket_w)
        draw.line([9*scale, 9*scale, 12*scale, 9*scale], fill=brand_gradient_start, width=bracket_w)
        
        # Top-right
        draw.line([22*scale, 12*scale, 22*scale, 9*scale], fill=brand_gradient_start, width=bracket_w)
        draw.line([22*scale, 9*scale, 19*scale, 9*scale], fill=brand_gradient_start, width=bracket_w)
        
        # Bottom-left
        draw.line([9*scale, 19*scale, 9*scale, 22*scale], fill=brand_gradient_start, width=bracket_w)
        draw.line([9*scale, 22*scale, 12*scale, 22*scale], fill=brand_gradient_start, width=bracket_w)
        
        # Bottom-right
        draw.line([22*scale, 19*scale, 22*scale, 22*scale], fill=brand_gradient_start, width=bracket_w)
        draw.line([22*scale, 22*scale, 19*scale, 22*scale], fill=brand_gradient_start, width=bracket_w)
        
        # Draw inner lens core
        draw.ellipse([cx - r_inner, cy - r_inner, cx + r_inner, cy + r_inner], 
                     fill=brand_gradient_start)
        
        # Draw white plus sign inside inner core
        # Horizontal
        draw.line([cx - 1.5*scale, cy, cx + 1.5*scale, cy], fill=white, width=max(1, int(scale)))
        # Vertical
        draw.line([cx, cy - 1.5*scale, cx, cy + 1.5*scale], fill=white, width=max(1, int(scale)))
        
        images.append(img)
    
    # Save as ICO
    output_path = os.path.join('src', 'app', 'static', 'favicon.ico')
    images[1].save(output_path, format='ICO', sizes=[(16,16), (32,32), (48,48)], append_images=[images[0], images[2]])
    print(f"Generated favicon.ico successfully at: {output_path}")

if __name__ == '__main__':
    generate_favicon()
