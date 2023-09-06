#!/bin/bash
set -eo pipefail



# Check if RUN_MIGRATIONS variable is set to "true"
echo "RUN_MIGRATIONS: $RUN_MIGRATIONS"
if [ "$RUN_MIGRATIONS" == "True" ]; then
    echo "Running makemigrations..."
    python manage.py makemigrations
    #python manage.py makemigrations --noinput
    echo "Running migrate..."
    python manage.py migrate
    echo "Migrations complete."
else
    echo "Skipping migrations..."
fi
#python manage.py init_celery # call utility to init celery
gunicorn ps_web.wsgi:application --keep-alive=300 --timeout 300 -b 0.0.0.0:8000
