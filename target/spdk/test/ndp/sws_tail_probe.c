/*
 * sws_tail_probe - which bytes does a same-size yuv420p -> rgb24 sws_scale
 * leave unwritten, and does it write past the end of the buffer?
 *
 * This is exactly the conversion the darknet path in ndp_frame_to_letterbox_rgb()
 * asks swscale for (source size -> source size, SWS_BILINEAR). The output
 * buffer is filled with two different sentinels in turn; a byte holding the
 * sentinel both times was never written.
 *
 * Run it under Valgrind to see out-of-bounds stores:
 *   valgrind -q --error-exitcode=9 ./sws_tail_probe 1174 880 1176
 *
 * usage: sws_tail_probe <w> <h> <stride_px>     (TAILPAD=<bytes> adds slack)
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <libswscale/swscale.h>
#include <libavutil/imgutils.h>

int main(int argc, char **argv)
{
	if (argc != 4) {
		fprintf(stderr, "usage: %s <w> <h> <stride_px>\n", argv[0]);
		return 1;
	}
	int w = atoi(argv[1]), h = atoi(argv[2]), stride_px = atoi(argv[3]);
	size_t tailpad = getenv("TAILPAD") ? (size_t)atoi(getenv("TAILPAD")) : 0;

	uint8_t *src[4];
	int ls[4];
	av_image_alloc(src, ls, w, h, AV_PIX_FMT_YUV420P, 32);
	for (int p = 0; p < 3; p++)
		memset(src[p], 90 + p * 40, (size_t)ls[p] * (p ? (h + 1) / 2 : h));

	struct SwsContext *c = sws_getContext(w, h, AV_PIX_FMT_YUV420P, w, h, AV_PIX_FMT_RGB24,
					      SWS_BILINEAR, NULL, NULL, NULL);
	int stride = 3 * stride_px;
	size_t n = (size_t)stride * h + tailpad;
	uint8_t *a = malloc(n), *b = malloc(n);
	memset(a, 0xAB, n);
	memset(b, 0xCD, n);
	uint8_t *da[4] = { a }, *db[4] = { b };
	int dl[4] = { stride };
	sws_scale(c, (const uint8_t * const *)src, ls, 0, h, da, dl);
	sws_scale(c, (const uint8_t * const *)src, ls, 0, h, db, dl);

	int cmin = 1 << 30, cmax = -1, rows = 0;
	long cnt = 0;
	for (int y = 0; y < h; y++) {
		int hit = 0;
		for (int x = 0; x < w; x++)
			for (int k = 0; k < 3; k++) {
				size_t i = (size_t)y * stride + x * 3 + k;
				if (a[i] == 0xAB && b[i] == 0xCD) {
					cnt++; hit = 1;
					if (x < cmin) cmin = x;
					if (x > cmax) cmax = x;
				}
			}
		rows += hit;
	}
	printf("w=%d h=%d stride=%dpx tailpad=%zu (w%%8=%d): unwritten bytes=%ld",
	       w, h, stride_px, tailpad, w % 8, cnt);
	if (cnt)
		printf(" cols %d..%d (%d px) in %d/%d rows", cmin, cmax, cmax - cmin + 1, rows, h);
	printf("\n");

	sws_freeContext(c);
	av_freep(&src[0]);
	free(a);
	free(b);
	return 0;
}
