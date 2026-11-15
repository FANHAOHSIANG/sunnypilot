#pragma once
/* Bench-only source-matched FCA relay. No acceleration or steering limits live here.
 * A real hardware build must not define HYUNDAI_FCW_BENCH. This header alone is
 * not an AEB implementation or validation. Source bytes are never rewritten.
 */
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define HFCA_ADDRESS 0x160U
#define HFCA_LENGTH 16U
#define HFCA_SOURCE_BUS 2U
#define HFCA_DESTINATION_BUS 0U
#define HFCA_TIMEOUT_US 100000U
#define HFCA_MIN_INTERVAL_US 5000U
#define HFCA_ACQUIRE_FRAMES 3U
#define HFCA_BENCH_PARAM 2048U
#define HFCA_SUPPORTED_BASE_PARAM 1037U

typedef struct {
  bool enabled;
  bool seen;
  bool ready;
  bool pending;
  bool copy_authorized;
  uint8_t streak;
  uint8_t counter;
  uint32_t timestamp;
  uint32_t last_authorized_timestamp;
  uint8_t payload[HFCA_LENGTH];
} HyundaiFcaProxy;

static void hfca_reset(HyundaiFcaProxy *state, bool enabled) {
  *state = (HyundaiFcaProxy){.enabled = enabled};
}

static uint16_t hfca_crc_step(uint16_t crc, uint8_t value) {
  crc ^= (uint16_t)((uint16_t)value << 8U);
  for (uint8_t i = 0U; i < 8U; i++) {
    crc = ((crc & 0x8000U) != 0U) ? (uint16_t)((crc << 1U) ^ 0x1021U) : (uint16_t)(crc << 1U);
  }
  return crc;
}

/* Hyundai CAN FD 16-byte checksum: bytes 2..15, then little-endian CAN address.
 * Pinned hyundaicanfd.py applies the 16-byte xor constant 0x041D. */
static uint16_t hfca_crc(const uint8_t payload[HFCA_LENGTH]) {
  uint16_t crc = 0U;
  for (uint8_t i = 2U; i < HFCA_LENGTH; i++) {
    crc = hfca_crc_step(crc, payload[i]);
  }
  crc = hfca_crc_step(crc, (uint8_t)(HFCA_ADDRESS & 0xFFU));
  return (uint16_t)(hfca_crc_step(crc, (uint8_t)(HFCA_ADDRESS >> 8U)) ^ 0x041DU);
}

static bool hfca_transport_healthy(const HyundaiFcaProxy *state, uint32_t now) {
  return state->enabled && state->ready && ((uint32_t)(now - state->timestamp) <= HFCA_TIMEOUT_US);
}

static bool hfca_fcw_ready(const HyundaiFcaProxy *state, uint32_t now) {
  const uint8_t setting = state->payload[3] & 3U;
  // Receiving source frames is not enough: the paired host must also be
  // successfully presenting copies. Authorization is NOT proof of bus delivery.
  return hfca_transport_healthy(state, now) && state->copy_authorized &&
         ((uint32_t)(now - state->last_authorized_timestamp) <= HFCA_TIMEOUT_US) &&
         ((setting == 2U) || (setting == 3U));
}

static bool hfca_observe(HyundaiFcaProxy *state, unsigned int address, unsigned int bus,
                         const uint8_t *data, size_t length, uint32_t now) {
  if (!state->enabled || (address != HFCA_ADDRESS) || (bus != HFCA_SOURCE_BUS)) {
    return false;
  }
  if ((data == NULL) || (length != HFCA_LENGTH)) {
    hfca_reset(state, true);
    return false;
  }
  const uint16_t supplied_crc = (uint16_t)data[0] | (uint16_t)((uint16_t)data[1] << 8U);
  if (supplied_crc != hfca_crc(data)) {
    hfca_reset(state, true);
    return false;
  }
  const uint32_t elapsed = (uint32_t)(now - state->timestamp);
  const bool consecutive = state->seen && (data[2] == (uint8_t)(state->counter + 1U)) &&
                           (elapsed >= HFCA_MIN_INTERVAL_US) && (elapsed <= HFCA_TIMEOUT_US);
  if (!consecutive) {
    state->copy_authorized = false;
  }
  state->streak = consecutive ? (uint8_t)(state->streak + (state->streak < HFCA_ACQUIRE_FRAMES ? 1U : 0U)) : 1U;
  state->seen = true;
  state->counter = data[2];
  state->timestamp = now;
  state->ready = state->streak >= HFCA_ACQUIRE_FRAMES;
  state->pending = state->ready;
  for (uint8_t i = 0U; i < HFCA_LENGTH; i++) {
    state->payload[i] = data[i];
  }
  return state->ready;
}

static bool hfca_authorize_tx(HyundaiFcaProxy *state, unsigned int address, unsigned int bus,
                              const uint8_t *data, size_t length, uint32_t now) {
  if ((address != HFCA_ADDRESS) || (bus != HFCA_DESTINATION_BUS) || (length != HFCA_LENGTH) ||
      (data == NULL) || !state->pending || !hfca_transport_healthy(state, now)) {
    return false;
  }
  for (uint8_t i = 0U; i < HFCA_LENGTH; i++) {
    if (state->payload[i] != data[i]) {
      return false;
    }
  }
  state->pending = false;
  state->copy_authorized = true;
  state->last_authorized_timestamp = now;
  return true;
}
