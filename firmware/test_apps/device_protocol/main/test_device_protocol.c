#include "unity.h"

#include <limits.h>
#include <string.h>

#include "device_protocol.h"

static void assert_u64_equal(uint64_t expected, uint64_t actual)
{
    TEST_ASSERT_EQUAL_UINT32((uint32_t)(expected >> 32U), (uint32_t)(actual >> 32U));
    TEST_ASSERT_EQUAL_UINT32((uint32_t)expected, (uint32_t)actual);
}

static void test_transitions_reach_online_in_connection_order(void)
{
    device_protocol_context_t context;

    TEST_ASSERT_EQUAL(ESP_OK, device_protocol_init(&context, "test-boot-id", 1000U));
    TEST_ASSERT_EQUAL(DEVICE_CONNECTION_BOOT, device_protocol_get_state(&context));
    TEST_ASSERT_EQUAL(ESP_OK, device_protocol_transition(&context, DEVICE_CONNECTION_NETWORK_CONNECTING));
    TEST_ASSERT_EQUAL(ESP_OK, device_protocol_transition(&context, DEVICE_CONNECTION_BACKEND_CONNECTING));
    TEST_ASSERT_EQUAL(ESP_OK, device_protocol_transition(&context, DEVICE_CONNECTION_AUTHENTICATING));
    TEST_ASSERT_EQUAL(ESP_OK, device_protocol_transition(&context, DEVICE_CONNECTION_ONLINE));
    TEST_ASSERT_EQUAL(DEVICE_CONNECTION_ONLINE, device_protocol_get_state(&context));
}

static void test_missing_config_transitions_to_provisioning_without_consuming_sequence(void)
{
    device_protocol_context_t context;
    uint64_t sequence = 0U;

    TEST_ASSERT_EQUAL(ESP_OK, device_protocol_init(&context, "test-boot-id", 1000U));
    TEST_ASSERT_EQUAL(ESP_OK, device_protocol_transition_to_missing_config(&context));
    TEST_ASSERT_EQUAL(DEVICE_CONNECTION_PROVISIONING, device_protocol_get_state(&context));
    assert_u64_equal(0U, device_protocol_hello_sequence(&context));
    TEST_ASSERT_EQUAL(ESP_OK, device_protocol_next_sequence(&context, &sequence));
    assert_u64_equal(1U, sequence);
    TEST_ASSERT_EQUAL(ESP_OK, device_protocol_next_sequence(&context, &sequence));
    assert_u64_equal(2U, sequence);
}

static void test_provisioning_rejects_network_start_until_restart(void)
{
    device_protocol_context_t context;

    TEST_ASSERT_EQUAL(ESP_OK, device_protocol_init(&context, "test-boot-id", 1000U));
    TEST_ASSERT_EQUAL(ESP_OK, device_protocol_transition_to_missing_config(&context));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_STATE,
                      device_protocol_transition(&context, DEVICE_CONNECTION_NETWORK_CONNECTING));
    TEST_ASSERT_EQUAL(DEVICE_CONNECTION_PROVISIONING, device_protocol_get_state(&context));
}

static void test_invalid_transition_is_rejected_without_changing_state(void)
{
    device_protocol_context_t context;

    TEST_ASSERT_EQUAL(ESP_OK, device_protocol_init(&context, "test-boot-id", 1000U));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_STATE,
                      device_protocol_transition(&context, DEVICE_CONNECTION_ONLINE));
    TEST_ASSERT_EQUAL(DEVICE_CONNECTION_BOOT, device_protocol_get_state(&context));
}

static void test_negative_next_state_is_rejected_without_changing_state(void)
{
    device_protocol_context_t context;

    TEST_ASSERT_EQUAL(ESP_OK, device_protocol_init(&context, "test-boot-id", 1000U));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG,
                      device_protocol_transition(&context, (device_connection_state_t)-1));
    TEST_ASSERT_EQUAL(DEVICE_CONNECTION_BOOT, device_protocol_get_state(&context));
}

static void test_out_of_range_next_state_is_rejected_without_changing_state(void)
{
    device_protocol_context_t context;

    TEST_ASSERT_EQUAL(ESP_OK, device_protocol_init(&context, "test-boot-id", 1000U));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG,
                      device_protocol_transition(&context, DEVICE_CONNECTION_STATE_COUNT));
    TEST_ASSERT_EQUAL(DEVICE_CONNECTION_BOOT, device_protocol_get_state(&context));
}

static void test_out_of_range_context_state_is_rejected_before_transition_lookup(void)
{
    device_protocol_context_t context;

    TEST_ASSERT_EQUAL(ESP_OK, device_protocol_init(&context, "test-boot-id", 1000U));
    context.state = (device_connection_state_t)-1;
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG,
                      device_protocol_transition(&context, DEVICE_CONNECTION_NETWORK_CONNECTING));
}

static void test_upper_out_of_range_context_state_is_rejected_before_transition_lookup(void)
{
    device_protocol_context_t context;

    TEST_ASSERT_EQUAL(ESP_OK, device_protocol_init(&context, "test-boot-id", 1000U));
    context.state = DEVICE_CONNECTION_STATE_COUNT;
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG,
                      device_protocol_transition(&context, DEVICE_CONNECTION_NETWORK_CONNECTING));
}

static void test_authentication_disconnection_returns_to_network_connecting(void)
{
    device_protocol_context_t context;

    TEST_ASSERT_EQUAL(ESP_OK, device_protocol_init(&context, "test-boot-id", 1000U));
    TEST_ASSERT_EQUAL(ESP_OK, device_protocol_transition(&context, DEVICE_CONNECTION_NETWORK_CONNECTING));
    TEST_ASSERT_EQUAL(ESP_OK, device_protocol_transition(&context, DEVICE_CONNECTION_BACKEND_CONNECTING));
    TEST_ASSERT_EQUAL(ESP_OK, device_protocol_transition(&context, DEVICE_CONNECTION_AUTHENTICATING));
    TEST_ASSERT_EQUAL(ESP_OK, device_protocol_transition(&context, DEVICE_CONNECTION_NETWORK_CONNECTING));
    TEST_ASSERT_EQUAL(DEVICE_CONNECTION_NETWORK_CONNECTING, device_protocol_get_state(&context));
}

static void test_online_disconnection_returns_to_network_connecting(void)
{
    device_protocol_context_t context;

    TEST_ASSERT_EQUAL(ESP_OK, device_protocol_init(&context, "test-boot-id", 1000U));
    TEST_ASSERT_EQUAL(ESP_OK, device_protocol_transition(&context, DEVICE_CONNECTION_NETWORK_CONNECTING));
    TEST_ASSERT_EQUAL(ESP_OK, device_protocol_transition(&context, DEVICE_CONNECTION_BACKEND_CONNECTING));
    TEST_ASSERT_EQUAL(ESP_OK, device_protocol_transition(&context, DEVICE_CONNECTION_AUTHENTICATING));
    TEST_ASSERT_EQUAL(ESP_OK, device_protocol_transition(&context, DEVICE_CONNECTION_ONLINE));
    TEST_ASSERT_EQUAL(ESP_OK, device_protocol_transition(&context, DEVICE_CONNECTION_NETWORK_CONNECTING));
    TEST_ASSERT_EQUAL(DEVICE_CONNECTION_NETWORK_CONNECTING, device_protocol_get_state(&context));
}

static void test_outbound_sequence_is_preserved_across_reconnect_transition(void)
{
    device_protocol_context_t context;
    uint64_t sequence = 0U;

    TEST_ASSERT_EQUAL(ESP_OK, device_protocol_init(&context, "test-boot-id", 1000U));
    TEST_ASSERT_EQUAL(ESP_OK, device_protocol_next_sequence(&context, &sequence));
    assert_u64_equal(1U, sequence);
    TEST_ASSERT_EQUAL(ESP_OK, device_protocol_transition(&context, DEVICE_CONNECTION_NETWORK_CONNECTING));
    TEST_ASSERT_EQUAL(ESP_OK, device_protocol_transition(&context, DEVICE_CONNECTION_BACKEND_CONNECTING));
    TEST_ASSERT_EQUAL(ESP_OK, device_protocol_transition(&context, DEVICE_CONNECTION_AUTHENTICATING));
    TEST_ASSERT_EQUAL(ESP_OK, device_protocol_transition(&context, DEVICE_CONNECTION_ONLINE));
    TEST_ASSERT_EQUAL(ESP_OK, device_protocol_transition(&context, DEVICE_CONNECTION_NETWORK_CONNECTING));
    TEST_ASSERT_EQUAL(ESP_OK, device_protocol_next_sequence(&context, &sequence));
    assert_u64_equal(2U, sequence);
}

static void test_outbound_sequence_stops_at_v1_signed_maximum(void)
{
    device_protocol_context_t context;
    uint64_t sequence = 0U;

    TEST_ASSERT_EQUAL(ESP_OK, device_protocol_init(&context, "test-boot-id", 1000U));
    context.next_seq = (uint64_t)INT64_MAX - 1U;
    TEST_ASSERT_EQUAL(ESP_OK, device_protocol_next_sequence(&context, &sequence));
    assert_u64_equal((uint64_t)INT64_MAX, sequence);
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_STATE, device_protocol_next_sequence(&context, &sequence));
    assert_u64_equal((uint64_t)INT64_MAX, context.next_seq);
}

static void test_null_sequence_output_is_rejected_without_advancing_sequence(void)
{
    device_protocol_context_t context;

    TEST_ASSERT_EQUAL(ESP_OK, device_protocol_init(&context, "test-boot-id", 1000U));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, device_protocol_next_sequence(&context, NULL));
    assert_u64_equal(0U, context.next_seq);
}

static void test_null_context_is_rejected_for_sequence_functions(void)
{
    uint64_t sequence = 0U;

    assert_u64_equal(0U, device_protocol_hello_sequence(NULL));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, device_protocol_next_sequence(NULL, &sequence));
}

static void test_boot_id_accepts_v1_grammar_through_128_characters(void)
{
    device_protocol_context_t context;
    char boot_id[129];

    memset(boot_id, 'a', sizeof(boot_id) - 1U);
    boot_id[0] = 'A';
    boot_id[1] = '_';
    boot_id[2] = '-';
    boot_id[sizeof(boot_id) - 1U] = '\0';

    TEST_ASSERT_EQUAL(ESP_OK, device_protocol_init(&context, boot_id, 1000U));
    TEST_ASSERT_EQUAL_STRING(boot_id, context.boot_id);
}

static void test_boot_id_rejects_characters_outside_v1_grammar(void)
{
    device_protocol_context_t context;

    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, device_protocol_init(&context, "invalid.boot", 1000U));
}

void app_main(void)
{
    UNITY_BEGIN();
    RUN_TEST(test_transitions_reach_online_in_connection_order);
    RUN_TEST(test_missing_config_transitions_to_provisioning_without_consuming_sequence);
    RUN_TEST(test_provisioning_rejects_network_start_until_restart);
    RUN_TEST(test_invalid_transition_is_rejected_without_changing_state);
    RUN_TEST(test_negative_next_state_is_rejected_without_changing_state);
    RUN_TEST(test_out_of_range_next_state_is_rejected_without_changing_state);
    RUN_TEST(test_out_of_range_context_state_is_rejected_before_transition_lookup);
    RUN_TEST(test_upper_out_of_range_context_state_is_rejected_before_transition_lookup);
    RUN_TEST(test_authentication_disconnection_returns_to_network_connecting);
    RUN_TEST(test_online_disconnection_returns_to_network_connecting);
    RUN_TEST(test_outbound_sequence_is_preserved_across_reconnect_transition);
    RUN_TEST(test_outbound_sequence_stops_at_v1_signed_maximum);
    RUN_TEST(test_null_sequence_output_is_rejected_without_advancing_sequence);
    RUN_TEST(test_null_context_is_rejected_for_sequence_functions);
    RUN_TEST(test_boot_id_accepts_v1_grammar_through_128_characters);
    RUN_TEST(test_boot_id_rejects_characters_outside_v1_grammar);
    UNITY_END();
}

