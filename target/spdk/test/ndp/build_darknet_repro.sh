#!/bin/bash
# Build darknet_repro against the CURRENT lib/nvmf/ctrlr_bdev.c.
#   usage: build_darknet_repro.sh <out_dir> [asan]
# Produces <out_dir>/darknet_repro (and darknet_repro_asan with "asan").
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
SPDK=$(cd "$HERE/../.." && pwd)
OUT=${1:?out_dir}
mkdir -p "$OUT"
python3 -I "$HERE/extract_preproc.py" "$SPDK/lib/nvmf/ctrlr_bdev.c" "$OUT/ndp_preproc_extracted.h"
LIBS=$(pkg-config --cflags --libs libavformat libavcodec libavutil libswscale)
gcc -O2 -g -Wall -I"$OUT" -o "$OUT/darknet_repro" "$HERE/darknet_repro.c" $LIBS -lm
if [ "$2" = "asan" ]; then
    gcc -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer -I"$OUT" \
        -o "$OUT/darknet_repro_asan" "$HERE/darknet_repro.c" $LIBS -lm
fi
echo "built in $OUT"
