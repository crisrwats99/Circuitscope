/**
 * @file    lcd_fmt.h
 * @brief   Hardware-independent helpers for the 16x2 I2C LCD:
 *          byte encoding for the PCF8574 backpack and line formatting.
 *          Unit-tested on the PC (Tests/test_lcd.c).
 *
 * The backpack is a PCF8574 I/O expander wired to an HD44780 LCD in 4-bit
 * mode. Each PCF8574 output bit drives one LCD pin:
 *     P0 = RS (0 = command, 1 = character)
 *     P1 = RW (always 0 = write)
 *     P2 = EN (data is latched on the falling edge)
 *     P3 = backlight
 *     P4..P7 = LCD data lines D4..D7
 * A byte is sent as two 4-bit halves (high first), each with an EN pulse.
 */
#ifndef LCD_FMT_H
#define LCD_FMT_H

#include <stdint.h>
#include <stdbool.h>

#define LCD_COLS       16u

#define LCD_BIT_RS     0x01u
#define LCD_BIT_RW     0x02u
#define LCD_BIT_EN     0x04u
#define LCD_BIT_BL     0x08u

/**
 * @brief Encode one LCD byte as the 4 PCF8574 output bytes that send it:
 *        [high nibble + EN=1], [high nibble + EN=0],
 *        [low nibble  + EN=1], [low nibble  + EN=0]
 * @param value      command or character code
 * @param is_char    true = character (RS=1), false = command (RS=0)
 * @param backlight  true = backlight on
 */
void lcd_encode(uint8_t value, bool is_char, bool backlight, uint8_t out[4]);

/**
 * @brief Fit text to exactly 16 characters: cut if longer, pad with spaces
 *        if shorter (overwrites the old text without clearing the screen,
 *        so there is no flicker). Non-printable characters become '?'.
 */
void lcd_fit(char out[LCD_COLS + 1], const char *text);

#endif /* LCD_FMT_H */
