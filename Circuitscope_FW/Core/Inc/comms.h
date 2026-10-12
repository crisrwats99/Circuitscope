/**
 * @file    comms.h
 * @brief   USART2 link to the PC (ST-LINK virtual COM port, 115200 8N1).
 *
 * Receiving runs in the background on the UART interrupt; the main loop
 * collects finished lines with comms_get_line(). Replies are sent with
 * comms_send_line(), which appends CR+LF.
 */
#ifndef COMMS_H
#define COMMS_H

#include <stddef.h>

/** What comms_get_line() found. */
typedef enum {
    COMMS_NONE = 0,   /* no new command yet                         */
    COMMS_LINE,       /* a command line was copied to the buffer    */
    COMMS_TOO_LONG    /* a line was too long and has been discarded */
} comms_result_t;

/** Start receiving on USART2. Call once after MX_USART2_UART_Init(). */
void comms_init(void);

/** Copy the next complete command line (if any) into out[]. */
comms_result_t comms_get_line(char *out, size_t out_size);

/** Send text followed by CR+LF. */
void comms_send_line(const char *text);

/** Send raw bytes with no line ending (used for long DATA lines in Stage 3). */
void comms_send(const char *data, size_t len);

#endif /* COMMS_H */
