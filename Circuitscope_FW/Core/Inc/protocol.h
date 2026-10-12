/**
 * @file    protocol.h
 * @brief   Circuitscope serial protocol (version 1) - command parser and replies.
 *
 * The PC sends one ASCII command per line; the box answers every command
 * with exactly one reply line (or, for SAMPLE, DATA lines followed by DONE).
 *
 *   PING                          -> PONG
 *   INFO?                         -> INFO <protocol> <max_points> <max_rate_hz> <adc_bits> <vref_mv>
 *   ID?                           -> ID <w0> <w1> <w2>
 *   SAMPLE <ch>[,<ch2>] <rate> <n> -> DATA ... / DONE <actual_rate_hz>
 *   anything wrong                -> ERR <CODE> <message>
 *
 * This module contains NO hardware access, so it also compiles on a PC for
 * the unit tests in Tests/test_protocol.c.
 */
#ifndef PROTOCOL_H
#define PROTOCOL_H

#include <stdint.h>

/* ---- Values reported by INFO? (the PC builds its limits from these) ---- */
#define PROTOCOL_VERSION     1u       /* PC refuses any other version          */
#define MAX_POINTS           4000u    /* samples per channel; 32 KB RAM limit  */
#define MAX_RATE_HZ          100000u  /* highest sample rate accepted          */
#define ADC_BITS             12u      /* counts are 0..4095                    */
#define VREF_MV              3300u    /* ADC reference voltage in millivolts   */

#define MAX_SAMPLE_CHANNELS  2u       /* SAMPLE accepts one or two channels    */

/**
 * @brief Handle one complete command line (terminator already removed).
 *        Sends the reply through comms_send_line(). A blank line is ignored.
 */
void protocol_handle_line(const char *line);

/**
 * @brief Called when a received line was longer than CMD_LINE_MAX and was
 *        discarded. Sends ERR BADCMD so the box never stays silent.
 */
void protocol_handle_too_long(void);

#endif /* PROTOCOL_H */
