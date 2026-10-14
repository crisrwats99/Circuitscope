/**
 * @file    protocol.c
 * @brief   Parses PC commands and sends replies (protocol version 1).
 *
 * Flow:  protocol_handle_line()
 *          -> split the line into words (tokens)
 *          -> find the command in the command table
 *          -> the command handler checks its arguments and replies
 *
 * Every error reply has the form  ERR <CODE> <message>  where the message
 * says what was wrong and how to fix it. Codes (from the firmware brief):
 *   BADCMD   unknown command, or line too long
 *   BADARGS  wrong number/format of arguments
 *   BADCH    unknown channel name
 *   BADRATE  sample rate outside 1..MAX_RATE_HZ
 *   BADCOUNT sample count outside 1..MAX_POINTS
 *
 * LCD <text> is the one command whose argument may contain spaces, so it is
 * recognised before the line is split into words (see protocol_handle_line).
 */
#include "protocol.h"
#include "linebuf.h"   /* CMD_LINE_MAX */
#include "channels.h"
#include "comms.h"
#include "board.h"
#include "measure.h"
#include "lcd.h"
#ifdef DEBUG
#include "watchdog.h"
#endif

#include <stdio.h>
#include <stdarg.h>
#include <string.h>
#include <ctype.h>
#include <stdbool.h>

#define MAX_TOKENS  5u     /* SAMPLE + 3 arguments, plus 1 to detect extras */
#define REPLY_MAX   192u   /* longest reply line we build                   */

/* ------------------------------------------------------------------------ */
/*  Reply helpers                                                           */
/* ------------------------------------------------------------------------ */

/** Send "ERR <code> <formatted message>". */
static void reply_err(const char *code, const char *fmt, ...)
{
    char    msg[REPLY_MAX];
    int     used = snprintf(msg, sizeof msg, "ERR %s ", code);
    va_list ap;

    va_start(ap, fmt);
    vsnprintf(msg + used, sizeof msg - (size_t)used, fmt, ap);
    va_end(ap);
    comms_send_line(msg);
}

/** Build the list of valid channel names, e.g. "P1 P2 ... I2", for error messages. */
static void channel_name_list(char *out, size_t size)
{
    size_t pos = 0;
    out[0] = '\0';
    for (uint8_t i = 0; i < g_channel_count && pos < size; i++) {
        pos += (size_t)snprintf(out + pos, size - pos, "%s%s",
                                (i == 0) ? "" : " ", g_channels[i].name);
    }
}

/* ------------------------------------------------------------------------ */
/*  Parsing helpers                                                         */
/* ------------------------------------------------------------------------ */

/**
 * @brief  Split s (modified in place) into words separated by spaces.
 * @return Number of words; capped at max_tokens (a full array means "too many").
 */
static uint32_t tokenize(char *s, char *tok[], uint32_t max_tokens)
{
    uint32_t n = 0;
    while (*s != '\0' && n < max_tokens) {
        while (*s == ' ') s++;          /* skip spaces before the word */
        if (*s == '\0') break;
        tok[n++] = s;                   /* word starts here            */
        while (*s != '\0' && *s != ' ') s++;
        if (*s == ' ') *s++ = '\0';     /* end the word                */
    }
    return n;
}

/** Case-insensitive string compare (used only to suggest the right spelling). */
static bool str_ieq(const char *a, const char *b)
{
    while (*a && *b) {
        if (toupper((unsigned char)*a) != toupper((unsigned char)*b)) return false;
        a++; b++;
    }
    return *a == *b;
}

typedef enum { NUM_OK, NUM_NOT_NUMBER, NUM_OUT_OF_RANGE } num_status_t;

/**
 * @brief  Parse a whole number and check it lies in [min, max].
 *         "abc", "10.5", "1e3", "" -> NUM_NOT_NUMBER  (reported as BADARGS)
 *         "0", "-5", "999999999999" -> NUM_OUT_OF_RANGE (BADRATE/BADCOUNT)
 */
static num_status_t parse_number(const char *s, uint32_t min, uint32_t max, uint32_t *out)
{
    bool        negative = false;
    bool        too_big  = false;
    uint64_t    value    = 0;
    const char *p        = s;

    if (*p == '+' || *p == '-') {       /* optional sign */
        negative = (*p == '-');
        p++;
    }
    if (*p == '\0') {
        return NUM_NOT_NUMBER;          /* sign with no digits, or empty */
    }
    for (; *p != '\0'; p++) {
        if (!isdigit((unsigned char)*p)) {
            return NUM_NOT_NUMBER;      /* any non-digit makes it invalid */
        }
        if (!too_big) {
            value = value * 10u + (uint64_t)(*p - '0');
            if (value > 0xFFFFFFFFull) too_big = true;  /* avoid overflow */
        }
    }
    if (too_big || negative || value < min || value > max) {
        return NUM_OUT_OF_RANGE;
    }
    *out = (uint32_t)value;
    return NUM_OK;
}

/* ------------------------------------------------------------------------ */
/*  Command handlers                                                        */
/*  argc/argv work like in a C main(): argv[0] is the command word.         */
/* ------------------------------------------------------------------------ */

/** Reject arguments for commands that take none. Returns true if OK. */
static bool expect_no_args(uint32_t argc, char *argv[])
{
    if (argc != 1) {
        reply_err("BADARGS", "%s takes no arguments; send just '%s'", argv[0], argv[0]);
        return false;
    }
    return true;
}

/** PING -> PONG  (link check) */
static void cmd_ping(uint32_t argc, char *argv[])
{
    if (!expect_no_args(argc, argv)) return;
    comms_send_line("PONG");
}

/** INFO? -> INFO <protocol> <max_points> <max_rate_hz> <adc_bits> <vref_mv> */
static void cmd_info(uint32_t argc, char *argv[])
{
    char msg[REPLY_MAX];
    if (!expect_no_args(argc, argv)) return;
    snprintf(msg, sizeof msg, "INFO %u %u %lu %u %u",
             (unsigned)PROTOCOL_VERSION, (unsigned)MAX_POINTS,
             (unsigned long)MAX_RATE_HZ, (unsigned)ADC_BITS, (unsigned)VREF_MV);
    comms_send_line(msg);
}

/** ID? -> ID <w0> <w1> <w2>  (96-bit unique chip ID, 8 upper-case hex digits each) */
static void cmd_id(uint32_t argc, char *argv[])
{
    char     msg[REPLY_MAX];
    uint32_t uid[3];
    if (!expect_no_args(argc, argv)) return;
    board_get_uid(uid);
    snprintf(msg, sizeof msg, "ID %08lX %08lX %08lX",
             (unsigned long)uid[0], (unsigned long)uid[1], (unsigned long)uid[2]);
    comms_send_line(msg);
}

/** SAMPLE <ch>[,<ch2>] <rate_hz> <n>  - validate everything, then hand to measure_run(). */
static void cmd_sample(uint32_t argc, char *argv[])
{
    char     list_copy[CMD_LINE_MAX + 1]; /* channel list, split in place  */
    char     names[64];                   /* valid names, for messages     */
    uint8_t  ch_index[MAX_SAMPLE_CHANNELS];
    uint8_t  n_ch = 0;
    uint32_t rate_hz = 0;
    uint32_t n = 0;

    /* ---- 1. Argument count ---- */
    if (argc != 4) {
        reply_err("BADARGS",
                  "SAMPLE needs 3 arguments (got %lu): SAMPLE <ch>[,<ch2>] <rate_hz> <n>, "
                  "e.g. SAMPLE P1,P2 10000 2000", (unsigned long)(argc - 1u));
        return;
    }

    /* ---- 2. Channel list: "P1" or "P1,P2" ---- */
    strncpy(list_copy, argv[1], sizeof list_copy - 1u);
    list_copy[sizeof list_copy - 1u] = '\0';

    char *piece = list_copy;
    while (piece != NULL) {
        char *comma = strchr(piece, ',');
        if (comma != NULL) *comma = '\0';           /* cut off this name */

        if (*piece == '\0') {
            reply_err("BADARGS", "empty channel name in '%s'; write P1 or P1,P2 "
                      "with no spaces and no trailing comma", argv[1]);
            return;
        }
        if (n_ch == MAX_SAMPLE_CHANNELS) {
            reply_err("BADARGS", "SAMPLE takes at most %u channels, '%s' has more",
                      (unsigned)MAX_SAMPLE_CHANNELS, argv[1]);
            return;
        }
        int idx = channels_find(piece);
        if (idx < 0) {
            channel_name_list(names, sizeof names);
            reply_err("BADCH", "unknown channel '%s'; valid channels are %s "
                      "(upper-case)", piece, names);
            return;
        }
        for (uint8_t k = 0; k < n_ch; k++) {
            if (ch_index[k] == (uint8_t)idx) {
                reply_err("BADARGS", "channel %s is listed twice in '%s'; "
                          "use two different channels", piece, argv[1]);
                return;
            }
        }
        ch_index[n_ch++] = (uint8_t)idx;
        piece = (comma != NULL) ? comma + 1 : NULL; /* next name, if any */
    }

    /* ---- 3. Sample rate ---- */
    switch (parse_number(argv[2], 1u, MAX_RATE_HZ, &rate_hz)) {
    case NUM_NOT_NUMBER:
        reply_err("BADARGS", "rate '%s' must be a whole number of Hz, e.g. 10000", argv[2]);
        return;
    case NUM_OUT_OF_RANGE:
        reply_err("BADRATE", "rate %s Hz is out of range; use 1 to %lu",
                  argv[2], (unsigned long)MAX_RATE_HZ);
        return;
    default:
        break;
    }

    /* ---- 4. Number of samples ---- */
    switch (parse_number(argv[3], 1u, MAX_POINTS, &n)) {
    case NUM_NOT_NUMBER:
        reply_err("BADARGS", "count '%s' must be a whole number of samples, e.g. 2000", argv[3]);
        return;
    case NUM_OUT_OF_RANGE:
        reply_err("BADCOUNT", "count %s is out of range; use 1 to %u samples per channel",
                  argv[3], (unsigned)MAX_POINTS);
        return;
    default:
        break;
    }

    /* ---- 5. All valid: capture and send (DATA lines + DONE) ---- */
    measure_run(ch_index, n_ch, rate_hz, n);
}

/**
 * LCD <text>  - show up to 16 characters on LCD line 2 (instructions or a
 * reading worked out by the PC, e.g. "Probe P1 -> Vout" or "P1 = 2.483 V").
 * Everything after the first space is shown as-is (inner spaces kept,
 * trailing spaces dropped). "LCD" alone clears line 2.
 * Reply: OK, or OK NOLCD if no display is fitted (still a success for the PC).
 *
 * @param text  the raw text after "LCD" (may be empty)
 */
static void cmd_lcd(const char *text)
{
    size_t len;

    if (*text == ' ') text++;                   /* the one separating space */
    len = strlen(text);
    while (len > 0u && text[len - 1u] == ' ') { /* drop trailing spaces     */
        len--;
    }
    if (len > LCD_TEXT_MAX) {
        reply_err("BADARGS", "LCD text is %u characters; the display line holds at most %u",
                  (unsigned)len, (unsigned)LCD_TEXT_MAX);
        return;
    }

    char line[LCD_TEXT_MAX + 1u];
    memcpy(line, text, len);
    line[len] = '\0';
    lcd_message(line);
    comms_send_line(lcd_present() ? "OK" : "OK NOLCD");
}

#ifdef DEBUG
/** WDTEST (Debug builds only, not part of the PC protocol): freeze on
 *  purpose to prove the watchdog resets the box. Not present in Release. */
static void cmd_wdtest(uint32_t argc, char *argv[])
{
    if (!expect_no_args(argc, argv)) return;
    comms_send_line("OK freezing now - watchdog should reset the box within ~2 s");
    watchdog_test_hang();
}
#endif

/* ------------------------------------------------------------------------ */
/*  Command table and dispatcher                                            */
/* ------------------------------------------------------------------------ */

typedef struct {
    const char *name;
    void      (*handler)(uint32_t argc, char *argv[]);
} command_t;

static const command_t s_commands[] = {
    { "PING",   cmd_ping   },
    { "INFO?",  cmd_info   },
    { "ID?",    cmd_id     },
    { "SAMPLE", cmd_sample },
    { "LCD",    NULL       },   /* raw-text command: handled before tokenizing */
#ifdef DEBUG
    { "WDTEST", cmd_wdtest },   /* Debug builds only */
#endif
};
#define N_COMMANDS  (sizeof s_commands / sizeof s_commands[0])

void protocol_handle_line(const char *line)
{
    char     buf[CMD_LINE_MAX + 1];
    char    *argv[MAX_TOKENS];
    uint32_t argc;

    /* Work on a copy: tokenize() writes NULs into the string. */
    strncpy(buf, line, sizeof buf - 1u);
    buf[sizeof buf - 1u] = '\0';

    /* LCD <text>: the text may contain spaces, so take it straight from the line. */
    {
        const char *p = buf;
        while (*p == ' ') p++;
        if (strncmp(p, "LCD", 3) == 0 && (p[3] == ' ' || p[3] == '\0')) {
            cmd_lcd(p + 3);
            return;
        }
    }

    argc = tokenize(buf, argv, MAX_TOKENS);
    if (argc == 0) {
        return;                         /* blank line: ignored, no reply */
    }

    /* Exact match first (commands are case-sensitive). */
    for (uint32_t i = 0; i < N_COMMANDS; i++) {
        if (s_commands[i].handler != NULL && strcmp(argv[0], s_commands[i].name) == 0) {
            s_commands[i].handler(argc, argv);
            return;
        }
    }

    /* Not found: if only the case is wrong, say so. */
    for (uint32_t i = 0; i < N_COMMANDS; i++) {
        if (str_ieq(argv[0], s_commands[i].name)) {
            reply_err("BADCMD", "unknown command '%s'; commands are upper-case, "
                      "did you mean %s?", argv[0], s_commands[i].name);
            return;
        }
    }
    reply_err("BADCMD", "unknown command '%s'; valid commands are PING, INFO?, ID?, SAMPLE, LCD",
              argv[0]);
}

void protocol_handle_too_long(void)
{
    reply_err("BADCMD", "line longer than %u characters was ignored; send one command per line",
              (unsigned)CMD_LINE_MAX);
}
