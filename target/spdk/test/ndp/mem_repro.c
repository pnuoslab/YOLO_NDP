/*
 * mem_repro - why does heap_peak_delta_mb vary between repeats?
 *
 * Replays a sequence of 0xC0-like requests in one long-lived process and
 * measures heap_peak_delta the way the target does: write "5" to
 * /proc/self/clear_refs, read VmRSS (the entry baseline), run the request on
 * a fresh worker pthread, then read VmHWM. Each request mirrors
 * ndp_preprocess_worker(): in-memory AVIO over the whole file, decoder with
 * thread_count = 0 and FF_THREAD_FRAME, then per frame the production
 * ndp_frame_to_letterbox_rgb() + encode_jpeg_from_rgb() (copied verbatim by
 * extract_preproc.py), payloads held until the end.
 *
 * Unlike the target, the compressed input lives in an ordinary buffer
 * allocated once before any request, and no result blob is built. In the
 * target both are hugepage DMA buffers that never show up in VmRSS, so
 * leaving them out of the measured window keeps the comparison fair. No
 * hugepages are used here, so a target running on the same host cannot be
 * starved.
 *
 * MEM_TRIM=1 calls malloc_trim(0) right before the peak reset - the fix
 * candidate - and reports how long it took.
 *
 * Alongside the RSS-based delta it reports an allocation-based one,
 * inuseΔ: mallinfo2() bytes in use (uordblks + hblkhd) sampled on the worker
 * right after the decode loop - where thr_peak is sampled in the target -
 * minus the same figure at request entry. It counts what the request holds,
 * regardless of how much freed memory earlier requests left resident.
 *
 * MEM_DEBUG=1 prints, before each request, where the resident baseline
 * sits: mallinfo2() (bytes in use vs free inside malloc arenas, bytes in
 * separate mmap chunks) and VmRSS split by mapping kind from /proc/self/smaps
 * ([heap] = main arena, 64 MB-reserved anonymous regions = per-thread arenas,
 * [stack]/thread stacks, other anonymous, file-backed).
 *
 * usage: mem_repro <video> <seq>      seq like "q85,raw,q85,q85"
 *        (raw = no JPEG; qNN = JPEG quality NN). Worker pinned to CPUs 1,17.
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stdbool.h>
#include <malloc.h>
#include <pthread.h>
#include <sched.h>
#include <time.h>
#include <libavformat/avformat.h>
#include <libavcodec/avcodec.h>
#include <libavutil/imgutils.h>
#include <libswscale/swscale.h>
#include <jpeglib.h>

#include "ndp_preproc_extracted.h"

struct bd { uint8_t *ptr, *start; size_t size, total; };

static int rd(void *o, uint8_t *b, int n)
{
	struct bd *d = o;
	if (n <= 0 || d->size == 0) return AVERROR_EOF;
	if ((size_t)n > d->size) n = (int)d->size;
	memcpy(b, d->ptr, n); d->ptr += n; d->size -= n;
	return n;
}

static int64_t sk(void *o, int64_t off, int wh)
{
	struct bd *d = o;
	uint8_t *np;
	switch (wh) {
	case SEEK_SET: np = d->start + off; break;
	case SEEK_CUR: np = d->ptr + off; break;
	case SEEK_END: np = d->start + d->total + off; break;
	case AVSEEK_SIZE: return d->total;
	default: return -1;
	}
	if (np < d->start || np > d->start + d->total) return -1;
	d->ptr = np; d->size = d->total - (np - d->start);
	return np - d->start;
}

static long status_kb(const char *key)
{
	FILE *f = fopen("/proc/self/status", "r");
	char l[256];
	long v = -1;
	size_t k = strlen(key);
	while (f && fgets(l, sizeof(l), f))
		if (!strncmp(l, key, k)) { sscanf(l + k, " %ld", &v); break; }
	if (f) fclose(f);
	return v;
}

static void reset_peak(void)
{
	FILE *f = fopen("/proc/self/clear_refs", "w");
	if (f) { fputs("5", f); fclose(f); }
}

struct job { uint8_t *file; size_t size; int q; int frames; size_t payload;
             size_t inuse_end; double mallinfo_ms; };

static size_t inuse_bytes(void)
{
	struct mallinfo2 mi = mallinfo2();
	return mi.uordblks + mi.hblkhd;
}

static void *worker(void *arg)
{
	struct job *j = arg;
	struct bd d = { j->file, j->file, j->size, j->size };
	uint8_t *ab = av_malloc(4096);
	AVIOContext *avio = avio_alloc_context(ab, 4096, 0, &d, rd, NULL, sk);
	AVFormatContext *fmt = avformat_alloc_context();
	fmt->pb = avio;
	avformat_open_input(&fmt, NULL, NULL, NULL);
	avformat_find_stream_info(fmt, NULL);
	int vi = av_find_best_stream(fmt, AVMEDIA_TYPE_VIDEO, -1, -1, NULL, 0);
	const AVCodec *codec = avcodec_find_decoder(fmt->streams[vi]->codecpar->codec_id);
	AVCodecContext *cc = avcodec_alloc_context3(codec);
	avcodec_parameters_to_context(cc, fmt->streams[vi]->codecpar);
	cc->thread_count = 0;
	cc->thread_type = FF_THREAD_FRAME;
	avcodec_open2(cc, codec, NULL);

	int64_t nb = fmt->streams[vi]->nb_frames;
	if (nb <= 0) nb = 1000;
	uint8_t **pl = calloc(nb, sizeof(*pl));
	AVPacket *pkt = av_packet_alloc();
	AVFrame *frm = av_frame_alloc();
	int n = 0, eof = 0;
	while (!eof) {
		int got = av_read_frame(fmt, pkt) >= 0;
		if (!got) eof = 1;
		if (got && pkt->stream_index != vi) { av_packet_unref(pkt); continue; }
		avcodec_send_packet(cc, got ? pkt : NULL);
		if (got) av_packet_unref(pkt);
		while (avcodec_receive_frame(cc, frm) == 0 && n < nb) {
			int w, h, sw, sh;
			uint8_t *lb = ndp_frame_to_letterbox_rgb(frm, 416, 416, 0, &w, &h, &sw, &sh);
			size_t js = 0;
			if (j->q < 0) { pl[n] = lb; js = (size_t)w * h * 3; }
			else { pl[n] = encode_jpeg_from_rgb(lb, w, h, j->q, 0, &js); free(lb); }
			j->payload += js;
			n++;
		}
	}
	j->frames = n;
	{
		struct timespec a, b;
		clock_gettime(CLOCK_MONOTONIC, &a);
		j->inuse_end = inuse_bytes();
		clock_gettime(CLOCK_MONOTONIC, &b);
		j->mallinfo_ms = (b.tv_sec - a.tv_sec) * 1e3 + (b.tv_nsec - a.tv_nsec) / 1e6;
	}
	for (int i = 0; i < n; i++) free(pl[i]);
	free(pl);
	av_frame_free(&frm);
	av_packet_free(&pkt);
	avcodec_free_context(&cc);
	avformat_close_input(&fmt);
	av_freep(&avio->buffer);
	av_freep(&avio);
	return NULL;
}

static void debug_baseline(void)
{
	struct mallinfo2 mi = mallinfo2();
	FILE *f = fopen("/proc/self/smaps", "r");
	char l[512], name[256];
	unsigned long a, b, rss = 0;
	double heap = 0, arena = 0, anon = 0, file = 0, stack = 0;
	int kind = 0;   /* 1 heap, 2 arena, 3 anon, 4 file, 5 stack */
	while (f && fgets(l, sizeof(l), f)) {
		name[0] = 0;
		if (sscanf(l, "%lx-%lx %*s %*s %*s %*s %255s", &a, &b, name) >= 2 && strchr(l, '-') == l + strcspn(l, "-")
		    && l[0] != ' ' && strncmp(l, "Rss", 3) && strchr("0123456789abcdef", l[0])) {
			unsigned long len = b - a;
			if (!strcmp(name, "[heap]")) kind = 1;
			else if (!strncmp(name, "[stack", 6)) kind = 5;
			else if (name[0] == '/') kind = 4;
			else if (name[0] == 0 && (len == (64UL << 20) || (a & ((64UL << 20) - 1)) == 0)) kind = 2;
			else kind = 3;
		} else if (sscanf(l, "Rss: %lu kB", &rss) == 1) {
			double mb = rss / 1024.0;
			if (kind == 1) heap += mb; else if (kind == 2) arena += mb;
			else if (kind == 4) file += mb; else if (kind == 5) stack += mb; else anon += mb;
		}
	}
	if (f) fclose(f);
	printf("    baseline: mallinfo in_use=%.1f free_in_arenas=%.1f mmap_chunks=%.1f MB | "
	       "RSS [heap]=%.1f thread-arenas=%.1f other-anon=%.1f stack=%.1f file=%.1f MB\n",
	       mi.uordblks / 1048576.0, mi.fordblks / 1048576.0, mi.hblkhd / 1048576.0,
	       heap, arena, anon, stack, file);
}

static double now_ms(void)
{
	struct timespec t;
	clock_gettime(CLOCK_MONOTONIC, &t);
	return t.tv_sec * 1e3 + t.tv_nsec / 1e6;
}

int main(int argc, char **argv)
{
	if (argc != 3) { fprintf(stderr, "usage: %s <video> <seq>\n", argv[0]); return 1; }
	int trim = getenv("MEM_TRIM") && atoi(getenv("MEM_TRIM"));
	int dbg = getenv("MEM_DEBUG") && atoi(getenv("MEM_DEBUG"));
	av_log_set_level(AV_LOG_ERROR);

	FILE *f = fopen(argv[1], "rb");
	fseek(f, 0, SEEK_END);
	size_t sz = ftell(f);
	fseek(f, 0, SEEK_SET);
	uint8_t *file = malloc(sz);
	if (fread(file, 1, sz, f) != sz) return 2;
	fclose(f);

	cpu_set_t set;
	CPU_ZERO(&set); CPU_SET(1, &set); CPU_SET(17, &set);
	pthread_attr_t at;
	pthread_attr_init(&at);
	pthread_attr_setaffinity_np(&at, sizeof(set), &set);

	printf("# input=%s (%.1f MB) trim=%d\n", argv[1], sz / 1048576.0, trim);
	printf("%-3s %-5s | %9s %9s %9s | %9s | %9s %8s\n", "#", "mode", "rss_entry", "vmhwm", "heapΔ(RSS)", "inuseΔ", "payload", "mi_ms");
	char *seq = strdup(argv[2]), *save = NULL;
	int idx = 0;
	for (char *t = strtok_r(seq, ",", &save); t; t = strtok_r(NULL, ",", &save)) {
		struct job j = { file, sz, strcmp(t, "raw") ? atoi(t + 1) : -1, 0, 0, 0, 0 };
		double tm = 0;
		if (trim) { double a = now_ms(); malloc_trim(0); tm = now_ms() - a; }
		if (dbg) debug_baseline();
		reset_peak();
		long entry = status_kb("VmRSS:");
		size_t inuse0 = inuse_bytes();
		pthread_t th;
		pthread_create(&th, &at, worker, &j);
		pthread_join(th, NULL);
		long hwm = status_kb("VmHWM:");
		printf("%-3d %-5s | %9.0f %9.0f %9.0f | %9.1f | %9.1f %8.2f\n", ++idx, t,
		       entry / 1024.0, hwm / 1024.0, (hwm - entry) / 1024.0,
		       ((double)j.inuse_end - (double)inuse0) / 1048576.0, j.payload / 1048576.0, j.mallinfo_ms);
		(void)tm;
		fflush(stdout);
	}
	return 0;
}
