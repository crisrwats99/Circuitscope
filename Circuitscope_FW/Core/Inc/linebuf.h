/**
 * @file    linebuf.h
 * @brief   Assembles received serial bytes into command lines.
 *
 * Bytes arrive one at a time from the UART interrupt. linebuf_push() collects
 * them until CR or LF, then hands back a complete line. It:
 *   - accepts LF, CR or CR+LF as the line ending (blank lines are ignored),
 *   - drops stray non-printable characters,
 *   - handles backspace (useful when typing in PuTTY),
 *   - flags lines longer than CMD_LINE_MAX instead of overflowing memory.
 *
 * No hardware access - also compiled on the PC for the unit tests.
 */
#ifndef LINEBUF_H
#define LINEBUF_H

#include <stdint.h>
#include <stdbool.h>

#define CMD_LINE_MAX  64u   /* longest accepted command, in characters */

/** Result of pushing one byte. */
typedef enum {
    LB_NONE = 0,    /* nothing complete yet                                   */
    LB_LINE,        /* a complete line is now in lb->line                     */
    LB_OVERFLOW     /* a line ended but was too long; it has been discarded   */
} lb_result_t;

typedef struct {
    char     work[CMD_LINE_MAX + 1];  /* line being typed/received          */
    char     line[CMD_LINE_MAX + 1];  /* last complete line (NUL-terminated) */
    uint16_t len;                     /* characters currently in work[]      */
    bool     overflow;                /* true once the current line is too long */
} linebuf_t;

void        linebuf_reset(linebuf_t *lb);
lb_result_t linebuf_push(linebuf_t *lb, char c);

#endif /* LINEBUF_H */
