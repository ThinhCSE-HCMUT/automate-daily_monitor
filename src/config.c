#define _DEFAULT_SOURCE
#include "monitor.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>

static void rstrip(char *s)
{
    size_t n = strlen(s);
    while (n > 0 && (s[n - 1] == '\n' || s[n - 1] == '\r' || isspace((unsigned char)s[n - 1]))) {
        s[--n] = '\0';
    }
}

static void copy_field(char *dst, size_t n, const char *src)
{
    snprintf(dst, n, "%s", src);
}

void config_set_defaults(Config *cfg)
{
    memset(cfg, 0, sizeof(*cfg));
    copy_field(cfg->lab_ssid_5g, sizeof(cfg->lab_ssid_5g), "Simplifi Lab 5Ghz");
    copy_field(cfg->lab_ssid_24g, sizeof(cfg->lab_ssid_24g), "Simplifi Lab 2.4Ghz");
    cfg->lab_password[0] = '\0';
    copy_field(cfg->wifi_iface, sizeof(cfg->wifi_iface), "wlan0");
    copy_field(cfg->router_gateway, sizeof(cfg->router_gateway), "192.168.2.1");
    copy_field(cfg->router_gateway_alt, sizeof(cfg->router_gateway_alt), "192.168.10.1");
    copy_field(cfg->ssh_user, sizeof(cfg->ssh_user), "root");
    copy_field(cfg->output_csv, sizeof(cfg->output_csv), "output/daily_monitor.csv");
    copy_field(cfg->log_file, sizeof(cfg->log_file), "output/monitor.log");
    copy_field(cfg->portal_conf, sizeof(cfg->portal_conf), "portal.conf");
    copy_field(cfg->fax_conf, sizeof(cfg->fax_conf), "fax.conf");
    copy_field(cfg->sharepoint_conf, sizeof(cfg->sharepoint_conf), "sharepoint.conf");
    copy_field(cfg->laptop_host, sizeof(cfg->laptop_host), "voicelink");
    cfg->laptop_user[0] = '\0';
    copy_field(cfg->laptop_dest, sizeof(cfg->laptop_dest), "D:/daily_monitor_result");
    cfg->laptop_key[0] = '\0';
    cfg->wifi_timeout_sec = 45;
    cfg->ssh_timeout_sec = 25;
    cfg->portal_logs = 1;
    cfg->fax_send = 1;
    cfg->laptop_sync = 1;
    cfg->sharepoint_excel = 1;
    cfg->laptop_port = 0;

    /* Defaults from router_infor.md — override in monitor.conf */
    copy_field(cfg->routers[0].name, sizeof(cfg->routers[0].name), "Voicelink Station No. 1");
    copy_field(cfg->routers[0].imei, sizeof(cfg->routers[0].imei), "866758040553188");
    copy_field(cfg->routers[0].anydesk, sizeof(cfg->routers[0].anydesk), "1267941734");
    copy_field(cfg->routers[0].ssid, sizeof(cfg->routers[0].ssid), "Simplifi-3188");
    copy_field(cfg->routers[0].password, sizeof(cfg->routers[0].password), "5613EACF");

    copy_field(cfg->routers[1].name, sizeof(cfg->routers[1].name), "Voicelink Station No. 2");
    copy_field(cfg->routers[1].imei, sizeof(cfg->routers[1].imei), "861107035967513");
    copy_field(cfg->routers[1].anydesk, sizeof(cfg->routers[1].anydesk), "1267941734");
    copy_field(cfg->routers[1].ssid, sizeof(cfg->routers[1].ssid), "Simplifi-7513");
    copy_field(cfg->routers[1].password, sizeof(cfg->routers[1].password), "16415A35");

    copy_field(cfg->routers[2].name, sizeof(cfg->routers[2].name), "Fax Station No. 1");
    copy_field(cfg->routers[2].imei, sizeof(cfg->routers[2].imei), "861107035990853");
    copy_field(cfg->routers[2].anydesk, sizeof(cfg->routers[2].anydesk), "1484607357");
    copy_field(cfg->routers[2].ssid, sizeof(cfg->routers[2].ssid), "Simplifi-0853");
    copy_field(cfg->routers[2].password, sizeof(cfg->routers[2].password), "BAB1A756");

    copy_field(cfg->routers[3].name, sizeof(cfg->routers[3].name), "Fax Station No. 2");
    copy_field(cfg->routers[3].imei, sizeof(cfg->routers[3].imei), "866758040526465");
    copy_field(cfg->routers[3].anydesk, sizeof(cfg->routers[3].anydesk), "1628162772");
    copy_field(cfg->routers[3].ssid, sizeof(cfg->routers[3].ssid), "Simplifi-6465");
    copy_field(cfg->routers[3].password, sizeof(cfg->routers[3].password), "3BCF3F5A");

    cfg->router_count = 4;
}

static int parse_router_key(const char *key, int *idx, char *field, size_t field_n)
{
    /* router.0.ssid */
    if (strncmp(key, "router.", 7) != 0)
        return -1;
    const char *p = key + 7;
    if (!isdigit((unsigned char)*p))
        return -1;
    int i = atoi(p);
    while (*p && *p != '.')
        p++;
    if (*p != '.')
        return -1;
    p++;
    if (i < 0 || i >= MAX_ROUTERS)
        return -1;
    *idx = i;
    snprintf(field, field_n, "%s", p);
    return 0;
}

int config_load(const char *path, Config *cfg)
{
    config_set_defaults(cfg);

    FILE *fp = fopen(path, "r");
    if (!fp) {
        log_msg("WARN", "Cannot open %s, using built-in router defaults", path);
        return 0;
    }

    int seen_router = 0;
    char line[512];
    while (fgets(line, sizeof(line), fp)) {
        rstrip(line);
        if (line[0] == '\0' || line[0] == '#' || line[0] == ';')
            continue;

        char *eq = strchr(line, '=');
        if (!eq)
            continue;
        *eq = '\0';
        char *key = line;
        char *val = eq + 1;
        rstrip(key);

        if (strcmp(key, "lab_ssid_5g") == 0)
            copy_field(cfg->lab_ssid_5g, sizeof(cfg->lab_ssid_5g), val);
        else if (strcmp(key, "lab_ssid_24g") == 0)
            copy_field(cfg->lab_ssid_24g, sizeof(cfg->lab_ssid_24g), val);
        else if (strcmp(key, "lab_password") == 0)
            copy_field(cfg->lab_password, sizeof(cfg->lab_password), val);
        else if (strcmp(key, "wifi_iface") == 0)
            copy_field(cfg->wifi_iface, sizeof(cfg->wifi_iface), val);
        else if (strcmp(key, "router_gateway") == 0)
            copy_field(cfg->router_gateway, sizeof(cfg->router_gateway), val);
        else if (strcmp(key, "router_gateway_alt") == 0)
            copy_field(cfg->router_gateway_alt, sizeof(cfg->router_gateway_alt), val);
        else if (strcmp(key, "ssh_user") == 0)
            copy_field(cfg->ssh_user, sizeof(cfg->ssh_user), val);
        else if (strcmp(key, "output_csv") == 0)
            copy_field(cfg->output_csv, sizeof(cfg->output_csv), val);
        else if (strcmp(key, "log_file") == 0)
            copy_field(cfg->log_file, sizeof(cfg->log_file), val);
        else if (strcmp(key, "wifi_timeout_sec") == 0)
            cfg->wifi_timeout_sec = atoi(val);
        else if (strcmp(key, "ssh_timeout_sec") == 0)
            cfg->ssh_timeout_sec = atoi(val);
        else if (strcmp(key, "portal_conf") == 0)
            copy_field(cfg->portal_conf, sizeof(cfg->portal_conf), val);
        else if (strcmp(key, "portal_logs") == 0)
            cfg->portal_logs = atoi(val);
        else if (strcmp(key, "fax_conf") == 0)
            copy_field(cfg->fax_conf, sizeof(cfg->fax_conf), val);
        else if (strcmp(key, "fax_send") == 0)
            cfg->fax_send = atoi(val);
        else if (strcmp(key, "laptop_sync") == 0)
            cfg->laptop_sync = atoi(val);
        else if (strcmp(key, "laptop_host") == 0)
            copy_field(cfg->laptop_host, sizeof(cfg->laptop_host), val);
        else if (strcmp(key, "laptop_user") == 0)
            copy_field(cfg->laptop_user, sizeof(cfg->laptop_user), val);
        else if (strcmp(key, "laptop_port") == 0)
            cfg->laptop_port = atoi(val);
        else if (strcmp(key, "laptop_dest") == 0)
            copy_field(cfg->laptop_dest, sizeof(cfg->laptop_dest), val);
        else if (strcmp(key, "laptop_key") == 0)
            copy_field(cfg->laptop_key, sizeof(cfg->laptop_key), val);
        else if (strcmp(key, "sharepoint_excel") == 0)
            cfg->sharepoint_excel = atoi(val);
        else if (strcmp(key, "sharepoint_conf") == 0)
            copy_field(cfg->sharepoint_conf, sizeof(cfg->sharepoint_conf), val);
        else {
            int idx;
            char field[32];
            if (parse_router_key(key, &idx, field, sizeof(field)) == 0) {
                if (!seen_router) {
                    cfg->router_count = 0;
                    memset(cfg->routers, 0, sizeof(cfg->routers));
                    seen_router = 1;
                }
                if (idx + 1 > cfg->router_count)
                    cfg->router_count = idx + 1;
                Router *r = &cfg->routers[idx];
                if (strcmp(field, "name") == 0)
                    copy_field(r->name, sizeof(r->name), val);
                else if (strcmp(field, "imei") == 0)
                    copy_field(r->imei, sizeof(r->imei), val);
                else if (strcmp(field, "anydesk") == 0)
                    copy_field(r->anydesk, sizeof(r->anydesk), val);
                else if (strcmp(field, "ssid") == 0)
                    copy_field(r->ssid, sizeof(r->ssid), val);
                else if (strcmp(field, "password") == 0)
                    copy_field(r->password, sizeof(r->password), val);
            }
        }
    }
    fclose(fp);
    return 0;
}
