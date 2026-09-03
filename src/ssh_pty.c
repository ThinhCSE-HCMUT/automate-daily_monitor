#define _GNU_SOURCE
#include "monitor.h"

#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <pty.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/select.h>
#include <sys/wait.h>
#include <termios.h>
#include <time.h>
#include <unistd.h>

static void ssh_pty_reset(SshPty *s)
{
    s->master_fd = -1;
    s->pid = -1;
    s->len = 0;
    s->buf[0] = '\0';
}

int ssh_delete_known_hosts(void)
{
    const char *home = getenv("HOME");
    if (!home || !home[0])
        home = "/home/pi";

    char path[MAX_PATH];
    char path_old[MAX_PATH];
    snprintf(path, sizeof(path), "%s/.ssh/known_hosts", home);
    snprintf(path_old, sizeof(path_old), "%s/.ssh/known_hosts.old", home);

    if (unlink(path) == 0)
        log_msg("INFO", "Deleted %s", path);
    else if (errno != ENOENT)
        log_msg("WARN", "Could not delete %s: %s", path, strerror(errno));

    unlink(path_old);
    return 0;
}

static int pty_read_more(SshPty *s, int timeout_ms)
{
    fd_set rfds;
    struct timeval tv;
    tv.tv_sec = timeout_ms / 1000;
    tv.tv_usec = (timeout_ms % 1000) * 1000;

    FD_ZERO(&rfds);
    FD_SET(s->master_fd, &rfds);
    int r = select(s->master_fd + 1, &rfds, NULL, NULL, &tv);
    if (r < 0) {
        if (errno == EINTR)
            return 0;
        return -1;
    }
    if (r == 0)
        return 0; /* timeout, no data */

    if (s->len >= PTY_BUF_SIZE - 1) {
        /* Keep the tail so we do not miss a prompt split across the buffer. */
        memmove(s->buf, s->buf + PTY_BUF_SIZE / 2, s->len - PTY_BUF_SIZE / 2);
        s->len -= PTY_BUF_SIZE / 2;
        s->buf[s->len] = '\0';
    }

    ssize_t n = read(s->master_fd, s->buf + s->len, PTY_BUF_SIZE - 1 - s->len);
    if (n < 0) {
        if (errno == EIO)
            return -1; /* slave closed */
        if (errno == EAGAIN || errno == EINTR)
            return 0;
        return -1;
    }
    if (n == 0)
        return -1;
    s->len += (size_t)n;
    s->buf[s->len] = '\0';
    return 1;
}

static int contains(const char *hay, const char *needle)
{
    return strstr(hay, needle) != NULL;
}

static int pty_wait_any(SshPty *s, const char **needles, int nneedles, int timeout_sec)
{
    time_t deadline = time(NULL) + timeout_sec;
    while (time(NULL) <= deadline) {
        for (int i = 0; i < nneedles; i++) {
            if (needles[i] && contains(s->buf, needles[i]))
                return i;
        }
        int left_ms = (int)((deadline - time(NULL)) * 1000);
        if (left_ms < 50)
            left_ms = 50;
        if (left_ms > 400)
            left_ms = 400;
        int r = pty_read_more(s, left_ms);
        if (r < 0)
            return -1;
    }
    for (int i = 0; i < nneedles; i++) {
        if (needles[i] && contains(s->buf, needles[i]))
            return i;
    }
    return -2; /* timeout */
}

static int pty_write_line(SshPty *s, const char *line)
{
    char tmp[256];
    snprintf(tmp, sizeof(tmp), "%s\n", line);
    size_t n = strlen(tmp);
    size_t off = 0;
    while (off < n) {
        ssize_t w = write(s->master_fd, tmp + off, n - off);
        if (w < 0) {
            if (errno == EINTR)
                continue;
            return -1;
        }
        off += (size_t)w;
    }
    return 0;
}

int ssh_pty_open(SshPty *s, const char *user, const char *host)
{
    ssh_pty_reset(s);

    struct winsize ws;
    memset(&ws, 0, sizeof(ws));
    ws.ws_row = 40;
    ws.ws_col = 200;

    pid_t pid = forkpty(&s->master_fd, NULL, NULL, &ws);
    if (pid < 0) {
        log_msg("ERROR", "forkpty failed: %s", strerror(errno));
        ssh_pty_reset(s);
        return -1;
    }

    if (pid == 0) {
        /* Child: exec OpenSSH so the host-key prompt behaves like a human session. */
        char target[192];
        snprintf(target, sizeof(target), "%s@%s", user, host);
        execlp("ssh", "ssh",
               "-tt",
               "-o", "StrictHostKeyChecking=ask",
               "-o", "UserKnownHostsFile=/dev/null",
               "-o", "GlobalKnownHostsFile=/dev/null",
               "-o", "UpdateHostKeys=no",
               "-o", "PubkeyAuthentication=no",
               "-o", "PreferredAuthentications=keyboard-interactive,password",
               "-o", "NumberOfPasswordPrompts=2",
               "-o", "ConnectTimeout=25",
               "-o", "ServerAliveInterval=5",
               "-o", "ServerAliveCountMax=3",
               target,
               (char *)NULL);
        _exit(127);
    }

    s->pid = pid;
    int flags = fcntl(s->master_fd, F_GETFL, 0);
    if (flags >= 0)
        fcntl(s->master_fd, F_SETFL, flags | O_NONBLOCK);
    return 0;
}

int ssh_pty_login(SshPty *s, const char *router_password, int timeout_sec)
{
    const char *host_prompt = "Are you sure you want to continue connecting";
    const char *simplifi_root = "Simplifi root:";
    const char *password = "Password:";
    const char *shell = "root@Simplifi";
    const char *denied = "Permission denied";
    const char *changed = "REMOTE HOST IDENTIFICATION HAS CHANGED";
    const char *refused = "Connection refused";
    const char *timedout = "Connection timed out";
    const char *unreachable = "No route to host";

    const char *needles[] = {
        host_prompt,
        simplifi_root,
        password,
        shell,
        denied,
        changed,
        refused,
        timedout,
        unreachable,
        "Host key verification failed",
    };
    const int n = (int)(sizeof(needles) / sizeof(needles[0]));

    int sent_yes = 0;
    int sent_root_name = 0;
    int sent_password = 0;
    time_t deadline = time(NULL) + timeout_sec;

    while (time(NULL) < deadline) {
        int left = (int)(deadline - time(NULL));
        if (left < 1)
            left = 1;
        int hit = pty_wait_any(s, needles, n, left > 3 ? 3 : left);

        if (hit == 0 && !sent_yes) {
            log_msg("INFO", "Host key prompt detected, sending yes");
            if (pty_write_line(s, "yes") != 0)
                return -1;
            sent_yes = 1;
            /* Drop the matched text so we do not re-match forever. */
            char *p = strstr(s->buf, host_prompt);
            if (p)
                *p = 'X';
            continue;
        }
        if (hit == 1 && !sent_root_name) {
            log_msg("INFO", "Login prompt 'Simplifi root:' detected, sending root");
            if (pty_write_line(s, "root") != 0)
                return -1;
            sent_root_name = 1;
            char *p = strstr(s->buf, simplifi_root);
            if (p)
                *p = 'X';
            continue;
        }
        if ((hit == 2 || contains(s->buf, "'s password:")) && !sent_password) {
            log_msg("INFO", "Password prompt detected, sending router password");
            if (pty_write_line(s, router_password ? router_password : "") != 0)
                return -1;
            sent_password = 1;
            char *p = strstr(s->buf, password);
            if (!p)
                p = strstr(s->buf, "'s password:");
            if (p)
                *p = 'X';
            continue;
        }
        if (hit == 3) {
            log_msg("INFO", "SSH shell ready (root@Simplifi)");
            return 0;
        }
        if (hit == 4) {
            log_msg("ERROR", "SSH permission denied");
            return -1;
        }
        if (hit == 5) {
            log_msg("ERROR", "SSH host key changed — known_hosts should have been deleted");
            return -1;
        }
        if (hit == 6 || hit == 7 || hit == 8 || hit == 9) {
            log_msg("ERROR", "SSH connect failed: %s", needles[hit]);
            return -1;
        }
        if (hit == -1) {
            log_msg("ERROR", "SSH PTY closed during login");
            return -1;
        }
        /* hit == -2: slice timeout, loop until overall deadline */
    }

    log_msg("ERROR", "SSH login timed out. Tail of session:\n%s", s->buf);
    return -1;
}

static void strip_ansi(char *s)
{
    char *r = s, *w = s;
    while (*r) {
        if (*r == '\x1b') {
            r++;
            if (*r == '[') {
                r++;
                while (*r && *r != 'm' && !(isalpha((unsigned char)*r)))
                    r++;
                if (*r)
                    r++;
            }
            continue;
        }
        *w++ = *r++;
    }
    *w = '\0';
}

int ssh_pty_exec(SshPty *s, const char *cmd, char *out, size_t out_sz, int timeout_sec)
{
    /* Clear buffer so we only capture this command's output. */
    s->len = 0;
    s->buf[0] = '\0';

    if (pty_write_line(s, cmd) != 0)
        return -1;

    const char *needles[] = { "root@Simplifi" };
    int hit = pty_wait_any(s, needles, 1, timeout_sec);
    if (hit < 0) {
        log_msg("WARN", "Timeout waiting for prompt after: %s", cmd);
        if (out && out_sz) {
            snprintf(out, out_sz, "%s", s->buf);
        }
        return -1;
    }

    if (!out || out_sz == 0)
        return 0;

    /* Copy, then drop the echoed command line and the trailing prompt. */
    char tmp[PTY_BUF_SIZE];
    snprintf(tmp, sizeof(tmp), "%s", s->buf);
    strip_ansi(tmp);

    char *start = tmp;
    char *nl = strchr(tmp, '\n');
    if (nl)
        start = nl + 1;

    char *prompt = strstr(start, "root@Simplifi");
    if (prompt) {
        /* Also drop the last partial line before the prompt if it is CR leftover. */
        while (prompt > start && (prompt[-1] == '\r' || prompt[-1] == '\n'))
            prompt--;
        *prompt = '\0';
    }

    snprintf(out, out_sz, "%s", start);
    return 0;
}

void ssh_pty_close(SshPty *s)
{
    if (!s)
        return;
    if (s->master_fd >= 0) {
        pty_write_line(s, "exit");
        close(s->master_fd);
        s->master_fd = -1;
    }
    if (s->pid > 0) {
        int st = 0;
        for (int i = 0; i < 20; i++) {
            pid_t w = waitpid(s->pid, &st, WNOHANG);
            if (w == s->pid)
                break;
            if (i == 5)
                kill(s->pid, SIGTERM);
            if (i == 15)
                kill(s->pid, SIGKILL);
            usleep(100000);
        }
        waitpid(s->pid, &st, WNOHANG);
    }
    ssh_pty_reset(s);
}
