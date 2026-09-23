#!/usr/bin/env python3
"""Type-check firmware sources WITHOUT a full idf.py build.

Reuses the existing `build/compile_commands.json` (so target, sdkconfig and
include paths are exactly what the last build used) and re-runs the compiler
with `-fsyntax-only`, which performs full parsing, semantic analysis and
template instantiation without emitting code.

Why: `idf.py build` needs the whole ESP-IDF toolchain/venv and takes minutes,
so contributors often skip it. This check catches the mistakes that actually
happen when editing C/C++ (typos, wrong members, missing includes, wrong
argument types) in a couple of seconds per file.

Usage (repo root):
    python3 tools/fw_syntax_check.py main/audio/music_player.cc
    python3 tools/fw_syntax_check.py music_player no_audio_codec
    python3 tools/fw_syntax_check.py --all-changed      # files changed vs HEAD

Exit code 0 means every requested file passed. This is a syntax/semantic
check only: it does not replace `idf.py build` (no link step, no generated
binary), but it is a fast pre-flight gate.
"""

import argparse
import json
import os
import re
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMPILE_DB = os.path.join(REPO_ROOT, "build", "compile_commands.json")

# Flags that do not belong in a syntax-only run.
_STRIP_FLAGS = {"-o", "-c", "-MD", "-MMD", "-MT", "-MF", "-MJ"}


def load_db():
    if not os.path.isfile(COMPILE_DB):
        sys.exit(
            f"error: {COMPILE_DB} not found.\n"
            "Run a build once (python3 scripts/build.py <board> --name <variant>)\n"
            "so the compiler command database exists.")
    with open(COMPILE_DB, encoding="utf-8") as f:
        return json.load(f)


def tokenize(command: str):
    # ESP-IDF quotes defines such as -DBOARD_NAME="bread-compact-wifi".
    return re.findall(r'"[^"]*"|\S+', command)


def unescape(tokens):
    """Undo the shell-style escaping used inside compile_commands.json.

    The database stores defines as `-DMBEDTLS_CONFIG_FILE=\\"mbedtls/esp_config.h\\"`
    because the build runs them through a shell, which strips one level of
    escaping. We hand the arguments straight to CreateProcess (no shell), so the
    `\\"` sequences must be turned back into real quotes or the compiler sees
    broken macro values (e.g. `#include MBEDTLS_CONFIG_FILE` fails).
    """
    return [tok.replace('\\"', '"') for tok in tokens]


def build_syntax_cmd(entry):
    tokens = unescape(tokenize(entry["command"]))
    out = []
    skip_next = False
    for tok in tokens:
        if skip_next:
            skip_next = False
            continue
        # Drop flags that only make sense when producing an object file.
        flag = tok.lstrip('@"') if tok.startswith('@"') else tok
        if flag in _STRIP_FLAGS:
            skip_next = flag in ("-o", "-MT", "-MF", "-MJ")
            continue
        if flag.startswith(("-o=", "-MD", "-MMD")):
            continue
        # clang-style response files are not understood by the GCC driver.
        if tok.startswith('@"') or tok.startswith("@"):
            out.append("-Wno-unknown-pragmas")
            continue
        out.append(tok)
    # Replace the source path + object output with a syntax-only invocation.
    return [t for t in out if not t.endswith(".obj")]


def pick_entries(db, patterns):
    """Match a source by full path or by basename substring."""
    chosen = []
    for entry in db:
        src = entry["file"].replace("\\", "/")
        for pat in patterns:
            p = pat.replace("\\", "/")
            if src.endswith(p) or p in src:
                chosen.append((pat, entry))
                break
    missing = [p for p in patterns
               if not any(p.replace("\\", "/") in c[1]["file"].replace("\\", "/")
                          for c in chosen)]
    return chosen, missing


def changed_vs_head():
    try:
        out = subprocess.run(
            ["git", "diff", "--name-only", "HEAD", "--", "*.c", "*.cc", "*.cpp", "*.h"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        sys.exit(f"error: git diff failed ({exc})")
    return [line.strip() for line in out.splitlines() if line.strip()]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sources", nargs="*",
                    help="source path or basename substring (e.g. music_player)")
    ap.add_argument("--all-changed", action="store_true",
                    help="check every C/C++ file changed vs HEAD that is in the build")
    args = ap.parse_args()

    patterns = list(args.sources)
    if args.all_changed:
        patterns += changed_vs_head()
    if not patterns:
        ap.error("give at least one source, or --all-changed")

    db = load_db()
    entries, missing = pick_entries(db, patterns)
    for pat in missing:
        print(f"skip: '{pat}' is not part of the last build (not compiled by this target)")
    if not entries:
        sys.exit("error: nothing to check")

    failed = []
    for pat, entry in entries:
        cmd = build_syntax_cmd(entry) + ["-fsyntax-only"]
        name = os.path.relpath(entry["file"], REPO_ROOT)
        res = subprocess.run(cmd, cwd=entry["directory"], capture_output=True, text=True)
        if res.returncode == 0:
            print(f"OK    {name}")
        else:
            failed.append(name)
            print(f"FAIL  {name}")
            sys.stderr.write(res.stderr)

    if failed:
        print(f"\n{len(failed)} file(s) failed: {', '.join(failed)}")
        return 1
    print(f"\n{len(entries)} file(s) passed syntax/semantic check")
    return 0


if __name__ == "__main__":
    sys.exit(main())
