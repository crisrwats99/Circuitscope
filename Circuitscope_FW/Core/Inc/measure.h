/**
 * @file    measure.h
 * @brief   Timed ADC capture for the SAMPLE command (TIM2 -> ADC1/ADC2 -> DMA).
 */
#ifndef MEASURE_H
#define MEASURE_H

#include <stdint.h>

/**
 * @brief Calibrate both ADCs. Call once at start-up, after the CubeMX
 *        MX_ADCx_Init() calls and before the first SAMPLE.
 *        (This is the chip's internal offset calibration - not the PC's
 *        two-point calibration, which stays on the PC.)
 */
void measure_init(void);

/**
 * @brief Capture and send samples. Arguments are already validated by
 *        protocol.c, so this function can trust them.
 * @param ch_index  indices into g_channels[], in the order the PC asked
 * @param n_ch      1 or 2
 * @param rate_hz   requested sample rate, 1..MAX_RATE_HZ
 * @param n         samples per channel, 1..MAX_POINTS
 *
 * Replies: one "DATA <n> c0,c1,..." line per channel (in the order asked),
 * then "DONE <actual_rate_hz>". On a hardware failure: "ERR HWFAIL <message>".
 */
void measure_run(const uint8_t *ch_index, uint8_t n_ch, uint32_t rate_hz, uint32_t n);

#endif /* MEASURE_H */
