/**
 * @file    measure.c
 * @brief   SAMPLE capture on the hardware: TIM2 -> ADC1/ADC2 -> DMA -> DATA/DONE.
 *
 * How one capture works
 *   1. sampling_plan() decides which ADC reads which channel (see sampling.c).
 *   2. Each ADC that takes part is re-configured: 1 channel, or 2 in scan mode.
 *   3. TIM2 is loaded with the divider for the requested rate.
 *   4. DMA is armed on each ADC (n or 2n transfers), then TIM2 is started.
 *      Every TIM2 update event (TRGO) triggers ONE conversion on each ADC,
 *      so sample spacing is set by the hardware timer, not by software.
 *   5. When DMA has stored all samples, its interrupt calls
 *      HAL_ADC_ConvCpltCallback(), which stops TIM2 at once.
 *   6. Only then are the samples sent (capture first, then send - as the
 *      brief requires), followed by DONE with the rate really achieved.
 *
 * While capturing, nothing else is switched (no LED, no LCD) to keep noise low.
 * The LCD is updated only BEFORE the ADCs are armed ("Sampling P1,P2") and
 * AFTER they have stopped ("Sending data", then "Ready").
 */
#include "measure.h"
#include "sampling.h"
#include "channels.h"
#include "protocol.h"   /* MAX_POINTS */
#include "comms.h"
#include "lcd.h"
#include "watchdog.h"
#include "adc.h"        /* hadc1, hadc2 (CubeMX) */
#include "tim.h"        /* htim2 (CubeMX)        */
#include <stdio.h>

/* One shared sample buffer: 2 x MAX_POINTS x 2 bytes = 16 KB.
 *  - one ADC:            elements 0 .. n-1            (or 0 .. 2n-1 in scan)
 *  - both ADCs:          ADC1 -> 0 .. n-1,  ADC2 -> MAX_POINTS .. MAX_POINTS+n-1 */
static uint16_t s_buf[2u * MAX_POINTS];

/* Completion flags, set from the DMA interrupt (hence volatile). */
static volatile bool s_done[2];    /* [0] ADC1, [1] ADC2 */
static volatile bool s_error;

static ADC_HandleTypeDef *const s_hadc[2] = { &hadc1, &hadc2 };

/* ------------------------------------------------------------------------ */
/*  Start-up                                                                */
/* ------------------------------------------------------------------------ */

void measure_init(void)
{
    /* Internal offset calibration; ADCs must be disabled (they are after MX init). */
    HAL_ADCEx_Calibration_Start(&hadc1, ADC_SINGLE_ENDED);
    HAL_ADCEx_Calibration_Start(&hadc2, ADC_SINGLE_ENDED);
}

/* ------------------------------------------------------------------------ */
/*  Hardware helpers                                                        */
/* ------------------------------------------------------------------------ */

/**
 * @brief Re-configure one ADC for this capture: 1 channel, or 2 in scan mode,
 *        triggered by TIM2 TRGO, one DMA request per conversion.
 * @param ch  HAL channel constants for rank 1 (and rank 2)
 */
static HAL_StatusTypeDef adc_setup(ADC_HandleTypeDef *h, uint8_t n_ranks, const uint32_t ch[2])
{
    ADC_ChannelConfTypeDef cfg = {0};

    /* Only the fields that change per capture; the rest come from CubeMX. */
    h->Init.ScanConvMode          = (n_ranks > 1u) ? ADC_SCAN_ENABLE : ADC_SCAN_DISABLE;
    h->Init.NbrOfConversion       = n_ranks;
    h->Init.ContinuousConvMode    = DISABLE;                    /* timer paces it */
    h->Init.DiscontinuousConvMode = DISABLE;
    h->Init.ExternalTrigConv      = ADC_EXTERNALTRIG_T2_TRGO;
    h->Init.ExternalTrigConvEdge  = ADC_EXTERNALTRIGCONVEDGE_RISING;
    h->Init.DMAContinuousRequests = DISABLE;                    /* stop after n   */
    h->Init.Overrun               = ADC_OVR_DATA_OVERWRITTEN;
    if (HAL_ADC_Init(h) != HAL_OK) {
        return HAL_ERROR;
    }

    cfg.SamplingTime = ADC_SAMPLETIME_47CYCLES_5;  /* 1.12 us sampling: low noise */
    cfg.SingleDiff   = ADC_SINGLE_ENDED;
    cfg.OffsetNumber = ADC_OFFSET_NONE;
    cfg.Offset       = 0;
    for (uint8_t r = 0; r < n_ranks; r++) {
        cfg.Channel = ch[r];
        cfg.Rank    = (r == 0u) ? ADC_REGULAR_RANK_1 : ADC_REGULAR_RANK_2;
        if (HAL_ADC_ConfigChannel(h, &cfg) != HAL_OK) {
            return HAL_ERROR;
        }
    }
    return HAL_OK;
}

/** Load TIM2 so it overflows every `divider` clock ticks (rate = 170 MHz / divider). */
static void timer_setup(uint32_t divider)
{
    HAL_TIM_Base_Stop(&htim2);
    __HAL_TIM_SET_PRESCALER(&htim2, 0u);
    __HAL_TIM_SET_AUTORELOAD(&htim2, divider - 1u);
    __HAL_TIM_SET_COUNTER(&htim2, 0u);
    /* Force an update so the new values take effect now. This also pulses
     * TRGO once - harmless, because the ADCs are not armed yet. */
    htim2.Instance->EGR = TIM_EGR_UG;
    __HAL_TIM_CLEAR_FLAG(&htim2, TIM_FLAG_UPDATE);
}

/** Stop the timer and both ADCs, whatever state they are in. */
static void capture_stop(const capture_plan_t *plan)
{
    __HAL_TIM_DISABLE(&htim2);
    HAL_TIM_Base_Stop(&htim2);
    for (int a = 0; a < 2; a++) {
        if (plan->adc[a].used) {
            HAL_ADC_Stop_DMA(s_hadc[a]);
        }
    }
}

/** Send DATA text and keep the watchdog alive: a 4000-sample line takes
 *  ~1.6 s at 115200 baud, so it is kicked after every 128-byte chunk. */
static void send_and_kick(const char *data, size_t len)
{
    comms_send(data, len);
    watchdog_kick();
}

/** Send "ERR HWFAIL <message>". */
static void reply_hwfail(const char *msg)
{
    char line[160];
    snprintf(line, sizeof line, "ERR HWFAIL %s", msg);
    comms_send_line(line);
    lcd_status("HW error - reset");
}

/* ------------------------------------------------------------------------ */
/*  DMA / ADC callbacks (called by HAL from the DMA interrupt)              */
/* ------------------------------------------------------------------------ */

void HAL_ADC_ConvCpltCallback(ADC_HandleTypeDef *hadc)
{
    /* Last sample stored: stop the timer immediately so no extra
     * conversions happen. In a simultaneous capture both ADCs were
     * triggered by the same pulse, so the other one is finishing too. */
    __HAL_TIM_DISABLE(&htim2);
    if (hadc->Instance == ADC1) s_done[0] = true;
    if (hadc->Instance == ADC2) s_done[1] = true;
}

void HAL_ADC_ErrorCallback(ADC_HandleTypeDef *hadc)
{
    /* Errors after an ADC has finished (e.g. overrun once DMA stopped) are harmless. */
    if (hadc->Instance == ADC1 && !s_done[0]) s_error = true;
    if (hadc->Instance == ADC2 && !s_done[1]) s_error = true;
}

/* ------------------------------------------------------------------------ */
/*  SAMPLE                                                                  */
/* ------------------------------------------------------------------------ */

void measure_run(const uint8_t *ch_index, uint8_t n_ch, uint32_t rate_hz, uint32_t n)
{
    capture_plan_t plan;
    uint8_t        adc_code[2];
    uint32_t       divider;
    char           line[96];

    /* ---- 1. Plan ---- */
    for (uint8_t k = 0; k < n_ch; k++) {
        adc_code[k] = g_channels[ch_index[k]].adc;
    }
    if (!sampling_plan(adc_code, n_ch, n, MAX_POINTS, &plan)) {
        reply_hwfail("internal error: could not plan this capture; please report the command used");
        return;
    }

    /* ---- 2. Configure the ADC(s) taking part ---- */
    for (int a = 0; a < 2; a++) {
        if (!plan.adc[a].used) continue;
        uint32_t ch[2];
        for (uint8_t r = 0; r < plan.adc[a].n_ranks; r++) {
            ch[r] = g_channels[ch_index[plan.adc[a].rank_req[r]]].hal_channel;
        }
        if (adc_setup(s_hadc[a], plan.adc[a].n_ranks, ch) != HAL_OK) {
            snprintf(line, sizeof line, "ADC%d configuration failed; reset the box", a + 1);
            reply_hwfail(line);
            return;
        }
    }

    /* ---- LCD: show what is being captured (last I2C traffic before sampling) ---- */
    snprintf(line, sizeof line, "Sampling %s%s%s", g_channels[ch_index[0]].name,
             (n_ch == 2u) ? "," : "", (n_ch == 2u) ? g_channels[ch_index[1]].name : "");
    lcd_status(line);

    /* ---- 3. Timer ---- */
    divider = sampling_divider(rate_hz);
    timer_setup(divider);

    /* ---- 4. Arm DMA on each ADC, then start the timer ---- */
    s_done[0] = s_done[1] = false;
    s_error   = false;
    for (int a = 1; a >= 0; a--) {             /* ADC2 first, then ADC1 */
        if (!plan.adc[a].used) continue;
        if (HAL_ADC_Start_DMA(s_hadc[a], (uint32_t *)&s_buf[plan.adc[a].buf_offset],
                              plan.adc[a].length) != HAL_OK) {
            capture_stop(&plan);
            snprintf(line, sizeof line, "ADC%d DMA start failed; reset the box", a + 1);
            reply_hwfail(line);
            return;
        }
    }
    HAL_TIM_Base_Start(&htim2);

    /* ---- 5. Wait for all samples (expected time + 1 s margin) ---- */
    uint32_t limit_ms = sampling_duration_ms(divider, n) + 1000u;
    uint32_t t0       = HAL_GetTick();
    while ((plan.adc[0].used && !s_done[0]) || (plan.adc[1].used && !s_done[1])) {
        watchdog_kick();                 /* long captures (up to ~4000 s) are normal */
        if (s_error) {
            capture_stop(&plan);
            reply_hwfail("ADC/DMA error during capture; try again, reset the box if it repeats");
            return;
        }
        if ((HAL_GetTick() - t0) > limit_ms) {
            capture_stop(&plan);
            snprintf(line, sizeof line, "capture timed out after %lu ms; check TIM2 TRGO = Update Event",
                     (unsigned long)limit_ms);
            reply_hwfail(line);
            return;
        }
    }
    capture_stop(&plan);
    lcd_status("Sending data");          /* ADCs stopped: safe to use I2C again */

    /* ---- 6. Send each channel in the order asked, then DONE ---- */
    for (uint8_t k = 0; k < n_ch; k++) {
        sampling_send_data(s_buf, plan.out_offset[k], plan.out_stride[k], n, send_and_kick);
    }
    sampling_format_done(line, sizeof line, divider);
    comms_send_line(line);
    lcd_status("Ready");
}
