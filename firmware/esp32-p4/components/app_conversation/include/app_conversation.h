#pragma once

#include <stdbool.h>
#include "esp_err.h"
#include "portero_codec.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    CONV_STATE_IDLE,
    CONV_STATE_ACTIVE,
} conv_state_t;

/**
 * Initialize conversation FSM. Call after board_audio_init().
 */
esp_err_t app_conversation_init(void);

/** Called from ws_transport callback on conversation.start. */
void app_conversation_on_start(const portero_conversation_start_t *msg);

/** Called from ws_transport callback on conversation.stop. */
void app_conversation_on_stop(const portero_conversation_stop_t *msg);

/** Called from ws_transport callback on conversation.audio.clear. */
void app_conversation_on_audio_clear(const portero_conversation_audio_clear_t *msg);

/** Send device.ring to backend (doorbell trigger). */
void app_conversation_ring(void);

/**
 * Trigger barge-in: flush speaker buffer.
 * Called from audio TX pipeline (FW2-11) when sustained voice detected.
 */
void app_conversation_barge_in(void);

conv_state_t app_conversation_get_state(void);
const char  *app_conversation_get_stream_id(void);

/** FW2-12 checks this to know when to flush the speaker queue. */
bool app_conversation_is_barge_pending(void);
void app_conversation_clear_barge_pending(void);

#ifdef __cplusplus
}
#endif
