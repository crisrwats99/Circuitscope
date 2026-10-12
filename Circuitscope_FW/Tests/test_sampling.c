/**
 * @file    test_sampling.c
 * @brief   PC unit tests for sampling.c: timer maths, capture plan, DATA/DONE output.
 *
 * Build and run (from the project folder, with gcc installed):
 *   gcc -DHOST_TEST -ICore/Inc Tests/test_sampling.c Core/Src/sampling.c \
 *       Core/Src/channels.c -lm -o test_sampling
 *   ./test_sampling
 *
 * Wrapped in #ifdef HOST_TEST so it compiles to nothing inside CubeIDE.
 */
#ifdef HOST_TEST

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

#include "sampling.h"
#include "channels.h"

#define MAXP 4000u   /* = MAX_POINTS */

static int g_pass, g_fail;

static void check(int ok, const char *what, const char *detail)
{
    if (ok) { g_pass++; }
    else    { g_fail++; printf("FAIL: %s\n      %s\n", what, detail); }
}

/* ======================================================================== */
/*  1. Timer maths                                                          */
/* ======================================================================== */

static void test_divider(void)
{
    struct { uint32_t rate, div; } c[] = {
        { 100000u, 1700u },          /* max rate: exact          */
        {  10000u, 17000u },         /* exact                    */
        {  20000u, 8500u },          /* single-reading rate      */
        {  30000u, 5667u },          /* 5666.67 rounds up        */
        {      3u, 56666667u },      /* 56666666.67 rounds up    */
        {      1u, 170000000u },     /* min rate, fits 32 bits   */
    };
    char d[96];
    for (size_t i = 0; i < sizeof c / sizeof c[0]; i++) {
        uint32_t got = sampling_divider(c[i].rate);
        snprintf(d, sizeof d, "rate %lu: expected %lu, got %lu",
                 (unsigned long)c[i].rate, (unsigned long)c[i].div, (unsigned long)got);
        check(got == c[i].div, "divider", d);
    }
}

static void test_done_format(void)
{
    struct { uint32_t div; const char *s; } c[] = {
        { 17000u,     "DONE 10000.000" },
        { 1700u,      "DONE 100000.000" },
        { 5667u,      "DONE 29998.235" },     /* honest rate, not 30000 */
        { 170000000u, "DONE 1.000" },
        { 56666667u,  "DONE 3.000" },
        { 17001u,     "DONE 9999.412" },
    };
    char out[48], d[96];
    for (size_t i = 0; i < sizeof c / sizeof c[0]; i++) {
        sampling_format_done(out, sizeof out, c[i].div);
        snprintf(d, sizeof d, "expected '%s', got '%s'", c[i].s, out);
        check(strcmp(out, c[i].s) == 0, "DONE format", d);
    }
}

/* Every allowed rate: achieved rate within 0.03 % of the request, and the
 * DONE value matches clock/divider to within 0.0005 Hz. */
static void test_rate_sweep(void)
{
    double worst = 0.0; uint32_t worst_rate = 0; int bad_fmt = 0;
    char out[48];
    for (uint32_t r = 1; r <= 100000u; r++) {
        uint32_t div = sampling_divider(r);
        double actual = (double)TIMER_CLOCK_HZ / div;
        double err = fabs(actual - r) / r;
        if (err > worst) { worst = err; worst_rate = r; }
        sampling_format_done(out, sizeof out, div);
        if (fabs(atof(out + 5) - actual) > 0.0005) bad_fmt++;
    }
    char d[96];
    snprintf(d, sizeof d, "worst error %.5f %% at %lu Hz", worst * 100.0, (unsigned long)worst_rate);
    check(worst <= 0.0003, "all rates 1..100000 Hz within 0.03 %", d);
    snprintf(d, sizeof d, "%d rates formatted wrongly", bad_fmt);
    check(bad_fmt == 0, "DONE value matches clock/divider for every rate", d);
    printf("  info: worst rate error over 1..100000 Hz = %.5f %% (at %lu Hz)\n",
           worst * 100.0, (unsigned long)worst_rate);
}

static void test_duration(void)
{
    char d[64];
    uint32_t ms = sampling_duration_ms(sampling_divider(1000u), 4000u);
    snprintf(d, sizeof d, "got %lu", (unsigned long)ms);
    check(ms == 4000u, "4000 samples at 1 kHz = 4000 ms", d);
    ms = sampling_duration_ms(sampling_divider(1u), 4000u);
    snprintf(d, sizeof d, "got %lu", (unsigned long)ms);
    check(ms == 4000000u, "4000 samples at 1 Hz = 4 000 000 ms (no overflow)", d);
    ms = sampling_duration_ms(sampling_divider(20000u), 400u);
    snprintf(d, sizeof d, "got %lu", (unsigned long)ms);
    check(ms == 20u, "400 samples at 20 kHz = 20 ms", d);
}

/* ======================================================================== */
/*  2. Capture plan                                                         */
/* ======================================================================== */

static uint8_t code_of(const char *name) { return g_channels[channels_find(name)].adc; }

/** Plan a capture for 1 or 2 named channels. */
static capture_plan_t plan_for(const char *a, const char *b, uint32_t n, int *ok)
{
    capture_plan_t p;
    uint8_t codes[2] = { code_of(a), b ? code_of(b) : 0u };
    *ok = sampling_plan(codes, b ? 2u : 1u, n, MAXP, &p);
    return p;
}

/** Which ADC (1 or 2) reads requested channel k? 0 if none. */
static int adc_reading(const capture_plan_t *p, int k)
{
    for (int a = 0; a < 2; a++) {
        if (!p->adc[a].used) continue;
        for (int r = 0; r < p->adc[a].n_ranks; r++)
            if (p->adc[a].rank_req[r] == k) return a + 1;
    }
    return 0;
}

static void test_plan(void)
{
    int ok; capture_plan_t p; char d[128];

    /* single channels */
    p = plan_for("P1", NULL, 400, &ok);
    check(ok && adc_reading(&p, 0) == 1 && !p.adc[1].used && p.adc[0].length == 400,
          "P1 alone -> ADC1, 400 transfers", "");
    p = plan_for("P3", NULL, 400, &ok);
    check(ok && adc_reading(&p, 0) == 2 && !p.adc[0].used && p.out_offset[0] == 0,
          "P3 alone -> ADC2, data at offset 0", "");
    p = plan_for("I1", NULL, 10, &ok);
    check(ok && adc_reading(&p, 0) == 1, "I1 alone -> ADC1", "");

    /* simultaneous pairs: one channel per ADC */
    struct { const char *a, *b; int adc_a, adc_b; } sim[] = {
        { "P1", "P2", 1, 2 },   /* both flexible            */
        { "P1", "I1", 2, 1 },   /* P1 moves to ADC2         */
        { "I1", "P1", 1, 2 },
        { "P3", "P1", 2, 1 },   /* P1 moves to ADC1         */
        { "P6", "P3", 1, 2 },   /* fixed, different ADCs    */
        { "I2", "I1", 2, 1 },   /* both current inputs      */
        { "P2", "P7", 1, 2 },
    };
    for (size_t i = 0; i < sizeof sim / sizeof sim[0]; i++) {
        p = plan_for(sim[i].a, sim[i].b, 2000, &ok);
        int ga = adc_reading(&p, 0), gb = adc_reading(&p, 1);
        snprintf(d, sizeof d, "%s on ADC%d (want %d), %s on ADC%d (want %d), simultaneous=%d",
                 sim[i].a, ga, sim[i].adc_a, sim[i].b, gb, sim[i].adc_b, p.simultaneous);
        check(ok && ga == sim[i].adc_a && gb == sim[i].adc_b && p.simultaneous &&
              p.out_stride[0] == 1 && p.out_stride[1] == 1 &&
              p.out_offset[0] == (ga == 1 ? 0u : MAXP) && p.out_offset[1] == (gb == 1 ? 0u : MAXP) &&
              p.adc[0].length == 2000 && p.adc[1].length == 2000,
              "simultaneous pair", d);
    }

    /* same-ADC pairs: scan with interleaved data */
    struct { const char *a, *b; int adc; } scan[] = {
        { "P3", "P4", 2 }, { "P5", "I2", 2 }, { "I2", "P7", 2 }, { "P6", "I1", 1 },
    };
    for (size_t i = 0; i < sizeof scan / sizeof scan[0]; i++) {
        p = plan_for(scan[i].a, scan[i].b, 1000, &ok);
        adc_job_t *j = &p.adc[scan[i].adc - 1];
        snprintf(d, sizeof d, "%s,%s: used=%d ranks=%d len=%lu", scan[i].a, scan[i].b,
                 j->used, j->n_ranks, (unsigned long)j->length);
        check(ok && j->used && j->n_ranks == 2 && j->rank_req[0] == 0 && j->rank_req[1] == 1 &&
              j->length == 2000 && !p.adc[2 - scan[i].adc].used && !p.simultaneous &&
              p.out_offset[0] == 0 && p.out_offset[1] == 1 &&
              p.out_stride[0] == 2 && p.out_stride[1] == 2,
              "same-ADC pair -> scan, interleaved", d);
    }

    /* buffer limits: largest captures fit the 2 x 4000 buffer */
    p = plan_for("P3", "P4", MAXP, &ok);
    check(ok && p.adc[1].buf_offset + p.adc[1].length <= 2 * MAXP, "scan 2 x 4000 fits buffer", "");
    p = plan_for("P1", "P3", MAXP, &ok);
    check(ok && p.adc[1].buf_offset + p.adc[1].length <= 2 * MAXP &&
          p.adc[0].buf_offset + p.adc[0].length <= MAXP, "dual 2 x 4000 fits buffer, no overlap", "");

    /* impossible requests rejected */
    uint8_t codes[2] = { CH_ADC1, CH_ADC2 };
    check(!sampling_plan(codes, 2, 0, MAXP, &p), "n = 0 rejected", "");
    check(!sampling_plan(codes, 2, MAXP + 1, MAXP, &p), "n > MAX_POINTS rejected", "");
    check(!sampling_plan(codes, 3, 10, MAXP, &p), "3 channels rejected", "");
}

/* ======================================================================== */
/*  3. DATA output                                                          */
/* ======================================================================== */

static char   g_out[64000];
static size_t g_out_len;
static size_t g_max_chunk;

static void fake_send(const char *data, size_t len)
{
    memcpy(g_out + g_out_len, data, len);
    g_out_len += len;
    if (len > g_max_chunk) g_max_chunk = len;
    g_out[g_out_len] = '\0';
}

static void reset_out(void) { g_out_len = 0; g_out[0] = '\0'; g_max_chunk = 0; }

/** Parse a DATA line back into values; returns count, or -1 if malformed. */
static long parse_data(const char *s, uint16_t *vals, long max)
{
    if (strncmp(s, "DATA ", 5) != 0) return -1;
    char *p; long n = strtol(s + 5, &p, 10);
    if (*p != ' ') return -1;
    p++;
    for (long i = 0; i < n && i < max; i++) {
        vals[i] = (uint16_t)strtol(p, &p, 10);
        if (i + 1 < n) { if (*p != ',') return -1; p++; }
    }
    return (strcmp(p, "\r\n") == 0) ? n : -1;
}

static void test_data_output(void)
{
    uint16_t buf[2 * MAXP];
    static uint16_t back[MAXP];
    char d[128];

    /* small exact example */
    uint16_t small[] = { 0, 4095, 2048 };
    reset_out();
    sampling_send_data(small, 0, 1, 3, fake_send);
    check(strcmp(g_out, "DATA 3 0,4095,2048\r\n") == 0, "DATA 3 values exact", g_out);

    reset_out();
    sampling_send_data(small, 1, 1, 1, fake_send);
    check(strcmp(g_out, "DATA 1 4095\r\n") == 0, "DATA single value", g_out);

    /* 4000 values, full 0..4095 range: round trip */
    for (uint32_t i = 0; i < MAXP; i++) buf[i] = (uint16_t)((i * 37u) % 4096u);
    reset_out();
    sampling_send_data(buf, 0, 1, MAXP, fake_send);
    long n = parse_data(g_out, back, MAXP);
    int same = (n == (long)MAXP) && memcmp(buf, back, MAXP * sizeof(uint16_t)) == 0;
    snprintf(d, sizeof d, "parsed n=%ld, line length %zu, largest chunk %zu", n, g_out_len, g_max_chunk);
    check(same, "4000-value DATA line parses back exactly", d);
    check(g_max_chunk <= 128, "chunks never exceed 128 bytes", d);
    printf("  info: 4000-sample DATA line = %zu bytes (~%.1f s at 115200 baud)\n",
           g_out_len, g_out_len / 11520.0);

    /* interleaved scan buffer: ch0 at even, ch1 at odd elements */
    for (uint32_t i = 0; i < 2 * 500; i++) buf[i] = (i % 2 == 0) ? (uint16_t)(1000 + i / 2) : (uint16_t)(3000 + i / 2);
    reset_out();
    sampling_send_data(buf, 1, 2, 500, fake_send);
    n = parse_data(g_out, back, MAXP);
    int ok = (n == 500);
    for (int i = 0; ok && i < 500; i++) ok = (back[i] == 3000 + i);
    check(ok, "stride 2 / offset 1 extracts channel 2 of a scan", "");

    /* dual buffer: ADC2 region starts at MAXP */
    buf[MAXP] = 111; buf[MAXP + 1] = 222;
    reset_out();
    sampling_send_data(buf, MAXP, 1, 2, fake_send);
    check(strcmp(g_out, "DATA 2 111,222\r\n") == 0, "reads ADC2 region at offset MAX_POINTS", g_out);
}

int main(void)
{
    test_divider();
    test_done_format();
    test_rate_sweep();
    test_duration();
    test_plan();
    test_data_output();
    printf("\n%d passed, %d failed\n", g_pass, g_fail);
    return (g_fail == 0) ? 0 : 1;
}

#endif /* HOST_TEST */
