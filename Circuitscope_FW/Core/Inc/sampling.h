/**
 * @file    sampling.h
 * @brief   Hardware-independent parts of a SAMPLE capture:
 *            1. timer maths  - which divider gives the requested rate, and
 *                              the rate actually achieved ("honest rate")
 *            2. capture plan - which ADC reads which channel, where DMA
 *                              writes, and how to read the data back out
 *            3. output       - formatting the DATA and DONE reply lines
 *
 * No HAL code here, so all of it is unit-tested on the PC
 * (Tests/test_sampling.c). measure.c uses it to drive the hardware.
 */
#ifndef SAMPLING_H
#define SAMPLING_H

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

/* TIM2 counts at the APB1 timer clock = SYSCLK = 170 MHz (APB1 prescaler /1). */
#define TIMER_CLOCK_HZ  170000000u

/* ------------------------------------------------------------------------ */
/*  1. Timer maths                                                          */
/* ------------------------------------------------------------------------ */

/**
 * @brief  Timer divider for a requested rate: round(TIMER_CLOCK_HZ / rate).
 *         TIM2 is 32-bit, so even 1 Hz (divider 170 000 000) needs no prescaler.
 *         The timer's auto-reload register gets (divider - 1).
 */
uint32_t sampling_divider(uint32_t rate_hz);

/** Rate the timer really produces with this divider, in milli-hertz (rounded). */
uint64_t sampling_actual_rate_mhz(uint32_t divider);

/** Write "DONE <actual_rate_hz>" with 3 decimals, e.g. "DONE 29998.235". */
void sampling_format_done(char *out, size_t size, uint32_t divider);

/** Expected capture duration in milliseconds (n samples at this divider). */
uint32_t sampling_duration_ms(uint32_t divider, uint32_t n);

/* ------------------------------------------------------------------------ */
/*  2. Capture plan                                                         */
/* ------------------------------------------------------------------------ */

/** What one ADC must do during the capture. */
typedef struct {
    bool     used;        /* this ADC takes part                                 */
    uint8_t  n_ranks;     /* channels it converts per trigger: 1 or 2 (scan)     */
    uint8_t  rank_req[2]; /* for each rank: which requested channel (0 or 1)     */
    uint32_t buf_offset;  /* first buffer element its DMA writes                 */
    uint32_t length;      /* DMA transfers = n * n_ranks                         */
} adc_job_t;

/** The full plan for one SAMPLE command. */
typedef struct {
    adc_job_t adc[2];         /* [0] = ADC1, [1] = ADC2                          */
    uint32_t  out_offset[2];  /* per requested channel: index of its 1st sample  */
    uint32_t  out_stride[2];  /* per requested channel: step between samples     */
    bool      simultaneous;   /* true if both channels are sampled at the same   */
                              /* instant (one on each ADC, same trigger)         */
} capture_plan_t;

/**
 * @brief  Decide how to capture.
 *  - 1 channel:                 the ADC that owns the pin.
 *  - 2 channels on different ADCs (or either can move): one per ADC, both
 *    started by the same timer pulse -> truly simultaneous.
 *  - 2 channels both fixed to the same ADC: that ADC converts both in turn
 *    (scan) -> second channel lags by one conversion (~1.41 us).
 * @param adc_code    channel_t.adc of each requested channel, in PC order
 * @param n_ch        1 or 2
 * @param n           samples per channel (1..MAX_POINTS)
 * @param buf_points  per-ADC buffer region size (MAX_POINTS); ADC2 writes
 *                    from element buf_points when both ADCs are used
 * @return false if the arguments are impossible (should never happen after
 *         protocol.c has validated them)
 */
bool sampling_plan(const uint8_t adc_code[], uint8_t n_ch, uint32_t n,
                   uint32_t buf_points, capture_plan_t *plan);

/* ------------------------------------------------------------------------ */
/*  3. DATA line output                                                     */
/* ------------------------------------------------------------------------ */

/** Function that transmits raw bytes (comms_send in the firmware). */
typedef void (*sampling_send_fn)(const char *data, size_t len);

/**
 * @brief  Send "DATA <n> c0,c1,...,c(n-1)\r\n" in small chunks, reading
 *         sample i from buf[offset + i*stride]. Chunking keeps RAM use tiny
 *         even for 4000 samples (~20 KB of text).
 */
void sampling_send_data(const uint16_t *buf, uint32_t offset, uint32_t stride,
                        uint32_t n, sampling_send_fn send);

#endif /* SAMPLING_H */
