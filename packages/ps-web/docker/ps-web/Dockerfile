# Build-time argument for Python version
ARG PYTHON_VERSION=3.11
FROM python:${PYTHON_VERSION}

# prevents Python from buffering stdout and stderr
ENV PYTHONUNBUFFERED=1

# prevent Python from writing pyc files to disc 
ENV PYTHONDONTWRITEBYTECODE 1

# upgrade PIP as root user
RUN pip install --upgrade pip
# Install postgres client;clean up the cache after installing to reduce the image size
RUN apt-get update && apt-get install -y postgresql-client && rm -rf /var/lib/apt/lists/*

RUN adduser reg_user
USER reg_user
ENV PATH="/home/reg_user/.local/bin:${PATH}"
WORKDIR /home/reg_user/ps/htmlcov
WORKDIR /home/reg_user/ps/staticfiles 
WORKDIR /home/reg_user/ps
COPY --chown=reg_user:reg_user ./stage/requirements.txt requirements.txt
RUN pip install --user -r requirements.txt 
RUN pip freeze > requirements.freeze.txt
COPY --chown=reg_user:reg_user ./stage/docker-entrypoint.sh /home/reg_user/ps/
COPY --chown=reg_user:reg_user ./stage/protos/              /home/reg_user/ps_server/protos/
COPY --chown=reg_user:reg_user ./stage/manage.py            /home/reg_user/ps/
COPY --chown=reg_user:reg_user ./stage/pytest.ini           /home/reg_user/ps/
COPY --chown=reg_user:reg_user ./stage/static               /home/reg_user/ps/static/
COPY --chown=reg_user:reg_user ./stage/ps_web               /home/reg_user/ps/ps_web/
COPY --chown=reg_user:reg_user ./stage/api                  /home/reg_user/ps/api/
COPY --chown=reg_user:reg_user ./stage/templates            /home/reg_user/ps/templates/
COPY --chown=reg_user:reg_user ./stage/users                /home/reg_user/ps/users/
COPY --chown=reg_user:reg_user ./stage/.versions            .versions

COPY --chown=reg_user:reg_user . .

RUN python -m grpc_tools.protoc -I/home/reg_user/ps_server/protos/ --python_out=. --grpc_python_out=. /home/reg_user/ps_server/protos/ps_server.proto

RUN python manage.py collectstatic --noinput --clear
# hack for django rest framework
COPY --chown=reg_user:reg_user ./stage/static/images/favicon.ico  /staticfiles/images/favicon.ico
RUN rm -rf ./stage

ENTRYPOINT [ "/home/reg_user/ps/docker-entrypoint.sh" ]
