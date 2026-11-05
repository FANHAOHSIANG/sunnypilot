// Desktop inspection only. Print the selected upstream allowlist; no Panda or CAN device.
#include "opendbc/safety/tests/libsafety/safety.c"

int main(int argc, char **argv) {
  if (argc != 2) return 2;
  char *end = NULL;
  long param = strtol(argv[1], &end, 10);
  if (end == argv[1] || *end || param < 0 || param > UINT16_MAX) return 2;
  if (set_safety_hooks(SAFETY_HYUNDAI_CANFD, (uint16_t)param) != 0) return 1;
  for (int i = 0; i < current_safety_config.tx_msgs_len; i++) {
    const CanMsg row = current_safety_config.tx_msgs[i];
    printf("%d %u %d\n", row.addr, row.bus, row.len);
  }
  return 0;
}
