/**
 * @file    channels.h
 * @brief   Maps protocol channel names (P1..P7, I1, I2) to ADC inputs.
 *
 * The table itself lives in channels.c - THAT is the file to edit if a
 * channel name must read a different pin.
 */
#ifndef CHANNELS_H
#define CHANNELS_H

#include <stdint.h>

/* Which converter(s) can read a pin (channel_t.adc). */
#define CH_ADC1     1u   /* pin only connects to ADC1                          */
#define CH_ADC2     2u   /* pin only connects to ADC2                          */
#define CH_ADC_ANY  3u   /* pin connects to both (PA0, PA1: "ADC12_INx"); the  */
                         /* firmware picks whichever ADC is free so that two   */
                         /* channels can be sampled at exactly the same time   */

/** One measurement input. */
typedef struct {
    const char *name;         /* name used in SAMPLE commands, e.g. "P1"       */
    uint8_t     adc;          /* CH_ADC1, CH_ADC2 or CH_ADC_ANY                */
    uint32_t    hal_channel;  /* HAL channel constant, e.g. ADC_CHANNEL_1      */
} channel_t;

extern const channel_t g_channels[];   /* the table, in channels.c */
extern const uint8_t   g_channel_count; /* number of rows in the table */

/**
 * @brief  Look up a channel by its exact (case-sensitive) name.
 * @return Index into g_channels[], or -1 if the name is unknown.
 */
int channels_find(const char *name);

#endif /* CHANNELS_H */
