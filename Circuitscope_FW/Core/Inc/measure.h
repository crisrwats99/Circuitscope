/**
 * @file    measure.h
 * @brief   Timed ADC capture for the SAMPLE command.
 *
 * Stage 2: stub only (replies that capture is not built yet).
 * Stage 3: TIM2-triggered ADC + DMA capture, then DATA / DONE replies.
 */
#ifndef MEASURE_H
#define MEASURE_H

#include <stdint.h>

/**
 * @brief Capture and send samples. Arguments are already validated by
 *        protocol.c, so this function can trust them.
 * @param ch_index  indices into g_channels[], in the order the PC asked
 * @param n_ch      1 or 2
 * @param rate_hz   requested sample rate, 1..MAX_RATE_HZ
 * @param n         samples per channel, 1..MAX_POINTS
 */
void measure_run(const uint8_t *ch_index, uint8_t n_ch, uint32_t rate_hz, uint32_t n);

#endif /* MEASURE_H */
