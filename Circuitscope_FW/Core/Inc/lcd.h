/**
 * @file    lcd.h
 * @brief   16x2 status LCD on I2C2 (PA8 SDA = D9, PA9 SCL = D1), PCF8574 backpack.
 *
 *   Line 1: status - "Circuitscope", "PC connected", "Sampling P1,P2",
 *           "Sending data", "Ready", "HW error - reset"
 *   Line 2: set by the PC with the LCD command (step instructions or a
 *           reading, e.g. "Probe P1 -> Vout", "P1 = 2.483 V"). At start-up
 *           it shows a short box tag, e.g. "Box A3F1" (see lcd_box_tag()),
 *           so tutors can tell boxes apart without showing the full chip ID.
 *
 * If no LCD answers at start-up (or it stops answering), every function
 * below does nothing: the measurement box keeps working without a display.
 * Never call these during an ADC capture (I2C switching adds noise).
 */
#ifndef LCD_H
#define LCD_H

#include <stdbool.h>

#define LCD_TEXT_MAX  16u   /* characters per LCD line */

/** Find the LCD (address 0x27 or 0x3F), initialise it, show the start-up screen. */
void lcd_init(void);

/** True if an LCD was found and is still responding. */
bool lcd_present(void);

/** Show text on line 1 (cut/padded to 16 characters). */
void lcd_status(const char *text);

/** Show text from the PC on line 2 (cut/padded to 16 characters; "" clears it). */
void lcd_message(const char *text);

/** Show "PC connected" the first time a command arrives; later calls do nothing. */
void lcd_pc_connected(void);

#endif /* LCD_H */
