PYTHON ?= python3
PIPX ?= pipx
CONFIG ?= $(HOME)/.config/codex-monitor/projects.toml
SYSTEM := $(shell uname -s)

.PHONY: build test install install-service uninstall uninstall-service

build:
	$(PYTHON) -m build

test:
	$(PYTHON) -m pytest

install:
	$(PIPX) install --force .

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
	$(PIPX) uninstall codex-goal-monitor

uninstall-service:
ifeq ($(SYSTEM),Darwin)
	scripts/uninstall-macos.sh
else ifeq ($(SYSTEM),Linux)
	scripts/uninstall-systemd-user.sh
else
	@echo "error: unsupported operating system: $(SYSTEM)" >&2
	@exit 2
endif
