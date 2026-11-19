#!/bin/bash
set -a; . /etc/klosa/env; set +a
exec /srv/klosa/venv/bin/python /srv/klosa/app/manage.py "$@"