#!/bin/sh
export PYTHONPATH="/app/lib/python${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m bulkuninstaller "$@"
