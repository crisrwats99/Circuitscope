/**
 * @file    lcd_fmt.c
 * @brief   PCF8574 byte encoding and 16-character line formatting (see lcd_fmt.h).
 */
#include "lcd_fmt.h"

void lcd_encode(uint8_t value, bool is_char, bool backlight, uint8_t out[4])
{
    /* Control bits shared by all four bytes (RW stays 0 = write). */
    uint8_t ctrl = (uint8_t)((is_char ? LCD_BIT_RS : 0u) | (backlight ? LCD_BIT_BL : 0u));
    uint8_t high = (uint8_t)(value & 0xF0u);          /* D7..D4 already on P7..P4 */
    uint8_t low  = (uint8_t)((value << 4) & 0xF0u);   /* D3..D0 moved to P7..P4   */

    out[0] = (uint8_t)(high | ctrl | LCD_BIT_EN);     /* present high nibble, EN high */
    out[1] = (uint8_t)(high | ctrl);                  /* EN low: LCD latches it       */
    out[2] = (uint8_t)(low  | ctrl | LCD_BIT_EN);     /* present low nibble, EN high  */
    out[3] = (uint8_t)(low  | ctrl);                  /* EN low: LCD latches it       */
}

void lcd_fit(char out[LCD_COLS + 1], const char *text)
{
    uint8_t i = 0;

    if (text != 0) {
        for (; i < LCD_COLS && text[i] != '\0'; i++) {
            char c = text[i];
            out[i] = (c >= 0x20 && c <= 0x7E) ? c : '?';  /* LCD shows ASCII only */
        }
    }
    for (; i < LCD_COLS; i++) {
        out[i] = ' ';                                     /* pad: erase old text */
    }
    out[LCD_COLS] = '\0';
}

uint16_t lcd_box_tag(const uint32_t uid[3])
{
    uint16_t crc = 0xFFFFu;
    for (uint8_t w = 0; w < 3u; w++) {
        for (uint8_t b = 0; b < 4u; b++) {
            uint8_t byte = (uint8_t)(uid[w] >> (8u * b));   /* little-endian */
            crc ^= (uint16_t)((uint16_t)byte << 8);
            for (uint8_t bit = 0; bit < 8u; bit++) {
                crc = (crc & 0x8000u) ? (uint16_t)((crc << 1) ^ 0x1021u) : (uint16_t)(crc << 1);
            }
        }
    }
    return crc;
}
