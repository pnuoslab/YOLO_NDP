/*
 * ndp_cv.cpp — NDP 전처리의 OpenCV 백엔드
 *
 * ──────────────────────────────────────────────────────────────
 * 왜 필요한가
 * ──────────────────────────────────────────────────────────────
 * host baseline(Ultralytics)은 프레임을 두 단계로 얻는다.
 *
 *   cv2.VideoCapture.retrieve()   YUV → 전해상도 BGR24
 *   LetterBox.__call__()          전해상도 BGR24 → imgsz
 *
 * 기존 SPDK 구현은 sws_scale 한 번으로 YUV를 곧바로 imgsz RGB24로
 * 만든다(융합). 두 경로는 중간 산물이 다르므로 단계별 비교가
 * 성립하지 않는다. 이 파일은 baseline과 같은 2단계 구성을
 * storage 측에서도 제공한다.
 *
 *   ndp_cv_yuv2bgr   ← retrieve() 대응
 *   ndp_cv_letterbox ← LetterBox() 대응
 *
 * ──────────────────────────────────────────────────────────────
 * 계측 공정성에 대한 주의
 * ──────────────────────────────────────────────────────────────
 * 1) OpenCV의 VideoCapture(FFmpeg 백엔드)는 내부적으로 sws_scale로
 *    BGR24 변환을 수행한다. 즉 "retrieve와 구현까지 동일한" 대조군은
 *    backend=swscale의 staged 경로이고, 이 파일의 cvtColor 경로는
 *    "순수 OpenCV 구성" 대조군이다.
 * 2) cvtColor(COLOR_YUV2BGR_I420)는 CV_8U 전용이다. yuv420p10le 등
 *    10-bit 입력은 지원하지 않으므로 호출부(ctrlr_bdev.c)가 사전에
 *    포맷을 확인하고 swscale로 폴백한다.
 * 3) cvtColor는 연속(I420 planar contiguous) 메모리를 요구한다.
 *    AVFrame의 plane이 이미 연속이면 복사 없이 래핑하고, linesize
 *    패딩이 있으면 스테이징 버퍼로 1회 복사한다(핸들에 상주,
 *    프레임마다 재할당하지 않음). 이 복사는 VideoCapture 내부에는
 *    없는 비용이므로, 발생 여부가 로그로 남는다.
 * 4) letterbox의 패딩 채우기는 baseline(LetterBox가 매 호출
 *    copyMakeBorder로 전체 배열을 씀)과 같게 매 호출 수행한다.
 *    swscale staged 경로(1회 채움)와는 이 점이 다르다.
 *
 * ──────────────────────────────────────────────────────────────
 * 빌드
 * ──────────────────────────────────────────────────────────────
 *   g++ -O2 -fPIC -c ndp_cv.cpp $(pkg-config --cflags opencv4)
 *   링크: $(pkg-config --libs opencv4) 중 -lopencv_core -lopencv_imgproc
 *   OpenCV 없이 빌드하려면 이 파일을 제외하고 ctrlr_bdev.c 를
 *   -DNDP_NO_OPENCV 로 컴파일하면 된다(스텁이 -1을 반환해 sws 폴백).
 */

#include <opencv2/core.hpp>
#include <opencv2/imgproc.hpp>
#include <cstdint>
#include <cstring>
#include <cstdio>
#include <algorithm>

struct ndp_cv_state {
    cv::Mat i420_staging;   /* (h*3/2, w) CV_8UC1, 비연속 plane용 */
    bool    copy_warned = false;
};

extern "C" int
ndp_cv_yuv2bgr(void **handle,
               const uint8_t *const data[4],
               const int linesize[4],
               int w, int h, uint8_t *dst_bgr)
{
    if (!handle || !data || !linesize || !dst_bgr || w <= 0 || h <= 0)
        return -1;
    if ((w & 1) || (h & 1))          /* I420은 짝수 크기 전제 */
        return -1;

    ndp_cv_state *st = static_cast<ndp_cv_state *>(*handle);
    if (!st) {
        st = new (std::nothrow) ndp_cv_state();
        if (!st) return -1;
        *handle = st;
    }

    const int cw = w / 2, ch = h / 2;

    /* plane이 이미 연속 I420 배치인지 확인 (복사 회피) */
    const bool contiguous =
        linesize[0] == w && linesize[1] == cw && linesize[2] == cw &&
        data[1] == data[0] + (size_t)w * h &&
        data[2] == data[1] + (size_t)cw * ch;

    cv::Mat i420;
    if (contiguous) {
        i420 = cv::Mat(h * 3 / 2, w, CV_8UC1,
                       const_cast<uint8_t *>(data[0]), (size_t)w);
    } else {
        if (!st->copy_warned) {
            std::fprintf(stderr,
                "[NDP-cv] non-contiguous planes (ls=%d,%d,%d) — "
                "staging copy per frame\n",
                linesize[0], linesize[1], linesize[2]);
            st->copy_warned = true;
        }
        st->i420_staging.create(h * 3 / 2, w, CV_8UC1);
        uint8_t *p = st->i420_staging.ptr<uint8_t>(0);
        for (int y = 0; y < h; y++)
            std::memcpy(p + (size_t)y * w, data[0] + (size_t)y * linesize[0], w);
        p += (size_t)w * h;
        for (int y = 0; y < ch; y++)
            std::memcpy(p + (size_t)y * cw, data[1] + (size_t)y * linesize[1], cw);
        p += (size_t)cw * ch;
        for (int y = 0; y < ch; y++)
            std::memcpy(p + (size_t)y * cw, data[2] + (size_t)y * linesize[2], cw);
        i420 = st->i420_staging;
    }

    /* dst를 래핑 — 크기/타입이 일치하므로 cvtColor의 create()는
     * 재할당하지 않고 제자리에 기록한다 */
    cv::Mat dst(h, w, CV_8UC3, dst_bgr, (size_t)w * 3);
    try {
        cv::cvtColor(i420, dst, cv::COLOR_YUV2BGR_I420);
    } catch (const cv::Exception &e) {
        std::fprintf(stderr, "[NDP-cv] cvtColor failed: %s\n", e.what());
        return -1;
    }
    return 0;
}

/*
 * 전해상도 BGR24 → net_w×net_h letterbox RGB24
 *
 * baseline LetterBox와 같은 구성: min-ratio 축소(INTER_LINEAR) 후
 * 중앙 패딩. 기하(truncation, 중앙 정렬)와 패딩 값(pad_val)은
 * ctrlr_bdev.c의 swscale 경로와 동일하게 맞춰 경로 간 출력 픽셀
 * 일관성을 유지한다. 마지막 BGR→RGB 스왑은 JPEG 인코더(JCS_RGB)
 * 입력 규격을 위한 것으로, net 해상도에서의 element-wise 연산이라
 * 비용이 작고 in-place로 안전하다.
 */
extern "C" int
ndp_cv_letterbox(const uint8_t *bgr, int w, int h,
                 uint8_t *dst_rgb, int net_w, int net_h, int pad_val)
{
    if (!bgr || !dst_rgb || w <= 0 || h <= 0 || net_w <= 0 || net_h <= 0)
        return -1;

    const float r = std::min((float)net_w / w, (float)net_h / h);
    const int new_w = (int)(w * r);
    const int new_h = (int)(h * r);
    const int dx = (net_w - new_w) / 2;
    const int dy = (net_h - new_h) / 2;
    if (new_w <= 0 || new_h <= 0)
        return -1;

    cv::Mat src(h, w, CV_8UC3, const_cast<uint8_t *>(bgr), (size_t)w * 3);
    cv::Mat out(net_h, net_w, CV_8UC3, dst_rgb, (size_t)net_w * 3);

    try {
        out.setTo(cv::Scalar(pad_val, pad_val, pad_val));
        cv::Mat roi = out(cv::Rect(dx, dy, new_w, new_h));
        cv::resize(src, roi, roi.size(), 0, 0, cv::INTER_LINEAR);
        cv::cvtColor(out, out, cv::COLOR_BGR2RGB);
    } catch (const cv::Exception &e) {
        std::fprintf(stderr, "[NDP-cv] letterbox failed: %s\n", e.what());
        return -1;
    }
    return 0;
}

extern "C" void
ndp_cv_handle_free(void **handle)
{
    if (!handle || !*handle) return;
    delete static_cast<ndp_cv_state *>(*handle);
    *handle = nullptr;
}