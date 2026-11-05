#!/bin/bash
# Check the darknet sws-tail fix on a real input, end to end.
#   usage: check_mot20_fix.sh <video> <work_dir> [nframes=30] [runs=5]
# Builds the repro harness twice from fixed commits - pre-fix 646a3ca and
# post-fix e8b216b - regardless of what is checked out, then reports:
#   1. sha256 of the input
#   2. pre-fix:  determinism with NDP_CHURN=1, and hashes under MALLOC_PERTURB_ 1/99/200
#   3. post-fix: the same
#   4. pixel-difference statistics, post vs pre (and sws/no-darknet control)
# Everything runs pinned to CPUs 1,17.
set -e
V=${1:?video}; W=${2:?work_dir}; NF=${3:-30}; RUNS=${4:-5}
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
T="taskset -c 1,17"
LIBS=$(pkg-config --cflags --libs libavformat libavcodec libavutil libswscale)
mkdir -p "$W"

build() {   # $1 = name, $2 = commit
	mkdir -p "$W/$1"
	git -C "$REPO" show "$2:spdk/lib/nvmf/ctrlr_bdev.c" > "$W/$1/ctrlr_bdev.c"
	python3 -I "$HERE/extract_preproc.py" "$W/$1/ctrlr_bdev.c" "$W/$1/ndp_preproc_extracted.h"
	$T gcc -O2 -g -Wall -I"$W/$1" -o "$W/$1/darknet_repro" "$HERE/darknet_repro.c" $LIBS -lm
}
build pre 646a3ca
build post e8b216b

echo "## 1. input"
sha256sum "$V"
ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,width,height,pix_fmt,nb_frames -of csv=p=0 "$V"

for b in pre post; do
	echo "## $b: darknet, NDP_CHURN=1, $NF frames x $RUNS runs"
	NDP_CHURN=1 $T "$W/$b/darknet_repro" "$V" 1 "$NF" "$RUNS" | tail -1
	echo "## $b: darknet, frame hashes across processes"
	for pv in 1 99 200; do
		printf "   MALLOC_PERTURB_=%-4s " $pv
		MALLOC_PERTURB_=$pv $T "$W/$b/darknet_repro" "$V" 1 3 1 | grep -o "hash0=[0-9a-f]*" | tr '\n' ' '
		echo
	done
done

echo "## 4. pixel differences (all $NF frames)"
for b in pre post; do
	for d in 1 0; do
		mkdir -p "$W/out_${b}_dk$d"
		MALLOC_PERTURB_=200 $T "$W/$b/darknet_repro" "$V" $d "$NF" 1 "$W/out_${b}_dk$d" > /dev/null
	done
done
mkdir -p "$W/out_pre_dk1_p1"
MALLOC_PERTURB_=1 $T "$W/pre/darknet_repro" "$V" 1 "$NF" 1 "$W/out_pre_dk1_p1" > /dev/null
python3 -I "$HERE/cmp_rgb_dirs.py" \
	"darknet  post vs pre(PERTURB=200)" "$W/out_post_dk1" "$W/out_pre_dk1" \
	"darknet  post vs pre(PERTURB=1)"   "$W/out_post_dk1" "$W/out_pre_dk1_p1" \
	"darknet  pre(P=1) vs pre(P=200)"   "$W/out_pre_dk1_p1" "$W/out_pre_dk1" \
	"sws      post vs pre (control)"    "$W/out_post_dk0" "$W/out_pre_dk0"
