#!/bin/bash
set -eo pipefail
python manage.py makemigrations users
python manage.py makemigrations
#python manage.py makemigrations --noinput
python manage.py migrate
python manage.py init_queues # call utility to init work queues
# python manage.py get_ps_server_versions
# python manage.py createcachetable # used by allauth
gunicorn ps_web.wsgi:application --workers 4 --keep-alive=300 --timeout 300 -b 0.0.0.0:8000
