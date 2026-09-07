#include "device_protocol.h"

#include <limits.h>
#include <stdbool.h>
#include <string.h>

static const bool s_allowed_transitions[DEVICE_CONNECTION_STATE_COUNT]
                                       [DEVICE_CONNECTION_STATE_COUNT] = {
    [DEVICE_CONNECTION_BOOT] = {
        [DEVICE_CONNECTION_PROVISIONING] = true,
        [DEVICE_CONNECTION_NETWORK_CONNECTING] = true,
    },
    [DEVICE_CONNECTION_PROVISIONING] = {
    },
    [DEVICE_CONNECTION_NETWORK_CONNECTING] = {
        [DEVICE_CONNECTION_BOOT] = true,
        [DEVICE_CONNECTION_BACKEND_CONNECTING] = true,
    },
    [DEVICE_CONNECTION_BACKEND_CONNECTING] = {
        [DEVICE_CONNECTION_NETWORK_CONNECTING] = true,
        [DEVICE_CONNECTION_AUTHENTICATING] = true,
    },
    [DEVICE_CONNECTION_AUTHENTICATING] = {
        [DEVICE_CONNECTION_NETWORK_CONNECTING] = true,
        [DEVICE_CONNECTION_BACKEND_CONNECTING] = true,
        [DEVICE_CONNECTION_ONLINE] = true,
    },
    [DEVICE_CONNECTION_ONLINE] = {
        [DEVICE_CONNECTION_NETWORK_CONNECTING] = true,
    },
};

static bool is_boot_id_character_valid(char value)
{
    return (value >= 'A' && value <= 'Z') || (value >= 'a' && value <= 'z') ||
           (value >= '0' && value <= '9') || value == '_' || value == '-';
}

static bool is_boot_id_valid(const char *boot_id, size_t boot_id_length)
{
    size_t index;

    if (boot_id_length == 0U || boot_id_length > DEVICE_PROTOCOL_BOOT_ID_MAX_LENGTH) {
        return false;
    }

    for (index = 0U; index < boot_id_length; index++) {
        if (!is_boot_id_character_valid(boot_id[index])) {
            return false;
        }
    }

    return true;
}

esp_err_t device_protocol_init(device_protocol_context_t *context,
                               const char *boot_id,
                               uint32_t reconnect_backoff_ms)
{
    size_t boot_id_length;

    if (context == NULL || boot_id == NULL || reconnect_backoff_ms == 0U) {
        return ESP_ERR_INVALID_ARG;
    }

    boot_id_length = strnlen(boot_id, DEVICE_PROTOCOL_BOOT_ID_BUFFER_SIZE);
    if (!is_boot_id_valid(boot_id, boot_id_length) ||
        boot_id_length == DEVICE_PROTOCOL_BOOT_ID_BUFFER_SIZE) {
        return ESP_ERR_INVALID_ARG;
    }

    memset(context, 0, sizeof(*context));
    memcpy(context->boot_id, boot_id, boot_id_length);
    context->state = DEVICE_CONNECTION_BOOT;
    context->reconnect_backoff_ms = reconnect_backoff_ms;

    return ESP_OK;
}

device_connection_state_t device_protocol_get_state(const device_protocol_context_t *context)
{
    return context == NULL ? DEVICE_CONNECTION_STATE_COUNT : context->state;
}

esp_err_t device_protocol_transition(device_protocol_context_t *context,
                                     device_connection_state_t next_state)
{
    if (context == NULL || context->state < DEVICE_CONNECTION_BOOT ||
        context->state >= DEVICE_CONNECTION_STATE_COUNT || next_state < DEVICE_CONNECTION_BOOT ||
        next_state >= DEVICE_CONNECTION_STATE_COUNT) {
        return ESP_ERR_INVALID_ARG;
    }

    if (!s_allowed_transitions[context->state][next_state]) {
        return ESP_ERR_INVALID_STATE;
    }

    context->state = next_state;
    return ESP_OK;
}

esp_err_t device_protocol_transition_to_missing_config(device_protocol_context_t *context)
{
    return device_protocol_transition(context, DEVICE_CONNECTION_PROVISIONING);
}

uint64_t device_protocol_hello_sequence(const device_protocol_context_t *context)
{
    (void)context;
    return 0U;
}

esp_err_t device_protocol_next_sequence(device_protocol_context_t *context, uint64_t *sequence)
{
    if (context == NULL || sequence == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    if (context->next_seq >= (uint64_t)INT64_MAX) {
        return ESP_ERR_INVALID_STATE;
    }

    context->next_seq++;
    *sequence = context->next_seq;

    return ESP_OK;
}

esp_err_t device_protocol_set_reconnect_backoff(device_protocol_context_t *context,
                                                uint32_t reconnect_backoff_ms)
{
    if (context == NULL || reconnect_backoff_ms == 0U) {
        return ESP_ERR_INVALID_ARG;
    }

    context->reconnect_backoff_ms = reconnect_backoff_ms;
    return ESP_OK;
}
