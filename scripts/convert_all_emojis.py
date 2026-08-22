import cv2
import numpy as np
import os
import sys
import glob

# Standard XiaoZhi emotion mapping
EMOTION_NAMES = [
    "neutral", "happy", "laughing", "funny", "sad", "angry", 
    "crying", "loving", "embarrassed", "surprised", "shocked", 
    "thinking", "winking", "cool", "relaxed", "delicious", 
    "kissy", "confident", "sleepy", "silly", "confused", "robot", "alien"
]

def convert_png_to_i1_bytes(png_path, width=64, height=64, invert=True):
    img = cv2.imread(png_path, cv2.IMREAD_UNCHANGED)
    if img is None:
        return None
        
    resized = cv2.resize(img, (width, height), interpolation=cv2.INTER_AREA)
    binary = np.zeros((height, width), dtype=np.uint8)
    
    if len(resized.shape) == 3 and resized.shape[2] == 4:
        b, g, r, a = cv2.split(resized)
        gray = cv2.cvtColor(resized[:, :, :3], cv2.COLOR_BGR2GRAY)
        if invert:
            binary[(gray <= 128) & (a > 128)] = 1
        else:
            binary[(gray > 128) & (a > 128)] = 1
    elif len(resized.shape) == 3:
        gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
        if invert:
            binary[gray <= 128] = 1
        else:
            binary[gray > 128] = 1
    else:
        if invert:
            binary[resized <= 128] = 1
        else:
            binary[resized > 128] = 1

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
            
    palette = bytes([
        0x00, 0x00, 0x00, 0xFF,
        0xFF, 0xFF, 0xFF, 0xFF
    ])
    
    return palette + pixel_bytes

def process_directory(input_dir, output_c_file, output_h_file, width=64, height=64):
    if not os.path.exists(input_dir):
        print(f"Directory {input_dir} not found!")
        return

    png_files = glob.glob(os.path.join(input_dir, "*.png"))
    print(f"Found {len(png_files)} PNG files in {input_dir}")
    
    c_lines = [
        '#include "lvgl.h"',
        '#include "custom_emojis.h"',
        '#include <string.h>',
        '',
        '#ifndef LV_ATTRIBUTE_MEM_ALIGN',
        '#define LV_ATTRIBUTE_MEM_ALIGN',
        '#endif',
        ''
    ]
    
    h_lines = [
        '#ifndef CUSTOM_EMOJIS_H',
        '#define CUSTOM_EMOJIS_H',
        '#include "lvgl.h"',
        '#ifdef __cplusplus',
        'extern "C" {',
        '#endif',
        '',
        'const lv_image_dsc_t* GetCustomEmojiImage(const char* emotion);',
        ''
    ]

    emoji_entries = []

    for filepath in png_files:
        filename = os.path.basename(filepath)
        name_no_ext = os.path.splitext(filename)[0].lower()
        # Clean var name
        var_name = "emoji_" + "".join(c if c.isalnum() else "_" for c in name_no_ext)
        
        full_data = convert_png_to_i1_bytes(filepath, width, height, invert=True)
        if full_data is None:
            continue
            
        c_lines.append(f'static const LV_ATTRIBUTE_MEM_ALIGN uint8_t {var_name}_map[] = {{')
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
        c_lines.append(f'    .header.stride = {(width + 7) // 8},')
        c_lines.append(f'    .data_size = {len(full_data)},')
        c_lines.append(f'    .data = {var_name}_map,')
        c_lines.append('};')
        c_lines.append('')
        
        h_lines.append(f'extern const lv_image_dsc_t {var_name};')
        emoji_entries.append((name_no_ext, var_name))

    # Build mapping function
    c_lines.append('typedef struct { const char* name; const lv_image_dsc_t* img; } emoji_map_t;')
    c_lines.append('static const emoji_map_t emoji_maps[] = {')
    for name, var in emoji_entries:
        c_lines.append(f'    {{"{name}", &{var}}},')
    c_lines.append('};')
    c_lines.append('')
    
    c_lines.append('const lv_image_dsc_t* GetCustomEmojiImage(const char* emotion) {')
    c_lines.append('    if (emotion == NULL || emotion[0] == \'\\0\') return NULL;')
    c_lines.append('    for (size_t i = 0; i < sizeof(emoji_maps)/sizeof(emoji_maps[0]); i++) {')
    c_lines.append('        if (strcmp(emoji_maps[i].name, emotion) == 0) {')
    c_lines.append('            return emoji_maps[i].img;')
    c_lines.append('        }')
    c_lines.append('    }')
    # Fallback to neutral or default if available
    c_lines.append('    for (size_t i = 0; i < sizeof(emoji_maps)/sizeof(emoji_maps[0]); i++) {')
    c_lines.append('        if (strcmp(emoji_maps[i].name, "neutral") == 0 || strcmp(emoji_maps[i].name, "bieucam-macdinh") == 0) {')
    c_lines.append('            return emoji_maps[i].img;')
    c_lines.append('        }')
    c_lines.append('    }')
    c_lines.append('    if (sizeof(emoji_maps)/sizeof(emoji_maps[0]) > 0) return emoji_maps[0].img;')
    c_lines.append('    return NULL;')
    c_lines.append('}')
    
    h_lines.append('')
    h_lines.append('#ifdef __cplusplus')
    h_lines.append('}')
    h_lines.append('#endif')
    h_lines.append('#endif')

    os.makedirs(os.path.dirname(output_c_file), exist_ok=True)
    with open(output_c_file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(c_lines))
        
    with open(output_h_file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(h_lines))
        
    print(f"Generated {output_c_file} and {output_h_file} with {len(emoji_entries)} emojis!")

if __name__ == '__main__':
    input_dir = "D:/xiaozhi/bieucam"
    c_out = "d:/xiaozhi/xiaozhi-esp32-diepvu203/main/assets/custom_emojis.c"
    h_out = "d:/xiaozhi/xiaozhi-esp32-diepvu203/main/assets/custom_emojis.h"
    process_directory(input_dir, c_out, h_out)
