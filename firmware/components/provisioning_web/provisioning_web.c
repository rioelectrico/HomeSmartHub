/*
 * HTTP without TLS is accepted only for the FW-1 LAN prototype
 * and is not the final product security model.
 */

#include "provisioning_web.h"

#include <stdlib.h>
#include <string.h>

#include "esp_http_server.h"
#include "esp_log.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "mbedtls/platform_util.h"

#include "device_config.h"

static const char *TAG = "prov_web";

static httpd_handle_t              s_server      = NULL;
static provisioning_web_restart_fn_t s_restart_fn = NULL;

/* ---------- helpers --------------------------------------------------- */

static void url_decode(char *str)
{
    char *src = str, *dst = str;
    while (*src) {
        if (*src == '%' && src[1] && src[2]) {
            char hex[3] = {src[1], src[2], 0};
            *dst++ = (char)strtol(hex, NULL, 16);
            src += 3;
        } else if (*src == '+') {
            *dst++ = ' ';
            src++;
        } else {
            *dst++ = *src++;
        }
    }
    *dst = '\0';
}

static int form_get_field(const char *body, const char *key,
                           char *dest, size_t dest_size)
{
    size_t klen = strlen(key);
    const char *p = body;
    while (*p) {
        if (strncmp(p, key, klen) == 0 && p[klen] == '=') {
            p += klen + 1;
            size_t i = 0;
            while (*p && *p != '&' && i < dest_size - 1) {
                dest[i++] = *p++;
            }
            dest[i] = '\0';
            url_decode(dest);
            return 0;
        }
        while (*p && *p != '&') { p++; }
        if (*p == '&') { p++; }
    }
    return -1;
}

/* ---------- HTML page ------------------------------------------------- */

static const char *HTML_FORM =
    "<!DOCTYPE html><html><head>"
    "<meta charset=\"utf-8\">"
    "<title>Portero - Configuracion</title>"
    "</head><body>"
    "<h1>Portero - Configuracion</h1>"
    "<form method=\"post\" action=\"/configure\">"
    "<p><label>Device ID:<br>"
    "<input name=\"device_id\" type=\"text\" size=\"12\"></label></p>"
    "<p><label>Backend URL:<br>"
    "<input name=\"backend_url\" type=\"text\" size=\"50\"></label></p>"
    "<p><label>Secret:<br>"
    "<input name=\"device_secret\" type=\"password\" size=\"40\"></label></p>"
    "<p><input type=\"submit\" value=\"Guardar configuracion\"></p>"
    "</form>"
    "</body></html>";

/* ---------- deferred restart ------------------------------------------ */

static void restart_task(void *arg)
{
    (void)arg;
    vTaskDelay(pdMS_TO_TICKS(200));
    provisioning_web_stop();
    if (s_restart_fn) {
        s_restart_fn();
    } else {
        esp_restart();
    }
    vTaskDelete(NULL);
}

/* ---------- URI handlers ---------------------------------------------- */

static esp_err_t get_root_handler(httpd_req_t *req)
{
    httpd_resp_set_type(req, "text/html");
    httpd_resp_send(req, HTML_FORM, strlen(HTML_FORM));
    return ESP_OK;
}

static esp_err_t post_configure_handler(httpd_req_t *req)
{
    /* 1. Verify Content-Type */
    char ct[80] = {0};
    if (httpd_req_get_hdr_value_str(req, "Content-Type", ct, sizeof(ct)) != ESP_OK ||
        strncmp(ct, "application/x-www-form-urlencoded",
                strlen("application/x-www-form-urlencoded")) != 0) {
        httpd_resp_set_status(req, "400 Bad Request");
        httpd_resp_sendstr(req, "Content-Type must be application/x-www-form-urlencoded");
        return ESP_OK;
    }

    /* 2. Enforce body size limit — read up to limit+1 to detect overflow */
    static char body[PROVISIONING_WEB_MAX_BODY_BYTES + 2];
    memset(body, 0, sizeof(body));

    int received = httpd_req_recv(req, body, sizeof(body) - 1);
    if (received < 0) {
        httpd_resp_set_status(req, "400 Bad Request");
        httpd_resp_sendstr(req, "Failed to read body");
        return ESP_OK;
    }
    if (received > PROVISIONING_WEB_MAX_BODY_BYTES) {
        mbedtls_platform_zeroize(body, sizeof(body));
        httpd_resp_set_status(req, "413 Payload Too Large");
        httpd_resp_sendstr(req, "Body too large");
        return ESP_OK;
    }
    body[received] = '\0';

    /* 3. Parse fields */
    char device_id[DEVICE_CONFIG_DEVICE_ID_BUFFER_SIZE]    = {0};
    char backend_url[DEVICE_CONFIG_BACKEND_URL_BUFFER_SIZE] = {0};
    char device_secret[DEVICE_CONFIG_SECRET_BUFFER_SIZE]    = {0};

    form_get_field(body, "device_id",     device_id,     sizeof(device_id));
    form_get_field(body, "backend_url",   backend_url,   sizeof(backend_url));
    form_get_field(body, "device_secret", device_secret, sizeof(device_secret));

    mbedtls_platform_zeroize(body, sizeof(body));

    /* 4. Validate */
    if (device_config_validate_device_id(device_id) != ESP_OK) {
        mbedtls_platform_zeroize(device_secret, sizeof(device_secret));
        httpd_resp_set_status(req, "400 Bad Request");
        httpd_resp_sendstr(req, "Invalid device_id");
        return ESP_OK;
    }
    if (device_config_validate_backend_url(backend_url) != ESP_OK) {
        mbedtls_platform_zeroize(device_secret, sizeof(device_secret));
        httpd_resp_set_status(req, "400 Bad Request");
        httpd_resp_sendstr(req, "Invalid backend_url");
        return ESP_OK;
    }
    if (device_config_validate_secret(device_secret) != ESP_OK) {
        mbedtls_platform_zeroize(device_secret, sizeof(device_secret));
        httpd_resp_set_status(req, "400 Bad Request");
        httpd_resp_sendstr(req, "Invalid device_secret");
        return ESP_OK;
    }

    /* 5. Save config */
    device_config_t cfg;
    memset(&cfg, 0, sizeof(cfg));
    strncpy(cfg.device_id,   device_id,     sizeof(cfg.device_id)   - 1);
    strncpy(cfg.backend_url, backend_url,   sizeof(cfg.backend_url) - 1);
    strncpy(cfg.secret,      device_secret, sizeof(cfg.secret)      - 1);

    mbedtls_platform_zeroize(device_secret, sizeof(device_secret));

    esp_err_t err = device_config_save(&cfg);
    mbedtls_platform_zeroize(cfg.secret, sizeof(cfg.secret));

    if (err != ESP_OK) {
        httpd_resp_set_status(req, "500 Internal Server Error");
        httpd_resp_sendstr(req, "Failed to save configuration");
        return ESP_OK;
    }

    /* 7. Respond 200 */
    httpd_resp_set_type(req, "text/html");
    httpd_resp_sendstr(req,
        "<!DOCTYPE html><html><body>"
        "<h1>Configuracion guardada. Reiniciando...</h1>"
        "</body></html>");

    /* 8. Deferred restart so response is flushed first */
    xTaskCreate(restart_task, "prov_restart", 2048, NULL, 5, NULL);
    return ESP_OK;
}

/* ---------- public API ------------------------------------------------ */

esp_err_t provisioning_web_start(void)
{
    if (s_server != NULL) {
        return ESP_ERR_INVALID_STATE;
    }

    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.lru_purge_enable = true;

    esp_err_t ret = httpd_start(&s_server, &config);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "httpd_start failed: %s", esp_err_to_name(ret));
        return ret;
    }

    static const httpd_uri_t uri_get = {
        .uri      = "/",
        .method   = HTTP_GET,
        .handler  = get_root_handler,
        .user_ctx = NULL,
    };
    static const httpd_uri_t uri_post = {
        .uri      = "/configure",
        .method   = HTTP_POST,
        .handler  = post_configure_handler,
        .user_ctx = NULL,
    };

    httpd_register_uri_handler(s_server, &uri_get);
    httpd_register_uri_handler(s_server, &uri_post);

    ESP_LOGI(TAG, "Provisioning portal active — open http://<device-ip>/ in browser");
    return ESP_OK;
}

esp_err_t provisioning_web_stop(void)
{
    if (s_server == NULL) {
        return ESP_OK;
    }
    httpd_stop(s_server);
    s_server = NULL;
    return ESP_OK;
}

bool provisioning_web_is_running(void)
{
    return s_server != NULL;
}

void provisioning_web_set_restart_fn(provisioning_web_restart_fn_t fn)
{
    s_restart_fn = fn;
}
