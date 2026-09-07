#include "device_config.h"

#include <string.h>

#include "nvs.h"
#include "nvs_flash.h"

#define NVS_NAMESPACE   "portero_cfg"
#define NVS_KEY_ACTIVE  "active"

static const char *s_id_keys[2]  = { "a_id",  "b_id"  };
static const char *s_sec_keys[2] = { "a_sec", "b_sec" };
static const char *s_url_keys[2] = { "a_url", "b_url" };
static const char *s_ok_keys[2]  = { "a_ok",  "b_ok"  };

esp_err_t device_config_validate_device_id(const char *device_id)
{
    size_t i;

    if (device_id == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (strnlen(device_id, DEVICE_CONFIG_DEVICE_ID_BUFFER_SIZE) !=
        DEVICE_CONFIG_DEVICE_ID_BUFFER_SIZE - 1U) {
        return ESP_ERR_INVALID_ARG;
    }
    if (device_id[0] != 'P' || device_id[1] != 'I' || device_id[2] != '-') {
        return ESP_ERR_INVALID_ARG;
    }
    for (i = 3U; i < DEVICE_CONFIG_DEVICE_ID_BUFFER_SIZE - 1U; i++) {
        if (device_id[i] < '0' || device_id[i] > '9') {
            return ESP_ERR_INVALID_ARG;
        }
    }
    return ESP_OK;
}

esp_err_t device_config_validate_secret(const char *secret)
{
    size_t len;

    if (secret == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    len = strnlen(secret, DEVICE_CONFIG_SECRET_BUFFER_SIZE);
    if (len < DEVICE_CONFIG_SECRET_MIN_LENGTH ||
        len >= DEVICE_CONFIG_SECRET_BUFFER_SIZE) {
        return ESP_ERR_INVALID_ARG;
    }
    return ESP_OK;
}

esp_err_t device_config_validate_backend_url(const char *backend_url)
{
    const char *after_scheme;
    const char *path_sep;
    size_t host_len;
    char host_buf[DEVICE_CONFIG_BACKEND_URL_BUFFER_SIZE];
    char *port_sep;

    if (backend_url == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (strncmp(backend_url, "ws://", 5) != 0) {
        return ESP_ERR_INVALID_ARG;
    }
    if (strchr(backend_url, '@') != NULL ||
        strchr(backend_url, '?') != NULL ||
        strchr(backend_url, '#') != NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    after_scheme = backend_url + 5;
    path_sep = strchr(after_scheme, '/');
    if (path_sep == NULL || strcmp(path_sep, "/ws/device") != 0) {
        return ESP_ERR_INVALID_ARG;
    }

    host_len = (size_t)(path_sep - after_scheme);
    if (host_len == 0U || host_len >= sizeof(host_buf)) {
        return ESP_ERR_INVALID_ARG;
    }
    memcpy(host_buf, after_scheme, host_len);
    host_buf[host_len] = '\0';

    /* Reject IPv6 loopback in any form before port stripping */
    if (strstr(host_buf, "::1") != NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    /* Strip port for non-bracketed hosts */
    if (host_buf[0] != '[') {
        port_sep = strrchr(host_buf, ':');
        if (port_sep != NULL) {
            *port_sep = '\0';
        }
    }

    if (strcmp(host_buf, "localhost") == 0 ||
        strncmp(host_buf, "127.", 4) == 0) {
        return ESP_ERR_INVALID_ARG;
    }

    return ESP_OK;
}

static esp_err_t open_nvs(nvs_open_mode_t mode, nvs_handle_t *handle)
{
    return nvs_open(NVS_NAMESPACE, mode, handle);
}

static bool slot_is_valid(nvs_handle_t handle, uint8_t slot)
{
    uint8_t ok = 0;

    return nvs_get_u8(handle, s_ok_keys[slot], &ok) == ESP_OK && ok == 1U;
}

bool device_config_is_provisioned(void)
{
    nvs_handle_t handle;
    uint8_t active;
    bool result = false;

    if (open_nvs(NVS_READONLY, &handle) != ESP_OK) {
        return false;
    }
    if (nvs_get_u8(handle, NVS_KEY_ACTIVE, &active) == ESP_OK && active <= 1U) {
        result = slot_is_valid(handle, active);
    }
    nvs_close(handle);
    return result;
}

esp_err_t device_config_load(device_config_t *out)
{
    nvs_handle_t handle;
    uint8_t active;
    uint8_t slot;
    size_t len;
    esp_err_t err;

    if (out == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    err = open_nvs(NVS_READONLY, &handle);
    if (err != ESP_OK) {
        return ESP_ERR_NOT_FOUND;
    }

    err = nvs_get_u8(handle, NVS_KEY_ACTIVE, &active);
    if (err != ESP_OK || active > 1U) {
        nvs_close(handle);
        return ESP_ERR_NOT_FOUND;
    }

    if (slot_is_valid(handle, active)) {
        slot = active;
    } else {
        /* Active slot corrupt — try alternate */
        slot = 1U - active;
        if (!slot_is_valid(handle, slot)) {
            nvs_close(handle);
            return ESP_ERR_NOT_FOUND;
        }
    }

    len = DEVICE_CONFIG_DEVICE_ID_BUFFER_SIZE;
    err = nvs_get_str(handle, s_id_keys[slot], out->device_id, &len);
    if (err != ESP_OK) {
        goto done;
    }

    len = DEVICE_CONFIG_SECRET_BUFFER_SIZE;
    err = nvs_get_str(handle, s_sec_keys[slot], out->secret, &len);
    if (err != ESP_OK) {
        goto done;
    }

    len = DEVICE_CONFIG_BACKEND_URL_BUFFER_SIZE;
    err = nvs_get_str(handle, s_url_keys[slot], out->backend_url, &len);

done:
    nvs_close(handle);
    return err;
}

esp_err_t device_config_save(const device_config_t *config)
{
    nvs_handle_t handle;
    uint8_t active;
    uint8_t inactive;
    esp_err_t err;

    if (config == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    err = device_config_validate_device_id(config->device_id);
    if (err != ESP_OK) {
        return err;
    }
    err = device_config_validate_secret(config->secret);
    if (err != ESP_OK) {
        return err;
    }
    err = device_config_validate_backend_url(config->backend_url);
    if (err != ESP_OK) {
        return err;
    }

    err = open_nvs(NVS_READWRITE, &handle);
    if (err != ESP_OK) {
        return err;
    }

    /* Default active=1 so first write goes to slot 0 */
    active = 1U;
    nvs_get_u8(handle, NVS_KEY_ACTIVE, &active);
    if (active > 1U) {
        active = 1U;
    }
    inactive = 1U - active;

    /* Write all fields to the inactive slot */
    err = nvs_set_str(handle, s_id_keys[inactive], config->device_id);
    if (err != ESP_OK) {
        goto done;
    }
    err = nvs_set_str(handle, s_sec_keys[inactive], config->secret);
    if (err != ESP_OK) {
        goto done;
    }
    err = nvs_set_str(handle, s_url_keys[inactive], config->backend_url);
    if (err != ESP_OK) {
        goto done;
    }
    err = nvs_set_u8(handle, s_ok_keys[inactive], 1U);
    if (err != ESP_OK) {
        goto done;
    }

    /* Commit data before switching active pointer */
    err = nvs_commit(handle);
    if (err != ESP_OK) {
        goto done;
    }

    /* Switch active pointer */
    err = nvs_set_u8(handle, NVS_KEY_ACTIVE, inactive);
    if (err != ESP_OK) {
        goto done;
    }
    err = nvs_commit(handle);

done:
    nvs_close(handle);
    return err;
}

esp_err_t device_config_erase(void)
{
    nvs_handle_t handle;
    esp_err_t err;

    err = open_nvs(NVS_READWRITE, &handle);
    if (err != ESP_OK) {
        return err;
    }
    err = nvs_erase_all(handle);
    if (err == ESP_OK) {
        err = nvs_commit(handle);
    }
    nvs_close(handle);
    return err;
}
