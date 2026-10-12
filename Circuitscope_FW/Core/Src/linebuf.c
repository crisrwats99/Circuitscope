/**
 * @file    linebuf.c
 * @brief   Assembles received serial bytes into command lines (see linebuf.h).
 */
#include "linebuf.h"
#include <string.h>

/** Empty the buffer and clear the overflow flag. */
void linebuf_reset(linebuf_t *lb)
{
    lb->len      = 0;
    lb->overflow = false;
    lb->work[0]  = '\0';
    lb->line[0]  = '\0';
}

/**
 * @brief  Add one received byte.
 * @return LB_LINE when a complete, non-blank line is ready in lb->line,
 *         LB_OVERFLOW when an over-long line has just ended (and was dropped),
 *         LB_NONE otherwise.
 */
lb_result_t linebuf_push(linebuf_t *lb, char c)
{
    /* ---- End of line: CR or LF (CR+LF gives a line, then a blank that is ignored) ---- */
    if (c == '\r' || c == '\n') {
        if (lb->overflow) {               /* the line was too long: discard it */
            lb->len      = 0;
            lb->overflow = false;
            return LB_OVERFLOW;
        }
        if (lb->len == 0) {               /* blank line: nothing to do */
            return LB_NONE;
        }
        lb->work[lb->len] = '\0';         /* finish the string ...           */
        memcpy(lb->line, lb->work, (size_t)lb->len + 1u); /* ... hand it over */
        lb->len = 0;                      /* start the next line fresh       */
        return LB_LINE;
    }

    /* ---- Backspace / DEL: remove the last character (typing in PuTTY) ---- */
    if (c == '\b' || c == 0x7F) {
        if (lb->len > 0 && !lb->overflow) {
            lb->len--;
        }
        return LB_NONE;
    }

    /* ---- Tabs count as spaces; other non-printable bytes are stray noise ---- */
    if (c == '\t') {
        c = ' ';
    }
    if (c < 0x20 || c > 0x7E) {
        return LB_NONE;
    }

    /* ---- Normal character: store it, or mark the line as too long ---- */
    if (lb->overflow) {
        return LB_NONE;                   /* keep discarding until the line ends */
    }
    if (lb->len >= CMD_LINE_MAX) {
        lb->overflow = true;              /* reported when the line ends */
        return LB_NONE;
    }
    lb->work[lb->len++] = c;
    return LB_NONE;
}
