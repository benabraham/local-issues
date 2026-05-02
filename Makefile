.PHONY: test install dev

# Stdlib-only test runner.
test:
	PYTHONPATH=src python3 -m unittest discover -s tests -v

# Local install (pipx).
install:
	pipx install --force .

# Editable dev install.
dev:
	pip install -e .
