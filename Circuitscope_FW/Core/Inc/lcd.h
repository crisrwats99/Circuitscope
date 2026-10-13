/**
 * @file    lcd.h
 * @brief   16x2 status LCD on I2C2 (PA8 SDA = D9, PA9 SCL = D1), PCF8574 backpack.
 *
 *   Line 1: status - "Circuitscope", "PC connected", "Sampling P1,P2",
 *           "Sending data", "Ready", "HW error - reset"
 *   Line 2: box ID - first word of the 96-bit unique ID, e.g. "ID 0041002E",
 *           so a tutor can match a physical box to a submission.
 *
 * If no LCD answers at start-up (or it stops answering), every function
 * below does nothing: the measurement box keeps working without a display.
 * Never call these during an ADC capture (I2C switching adds noise).
 */
#ifndef LCD_H
#define LCD_H

#include <stdbool.h>

/** Find the LCD (address 0x27 or 0x3F), initialise it, show the start-up screen. */
void lcd_init(void);

/** True if an LCD was found and is still responding. */
bool lcd_present(void);

/** Show text on line 1 (cut/padded to 16 characters). */
void lcd_status(const char *text);

/** Show "PC connected" the first time a command arrives; later calls do nothing. */
void lcd_pc_connected(void);

#endif /* LCD_H */
