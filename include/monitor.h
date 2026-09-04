#ifndef MONITOR_H
#define MONITOR_H

#include <stddef.h>
#include <stdio.h>
#include <sys/types.h>
#include <time.h>

#define MAX_NAME        64
#define MAX_SSID        64
#define MAX_PASS        64
#define MAX_IMEI        32
#define MAX_ANYDESK     24
#define MAX_IP          32
#define MAX_HOST        96
#define MAX_IFACE       32
#define MAX_PATH        256
#define MAX_FIELD       128
#define MAX_ROUTERS     8
#define PTY_BUF_SIZE    65536

typedef struct {
    char name[MAX_NAME];
    char imei[MAX_IMEI];
    char anydesk[MAX_ANYDESK];
    char ssid[MAX_SSID];
    char password[MAX_PASS];
    char access[16];      /* wifi (default) or tailscale */
    char ssh_host[MAX_HOST]; /* Tailscale MagicDNS or 100.x.x.x */
} Router;

typedef struct {
    char lab_ssid_5g[MAX_SSID];
    char lab_ssid_24g[MAX_SSID];
    char lab_password[MAX_PASS];
    char wifi_iface[MAX_IFACE];
    char router_gateway[MAX_IP];
    char router_gateway_alt[MAX_IP];
    char ssh_user[MAX_NAME];
    char output_csv[MAX_PATH];
    char log_file[MAX_PATH];
    char portal_conf[MAX_PATH];
    char fax_conf[MAX_PATH];
    char sharepoint_conf[MAX_PATH];
    char laptop_host[MAX_PATH];
    char laptop_user[MAX_NAME];
    char laptop_dest[MAX_PATH];
    char laptop_key[MAX_PATH];
    char jump_host[MAX_HOST];
    char jump_user[MAX_NAME];
    char jump_restore_ssid[MAX_SSID];
    int  wifi_timeout_sec;
    int  ssh_timeout_sec;
    int  jump_wait_sec;
    int  portal_logs;
    int  log_analysis;
    int  fax_send;
    int  laptop_sync;
    int  sharepoint_excel;
    int  laptop_port;
    Router routers[MAX_ROUTERS];
    int  router_count;
} Config;

typedef struct {
    char date[MAX_FIELD];
    char anydesk[MAX_ANYDESK];
    char station[MAX_NAME];
    char imei[MAX_FIELD];
    char firmware[MAX_FIELD];
    char uptime[MAX_FIELD];
    char voicelink_status[MAX_FIELD];
    char carrier[MAX_FIELD];
    char phone[MAX_FIELD];
    char rssi[MAX_FIELD];
    char wifi_sim_status[MAX_FIELD];
    char ssh_access[MAX_FIELD];
    char note[MAX_FIELD];
} MonitorRow;

typedef struct {
    int master_fd;
    pid_t pid;
    char buf[PTY_BUF_SIZE];
    size_t len;
} SshPty;

extern FILE *g_log;
extern Config g_cfg;

/* util */
void log_msg(const char *level, const char *fmt, ...);
int  run_cmd(const char *cmd, char *out, size_t out_sz, int timeout_sec);
int  file_exists(const char *path);
int  ensure_parent_dir(const char *path);
void set_na(char *field, size_t n);
void now_stamp(char *out, size_t n);
int  wait_ping(const char *ip, int timeout_sec);

/* config */
int  config_load(const char *path, Config *cfg);
void config_set_defaults(Config *cfg);
int  router_is_tailscale(const Router *r);

/* wifi (nmcli on Raspberry Pi OS) */
int  wifi_current_ssid(char *ssid, size_t n);
int  wifi_disconnect(void);
int  wifi_connect(const char *ssid, const char *password, int timeout_sec);
int  wifi_connect_lab(void);

/* ssh over a real PTY so host-key / login prompts work */
int  ssh_pty_open(SshPty *s, const char *user, const char *host);
int  ssh_pty_login(SshPty *s, const char *router_password, int timeout_sec);
int  ssh_pty_exec(SshPty *s, const char *cmd, char *out, size_t out_sz, int timeout_sec);
void ssh_pty_close(SshPty *s);
int  ssh_delete_known_hosts(void);

/* parse router command output */
int  json_get_string(const char *json, const char *key, char *out, size_t n);
void parse_uptime_hhmm(const char *uptime_out, char *out, size_t n);
void parse_ping_status(const char *ping_out, char *out, size_t n);
void parse_firmware_line(const char *fw_out, char *out, size_t n);
void fill_from_cellular(const char *ubus_out, MonitorRow *row);

#endif
