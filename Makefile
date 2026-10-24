# Build the satweather Debian package.
#
# Usage:
#   make deb                   # build (date-stamped version)
#   make deb VERSION=1.2.3     # build with explicit version
#   make test                  # backend tests
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

VERSION   ?= $(shell date +%Y%m%d.%H%M%S)
REPO_URL  ?= https://apt-upload.808.org/upload
BUILD_DIR := build
ROOT      := $(BUILD_DIR)/satweather_$(VERSION)_all
NPM       ?= npm

.PHONY: all deb web test publish clean

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

publish: deb
ifndef REPO_API_KEY
	$(error REPO_API_KEY is not set)
endif
	curl -f -H "Authorization: Bearer $(REPO_API_KEY)" \
		--upload-file $(ROOT).deb $(REPO_URL)/satweather_$(VERSION)_all.deb

clean:
	rm -rf $(BUILD_DIR) frontend/dist
