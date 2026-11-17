SHELL := /bin/bash
.DEFAULT_GOAL := build

API_DIR := apis/cloudflareworkers
EXAMPLES := minimal public private

.PHONY: build render test generate-configuration
build:
	up project build

generate-configuration:
	hops validate generate-configuration --path . --api-path "$(API_DIR)"

render:
	@set -euo pipefail; for example in $(EXAMPLES); do \
		up composition render --xrd=$(API_DIR)/definition.yaml $(API_DIR)/composition.yaml \
			"examples/cloudflareworkers/$$example.yaml" --extra-resources=examples/platform.yaml; \
	done

test:
	python3 scripts/test-render.py
