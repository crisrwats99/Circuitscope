/**
 * @file    watchdog.h
 * @brief   Independent watchdog (IWDG): resets the box if the firmware freezes.
 *
 * The IWDG runs from its own 32 kHz clock (LSI), separate from the main
 * clock, so it still works if the main program locks up. Once started it
 * cannot be stopped: the program must call watchdog_kick() at least every
 * ~2 s, or the chip resets itself and starts again cleanly.
 *
 * Kicked from: the main loop, the capture wait loop and between DATA chunks,
 * so even a 4000-second capture at 1 Hz never triggers it.
 *
 * Configured in code (direct registers) - no CubeMX change is needed.
 */
#ifndef WATCHDOG_H
#define WATCHDOG_H

#include <stdbool.h>

/** Start the watchdog with a ~2 s timeout. Call last in start-up, just before the main loop. */
void watchdog_init(void);

/** Reload the watchdog counter ("I am still alive"). */
void watchdog_kick(void);

/**
 * @brief  True if the last reset was caused by the watchdog. Also clears the
 *         chip's reset-cause flags, so call it once at start-up.
 */
bool watchdog_caused_reset(void);

#ifdef DEBUG
/** Debug builds only: stop kicking and wait, to prove the watchdog resets the box. */
void watchdog_test_hang(void);
#endif

#endif /* WATCHDOG_H */
