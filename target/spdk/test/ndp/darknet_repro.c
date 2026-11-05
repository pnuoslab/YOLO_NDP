/*
 * darknet_repro - is the target's letterbox path deterministic?
 *
 * Runs the production ndp_frame_to_letterbox_rgb() (pulled verbatim out of
 * lib/nvmf/ctrlr_bdev.c by extract_preproc.py) on decoded frames, several
 * times per frame with the same input, and reports whether the RGB output is
 * byte-identical across runs and where it differs.
 *
 * No target, no SPDK: it builds against the system libav*, so it can run
 * under Valgrind or with MALLOC_PERTURB_.
 *
 * Within one process glibc tends to hand back the same block with the same
 * stale contents, which can hide a read of never-written memory. Two ways to
 * expose one:
 *   - NDP_CHURN=1: before every run, malloc a block the size of the
 *     full-resolution conversion buffer, fill it with a run-specific byte and
 *     free it, so the next malloc() inside the production code likely gets
 *     that block back holding different garbage;
 *   - MALLOC_PERTURB_=<n>: glibc fills every malloc() with n^0xff; compare
 *     the output hashes of two processes started with different n.
 *
 * usage: darknet_repro <video> <darknet 0|1> <nframes> <runs> [out_dir]
 *   out_dir: if given, writes run-0 output as f%03d.rgb (416x416 RGB24) and
 *            the decoded frame as f%03d.yuv (planar, as decoded).
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <libavformat/avformat.h>
#include <libavcodec/avcodec.h>
#include <libavutil/imgutils.h>
#include <libswscale/swscale.h>

#include "ndp_preproc_extracted.h"

#define NET 416

static uint64_t fnv1a(const uint8_t *p, size_t n)
{
	uint64_t h = 1469598103934665603ULL;
	for (size_t i = 0; i < n; i++) { h ^= p[i]; h *= 1099511628211ULL; }
	return h;
}

int main(int argc, char **argv)
{
	if (argc < 5) {
		fprintf(stderr, "usage: %s <video> <darknet 0|1> <nframes> <runs> [out_dir]\n", argv[0]);
		return 1;
	}
	const char *path = argv[1];
	int darknet = atoi(argv[2]), nframes = atoi(argv[3]), runs = atoi(argv[4]);
	const char *outdir = argc > 5 ? argv[5] : NULL;
	int churn = getenv("NDP_CHURN") && atoi(getenv("NDP_CHURN"));
	av_log_set_level(AV_LOG_ERROR);

	AVFormatContext *fmt = NULL;
	if (avformat_open_input(&fmt, path, NULL, NULL) < 0) { fprintf(stderr, "open failed\n"); return 2; }
	avformat_find_stream_info(fmt, NULL);
	int vi = av_find_best_stream(fmt, AVMEDIA_TYPE_VIDEO, -1, -1, NULL, 0);
	const AVCodec *codec = avcodec_find_decoder(fmt->streams[vi]->codecpar->codec_id);
	AVCodecContext *cc = avcodec_alloc_context3(codec);
	avcodec_parameters_to_context(cc, fmt->streams[vi]->codecpar);
	cc->thread_count = 1;              /* keep decode itself trivially deterministic */
	if (avcodec_open2(cc, codec, NULL) < 0) { fprintf(stderr, "avcodec_open2 failed\n"); return 2; }

	const size_t out_bytes = (size_t)NET * NET * 3;
	uint8_t *ref = malloc(out_bytes);
	AVPacket *pkt = av_packet_alloc();
	AVFrame *frm = av_frame_alloc();
	int fidx = 0, eof = 0, frames_nondet = 0;
	long total_diff_bytes = 0;
	int g_rmin = NET, g_rmax = -1, g_cmin = NET, g_cmax = -1, g_maxabs = 0;

	printf("# input=%s darknet=%d runs=%d churn=%d perturb=%s\n",
	       path, darknet, runs, churn, getenv("MALLOC_PERTURB_") ? getenv("MALLOC_PERTURB_") : "-");

	while (!eof && fidx < nframes) {
		int got = av_read_frame(fmt, pkt) >= 0;
		if (!got) eof = 1;
		if (got && pkt->stream_index != vi) { av_packet_unref(pkt); continue; }
		avcodec_send_packet(cc, got ? pkt : NULL);
		if (got) av_packet_unref(pkt);

		while (fidx < nframes && avcodec_receive_frame(cc, frm) == 0) {
			int w = 0, h = 0, sw = 0, sh = 0;
			int diff_runs = 0;
			long diff_bytes = 0;
			int rmin = NET, rmax = -1, cmin = NET, cmax = -1, maxabs = 0;
			uint64_t h0 = 0;

			for (int r = 0; r < runs; r++) {
				if (churn) {
					size_t sz = (size_t)frm->width * frm->height * 3;
					uint8_t *junk = malloc(sz);
					memset(junk, (r * 53 + 7) & 0xff, sz);
					free(junk);
				}
				uint8_t *lb = ndp_frame_to_letterbox_rgb(frm, NET, NET, darknet, &w, &h, &sw, &sh);
				if (!lb) { fprintf(stderr, "letterbox failed\n"); return 3; }
				if (r == 0) {
					memcpy(ref, lb, out_bytes);
					h0 = fnv1a(lb, out_bytes);
					if (outdir) {
						char fn[1024];
						snprintf(fn, sizeof(fn), "%s/f%03d.rgb", outdir, fidx);
						FILE *f = fopen(fn, "wb"); fwrite(lb, 1, out_bytes, f); fclose(f);
						snprintf(fn, sizeof(fn), "%s/f%03d.yuv", outdir, fidx);
						f = fopen(fn, "wb");
						for (int p = 0; p < 3; p++) {
							int pw = p ? (frm->width + 1) / 2 : frm->width;
							int ph = p ? (frm->height + 1) / 2 : frm->height;
							for (int y = 0; y < ph; y++)
								fwrite(frm->data[p] + (size_t)y * frm->linesize[p], 1, pw, f);
						}
						fclose(f);
					}
				} else {
					int differs = 0;
					for (size_t i = 0; i < out_bytes; i++) {
						if (lb[i] != ref[i]) {
							int px = (int)(i / 3), row = px / NET, col = px % NET;
							int d = abs((int)lb[i] - (int)ref[i]);
							differs = 1; diff_bytes++;
							if (row < rmin) rmin = row;
							if (row > rmax) rmax = row;
							if (col < cmin) cmin = col;
							if (col > cmax) cmax = col;
							if (d > maxabs) maxabs = d;
						}
					}
					diff_runs += differs;
				}
				free(lb);
			}

			printf("frame %3d src=%dx%d fmt=%s scaled=%dx%d hash0=%016llx  runs_differing=%d/%d",
			       fidx, frm->width, frm->height, av_get_pix_fmt_name(frm->format),
			       sw, sh, (unsigned long long)h0, diff_runs, runs - 1);
			if (diff_runs) {
				printf("  diff_bytes=%ld rows=%d..%d cols=%d..%d max_abs=%d",
				       diff_bytes, rmin, rmax, cmin, cmax, maxabs);
				frames_nondet++;
				total_diff_bytes += diff_bytes;
				if (rmin < g_rmin) g_rmin = rmin;
				if (rmax > g_rmax) g_rmax = rmax;
				if (cmin < g_cmin) g_cmin = cmin;
				if (cmax > g_cmax) g_cmax = cmax;
				if (maxabs > g_maxabs) g_maxabs = maxabs;
			}
			printf("\n");
			fidx++;
		}
	}

	if (frames_nondet)
		printf("RESULT: NONDETERMINISTIC in %d/%d frames  diff_bytes=%ld  canvas rows=%d..%d cols=%d..%d  max_abs=%d\n",
		       frames_nondet, fidx, total_diff_bytes, g_rmin, g_rmax, g_cmin, g_cmax, g_maxabs);
	else
		printf("RESULT: DETERMINISTIC across %d runs x %d frames (within this process)\n", runs, fidx);

	free(ref);
	av_frame_free(&frm);
	av_packet_free(&pkt);
	avcodec_free_context(&cc);
	avformat_close_input(&fmt);
	sws_freeContext(g_sws_ctx);
	return 0;
}
