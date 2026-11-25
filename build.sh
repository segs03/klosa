#!/usr/bin/env bash
# Render build command:  ./build.sh      (make it executable once:  git update-index --chmod=+x build.sh)
set -o errexit

pip install -r requirements.txt

# Klosa's own face-check models. If this fails the site still works: face checks go to staff review.
python scripts/fetch_face_models.py || echo "WARNING: face models were not downloaded"

python manage.py collectstatic --noinput
python manage.py migrate --noinput
python manage.py createcachetable          # rate-limit counters shared by all server processes

# First admin. Needs DJANGO_SUPERUSER_USERNAME, DJANGO_SUPERUSER_EMAIL, DJANGO_SUPERUSER_PASSWORD.
# Harmless when the admin already exists.
python manage.py createsuperuser --noinput || echo "admin not created (already exists, or the DJANGO_SUPERUSER_* variables are not set)"
