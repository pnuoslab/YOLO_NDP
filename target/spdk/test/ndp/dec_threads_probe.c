/*
 * dec_threads_probe - how many threads does the NDP worker's decoder really
 * create?
 *
 * Mirrors ndp_preprocess_worker()'s decoder setup in ctrlr_bdev.c exactly:
 * avcodec_find_decoder(codecpar->codec_id), thread_count = N,
 * thread_type = FF_THREAD_FRAME, avcodec_open2(). It runs that in a pthread
 * created with the same affinity mask the target gives its worker (CPUs 2-15
 * by default), so libavcodec's auto sizing sees the same CPU count it sees in
 * production.
 *
 * It reports two numbers:
 *   - approx: the target's two-sample estimate (Threads just before the
 *     decode loop and just after it, max of the two). This is what
 *     [NDP-MEASURE] thr_peak logs.
 *   - true:   the peak seen by a separate thread polling /proc every 1 ms
 *     for the whole decode, used to check the approximation.
 * Both are reported as decoder threads, i.e. the process thread count minus
 * the count taken just before avcodec_open2().
 *
 * usage: dec_threads_probe <video> <thread_count> <cpulist|all> [max_frames]
 *        cpulist like "2-15" or "1,17"; "all" leaves affinity untouched.
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>
#include <sched.h>
#include <unistd.h>
#include <stdatomic.h>
#include <libavformat/avformat.h>
#include <libavcodec/avcodec.h>
#include <libavutil/cpu.h>

static long proc_threads(void)
{
	FILE *f = fopen("/proc/self/status", "r");
	char l[256];
	long v = -1;
	while (f && fgets(l, sizeof(l), f)) {
		if (!strncmp(l, "Threads:", 8)) { sscanf(l + 8, " %ld", &v); break; }
	}
	if (f) fclose(f);
	return v;
}

static atomic_int  g_stop;
static atomic_long g_true_peak;

static void *poller(void *a)
{
	(void)a;
	while (!atomic_load(&g_stop)) {
		long t = proc_threads();
		if (t > atomic_load(&g_true_peak)) atomic_store(&g_true_peak, t);
		usleep(1000);
	}
	return NULL;
}

struct job {
	const char *path;
	int thread_count;
	int max_frames;
	/* outputs */
	const char *dec_name;
	int threads_set;
	int cpu_count_seen;
	long base, before_loop, after_loop;
	int frames;
};

static void *worker(void *arg)
{
	struct job *j = arg;
	AVFormatContext *fmt = NULL;
	if (avformat_open_input(&fmt, j->path, NULL, NULL) < 0) { fprintf(stderr, "open failed\n"); exit(2); }
	avformat_find_stream_info(fmt, NULL);
	int vi = av_find_best_stream(fmt, AVMEDIA_TYPE_VIDEO, -1, -1, NULL, 0);

	/* ---- identical to ctrlr_bdev.c ---- */
	const AVCodec *codec = avcodec_find_decoder(fmt->streams[vi]->codecpar->codec_id);
	AVCodecContext *cc = avcodec_alloc_context3(codec);
	avcodec_parameters_to_context(cc, fmt->streams[vi]->codecpar);
	cc->thread_count = j->thread_count;
	cc->thread_type  = FF_THREAD_FRAME;

	j->cpu_count_seen = av_cpu_count();
	j->base = proc_threads();          /* includes main, this worker, poller */
	if (avcodec_open2(cc, codec, NULL) < 0) { fprintf(stderr, "avcodec_open2 failed\n"); exit(2); }
	j->dec_name    = codec->name;
	j->threads_set = cc->thread_count;
	/* ----------------------------------- */

	j->before_loop = proc_threads();
	AVPacket *pkt = av_packet_alloc();
	AVFrame *frm = av_frame_alloc();
	int eof = 0;
	while (!eof && (j->max_frames <= 0 || j->frames < j->max_frames)) {
		int got = av_read_frame(fmt, pkt) >= 0;
		if (!got) eof = 1;
		if (got && pkt->stream_index != vi) { av_packet_unref(pkt); continue; }
		avcodec_send_packet(cc, got ? pkt : NULL);
		if (got) av_packet_unref(pkt);
		while (avcodec_receive_frame(cc, frm) == 0) j->frames++;
	}
	j->after_loop = proc_threads();

	av_frame_free(&frm);
	av_packet_free(&pkt);
	avcodec_free_context(&cc);
	avformat_close_input(&fmt);
	return NULL;
}

int main(int argc, char **argv)
{
	if (argc < 4) { fprintf(stderr, "usage: %s <video> <thread_count> <cpulist|all> [max_frames]\n", argv[0]); return 1; }
	struct job j = { .path = argv[1], .thread_count = atoi(argv[2]),
	                 .max_frames = argc > 4 ? atoi(argv[4]) : 0 };
	av_log_set_level(AV_LOG_ERROR);

	pthread_attr_t attr;
	pthread_attr_init(&attr);
	int ncpu_mask = -1;
	if (strcmp(argv[3], "all") != 0) {
		cpu_set_t set;
		CPU_ZERO(&set);
		char *s = strdup(argv[3]), *tok, *save = NULL;
		for (tok = strtok_r(s, ",", &save); tok; tok = strtok_r(NULL, ",", &save)) {
			int a, b;
			if (sscanf(tok, "%d-%d", &a, &b) == 2) { for (int c = a; c <= b; c++) CPU_SET(c, &set); }
			else CPU_SET(atoi(tok), &set);
		}
		free(s);
		ncpu_mask = CPU_COUNT(&set);
		pthread_attr_setaffinity_np(&attr, sizeof(set), &set);
	}

	pthread_t pt, wt;
	atomic_store(&g_true_peak, 0);
	pthread_create(&pt, NULL, poller, NULL);
	usleep(5000);
	pthread_create(&wt, &attr, worker, &j);
	pthread_join(wt, NULL);
	atomic_store(&g_stop, 1);
	pthread_join(pt, NULL);

	long approx = (j.before_loop > j.after_loop ? j.before_loop : j.after_loop) - j.base;
	long truep  = atomic_load(&g_true_peak) - j.base;
	printf("dec=%-9s req=%-3d mask=%-4s av_cpu_count=%-3d set=%-3d dec_threads: approx=%-3ld true=%-3ld frames=%d\n",
	       j.dec_name, j.thread_count, argv[3], j.cpu_count_seen, j.threads_set,
	       approx, truep, j.frames);
	(void)ncpu_mask;
	return 0;
}
