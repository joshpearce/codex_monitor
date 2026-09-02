PYTHON ?= python3
UV ?= uv
CONFIG ?= $(HOME)/.config/codex-monitor/projects.toml
SYSTEM := $(shell uname -s)

.PHONY: build test install install-service uninstall uninstall-service

build:
	$(UV) build

test:
	$(PYTHON) -m pytest

install:
	$(UV) tool install --force .

install-service:
ifeq ($(SYSTEM),Darwin)
	scripts/install-macos.sh "$(CONFIG)"
else ifeq ($(SYSTEM),Linux)
	scripts/install-systemd-user.sh "$(CONFIG)"
else
	@echo "error: unsupported operating system: $(SYSTEM)" >&2
	@exit 2
endif

uninstall:
	$(UV) tool uninstall codex-goal-monitor

uninstall-service:
ifeq ($(SYSTEM),Darwin)
	scripts/uninstall-macos.sh
else ifeq ($(SYSTEM),Linux)
	scripts/uninstall-systemd-user.sh
else
	@echo "error: unsupported operating system: $(SYSTEM)" >&2
	@exit 2
endif
