#!/usr/bin/env python3
"""Convert GIF files to C arrays for embedding in firmware."""

import os
import sys
import glob

def gif_to_c_array(gif_path, var_name):
    """Read GIF file and convert to C byte array."""
    with open(gif_path, 'rb') as f:
        data = f.read()
    
    lines = []
    lines.append(f'static const uint8_t {var_name}_data[] = {{')
    row_str = "    "
    for i, b in enumerate(data):
        row_str += f"0x{b:02x}, "
        if (i + 1) % 12 == 0:
            lines.append(row_str)
            row_str = "    "
    if row_str.strip():
        lines.append(row_str)
    lines.append('};')
    lines.append('')
    return lines, len(data)

def process_gif(input_dir, output_c_file, output_h_file):
    """Process all GIF files in directory."""
    if not os.path.exists(input_dir):
        print(f"Directory {input_dir} not found!")
        return

    gif_files = glob.glob(os.path.join(input_dir, "*.gif"))
    print(f"Found {len(gif_files)} GIF files in {input_dir}")
    
    c_lines = [
        '#include "lvgl.h"',
        '#include "custom_gifs.h"',
        '#include <string.h>',
        '',
        '#ifndef LV_ATTRIBUTE_MEM_ALIGN',
        '#define LV_ATTRIBUTE_MEM_ALIGN',
        '#endif',
        ''
    ]
    
    h_lines = [
        '#ifndef CUSTOM_GIFS_H',
        '#define CUSTOM_GIFS_H',
        '#include "lvgl.h"',
        '#ifdef __cplusplus',
        'extern "C" {',
        '#endif',
        '',
        'const lv_image_dsc_t* GetCustomGifImage(const char* emotion);',
        ''
    ]

    gif_entries = []

    for filepath in gif_files:
        filename = os.path.basename(filepath)
        name_no_ext = os.path.splitext(filename)[0].lower()
        var_name = "gif_" + "".join(c if c.isalnum() else "_" for c in name_no_ext)
        
        data_lines, data_size = gif_to_c_array(filepath, var_name)
        c_lines.extend(data_lines)
        
        c_lines.append(f'const lv_image_dsc_t {var_name} = {{')
        c_lines.append('    .header.magic = LV_IMAGE_HEADER_MAGIC,')
        c_lines.append('    .header.cf = LV_COLOR_FORMAT_ARGB8888,')
        c_lines.append('    .header.flags = 0,')
        c_lines.append('    .header.w = 0,')
        c_lines.append('    .header.h = 0,')
        c_lines.append('    .header.stride = 0,')
        c_lines.append(f'    .data_size = {data_size},')
        c_lines.append(f'    .data = {var_name}_data,')
        c_lines.append('};')
        c_lines.append('')
        
        h_lines.append(f'extern const lv_image_dsc_t {var_name};')
        gif_entries.append((name_no_ext, var_name))

    # Build mapping function
    c_lines.append('typedef struct { const char* name; const lv_image_dsc_t* img; } gif_map_t;')
    c_lines.append('static const gif_map_t gif_maps[] = {')
    for name, var in gif_entries:
        c_lines.append(f'    {{"{name}", &{var}}},')
    c_lines.append('};')
    c_lines.append('')
    
    c_lines.append('const lv_image_dsc_t* GetCustomGifImage(const char* emotion) {')
    c_lines.append('    if (emotion == NULL || emotion[0] == \'\\0\') return NULL;')
    c_lines.append('    for (size_t i = 0; i < sizeof(gif_maps)/sizeof(gif_maps[0]); i++) {')
    c_lines.append('        if (strcmp(gif_maps[i].name, emotion) == 0) {')
    c_lines.append('            return gif_maps[i].img;')
    c_lines.append('        }')
    c_lines.append('    }')
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
        
    print(f"Generated {output_c_file} and {output_h_file} with {len(gif_entries)} GIFs!")

if __name__ == '__main__':
    input_dir = "D:/xiaozhi/bieucam/resized"
    c_out = "d:/xiaozhi/xiaozhi-esp32-diepvu203/main/assets/custom_gifs.c"
    h_out = "d:/xiaozhi/xiaozhi-esp32-diepvu203/main/assets/custom_gifs.h"
    process_gif(input_dir, c_out, h_out)
