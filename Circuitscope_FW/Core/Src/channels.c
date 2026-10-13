/**
 * @file    channels.c
 * @brief   Channel table: which pin each protocol channel name reads.
 *
 * ========================================================================
 *  TO MOVE A CHANNEL TO A DIFFERENT PIN
 *  1. In the .ioc (CubeMX) set the new pin to its ADCx_INy function,
 *     Single-ended, and give it the channel's user label. Regenerate code.
 *  2. Edit that channel's row below: ADC number (1 or 2) and ADC_CHANNEL_y.
 *  3. Rebuild and flash. The PC and PuTTY keep using the same name (e.g. P3).
 *
 *  Which pin you READ during a test is chosen by the command you type,
 *  e.g.  SAMPLE P3 1000 10  reads PA4 (header A3). Nothing to edit for that.
 * ========================================================================
 *
 *  NUCLEO-G431KB note: PF0/PF1 (D7/D8) are NOT connected to the header on a
 *  stock board (solder bridges SB8/SB11 are open), so only 7 inputs exist.
 */
#include "channels.h"
#include <string.h>

#ifdef HOST_TEST
/* PC unit tests: HAL headers are not available, so the HAL constant is
 * replaced by 0. The macro argument is never expanded, so it need not exist. */
#define CH(name, adc, hal_ch)  { (name), (adc), 0u }
#else
#include "adc.h"   /* brings in the HAL ADC_CHANNEL_x constants */
#define CH(name, adc, hal_ch)  { (name), (adc), (hal_ch) }
#endif

/* Order here is the order listed in error messages. */
/* ADC column: CH_ADC1 / CH_ADC2 = pin wired to that converter only;
 * CH_ADC_ANY = pin wired to both (the datasheet calls it ADC12_INx). */
const channel_t g_channels[] = {
    /*  name   ADC          HAL channel          pin    Nucleo header */
    CH("P1",   CH_ADC_ANY,  ADC_CHANNEL_1),   /* PA0    A0  (ADC12_IN1) */
    CH("P2",   CH_ADC_ANY,  ADC_CHANNEL_2),   /* PA1    A1  (ADC12_IN2) */
    CH("P3",   CH_ADC2,     ADC_CHANNEL_17),  /* PA4    A3 */
    CH("P4",   CH_ADC2,     ADC_CHANNEL_13),  /* PA5    A4 */
    CH("P5",   CH_ADC2,     ADC_CHANNEL_3),   /* PA6    A5 */
    CH("I1",   CH_ADC1,     ADC_CHANNEL_15),  /* PB0    D3  (current sense 1) */
    CH("I2",   CH_ADC2,     ADC_CHANNEL_4),   /* PA7    A6  (current sense 2) */
};

const uint8_t g_channel_count = (uint8_t)(sizeof g_channels / sizeof g_channels[0]);

/** Exact, case-sensitive lookup ("P1" matches, "p1" does not). */
int channels_find(const char *name)
{
    for (uint8_t i = 0; i < g_channel_count; i++) {
        if (strcmp(g_channels[i].name, name) == 0) {
            return (int)i;
        }
    }
    return -1;
}
