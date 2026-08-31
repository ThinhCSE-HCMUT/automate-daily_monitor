CC      ?= gcc
CFLAGS  ?= -std=c11 -Wall -Wextra -O2 -Iinclude
LDFLAGS ?= -lutil

PREFIX  ?= /opt/simplifi-monitor
BIN     := monitor

SRCS := src/monitor.c src/config.c src/wifi.c src/ssh_pty.c src/parse.c src/util.c
OBJS := $(SRCS:.c=.o)

.PHONY: all clean install deps timer

all: $(BIN)

$(BIN): $(OBJS)
	$(CC) $(CFLAGS) -o $@ $(OBJS) $(LDFLAGS)

src/%.o: src/%.c include/monitor.h
	$(CC) $(CFLAGS) -c -o $@ $<

# Raspberry Pi OS blocks system pip. Install Selenium into .venv.
deps:
	sudo apt-get install -y python3 python3-venv python3-pip chromium chromium-driver
	python3 -m venv .venv
	.venv/bin/pip install -U pip
	.venv/bin/pip install -r requirements.txt
	@echo "Python packages installed in .venv"

clean:
	rm -f $(OBJS) $(BIN)

timer:
	sed -i 's/\r$$//' deploy/install-timer.sh deploy/simplifi-monitor.service deploy/simplifi-monitor.timer
	sudo bash deploy/install-timer.sh

# sudo apt install -y build-essential network-manager openssh-client python3 python3-venv chromium chromium-driver
# make && make deps
# Fill portal.conf with Simplifi Portal password; set Virtual IMEIs in scripts/portal_imeis.csv
install: $(BIN)
	install -d $(PREFIX)
	install -m 0755 $(BIN) $(PREFIX)/$(BIN)
	install -m 0600 monitor.conf $(PREFIX)/monitor.conf
	install -d $(PREFIX)/output
	@echo "Installed to $(PREFIX)"
