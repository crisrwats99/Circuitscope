/**
 * @file    lcd.c
 * @brief   HD44780 16x2 LCD through a PCF8574 I2C backpack (see lcd.h).
 *
 * Bus: I2C2 on PA8 (SDA, header D9) and PA9 (SCL, header D1), 100 kHz
 * (PCF8574 maximum). One LCD byte = 4 I2C data bytes (see lcd_encode), so a
 * full 16-character line takes about 7 ms.
 *
 * Why I2C2 and not I2C1 (PB7/PA15): on a stock NUCLEO-G431KB, solder bridges
 * SB2/SB3 are closed, which joins PB7 to A4 (PA5 = P4) and PA15 to A5
 * (PA6 = P5). The LCD's 5 V pull-ups on I2C1 would therefore sit on two ADC
 * inputs. PA8/PA9 are free, 5 V-tolerant pins.
 */
#include "lcd.h"
#include "lcd_fmt.h"
#include "board.h"     /* board_get_uid() */
#include "i2c.h"       /* hi2c2 (CubeMX)  */
#include <stdio.h>

/* PCF8574 7-bit addresses: 0x27 (PCF8574) or 0x3F (PCF8574A). HAL wants them shifted left. */
static const uint8_t s_candidates[] = { 0x27u, 0x3Fu };

#define I2C_TIMEOUT_MS  10u     /* give up quickly if the LCD is unplugged */

/* HD44780 commands */
#define CMD_CLEAR        0x01u  /* needs ~1.6 ms to execute          */
#define CMD_ENTRY_MODE   0x06u  /* cursor moves right, no shift       */
#define CMD_DISPLAY_ON   0x0Cu  /* display on, cursor off, blink off  */
#define CMD_FUNCTION_SET 0x28u  /* 4-bit bus, 2 lines, 5x8 font       */
#define CMD_SET_DDRAM    0x80u  /* + address: 0x00 = line 1, 0x40 = line 2 */

#define LCD_I2C  hi2c2           /* I2C peripheral the LCD is on */

static uint16_t s_addr;          /* HAL (8-bit) address, 0 = no LCD */
static bool     s_present;
static bool     s_connected_shown;

/* ------------------------------------------------------------------------ */
/*  Low level                                                               */
/* ------------------------------------------------------------------------ */

/** Send raw PCF8574 bytes. On any I2C failure, stop using the LCD for good. */
static void pcf_write(const uint8_t *bytes, uint16_t len)
{
    if (!s_present) return;
    if (HAL_I2C_Master_Transmit(&LCD_I2C, s_addr, (uint8_t *)bytes, len, I2C_TIMEOUT_MS) != HAL_OK) {
        s_present = false;   /* LCD gone: never block the measurement box on it */
    }
}

/** Send one 4-bit value with an EN pulse (only used during the power-up reset). */
static void lcd_nibble(uint8_t nibble)
{
    uint8_t b[2] = { (uint8_t)((nibble << 4) | LCD_BIT_BL | LCD_BIT_EN),
                     (uint8_t)((nibble << 4) | LCD_BIT_BL) };
    pcf_write(b, 2u);
}

/** Send a full command or character byte. */
static void lcd_byte(uint8_t value, bool is_char)
{
    uint8_t b[4];
    lcd_encode(value, is_char, true, b);
    pcf_write(b, 4u);
}

/** Write 16 characters to line 0 or 1. */
static void lcd_line(uint8_t row, const char *text)
{
    char fitted[LCD_COLS + 1];
    lcd_fit(fitted, text);
    lcd_byte((uint8_t)(CMD_SET_DDRAM | (row ? 0x40u : 0x00u)), false);
    for (uint8_t i = 0; i < LCD_COLS; i++) {
        lcd_byte((uint8_t)fitted[i], true);
    }
}

/* ------------------------------------------------------------------------ */
/*  Public                                                                  */
/* ------------------------------------------------------------------------ */

void lcd_init(void)
{
    char     line[LCD_COLS + 1];
    uint32_t uid[3];

    /* 1. Find the backpack. */
    s_present = false;
    for (uint8_t i = 0; i < sizeof s_candidates; i++) {
        uint16_t a = (uint16_t)(s_candidates[i] << 1);
        if (HAL_I2C_IsDeviceReady(&LCD_I2C, a, 2u, I2C_TIMEOUT_MS) == HAL_OK) {
            s_addr    = a;
            s_present = true;
            break;
        }
    }
    if (!s_present) return;            /* no LCD: run without a display */

    /* 2. HD44780 power-up reset into 4-bit mode (datasheet sequence). */
    HAL_Delay(50);                     /* > 40 ms after LCD power-up        */
    lcd_nibble(0x3u); HAL_Delay(5);    /* "8-bit" x3 forces a known state   */
    lcd_nibble(0x3u); HAL_Delay(1);
    lcd_nibble(0x3u); HAL_Delay(1);
    lcd_nibble(0x2u); HAL_Delay(1);    /* switch to 4-bit                   */

    /* 3. Configure. */
    lcd_byte(CMD_FUNCTION_SET, false);
    lcd_byte(CMD_DISPLAY_ON, false);
    lcd_byte(CMD_CLEAR, false);
    HAL_Delay(2);                      /* clear is the one slow command     */
    lcd_byte(CMD_ENTRY_MODE, false);

    /* 4. Start-up screen: name + box ID (first UID word). */
    board_get_uid(uid);
    snprintf(line, sizeof line, "ID %08lX", (unsigned long)uid[0]);
    lcd_line(0, "Circuitscope");
    lcd_line(1, line);
}

bool lcd_present(void)
{
    return s_present;
}

void lcd_status(const char *text)
{
    lcd_line(0, text);
}

void lcd_pc_connected(void)
{
    if (!s_connected_shown) {
        s_connected_shown = true;
        lcd_status("PC connected");
    }
}
