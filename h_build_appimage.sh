#!/bin/bash

# based on scripts/_local/appimage.sh

# the shell will immediately exit when there is error
set -e

# setup environment variables
. "scripts/init_app_vars.sh"

# remove previous build
rm -f "$DIST_DIR"/*.whl

# package to python wheel
poetry build -f wheel

# generate app info
bash "$SCRIPTS_DIR/linux_meta/build.sh"

# start building
bash "$SCRIPTS_DIR/appimage/build.sh" "$@"
