import cv2
import numpy as np
import os
import sys

def convert_png_to_lvgl_i1(png_path, out_c_path, var_name="emoji_default", width=64, height=64):
    if not os.path.exists(png_path):
        print(f"Error: {png_path} does not exist")
        sys.exit(1)
        
    # Read image with alpha channel
    img = cv2.imread(png_path, cv2.IMREAD_UNCHANGED)
    if img is None:
        print(f"Failed to read image {png_path}")
        sys.exit(1)

    print(f"Original image shape: {img.shape}")
    
    # Handle resize
    resized = cv2.resize(img, (width, height), interpolation=cv2.INTER_AREA)
    
    # Convert to grayscale / binary 1-bit
    # If RGBA, check alpha and color
    if len(resized.shape) == 3 and resized.shape[2] == 4:
        b, g, r, a = cv2.split(resized)
        # Create grayscale
        gray = cv2.cvtColor(resized[:, :, :3], cv2.COLOR_BGR2GRAY)
        # Combine alpha and brightness: dark features (eyes) or non-background become white (1) on black (0) background
        binary = np.zeros((height, width), dtype=np.uint8)
        # Invert: dark pixels (eyes) become 1 (white glowing), light background becomes 0 (black)
        binary[(gray <= 128) & (a > 128)] = 1
    elif len(resized.shape) == 3:
        gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
        binary = np.zeros((height, width), dtype=np.uint8)
        binary[gray <= 128] = 1
    else:
        binary = np.zeros((height, width), dtype=np.uint8)
        binary[resized <= 128] = 1

    # Pack bits into bytes for LV_COLOR_FORMAT_I1
    # In LVGL I1, each byte contains 8 pixels, MSB first (bit 7 is leftmost pixel)
    stride = (width + 7) // 8
    pixel_bytes = bytearray()
    
    for y in range(height):
        for x_byte in range(stride):
            byte_val = 0
            for bit in range(8):
                x = x_byte * 8 + bit
                if x < width:
                    if binary[y, x]:
                        byte_val |= (1 << (7 - bit))
            pixel_bytes.append(byte_val)
            
    # Palette for I1: 2 colors (4 bytes per color: Blue, Green, Red, Alpha in LVGL BGR/RGB)
    # Color 0: Black (0, 0, 0, 255) -> 0x00, 0x00, 0x00, 0xFF
    # Color 1: White (255, 255, 255, 255) -> 0xFF, 0xFF, 0xFF, 0xFF
    palette = bytes([
        0x00, 0x00, 0x00, 0xFF,
        0xFF, 0xFF, 0xFF, 0xFF
    ])
    
    full_data = palette + pixel_bytes
    
    # Generate C source content
    c_lines = []
    c_lines.append('#include "lvgl.h"')
    c_lines.append('')
    c_lines.append('#ifndef LV_ATTRIBUTE_MEM_ALIGN')
    c_lines.append('#define LV_ATTRIBUTE_MEM_ALIGN')
    c_lines.append('#endif')
    c_lines.append('')
    c_lines.append('static const LV_ATTRIBUTE_MEM_ALIGN uint8_t ' + var_name + '_map[] = {')
    
    # Write bytes hex
    row_str = "    "
    for i, b in enumerate(full_data):
        row_str += f"0x{b:02x}, "
        if (i + 1) % 12 == 0:
            c_lines.append(row_str)
            row_str = "    "
    if row_str.strip():
        c_lines.append(row_str)
        
    c_lines.append('};')
    c_lines.append('')
    c_lines.append(f'const lv_image_dsc_t {var_name} = {{')
    c_lines.append('    .header.magic = LV_IMAGE_HEADER_MAGIC,')
    c_lines.append('    .header.cf = LV_COLOR_FORMAT_I1,')
    c_lines.append('    .header.flags = 0,')
    c_lines.append(f'    .header.w = {width},')
    c_lines.append(f'    .header.h = {height},')
    c_lines.append(f'    .header.stride = {stride},')
    c_lines.append(f'    .data_size = {len(full_data)},')
    c_lines.append(f'    .data = {var_name}_map,')
    c_lines.append('};')
    c_lines.append('')

    os.makedirs(os.path.dirname(out_c_path), exist_ok=True)
    with open(out_c_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(c_lines))
        
    print(f"Successfully generated {out_c_path} ({width}x{height}, data_size={len(full_data)})")

if __name__ == '__main__':
    src = "D:/xiaozhi/bieucam/bieucam-macdinh.png"
    dst = "d:/xiaozhi/xiaozhi-esp32-diepvu203/main/assets/emoji_default.c"
    convert_png_to_lvgl_i1(src, dst, var_name="emoji_default", width=128, height=64)
