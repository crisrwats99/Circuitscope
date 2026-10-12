/**
 * @file    sampling.c
 * @brief   Timer maths, capture planning and DATA/DONE formatting (see sampling.h).
 */
#include "sampling.h"
#include "channels.h"   /* CH_ADC1, CH_ADC2, CH_ADC_ANY */
#include <stdio.h>
#include <string.h>

/* ======================================================================== */
/*  1. Timer maths                                                          */
/* ======================================================================== */

uint32_t sampling_divider(uint32_t rate_hz)
{
    if (rate_hz == 0u) {
        return TIMER_CLOCK_HZ;                         /* never used: rate validated */
    }
    /* Round to nearest: (clock + rate/2) / rate */
    uint32_t div = (uint32_t)(((uint64_t)TIMER_CLOCK_HZ + rate_hz / 2u) / rate_hz);
    return (div == 0u) ? 1u : div;
}

uint64_t sampling_actual_rate_mhz(uint32_t divider)
{
    /* rate = clock / divider, scaled by 1000 and rounded */
    return ((uint64_t)TIMER_CLOCK_HZ * 1000u + divider / 2u) / divider;
}

void sampling_format_done(char *out, size_t size, uint32_t divider)
{
    uint64_t mhz = sampling_actual_rate_mhz(divider);
    /* Integer formatting only: printf with %f is not available in newlib-nano. */
    snprintf(out, size, "DONE %lu.%03lu",
             (unsigned long)(mhz / 1000u), (unsigned long)(mhz % 1000u));
}

uint32_t sampling_duration_ms(uint32_t divider, uint32_t n)
{
    /* n samples * divider ticks / (ticks per ms), rounded up */
    uint64_t ticks = (uint64_t)n * divider;
    uint64_t per_ms = TIMER_CLOCK_HZ / 1000u;
    return (uint32_t)((ticks + per_ms - 1u) / per_ms);
}

/* ======================================================================== */
/*  2. Capture plan                                                         */
/* ======================================================================== */

/** Configure one ADC job. */
static void set_job(adc_job_t *job, uint8_t n_ranks, uint8_t r0, uint8_t r1,
                    uint32_t buf_offset, uint32_t n)
{
    job->used        = true;
    job->n_ranks     = n_ranks;
    job->rank_req[0] = r0;
    job->rank_req[1] = r1;
    job->buf_offset  = buf_offset;
    job->length      = n * n_ranks;
}

bool sampling_plan(const uint8_t adc_code[], uint8_t n_ch, uint32_t n,
                   uint32_t buf_points, capture_plan_t *plan)
{
    memset(plan, 0, sizeof *plan);

    if (n == 0u || n > buf_points || n_ch < 1u || n_ch > 2u) {
        return false;
    }

    /* ---- One channel: use the ADC that owns the pin (ADC1 if either) ---- */
    if (n_ch == 1u) {
        uint8_t a = (adc_code[0] == CH_ADC2) ? 1u : 0u;   /* index 0=ADC1, 1=ADC2 */
        set_job(&plan->adc[a], 1u, 0u, 0u, 0u, n);
        plan->out_offset[0] = 0u;
        plan->out_stride[0] = 1u;
        plan->simultaneous  = true;                      /* trivially */
        return true;
    }

    /* ---- Two channels: work out which ADC reads each (0 = ADC1, 1 = ADC2) ---- */
    uint8_t c0 = adc_code[0], c1 = adc_code[1];
    int a0, a1;

    if (c0 == CH_ADC_ANY && c1 == CH_ADC_ANY) {          /* both flexible        */
        a0 = 0; a1 = 1;
    } else if (c0 == CH_ADC_ANY) {                       /* move ch0 to the other */
        a1 = (c1 == CH_ADC2) ? 1 : 0;
        a0 = 1 - a1;
    } else if (c1 == CH_ADC_ANY) {                       /* move ch1 to the other */
        a0 = (c0 == CH_ADC2) ? 1 : 0;
        a1 = 1 - a0;
    } else {                                             /* both fixed           */
        a0 = (c0 == CH_ADC2) ? 1 : 0;
        a1 = (c1 == CH_ADC2) ? 1 : 0;
    }

    if (a0 != a1) {
        /* Simultaneous: each ADC converts one channel on the same timer pulse.
         * ADC1 writes from element 0, ADC2 from element buf_points. */
        set_job(&plan->adc[a0], 1u, 0u, 0u, (a0 == 0) ? 0u : buf_points, n);
        set_job(&plan->adc[a1], 1u, 1u, 1u, (a1 == 0) ? 0u : buf_points, n);
        plan->out_offset[0] = plan->adc[a0].buf_offset;
        plan->out_offset[1] = plan->adc[a1].buf_offset;
        plan->out_stride[0] = 1u;
        plan->out_stride[1] = 1u;
        plan->simultaneous  = true;
    } else {
        /* Same ADC: scan two ranks per trigger. DMA stores them interleaved:
         * ch0, ch1, ch0, ch1, ... so each channel has stride 2. */
        set_job(&plan->adc[a0], 2u, 0u, 1u, 0u, n);
        plan->out_offset[0] = 0u;
        plan->out_offset[1] = 1u;
        plan->out_stride[0] = 2u;
        plan->out_stride[1] = 2u;
        plan->simultaneous  = false;
    }
    return true;
}

/* ======================================================================== */
/*  3. DATA line output                                                     */
/* ======================================================================== */

#define CHUNK_SIZE  128u   /* bytes per transmit; plenty for ~25 values */

/** Append the decimal digits of v to out; returns the number of characters. */
static size_t put_uint(char *out, uint32_t v)
{
    char   tmp[10];
    size_t k = 0, i;
    do {
        tmp[k++] = (char)('0' + (v % 10u));
        v /= 10u;
    } while (v != 0u);
    for (i = 0; i < k; i++) {
        out[i] = tmp[k - 1u - i];               /* digits were collected backwards */
    }
    return k;
}

void sampling_send_data(const uint16_t *buf, uint32_t offset, uint32_t stride,
                        uint32_t n, sampling_send_fn send)
{
    char   chunk[CHUNK_SIZE];
    size_t len;

    /* Header: "DATA <n> " */
    memcpy(chunk, "DATA ", 5u);
    len = 5u;
    len += put_uint(chunk + len, n);
    chunk[len++] = ' ';

    for (uint32_t i = 0; i < n; i++) {
        /* Worst case per value: 10 digits + comma; flush if it might not fit. */
        if (len + 12u > CHUNK_SIZE) {
            send(chunk, len);
            len = 0u;
        }
        len += put_uint(chunk + len, buf[offset + i * stride]);
        if (i + 1u < n) {
            chunk[len++] = ',';
        }
    }

    /* Line ending */
    if (len + 2u > CHUNK_SIZE) {
        send(chunk, len);
        len = 0u;
    }
    chunk[len++] = '\r';
    chunk[len++] = '\n';
    send(chunk, len);
}
