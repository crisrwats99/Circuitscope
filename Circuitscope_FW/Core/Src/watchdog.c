/**
 * @file    watchdog.c
 * @brief   Independent watchdog set-up using the IWDG registers directly
 *          (see watchdog.h).
 *
 * Timeout = (reload + 1) x prescaler / LSI
 *         = 1000 x 64 / 32 000 Hz = 2.0 s   (LSI varies ~29.5-34 kHz: 1.9-2.2 s)
 */
#include "watchdog.h"
#include "main.h"   /* CMSIS register definitions (IWDG, RCC, DBGMCU) */

/* IWDG key register values (reference manual RM0440, IWDG section) */
#define WDG_KEY_START    0xCCCCu   /* start the watchdog (also turns on LSI) */
#define WDG_KEY_UNLOCK   0x5555u   /* allow writes to PR and RLR             */
#define WDG_KEY_RELOAD   0xAAAAu   /* reload the counter ("kick")            */

#define WDG_PR_DIV64 4u        /* PR code 4 = divide LSI by 64 -> 500 Hz */
#define WDG_RELOAD       999u      /* 1000 ticks at 500 Hz = 2 s             */

void watchdog_init(void)
{
    /* Pause the watchdog while the debugger has the CPU halted (breakpoints),
     * otherwise every breakpoint longer than 2 s would reset the board. */
    DBGMCU->APB1FZR1 |= DBGMCU_APB1FZR1_DBG_IWDG_STOP;

    IWDG->KR  = WDG_KEY_START;           /* start counting down             */
    IWDG->KR  = WDG_KEY_UNLOCK;          /* unlock configuration            */
    IWDG->PR  = WDG_PR_DIV64;
    IWDG->RLR = WDG_RELOAD;
    while (IWDG->SR != 0u) {
        /* wait until the new prescaler/reload values are accepted (< 1 ms) */
    }
    IWDG->KR  = WDG_KEY_RELOAD;          /* load the new timeout now        */
}

void watchdog_kick(void)
{
    IWDG->KR = WDG_KEY_RELOAD;
}

bool watchdog_caused_reset(void)
{
    bool was_wdg = (RCC->CSR & RCC_CSR_IWDGRSTF) != 0u;
    RCC->CSR |= RCC_CSR_RMVF;             /* clear all reset-cause flags */
    return was_wdg;
}

#ifdef DEBUG
void watchdog_test_hang(void)
{
    /* Simulate a frozen program: no more kicks. The IWDG resets the chip
     * within ~2 s; after restart the LCD shows "Watchdog reset". */
    __disable_irq();
    while (1) {
    }
}
#endif
