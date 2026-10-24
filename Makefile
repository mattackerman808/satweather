# Build the satweather Debian package.
#
# Usage:
#   make deb                   # build (date-stamped version)
#   make deb VERSION=1.2.3     # build with explicit version
#   make test                  # backend tests
#   make push                  # this working tree straight onto satweather
#   make remote-test           # backend tests on satweather, against real PostGIS
#   make clean
#
# CI (.github/workflows/publish.yml) builds on every push to main and
# uploads to the 808 apt repo. Do not `make publish` by hand: a hand-built
# version colliding with a CI one wedges the repo.
#
# Layout in the deb (the venv is built on the host by salt/satweather,
# never shipped):
#   /opt/satweather/backend/pyproject.toml
#   /opt/satweather/backend/satweather/     (FastAPI app, workers, migrations)
#   /opt/satweather/web/                    (built frontend, served by Apache)
#
# `make push` is for iterating: it rsyncs the backend source and a fresh
# frontend build into those paths on $(HOST), reinstalls the venv only if
# pyproject.toml changed (the same marker salt checks), migrates, and
# restarts the workers. The install is editable, so that is all new code
# needs. The next deb from CI replaces whatever was pushed.
#
# `make remote-test` runs the backend tests on $(HOST) before a push: its
# own venv under $(TEST_DIR) (pip reruns when pyproject.toml changes), as
# the postgres superuser (PostGIS is not a trusted extension, and the DB
# tests recreate it) against satweather_test, never the live database.

VERSION   ?= $(shell date +%Y%m%d.%H%M%S)
REPO_URL  ?= https://apt-upload.808.org/upload
BUILD_DIR := build
ROOT      := $(BUILD_DIR)/satweather_$(VERSION)_all
NPM       ?= npm
HOST      ?= satweather
TEST_DIR  ?= /var/tmp/satweather-test

.PHONY: all deb web test push remote-test publish clean

all: deb

web:
	cd frontend && $(NPM) ci --no-audit --no-fund && $(NPM) run build

deb: web
	rm -rf $(ROOT)
	mkdir -p $(ROOT)/opt/satweather/backend $(ROOT)/opt/satweather/web
	cp    backend/pyproject.toml $(ROOT)/opt/satweather/backend/
	cp -a backend/satweather     $(ROOT)/opt/satweather/backend/
	cp -a frontend/dist/.        $(ROOT)/opt/satweather/web/
	find $(ROOT) -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find $(ROOT) -name '*.pyc' -delete 2>/dev/null || true
	rm -rf $(ROOT)/opt/satweather/backend/*.egg-info
	mkdir -p $(ROOT)/DEBIAN
	sed 's/{{VERSION}}/$(VERSION)/' packaging/satweather/DEBIAN/control > $(ROOT)/DEBIAN/control
	dpkg-deb --build --root-owner-group $(ROOT)
	@echo "  -> $(ROOT).deb"

test:
	cd backend && python3 -m pytest -q

push:
	cd frontend && { [ -d node_modules ] || $(NPM) ci --no-audit --no-fund; } && $(NPM) run build
	rsync -rlt --delete --chown=root:root --rsync-path="sudo rsync" --exclude __pycache__ --exclude '*.pyc' \
		backend/satweather/ $(HOST):/opt/satweather/backend/satweather/
	rsync -t --chown=root:root --rsync-path="sudo rsync" backend/pyproject.toml $(HOST):/opt/satweather/backend/
	rsync -rlt --delete --chown=root:root --rsync-path="sudo rsync" frontend/dist/ $(HOST):/opt/satweather/web/
	ssh $(HOST) 'set -e; cd /opt/satweather; \
		sudo sha256sum -c --status venv/.pyproject.sha256 || { \
			sudo venv/bin/pip install --quiet -e backend && \
			sudo sh -c "sha256sum /opt/satweather/backend/pyproject.toml > venv/.pyproject.sha256"; }; \
		sudo -u satweather sh -c "set -a; . /etc/satweather/satweather.env; exec venv/bin/satweather-migrate"; \
		sudo systemctl restart satweather-api satweather-ingest; \
		sudo systemctl try-restart satweather-imaging 2>/dev/null || true; \
		systemctl is-active satweather-api satweather-ingest satweather-imaging || true'

remote-test:
	rsync -rlt --mkpath --delete --exclude __pycache__ --exclude '*.pyc' --exclude .venv --exclude '*.egg-info' \
		backend/ $(HOST):$(TEST_DIR)/backend/
	ssh $(HOST) 'set -e; cd $(TEST_DIR); \
		[ -x venv/bin/python ] || python3 -m venv venv; \
		sha256sum -c --status venv/.pyproject.sha256 2>/dev/null || { \
			venv/bin/pip install --quiet -e "backend[test]" && \
			sha256sum $(TEST_DIR)/backend/pyproject.toml > venv/.pyproject.sha256; }; \
		sudo -u postgres psql -Atc "SELECT 1 FROM pg_database WHERE datname = '"'"'satweather_test'"'"'" | grep -q 1 \
			|| sudo -u postgres createdb satweather_test; \
		cd /tmp && sudo -u postgres env PYTHONDONTWRITEBYTECODE=1 \
			SATWEATHER_TEST_DATABASE_URL="postgresql+psycopg://postgres@/satweather_test?host=/var/run/postgresql" \
			$(TEST_DIR)/venv/bin/python -m pytest -q -p no:cacheprovider --rootdir $(TEST_DIR)/backend \
			-c $(TEST_DIR)/backend/pyproject.toml $(TEST_DIR)/backend/tests $(ARGS)'

publish: deb
ifndef REPO_API_KEY
	$(error REPO_API_KEY is not set)
endif
	curl -f -H "Authorization: Bearer $(REPO_API_KEY)" \
		--upload-file $(ROOT).deb $(REPO_URL)/satweather_$(VERSION)_all.deb

clean:
	rm -rf $(BUILD_DIR) frontend/dist
