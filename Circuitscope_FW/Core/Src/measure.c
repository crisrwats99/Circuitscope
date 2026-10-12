/**
 * @file    measure.c
 * @brief   SAMPLE capture.
 *
 * STAGE 2 STUB: the command has already been fully checked by protocol.c.
 * This stub just echoes what was understood so you can verify parsing in
 * PuTTY. Stage 3 replaces this file with the real TIM2 + ADC + DMA capture.
 */
#include "measure.h"
#include "channels.h"
#include "comms.h"
#include <stdio.h>

void measure_run(const uint8_t *ch_index, uint8_t n_ch, uint32_t rate_hz, uint32_t n)
{
    char msg[160];

    snprintf(msg, sizeof msg,
             "ERR BADCMD SAMPLE understood (%s%s%s at %lu Hz, %lu points) "
             "but capture is not built yet - added in Stage 3",
             g_channels[ch_index[0]].name,
             (n_ch == 2u) ? "," : "",
             (n_ch == 2u) ? g_channels[ch_index[1]].name : "",
             (unsigned long)rate_hz, (unsigned long)n);
    comms_send_line(msg);
}
