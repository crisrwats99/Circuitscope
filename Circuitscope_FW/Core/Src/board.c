/**
 * @file    board.c
 * @brief   Chip unique ID and status LED.
 */
#include "board.h"
#include "main.h"   /* HAL, LED_STATUS_Pin / LED_STATUS_GPIO_Port labels */

/* The STM32G4 stores a 96-bit factory-programmed unique ID at UID_BASE
 * (0x1FFF7590). It never changes and differs between chips, so the PC uses
 * it as the watermark that identifies which box produced a submission. */
void board_get_uid(uint32_t uid[3])
{
    uid[0] = HAL_GetUIDw0();   /* lowest address  */
    uid[1] = HAL_GetUIDw1();
    uid[2] = HAL_GetUIDw2();   /* highest address */
}

void board_led_toggle(void)
{
    HAL_GPIO_TogglePin(LED_STATUS_GPIO_Port, LED_STATUS_Pin);
}
