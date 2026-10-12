/**
 * @file    test_lcd.c
 * @brief   PC unit tests for lcd_fmt.c: PCF8574 byte encoding and line fitting.
 *
 * Build and run (from the project folder, with gcc installed):
 *   gcc -DHOST_TEST -ICore/Inc Tests/test_lcd.c Core/Src/lcd_fmt.c -o test_lcd
 *   ./test_lcd
 *
 * Wrapped in #ifdef HOST_TEST so it compiles to nothing inside CubeIDE.
 */
#ifdef HOST_TEST

#include <stdio.h>
#include <string.h>
#include "lcd_fmt.h"

static int g_pass, g_fail;

static void check(int ok, const char *what, const char *detail)
{
    if (ok) { g_pass++; }
    else    { g_fail++; printf("FAIL: %s\n      %s\n", what, detail); }
}

static void test_encode(void)
{
    uint8_t b[4]; char d[96];

    /* Character 'A' = 0x41, backlight on: RS=1, BL=1 -> ctrl 0x09 */
    lcd_encode('A', true, true, b);
    snprintf(d, sizeof d, "got %02X %02X %02X %02X", b[0], b[1], b[2], b[3]);
    check(b[0] == 0x4D && b[1] == 0x49 && b[2] == 0x1D && b[3] == 0x19, "'A' as character", d);

    /* Command 0x28 (function set), backlight on: RS=0, BL=1 -> ctrl 0x08 */
    lcd_encode(0x28, false, true, b);
    snprintf(d, sizeof d, "got %02X %02X %02X %02X", b[0], b[1], b[2], b[3]);
    check(b[0] == 0x2C && b[1] == 0x28 && b[2] == 0x8C && b[3] == 0x88, "command 0x28", d);

    /* Every byte value: EN pulses high then low, RW always 0, nibbles rebuild the value */
    int bad = 0;
    for (int v = 0; v < 256; v++) {
        for (int rs = 0; rs < 2; rs++) {
            lcd_encode((uint8_t)v, rs, true, b);
            int en_ok  = (b[0] & LCD_BIT_EN) && !(b[1] & LCD_BIT_EN) && (b[2] & LCD_BIT_EN) && !(b[3] & LCD_BIT_EN);
            int rw_ok  = !((b[0] | b[1] | b[2] | b[3]) & LCD_BIT_RW);
            int rs_ok  = rs ? ((b[0] & b[1] & b[2] & b[3] & LCD_BIT_RS) != 0)
                            : (((b[0] | b[1] | b[2] | b[3]) & LCD_BIT_RS) == 0);
            int bl_ok  = (b[0] & b[1] & b[2] & b[3] & LCD_BIT_BL) != 0;
            int val_ok = ((b[1] & 0xF0) | (b[3] >> 4)) == v && (b[0] & 0xF0) == (b[1] & 0xF0)
                         && (b[2] & 0xF0) == (b[3] & 0xF0);
            if (!(en_ok && rw_ok && rs_ok && bl_ok && val_ok)) bad++;
        }
    }
    snprintf(d, sizeof d, "%d of 512 encodings wrong", bad);
    check(bad == 0, "all 256 values x RS: EN pulse, RW=0, RS, backlight, nibbles", d);

    lcd_encode(0xFF, true, false, b);
    check(((b[0] | b[1] | b[2] | b[3]) & LCD_BIT_BL) == 0, "backlight off clears BL bit", "");
}

static void test_fit(void)
{
    char out[LCD_COLS + 1], d[64];

    lcd_fit(out, "Ready");
    snprintf(d, sizeof d, "got '%s'", out);
    check(strcmp(out, "Ready           ") == 0, "short text padded to 16", d);

    lcd_fit(out, "Sampling P1,P2 extra text");
    snprintf(d, sizeof d, "got '%s'", out);
    check(strcmp(out, "Sampling P1,P2 e") == 0, "long text cut to 16", d);

    lcd_fit(out, "");
    check(strcmp(out, "                ") == 0, "empty -> 16 spaces", out);

    lcd_fit(out, NULL);
    check(strcmp(out, "                ") == 0, "NULL -> 16 spaces", out);

    lcd_fit(out, "A\tB\x01" "C");
    snprintf(d, sizeof d, "got '%s'", out);
    check(strcmp(out, "A?B?C           ") == 0, "non-printable -> '?'", d);

    lcd_fit(out, "ID 0041002E");
    check(strlen(out) == 16 && strncmp(out, "ID 0041002E", 11) == 0, "ID line", out);

    /* every status text used by the firmware fits without being cut */
    const char *texts[] = { "Circuitscope", "PC connected", "Sampling P1,P2", "Sampling I2,P7",
                            "Sending data", "Ready", "HW error - reset" };
    for (size_t i = 0; i < sizeof texts / sizeof texts[0]; i++) {
        check(strlen(texts[i]) <= LCD_COLS, "status text fits 16 columns", texts[i]);
    }
}

int main(void)
{
    test_encode();
    test_fit();
    printf("\n%d passed, %d failed\n", g_pass, g_fail);
    return (g_fail == 0) ? 0 : 1;
}

#endif /* HOST_TEST */
