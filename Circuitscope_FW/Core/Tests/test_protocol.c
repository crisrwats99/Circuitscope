/**
 * @file    test_protocol.c
 * @brief   PC unit tests for the command parser (protocol.c), the line
 *          assembler (linebuf.c) and the channel table (channels.c).
 *
 * Runs on a normal PC - no STM32 needed. The hardware functions that
 * protocol.c calls (comms_send_line, board_get_uid, measure_run) are
 * replaced by fakes below that record what happened.
 *
 * Build and run (from the project folder, with gcc installed):
 *   gcc -DHOST_TEST -ICore/Inc Tests/test_protocol.c Core/Src/protocol.c \
 *       Core/Src/linebuf.c Core/Src/channels.c -o test_protocol && ./test_protocol
 *
 * This folder is NOT part of the firmware build (exclude it in CubeIDE).
 * Safety net: everything below is wrapped in #ifdef HOST_TEST, so if this
 * file is ever compiled by CubeIDE by mistake it produces no code at all
 * (no duplicate main() or fake functions).
 */
#ifdef HOST_TEST

#include <stdio.h>
#include <string.h>
#include <stdint.h>

#include "protocol.h"
#include "linebuf.h"
#include "channels.h"
#include "comms.h"
#include "board.h"
#include "measure.h"

/* ======================================================================== */
/*  Fakes for the hardware functions                                        */
/* ======================================================================== */

static char     g_reply[256];       /* last line "sent" to the PC   */
static int      g_reply_count;      /* how many lines were sent     */

void comms_send_line(const char *text)
{
    strncpy(g_reply, text, sizeof g_reply - 1);
    g_reply[sizeof g_reply - 1] = '\0';
    g_reply_count++;
}
void comms_send(const char *data, size_t len) { (void)data; (void)len; }

void board_get_uid(uint32_t uid[3])
{
    uid[0] = 0x0041002Eu;  uid[1] = 0x3138510Au;  uid[2] = 0x20333935u;
}

static int      g_measure_calls;
static uint8_t  g_m_ch[2], g_m_nch;
static uint32_t g_m_rate, g_m_n;

void measure_run(const uint8_t *ch, uint8_t n_ch, uint32_t rate_hz, uint32_t n)
{
    g_measure_calls++;
    g_m_nch = n_ch; g_m_ch[0] = ch[0]; g_m_ch[1] = (n_ch > 1) ? ch[1] : 0xFF;
    g_m_rate = rate_hz; g_m_n = n;
    comms_send_line("MEASURE_CALLED");
}

/* ======================================================================== */
/*  Tiny test framework                                                     */
/* ======================================================================== */

static int g_pass, g_fail;

static void reset_fakes(void)
{
    g_reply[0] = '\0'; g_reply_count = 0; g_measure_calls = 0;
}

static void check(int ok, const char *what, const char *detail)
{
    if (ok) { g_pass++; }
    else    { g_fail++; printf("FAIL: %s\n      got: %s\n", what, detail); }
}

/** Send cmd; expect exactly one reply that starts with `prefix`. */
static void expect(const char *cmd, const char *prefix)
{
    char what[160];
    reset_fakes();
    protocol_handle_line(cmd);
    snprintf(what, sizeof what, "'%s' -> '%s...'", cmd, prefix);
    check(g_reply_count == 1 && strncmp(g_reply, prefix, strlen(prefix)) == 0, what, g_reply);
}

/** Send cmd; expect an exact single reply. */
static void expect_exact(const char *cmd, const char *reply)
{
    char what[160];
    reset_fakes();
    protocol_handle_line(cmd);
    snprintf(what, sizeof what, "'%s' -> '%s'", cmd, reply);
    check(g_reply_count == 1 && strcmp(g_reply, reply) == 0, what, g_reply);
}

/** Feed a byte string through the line assembler and the parser, like the firmware does. */
static int feed(linebuf_t *lb, const char *bytes)
{
    int lines = 0;
    for (const char *p = bytes; *p; p++) {
        lb_result_t r = linebuf_push(lb, *p);
        if (r == LB_LINE)     { protocol_handle_line(lb->line); lines++; }
        if (r == LB_OVERFLOW) { protocol_handle_too_long();     lines++; }
    }
    return lines;
}

/* ======================================================================== */
/*  Tests                                                                   */
/* ======================================================================== */

static void test_basic_commands(void)
{
    expect_exact("PING",   "PONG");
    expect_exact("  PING  ", "PONG");                       /* extra spaces OK */
    expect_exact("INFO?",  "INFO 1 4000 100000 12 3300");
    expect_exact("ID?",    "ID 0041002E 3138510A 20333935");

    expect("PING x",       "ERR BADARGS");
    expect("INFO? 1",      "ERR BADARGS");
    expect("ID? now",      "ERR BADARGS");

    expect("ping",         "ERR BADCMD unknown command 'ping'; commands are upper-case, did you mean PING?");
    expect("FOO",          "ERR BADCMD unknown command 'FOO'; valid commands");
    expect("INFO",         "ERR BADCMD");                    /* missing '?' */

    reset_fakes(); protocol_handle_line("");
    check(g_reply_count == 0, "empty line gives no reply", g_reply);
    reset_fakes(); protocol_handle_line("    ");
    check(g_reply_count == 0, "all-space line gives no reply", g_reply);
}

static void test_sample_valid(void)
{
    char d[64];

    expect_exact("SAMPLE P1 1000 10", "MEASURE_CALLED");
    snprintf(d, sizeof d, "nch=%u ch0=%u rate=%lu n=%lu", (unsigned)g_m_nch, (unsigned)g_m_ch[0],
             (unsigned long)g_m_rate, (unsigned long)g_m_n);
    check(g_measure_calls == 1 && g_m_nch == 1 && g_m_ch[0] == 0 && g_m_rate == 1000 && g_m_n == 10,
          "SAMPLE P1 1000 10 passes P1, 1000 Hz, 10 points", d);

    expect_exact("SAMPLE P1,P2 10000 2000", "MEASURE_CALLED");    /* brief's example */
    check(g_m_nch == 2 && g_m_ch[0] == 0 && g_m_ch[1] == 1, "P1,P2 order kept", "");

    expect_exact("SAMPLE I2,P7 20000 400", "MEASURE_CALLED");     /* order as asked */
    check(g_m_ch[0] == channels_find("I2") && g_m_ch[1] == channels_find("P7"),
          "I2,P7 order kept", "");

    expect_exact("SAMPLE P1 1 1",          "MEASURE_CALLED");     /* lower limits */
    expect_exact("SAMPLE P1 100000 4000",  "MEASURE_CALLED");     /* upper limits */
    expect_exact("SAMPLE P1 +500 10",      "MEASURE_CALLED");     /* explicit + sign */
}

static void test_sample_errors(void)
{
    /* argument count */
    expect("SAMPLE",                 "ERR BADARGS SAMPLE needs 3 arguments (got 0)");
    expect("SAMPLE P1 1000",         "ERR BADARGS SAMPLE needs 3 arguments (got 2)");
    expect("SAMPLE P1 1000 10 5",    "ERR BADARGS SAMPLE needs 3 arguments");

    /* channels */
    expect("SAMPLE P8 1000 10",      "ERR BADCH unknown channel 'P8'; valid channels are P1 P2 P3 P4 P5 P6 P7 I1 I2");
    expect("SAMPLE p1 1000 10",      "ERR BADCH unknown channel 'p1'");
    expect("SAMPLE P1,P9 1000 10",   "ERR BADCH unknown channel 'P9'");
    expect("SAMPLE P1,P1 1000 10",   "ERR BADARGS channel P1 is listed twice");
    expect("SAMPLE P1,P2,P3 1000 10","ERR BADARGS SAMPLE takes at most 2 channels");
    expect("SAMPLE P1, 1000 10",     "ERR BADARGS empty channel name");
    expect("SAMPLE ,P1 1000 10",     "ERR BADARGS empty channel name");

    /* rate */
    expect("SAMPLE P1 0 10",         "ERR BADRATE rate 0 Hz is out of range; use 1 to 100000");
    expect("SAMPLE P1 100001 10",    "ERR BADRATE");
    expect("SAMPLE P1 -5 10",        "ERR BADRATE");
    expect("SAMPLE P1 99999999999 10","ERR BADRATE");
    expect("SAMPLE P1 abc 10",       "ERR BADARGS rate 'abc' must be a whole number");
    expect("SAMPLE P1 1000.5 10",    "ERR BADARGS rate");
    expect("SAMPLE P1 1e3 10",       "ERR BADARGS rate");

    /* count */
    expect("SAMPLE P1 1000 0",       "ERR BADCOUNT count 0 is out of range; use 1 to 4000");
    expect("SAMPLE P1 1000 4001",    "ERR BADCOUNT");
    expect("SAMPLE P1 1000 ten",     "ERR BADARGS count 'ten' must be a whole number");

    /* checks run in order: channel error reported before rate error */
    expect("SAMPLE P9 0 0",          "ERR BADCH");

    /* box keeps working after errors */
    expect_exact("PING", "PONG");
}

static void test_line_assembly(void)
{
    linebuf_t lb;
    int lines;

    linebuf_reset(&lb); reset_fakes();
    lines = feed(&lb, "PING\n");
    check(lines == 1 && strcmp(g_reply, "PONG") == 0, "LF ending", g_reply);

    linebuf_reset(&lb); reset_fakes();
    lines = feed(&lb, "PING\r\n");
    check(lines == 1 && g_reply_count == 1, "CR+LF gives exactly one reply", g_reply);

    linebuf_reset(&lb); reset_fakes();
    lines = feed(&lb, "PING\r");                         /* PuTTY sends CR only */
    check(lines == 1 && strcmp(g_reply, "PONG") == 0, "CR-only ending (PuTTY)", g_reply);

    linebuf_reset(&lb); reset_fakes();
    lines = feed(&lb, "\r\n\r\n");
    check(lines == 0 && g_reply_count == 0, "blank lines ignored", g_reply);

    linebuf_reset(&lb); reset_fakes();
    lines = feed(&lb, "\x01\x1bPI\x07NG\n");             /* stray control bytes */
    check(lines == 1 && strcmp(g_reply, "PONG") == 0, "stray bytes ignored", g_reply);

    linebuf_reset(&lb); reset_fakes();
    lines = feed(&lb, "PINX\bG\r");                      /* backspace edit */
    check(lines == 1 && strcmp(g_reply, "PONG") == 0, "backspace edits line", g_reply);

    linebuf_reset(&lb); reset_fakes();
    lines = feed(&lb, "SAMPLE\tP1\t1000\t10\n");         /* tabs = spaces */
    check(lines == 1 && strcmp(g_reply, "MEASURE_CALLED") == 0, "tabs treated as spaces", g_reply);

    /* exactly 64 characters is accepted; 65 is too long */
    char line64[80], line65[80];
    memset(line64, ' ', 64); memcpy(line64, "PING", 4); line64[64] = '\n'; line64[65] = '\0';
    memset(line65, ' ', 65); memcpy(line65, "PING", 4); line65[65] = '\n'; line65[66] = '\0';

    linebuf_reset(&lb); reset_fakes();
    lines = feed(&lb, line64);
    check(lines == 1 && strcmp(g_reply, "PONG") == 0, "64-char line accepted", g_reply);

    linebuf_reset(&lb); reset_fakes();
    lines = feed(&lb, line65);
    check(lines == 1 && strncmp(g_reply, "ERR BADCMD line longer than 64", 30) == 0,
          "65-char line -> ERR BADCMD", g_reply);

    reset_fakes();
    lines = feed(&lb, "PING\n");                          /* recovers afterwards */
    check(lines == 1 && strcmp(g_reply, "PONG") == 0, "works again after too-long line", g_reply);

    linebuf_reset(&lb); reset_fakes();
    lines = feed(&lb, "PING\nINFO?\n");                   /* two commands back to back */
    check(lines == 2 && g_reply_count == 2, "two commands in one burst", g_reply);
}

static void test_channel_table(void)
{
    const char *expected[] = { "P1","P2","P3","P4","P5","P6","P7","I1","I2" };
    const uint8_t adc[]    = {  1,   1,   2,   2,   2,   1,   2,   1,   2  };

    check(g_channel_count == 9, "9 channels in table", "");
    for (int i = 0; i < 9; i++) {
        check(channels_find(expected[i]) == i && g_channels[i].adc == adc[i],
              expected[i], "wrong position or ADC number");
    }
    check(channels_find("P0") == -1 && channels_find("") == -1, "unknown names return -1", "");
}

int main(void)
{
    test_basic_commands();
    test_sample_valid();
    test_sample_errors();
    test_line_assembly();
    test_channel_table();

    printf("\n%d passed, %d failed\n", g_pass, g_fail);
    return (g_fail == 0) ? 0 : 1;
}

#endif /* HOST_TEST */
