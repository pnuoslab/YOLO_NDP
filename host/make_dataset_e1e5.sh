#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════
# E1 (resolution) / E5 (codec) input set
#
# Source: ~/testd_video.mp4
#   AV1 Main, 3840x2160, 60000/1001 fps, 255735 frames, 4266.57 s, SDR
#   BT.709 8-bit yuv420p. Container title:
#   "New York City Walking Tour Part 1 - Midtown Manhattan (4k Ultra HD
#    60fps) - With Captions". No download URL survives in ~/.bash_history,
#   so the provenance is the title alone - see result_02_host.md.
#
# Excerpt: 60 s from 00:03:05. The start offset is the one the earlier
# testd clip set used (~/.bash_history: SRC=$HOME/testd_video.mp4
# SS=00:03:05 DUR=30). 60 s was chosen over the full 71 min because a
# full-length Baseline 4K run takes ~8.8 h, which makes the 357-run matrix
# impossible; 60 s also matches the "1 min" point of the earlier figure 6.
#
# Design rules carried over from the older ~/make_dataset.sh:
#   1) Every variant is encoded directly from the original. No chain
#      encoding (4K -> 1440p -> 1080p): the loss would accumulate and
#      contaminate the resolution comparison.
#   2) Fixed GOP. With different GOP structures a decode-time difference
#      cannot be attributed to the codec rather than to the GOP.
#   3) No upscaling. Every target resolution is <= the source.
# The source is SDR BT.709, so no tone mapping is applied (the older
# script needed it for an HDR source).
#
# All seven variants keep the source frame rate and frame count, carry no
# audio (-an), and are 8-bit yuv420p. Downscaling uses lanczos.
#
#   usage: ./make_dataset.sh [res|codec|psnr|probe]     (no arg = all)
# ══════════════════════════════════════════════════════════════════════
set -u

SRC=${SRC:-$HOME/testd_video.mp4}
DST=${DST:-$HOME/ndp_dataset}
SS=${SS:-00:03:05}
DUR=${DUR:-60}
GOP=${GOP:-60}
CRF264=${CRF264:-18}
CRF265=${CRF265:-20}
CRFAV1=${CRFAV1:-30}

STEPS=("$@")
do_step() { [ ${#STEPS[@]} -eq 0 ] && return 0
            for s in "${STEPS[@]}"; do [ "$s" = "$1" ] && return 0; done
            return 1; }

[ -f "$SRC" ] || { echo "[ERR] no source: $SRC"; exit 1; }
mkdir -p "$DST"

# AV1 decoding is the slow part of every command below, so give it all cores.
DEC="-threads 0"
CUT="-ss $SS -t $DUR"

echo "════════════════════════════════════════════════════════"
echo "source : $SRC"
echo "excerpt: $SS + ${DUR}s     GOP: $GOP     out: $DST"
echo "════════════════════════════════════════════════════════"

# ── source probe ──────────────────────────────────────────────────────
if do_step probe; then
  echo
  echo "── source ──"
  ffprobe -v error -count_frames -select_streams v:0 -show_entries \
    stream=codec_name,profile,width,height,r_frame_rate,nb_read_frames,bit_rate,pix_fmt,color_transfer,color_primaries \
    -show_entries format=size,duration -of json "$SRC"
  echo "── container tags (provenance) ──"
  ffprobe -v error -show_entries format_tags -of json "$SRC"
fi

# ── E1: resolution sweep, H.264 ───────────────────────────────────────
# res2160_h264 is a same-resolution re-encode, so it carries only the
# x264 loss and is the reference point the two smaller ones are read
# against.
if do_step res; then
  for spec in 1920:1080:1080 2560:1440:1440 3840:2160:2160; do
    IFS=: read -r W H TAG <<< "$spec"
    OUT="$DST/res${TAG}_h264.mp4"
    echo; echo "── $OUT (${W}x${H}, libx264 crf $CRF264) ──"
    ffmpeg -hide_banner -y $DEC $CUT -i "$SRC" \
      -vf "scale=${W}:${H}:flags=lanczos" -an \
      -c:v libx264 -preset medium -crf $CRF264 -pix_fmt yuv420p -g $GOP \
      "$OUT" 2>&1 | tail -2
  done
fi

# ── E5: codec sweep at 1080p and 4K ───────────────────────────────────
# H.264 at these two resolutions already exists from the res step, so only
# HEVC and AV1 are produced here. libsvtav1 is preferred over libaom-av1:
# both are present on this host, and libaom at 4K would take hours.
if do_step codec; then
  for spec in 1920:1080:1080 3840:2160:2160; do
    IFS=: read -r W H TAG <<< "$spec"

    OUT="$DST/res${TAG}_hevc.mp4"
    echo; echo "── $OUT (${W}x${H}, libx265 crf $CRF265) ──"
    ffmpeg -hide_banner -y $DEC $CUT -i "$SRC" \
      -vf "scale=${W}:${H}:flags=lanczos" -an \
      -c:v libx265 -preset medium -crf $CRF265 -pix_fmt yuv420p -g $GOP \
      -tag:v hvc1 "$OUT" 2>&1 | tail -2

    OUT="$DST/res${TAG}_av1.mp4"
    echo; echo "── $OUT (${W}x${H}, libsvtav1 preset 6 crf $CRFAV1) ──"
    ffmpeg -hide_banner -y $DEC $CUT -i "$SRC" \
      -vf "scale=${W}:${H}:flags=lanczos" -an \
      -c:v libsvtav1 -preset 6 -crf $CRFAV1 -pix_fmt yuv420p -g $GOP \
      "$OUT" 2>&1 | tail -2
  done
fi

# ── summary table + PSNR ──────────────────────────────────────────────
# PSNR reference: the same source excerpt scaled to the variant's own
# resolution with the same lanczos filter. So the number is the encoder's
# loss at that resolution, not the downscaling loss - comparing a 1080p
# file against a 4K reference would just measure the downscale.
if do_step psnr; then
  echo
  printf "%-20s %-6s %-10s %-8s %-12s %-12s %s\n" \
         file codec resolution frames bitrate size psnr_y_db
  for f in "$DST"/res1080_h264.mp4 "$DST"/res1440_h264.mp4 \
           "$DST"/res2160_h264.mp4 "$DST"/res1080_hevc.mp4 \
           "$DST"/res2160_hevc.mp4 "$DST"/res1080_av1.mp4 \
           "$DST"/res2160_av1.mp4; do
    [ -f "$f" ] || { echo "$(basename "$f")  MISSING"; continue; }
    read -r CN W H BR NF <<< "$(ffprobe -v error -count_frames \
      -select_streams v:0 -show_entries \
      stream=codec_name,width,height,bit_rate,nb_read_frames \
      -of csv=p=0 "$f" | tr ',' ' ')"
    SZ=$(stat -c %s "$f")
    PSNR=$(ffmpeg -hide_banner -nostats $DEC $CUT -i "$SRC" -i "$f" \
      -lavfi "[0:v]scale=${W}:${H}:flags=lanczos,format=yuv420p[ref];[1:v]format=yuv420p[dis];[dis][ref]psnr" \
      -f null - 2>&1 | grep -oP 'average:\K[0-9.]+' | tail -1)
    printf "%-20s %-6s %-10s %-8s %-12s %-12s %s\n" \
           "$(basename "$f")" "$CN" "${W}x${H}" "$NF" "$BR" "$SZ" "${PSNR:-n/a}"
  done
fi

echo
echo "done: $DST"
