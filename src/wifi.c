#define _DEFAULT_SOURCE
#include "monitor.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

/* Quote a string for a POSIX single-quoted shell argument. */
static void sh_quote(const char *in, char *out, size_t n)
{
    size_t j = 0;
    if (j + 1 < n)
        out[j++] = '\'';
    for (size_t i = 0; in[i] && j + 6 < n; i++) {
        if (in[i] == '\'') {
            memcpy(out + j, "'\\''", 4);
            j += 4;
        } else {
            out[j++] = in[i];
        }
    }
    if (j + 1 < n)
        out[j++] = '\'';
    out[j] = '\0';
}

static void rstrip_inplace(char *s)
{
    size_t n = strlen(s);
    while (n > 0 && (s[n - 1] == '\n' || s[n - 1] == '\r' || s[n - 1] == ' ' || s[n - 1] == '\t'))
        s[--n] = '\0';
}

static int nmcli_available(void)
{
    return run_cmd("command -v nmcli >/dev/null 2>&1", NULL, 0, 5) == 0;
}

static int is_lab_ssid(const char *ssid)
{
    return (g_cfg.lab_ssid_5g[0] && strcmp(ssid, g_cfg.lab_ssid_5g) == 0) ||
           (g_cfg.lab_ssid_24g[0] && strcmp(ssid, g_cfg.lab_ssid_24g) == 0);
}

/*
 * Look up the NetworkManager connection NAME for an SSID.
 * The profile name is often the SSID, but may differ (e.g. "preconfigured").
 */
static int wifi_find_profile_name(const char *ssid, char *name_out, size_t name_n)
{
    char list[4096];

    if (run_cmd("nmcli -t -f NAME,UUID,TYPE connection show 2>/dev/null",
                list, sizeof(list), 0) != 0)
        return -1;

    char copy[4096];
    snprintf(copy, sizeof(copy), "%s", list);

    char *save = NULL;
    for (char *line = strtok_r(copy, "\n", &save); line; line = strtok_r(NULL, "\n", &save)) {
        rstrip_inplace(line);
        /* NAME:UUID:TYPE  — UUID has no colons, NAME may contain colons rarely. */
        char *last = strrchr(line, ':');
        if (!last)
            continue;
        *last = '\0';
        const char *type = last + 1;
        char *mid = strrchr(line, ':');
        if (!mid)
            continue;
        *mid = '\0';
        const char *uuid = mid + 1;
        const char *name = line;

        if (strcmp(type, "802-11-wireless") != 0 && strcmp(type, "wifi") != 0)
            continue;

        char cmd[256];
        char ssid_out[256];
        snprintf(cmd, sizeof(cmd),
                 "nmcli -t -f 802-11-wireless.ssid connection show %s 2>/dev/null", uuid);
        if (run_cmd(cmd, ssid_out, sizeof(ssid_out), 0) != 0)
            continue;
        rstrip_inplace(ssid_out);
        char *val = strrchr(ssid_out, ':');
        val = val ? val + 1 : ssid_out;
        if (strcmp(val, ssid) == 0) {
            snprintf(name_out, name_n, "%s", name);
            return 0;
        }
    }
    return -1;
}

static int wifi_set_autoconnect(const char *profile_name, int enable)
{
    char qname[256];
    char cmd[512];
    char out[256];

    sh_quote(profile_name, qname, sizeof(qname));
    snprintf(cmd, sizeof(cmd),
             "nmcli connection modify id %s connection.autoconnect %s 2>&1",
             qname, enable ? "yes" : "no");
    int rc = run_cmd(cmd, out, sizeof(out), 0);
    if (rc != 0)
        log_msg("WARN", "Could not set autoconnect=%s on '%s': %s",
                enable ? "yes" : "no", profile_name, out);
    return rc;
}

/* Only toggles autoconnect — never touches ipv4.method / ipv4.addresses. */
static void wifi_set_lab_autoconnect(int enable)
{
    char name[128];
    if (g_cfg.lab_ssid_5g[0] &&
        wifi_find_profile_name(g_cfg.lab_ssid_5g, name, sizeof(name)) == 0) {
        log_msg("INFO", "Lab profile '%s' autoconnect=%s (static IP kept)",
                name, enable ? "yes" : "no");
        wifi_set_autoconnect(name, enable);
    }
    if (g_cfg.lab_ssid_24g[0] &&
        wifi_find_profile_name(g_cfg.lab_ssid_24g, name, sizeof(name)) == 0) {
        wifi_set_autoconnect(name, enable);
    }
}

/*
 * Delete saved WiFi profiles for a router SSID only.
 * Lab profiles are never deleted — they hold the Pi's static IP.
 */
static void wifi_delete_ssid_profiles(const char *ssid)
{
    if (is_lab_ssid(ssid)) {
        log_msg("INFO", "Skipping delete of lab profile '%s' (static IP preserved)", ssid);
        return;
    }

    char list[4096];
    char qssid[MAX_SSID * 4];

    sh_quote(ssid, qssid, sizeof(qssid));
    {
        char cmd[256];
        snprintf(cmd, sizeof(cmd), "nmcli connection delete id %s 2>/dev/null", qssid);
        run_cmd(cmd, NULL, 0, 0);
    }

    if (run_cmd("nmcli -t -f UUID,TYPE connection show 2>/dev/null", list, sizeof(list), 0) != 0)
        return;

    char copy[4096];
    snprintf(copy, sizeof(copy), "%s", list);

    char *save = NULL;
    for (char *line = strtok_r(copy, "\n", &save); line; line = strtok_r(NULL, "\n", &save)) {
        rstrip_inplace(line);
        char *colon = strchr(line, ':');
        if (!colon)
            continue;
        *colon = '\0';
        const char *uuid = line;
        const char *type = colon + 1;
        if (strcmp(type, "802-11-wireless") != 0 && strcmp(type, "wifi") != 0)
            continue;

        char cmd[256];
        char ssid_out[256];
        snprintf(cmd, sizeof(cmd),
                 "nmcli -t -f 802-11-wireless.ssid connection show %s 2>/dev/null", uuid);
        if (run_cmd(cmd, ssid_out, sizeof(ssid_out), 0) != 0)
            continue;
        rstrip_inplace(ssid_out);
        char *val = strrchr(ssid_out, ':');
        val = val ? val + 1 : ssid_out;
        if (strcmp(val, ssid) != 0)
            continue;
        if (is_lab_ssid(val))
            continue;

        log_msg("INFO", "Removing router WiFi profile uuid=%s for SSID '%s'", uuid, ssid);
        snprintf(cmd, sizeof(cmd), "nmcli connection delete uuid %s 2>/dev/null", uuid);
        run_cmd(cmd, NULL, 0, 0);
    }
}

static int wifi_create_profile(const char *ssid, const char *qssid,
                               const char *password, const char *qpass,
                               const char *key_mgmt)
{
    char cmd[2048];
    char out[2048];

    if (is_lab_ssid(ssid)) {
        log_msg("ERROR", "Refusing to recreate lab profile '%s' (would wipe static IP)", ssid);
        return -1;
    }

    if (password && password[0]) {
        snprintf(cmd, sizeof(cmd),
                 "nmcli connection add type wifi con-name %s ifname %s ssid %s "
                 "connection.autoconnect no ipv4.method auto ipv6.method ignore "
                 "wifi-sec.key-mgmt %s wifi-sec.psk %s 2>&1",
                 qssid, g_cfg.wifi_iface, qssid, key_mgmt, qpass);
    } else {
        snprintf(cmd, sizeof(cmd),
                 "nmcli connection add type wifi con-name %s ifname %s ssid %s "
                 "connection.autoconnect no ipv4.method auto ipv6.method ignore 2>&1",
                 qssid, g_cfg.wifi_iface, qssid);
    }

    int rc = run_cmd(cmd, out, sizeof(out), 0);
    if (rc != 0 && password && password[0]) {
        snprintf(cmd, sizeof(cmd),
                 "nmcli connection add type wifi con-name %s ifname %s ssid %s "
                 "connection.autoconnect no ipv4.method auto ipv6.method ignore -- "
                 "wifi-sec.key-mgmt %s wifi-sec.psk %s 2>&1",
                 qssid, g_cfg.wifi_iface, qssid, key_mgmt, qpass);
        rc = run_cmd(cmd, out, sizeof(out), 0);
    }
    if (rc != 0) {
        log_msg("WARN", "nmcli connection add (%s) failed: %s", key_mgmt, out);
        return -1;
    }
    log_msg("INFO", "Created router WiFi profile '%s' key-mgmt=%s", ssid, key_mgmt);
    return 0;
}

static int wifi_up_profile(const char *profile_name, int wait_sec, char *out, size_t out_sz)
{
    char qname[256];
    char cmd[512];

    sh_quote(profile_name, qname, sizeof(qname));
    /* Bring this profile up; NetworkManager deactivates the previous one.
     * Do NOT `device disconnect` — that drops wlan0 and can clear addressing. */
    snprintf(cmd, sizeof(cmd),
             "nmcli -w %d connection up id %s 2>&1",
             wait_sec, qname);
    return run_cmd(cmd, out, out_sz, 0);
}

static int wifi_wait_associated(const char *ssid, int seconds)
{
    char current[MAX_SSID];
    for (int i = 0; i < seconds; i++) {
        if (wifi_current_ssid(current, sizeof(current)) == 0 && strcmp(current, ssid) == 0)
            return 0;
        sleep(1);
    }
    return -1;
}

int wifi_current_ssid(char *ssid, size_t n)
{
    char out[512];
    char cmd[256];

    snprintf(cmd, sizeof(cmd),
             "iwgetid -r 2>/dev/null");
    if (run_cmd(cmd, out, sizeof(out), 0) == 0) {
        rstrip_inplace(out);
        if (out[0]) {
            snprintf(ssid, n, "%s", out);
            return 0;
        }
    }

    snprintf(cmd, sizeof(cmd),
             "nmcli -t -f IN-USE,SSID device wifi ifname %s 2>/dev/null",
             g_cfg.wifi_iface);
    if (run_cmd(cmd, out, sizeof(out), 0) == 0) {
        char *p = out;
        while (*p) {
            char *eol = strchr(p, '\n');
            if (eol)
                *eol = '\0';
            if (p[0] == '*' && p[1] == ':') {
                snprintf(ssid, n, "%s", p + 2);
                rstrip_inplace(ssid);
                return 0;
            }
            if (!eol)
                break;
            p = eol + 1;
        }
    }

    ssid[0] = '\0';
    return -1;
}

/*
 * Do not `nmcli device disconnect wlan0`.
 * That tears down the interface and can wipe the lab static-IP setup.
 * Between routers we only bring down the *current router* connection.
 */
int wifi_disconnect(void)
{
    char current[MAX_SSID];
    char name[128];
    char out[512];

    if (wifi_current_ssid(current, sizeof(current)) != 0 || current[0] == '\0')
        return 0;

    if (is_lab_ssid(current)) {
        log_msg("INFO", "On lab SSID '%s' — leaving profile and static IP untouched", current);
        return 0;
    }

    if (wifi_find_profile_name(current, name, sizeof(name)) != 0)
        snprintf(name, sizeof(name), "%s", current);

    log_msg("INFO", "Bringing down router connection '%s' (wlan0 device kept)", name);
    {
        char qname[256];
        char cmd[512];
        sh_quote(name, qname, sizeof(qname));
        snprintf(cmd, sizeof(cmd), "nmcli connection down id %s 2>&1", qname);
        run_cmd(cmd, out, sizeof(out), 0);
    }
    sleep(1);
    return 0;
}

static int wifi_up_existing_ssid(const char *ssid, int timeout_sec)
{
    char name[128];
    char out[2048];
    int wait = timeout_sec > 0 ? timeout_sec : 45;

    if (wifi_find_profile_name(ssid, name, sizeof(name)) != 0) {
        log_msg("ERROR", "No saved NetworkManager profile for SSID '%s'", ssid);
        return -1;
    }

    log_msg("INFO", "Activating existing profile '%s' for SSID '%s'", name, ssid);
    if (wifi_up_profile(name, wait, out, sizeof(out)) != 0) {
        log_msg("WARN", "connection up '%s' failed: %s", name, out);
        return -1;
    }
    if (wifi_wait_associated(ssid, 15) == 0) {
        log_msg("INFO", "Associated to '%s'", ssid);
        return 0;
    }
    log_msg("WARN", "Profile is up but current SSID is not yet '%s' — continuing", ssid);
    return 0;
}

int wifi_connect(const char *ssid, const char *password, int timeout_sec)
{
    char qssid[MAX_SSID * 4];
    char qpass[MAX_PASS * 4];
    char out[2048];
    int wait = timeout_sec > 0 ? timeout_sec : 45;

    if (!nmcli_available()) {
        log_msg("ERROR", "nmcli not found. Install NetworkManager on Raspberry Pi OS.");
        return -1;
    }

    /* Lab network: never delete/recreate — just activate the saved static-IP profile. */
    if (is_lab_ssid(ssid))
        return wifi_up_existing_ssid(ssid, timeout_sec);

    sh_quote(ssid, qssid, sizeof(qssid));
    if (password && password[0])
        sh_quote(password, qpass, sizeof(qpass));
    else
        qpass[0] = '\0';

    log_msg("INFO", "Switching to router SSID '%s' (lab profile kept)", ssid);
    run_cmd("nmcli radio wifi on", NULL, 0, 0);

    /* Stop lab from auto-grabbing wlan0 while we visit a router. Static IP is unchanged. */
    wifi_set_lab_autoconnect(0);

    wifi_delete_ssid_profiles(ssid);
    run_cmd("nmcli device wifi rescan", NULL, 0, 0);
    sleep(3);

    const char *methods[3];
    int nmethods = 0;
    if (password && password[0]) {
        methods[nmethods++] = "wpa-psk";
        methods[nmethods++] = "sae";
    } else {
        methods[nmethods++] = "none";
    }

    int connected = 0;
    for (int m = 0; m < nmethods && !connected; m++) {
        wifi_delete_ssid_profiles(ssid);
        if (wifi_create_profile(ssid, qssid, password, qpass, methods[m]) != 0)
            continue;

        int rc = wifi_up_profile(ssid, wait, out, sizeof(out));
        if (rc != 0) {
            log_msg("WARN", "connection up (%s) failed: %s", methods[m], out);
            continue;
        }
        connected = 1;
    }

    if (!connected) {
        log_msg("ERROR", "Failed to join '%s'", ssid);
        run_cmd("nmcli -t -f SSID,SIGNAL,SECURITY device wifi list 2>/dev/null | head -n 30",
                out, sizeof(out), 0);
        log_msg("INFO", "Visible SSIDs:\n%s", out);
        return -1;
    }

    if (wifi_wait_associated(ssid, 15) == 0) {
        log_msg("INFO", "Associated to '%s'", ssid);
        return 0;
    }

    log_msg("WARN", "Profile is up but current SSID is not yet '%s' — continuing", ssid);
    return 0;
}

int wifi_connect_lab(void)
{
    char current[MAX_SSID];

    if (wifi_current_ssid(current, sizeof(current)) == 0 && is_lab_ssid(current)) {
        log_msg("INFO", "Already on lab SSID '%s' — not bouncing wlan0", current);
        wifi_set_lab_autoconnect(1);
        return 0;
    }

    wifi_set_lab_autoconnect(1);

    log_msg("INFO", "Reactivating saved lab profile '%s' (static IP preserved)",
            g_cfg.lab_ssid_5g);
    if (wifi_up_existing_ssid(g_cfg.lab_ssid_5g, g_cfg.wifi_timeout_sec) == 0)
        return 0;

    log_msg("WARN", "Lab 5GHz profile failed, trying 2.4GHz");
    if (g_cfg.lab_ssid_24g[0] &&
        wifi_up_existing_ssid(g_cfg.lab_ssid_24g, g_cfg.wifi_timeout_sec) == 0)
        return 0;

    log_msg("ERROR",
            "Could not activate lab WiFi. The saved profile (with static IP) was not modified. "
            "Bring it up manually: nmcli connection up id '<lab-profile-name>'");
    return -1;
}
