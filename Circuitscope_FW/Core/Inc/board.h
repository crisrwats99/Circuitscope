/**
 * @file    board.h
 * @brief   Small board helpers: chip unique ID and the status LED.
 */
#ifndef BOARD_H
#define BOARD_H

#include <stdint.h>

/** Read the 96-bit factory unique ID as three 32-bit words (w0 = lowest address). */
void board_get_uid(uint32_t uid[3]);

/** Toggle the green LED (LD2 on PB8). Used to show a command was received. */
void board_led_toggle(void);

#endif /* BOARD_H */
