#!/bin/bash
# Planted defects in convert.py, verify.py and police.py: each must make test_police.py fail.
# The list and the runner are in mutate_police.py (temporary files only, no bytecode cache).
exec python "$(dirname "$0")/mutate_police.py"
