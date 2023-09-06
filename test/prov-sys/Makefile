ROOT = $(shell pwd)
DOCKER_REPO ?= icesat2sliderule
VERSION ?= latest
DOCKER_TAG ?= $(VERSION)
PYTEST_ARGS ?= 
NGINX_CONF ?= dev
DB_NAME ?= ps_postgres_db
DB_BACKUP_DIR = $(ROOT)/db_backups
PS_SERVER_SOURCE_DIR = $(ROOT)/../../packages/ps-server
PS_WEB_SOURCE_DIR = $(ROOT)/../../packages/ps-web
PS_NGINX_SOURCE_DIR = $(ROOT)/../../packages/ps-web # nginx docker is built in web directory
REPO ?= $(DOCKER_REPO) 
BLD_ENVVER = $(shell cd ../../; git --git-dir .git --work-tree . describe --abbrev --dirty --always --tags --long)
# PS_BRANCH is the branch of ps-server to use for fetching the init_aws_localstack.py script
PS_BRANCH ?= $(CURRENT_BRANCH)
PS_BRANCH ?= main
AWS_DEFAULT_REGION ?= us-west-2

PS_SERVER_DIR:=$(abspath $(PS_SERVER_SOURCE_DIR))
PS_WEB_DIR:=$(abspath $(PS_WEB_SOURCE_DIR))

DOMAIN ?= localhost
DOMAIN_ROOT = $(firstword $(subst ., ,$(DOMAIN)))

TASK_ARN = $(shell aws ecs list-tasks --cluster $(DOMAIN_ROOT)-ps-web-clstr | jq -r '.taskArns[0]')
TASK_ARN_testsliderule_prov-sys = $(shell aws ecs list-tasks --cluster testsliderule-ps-clstr | jq -r '.taskArns[0]')
TASK_ARN_slideruleearth_prov-sys = $(shell aws ecs list-tasks --cluster slideruleearth-ps-clstr | jq -r '.taskArns[0]')
TESTSLIDERULE_DB_HOST = $(shell aws rds describe-db-instances --db-instance-identifier testsliderule-org | jq -r '.DBInstances[].Endpoint.Address')
# This ugliness is due to the following issue: https://github.com/pennersr/django-allauth/issues/3026
CURRENT_SITE_ID_slideruleearth := 4
CURRENT_SITE_ID_testsliderule  := 5
CURRENT_SITE_ID_dev := 6
SITE_TITLE ?= 'SlideRule Earth'  # e.g. 'SlideRule Earth' or 'SlideRule Test'

ifeq ($(DOMAIN),127.0.0.1:8080)
	SITE_ID = CURRENT_SITE_ID_dev
else ifeq ($(DOMAIN),127.0.0.1)
	SITE_ID = CURRENT_SITE_ID_dev
else ifeq ($(DOMAIN),localhost:8080)
	SITE_ID = CURRENT_SITE_ID_dev
else ifeq ($(DOMAIN),localhost)
	SITE_ID = CURRENT_SITE_ID_dev
else
	SITE_ID = 'ERROR will robinson'
endif


all: help

aws-repo-docker-login: ## This authenticates the Docker CLI to use the aws ECR ( the asw container registry )
	aws ecr get-login-password --region $(AWS_DEFAULT_REGION) | docker login --username AWS --password-stdin $(REPO)

distclean:
	cd $(PS_WEB_SOURCE_DIR)    && make ps-web-clean-stage
	cd $(PS_NGINX_SOURCE_DIR)  && make ps-nginx-clean-stage
	cd $(PS_SERVER_SOURCE_DIR) && make ps-server-clean-stage

network: ## create a test network to connect to ps_server
	docker network create ps_test_network

docker-no-cache: distclean ## build docker containers 
	cd $(PS_NGINX_SOURCE_DIR)  && DOCKER_REPO=$(REPO) PS_WEB_DOCKER_TAG=$(DOCKER_TAG) PS_NGINX_DOCKER_TAG=$(DOCKER_TAG) NGINX_CONF=dev make ps-nginx-build-docker-no-cache
	cd $(PS_WEB_SOURCE_DIR)    && DOCKER_REPO=$(REPO) PS_WEB_DOCKER_TAG=$(DOCKER_TAG) PS_NGINX_DOCKER_TAG=$(DOCKER_TAG) make docker-no-cache
	cd $(PS_SERVER_SOURCE_DIR) && DOCKER_REPO=$(REPO) PS_SERVER_DOCKER_TAG=$(DOCKER_TAG) make docker-no-cache 
	@echo SITE_ID:$(SITE_ID)
	@echo DOMAIN:$(DOMAIN)
	@echo VERSION:$(VERSION)
	@echo BLD_ENVVER:$(BLD_ENVVER)

docker: distclean ## build TEST docker containers for testing
	cd $(PS_NGINX_SOURCE_DIR)  && DOCKER_REPO=$(REPO) PS_WEB_DOCKER_TAG=$(DOCKER_TAG) PS_NGINX_DOCKER_TAG=$(DOCKER_TAG) NGINX_CONF=dev make ps-nginx-build-docker
	cd $(PS_WEB_SOURCE_DIR)    && DOCKER_REPO=$(REPO) PS_WEB_DOCKER_TAG=$(DOCKER_TAG) PS_NGINX_DOCKER_TAG=$(DOCKER_TAG) PS_VERSION=$(VERSION) make docker-test
	cd $(PS_SERVER_SOURCE_DIR) && DOCKER_REPO=$(REPO) PS_SERVER_DOCKER_TAG=$(DOCKER_TAG) PS_VERSION=$(VERSION) make docker-test 
	@echo SITE_ID:$(SITE_ID) 
	@echo DOMAIN:$(DOMAIN)
	@echo VERSION:$(VERSION)
	@echo BLD_ENVVER:$(BLD_ENVVER)

clean-coverage: ## clean up coverage files
	rm -rf htmlcov/*

docker-test: distclean clean-coverage ## build docker containers for testing
	cd $(PS_NGINX_SOURCE_DIR)  && DOCKER_REPO=$(REPO) PS_WEB_DOCKER_TAG=$(DOCKER_TAG) PS_NGINX_DOCKER_TAG=$(DOCKER_TAG) NGINX_CONF=dev make ps-nginx-build-docker
	cd $(PS_WEB_SOURCE_DIR)    && DOCKER_REPO=$(REPO) PS_WEB_DOCKER_TAG=$(DOCKER_TAG) PS_NGINX_DOCKER_TAG=$(DOCKER_TAG) PS_VERSION=$(VERSION) make docker-test
	cd $(PS_SERVER_SOURCE_DIR) && DOCKER_REPO=$(REPO) PS_SERVER_DOCKER_TAG=$(DOCKER_TAG) PS_VERSION=$(VERSION) make docker 
	@echo SITE_ID:$(SITE_ID) 
	@echo DOMAIN:$(DOMAIN)
	@echo VERSION:$(VERSION)
	@echo BLD_ENVVER:$(BLD_ENVVER)

docker-no-versions: distclean ## build docker containers 
	cd $(PS_NGINX_SOURCE_DIR)  && DOCKER_REPO=$(REPO) PS_WEB_DOCKER_TAG=$(DOCKER_TAG) PS_NGINX_DOCKER_TAG=$(DOCKER_TAG) NGINX_CONF=dev make ps-nginx-build-docker 
	cd $(PS_WEB_SOURCE_DIR)    && DOCKER_REPO=$(REPO) PS_WEB_DOCKER_TAG=$(DOCKER_TAG) PS_NGINX_DOCKER_TAG=$(DOCKER_TAG) PS_VERSION=$(VERSION) make docker-no-versions
	cd $(PS_SERVER_SOURCE_DIR) && DOCKER_REPO=$(REPO) PS_SERVER_DOCKER_TAG=$(DOCKER_TAG) PS_VERSION=$(VERSION) make docker-no-versions 
	@echo SITE_ID:$(SITE_ID)
	@echo DOMAIN:$(DOMAIN)
	@echo VERSION:$(VERSION)
	@echo BLD_ENVVER:$(BLD_ENVVER)


run: docker ## run prov-sys locally
	make down && \
	export PS_SERVER_DIR=$(PS_SERVER_DIR) && \
	export PS_WEB_DIR=$(PS_WEB_DIR) && \
	export DOCKER_TAG=$(DOCKER_TAG) && \
	export PS_SERVER_SOURCE_DIR=$(PS_SERVER_SOURCE_DIR) && \
	docker compose up --detach
	make docker-versions && \
	make db-backup-timestamped-running
	@echo SITE_ID:$(SITE_ID)
	@echo SITE_ID value:$($(SITE_ID))
	@echo DOMAIN:$(DOMAIN)
	@echo BLD_ENVVER:$(BLD_ENVVER)

unit-tests-local-stack: docker-test ## run the web_server django unit tests against a running dev system with localstack then exit
	make down && \
	export PS_SERVER_DIR=$(PS_SERVER_DIR) && \
	export PS_WEB_DIR=$(PS_WEB_DIR) && \
	export DOCKER_TAG=$(DOCKER_TAG) && \
	export PS_SERVER_SOURCE_DIR=$(PS_SERVER_SOURCE_DIR) && \
	docker compose up --detach
	sleep 3
	docker exec -it ps-web coverage run -m pytest -m "not ps_server_stubbed and not recon_sim and not cost and not system" $(PYTEST_ARGS) && \
	docker exec -it ps-web coverage html
	make down
	@echo SITE_ID:$(SITE_ID)
	@echo SITE_ID value:$($(SITE_ID))
	@echo DOMAIN:$(DOMAIN)
	@echo BLD_ENVVER:$(BLD_ENVVER)

unit-tests-ps-web-only:   ## run the web_server unit tests against a running dev system then exit
	cd $(PS_WEB_SOURCE_DIR) && make unit-tests && make down

unit-tests-ps-web-only-dev:   ## run the web_server unit tests against a running dev system then exit
	cd $(PS_WEB_SOURCE_DIR) && make unit-tests-dev && make down

unit-tests-ps-server-only:   ## run the ps_server unit tests  then exit
	cd $(PS_SERVER_SOURCE_DIR) && make unit-tests && make down

system-tests:  ## (incurs $$) run the web_server django unit tests against a running dev system then exit
	pytest -m "system" $(PYTEST_ARGS)

unit-tests: ## run all unit-tests
	make unit-tests-ps-web-only && \
	make unit-tests-ps-server-only && \
	make unit-tests-local-stack 

unit-tests-with-cost: docker ## (incurs $$) run the web_server django unit tests against a running dev system then exit
	make down && \
	export PS_SERVER_DIR=$(PS_SERVER_DIR) && \
	export PS_WEB_DIR=$(PS_WEB_DIR) && \
	export DOCKER_TAG=$(DOCKER_TAG) && \
	docker compose up --detach
	sleep 3 
	docker exec -it ps-web pytest -m "not ps_server_stubbed and not dev and not recon_sim and not system" $(PYTEST_ARGS)
	make down
	@echo SITE_ID:$(SITE_ID)
	@echo SITE_ID value:$($(SITE_ID))
	@echo DOMAIN:$(DOMAIN)
	@echo BLD_ENVVER:$(BLD_ENVVER)

unit-tests-dev: docker ## run the web_server django unit tests against a running dev system then exit
	make down && \
	export PS_SERVER_DIR=$(PS_SERVER_DIR) && \
	export PS_WEB_DIR=$(PS_WEB_DIR) && \
	export DOCKER_TAG=$(DOCKER_TAG) && \
	export PS_SERVER_SOURCE_DIR=$(PS_SERVER_SOURCE_DIR) && \
	docker compose up --detach
	sleep 3
	docker exec -it ps-web pytest -m "not ps_server_stubbed and dev and not recon_sim and not system" $(PYTEST_ARGS)
	@echo SITE_ID:$(SITE_ID)
	@echo SITE_ID value:$($(SITE_ID))
	@echo DOMAIN:$(DOMAIN)
	@echo BLD_ENVVER:$(BLD_ENVVER)

docker-versions: ## dump pip freeze versions from containers
	docker cp ps-web:/home/reg_user/ps/requirements.freeze.txt ./ps-web-versions.$(DOCKER_TAG).txt 
	docker cp ps-server:/ps_server/requirements.freeze.txt ./ps-server-versions.$(DOCKER_TAG).txt 

down: ## bring down the test docker containers
	export PS_SERVER_DIR=$(PS_SERVER_DIR) && \
	export PS_WEB_DIR=$(PS_WEB_DIR) && \
	export DOCKER_TAG=$(DOCKER_TAG) && \
	export PS_SERVER_SOURCE_DIR=$(PS_SERVER_SOURCE_DIR) && \
	docker compose down

db-backup-FULL: ## backup the db test containers db e.g. make db-backup VERSION=v2.0.4 NOTE:This backs up from DB ps_postgres_db to file sliderule-postgres-backup-v2.0.4.sql.gz
	docker compose -f docker-compose-prov-sys-db-only.yml up --detach
	docker compose -f docker-compose-prov-sys-db-only.yml exec -it ps-db /bin/bash -c "/usr/bin/pg_dump --username=ps_admin ps_postgres_db" | gzip -9 > $(DB_BACKUP_DIR)/sliderule-postgres-backup-FULL-DEV-$(VERSION).sql.gz
	docker compose -f docker-compose-prov-sys-db-only.yml down 

db-backup-FULL-timestamped: ## backup the currently running development db e.g. make db-backup VERSION=v2.0.4 NOTE:This backs up from DB ps_postgres_db to file sliderule-postgres-backup-v2.0.4.sql.gz
	docker compose -f docker-compose-prov-sys-db-only.yml up --detach && \
	docker compose -f docker-compose-prov-sys-db-only.yml exec -it ps-db /bin/bash -c "/usr/bin/pg_dump --username=ps_admin ps_postgres_db" | gzip -9 > $(DB_BACKUP_DIR)/sliderule-postgres-backup-FULL-DEV-$(VERSION)-$(shell date +"%Y-%m-%d-%H-%M-%S").sql.gz
	docker compose -f docker-compose-prov-sys-db-only.yml down 

db-backup-SCHEMA-ONLY: ## backup the db test containers db e.g. make db-backup VERSION=v2.0.4 NOTE:This backs up from DB ps_postgres_db to file sliderule-postgres-backup-v2.0.4.sql.gz
	docker compose -f docker-compose-prov-sys-db-only.yml up --detach
	docker compose -f docker-compose-prov-sys-db-only.yml exec -it ps-db /bin/bash -c "/usr/bin/pg_dump --schema-only --username=ps_admin ps_postgres_db" | gzip -9 > $(DB_BACKUP_DIR)/sliderule-postgres-backup-SCHEMA-ONLY-DEV-$(VERSION).sql.gz
	docker compose -f docker-compose-prov-sys-db-only.yml down 

db-backup-SCHEMA-ONLY-timestamped: ## backup the db test containers db e.g. make db-backup VERSION=v2.0.4 NOTE:This backs up from DB ps_postgres_db to file sliderule-postgres-backup-v2.0.4.sql.gz
	docker compose -f docker-compose-prov-sys-db-only.yml up --detach
	docker compose -f docker-compose-prov-sys-db-only.yml exec -it ps-db /bin/bash -c "/usr/bin/pg_dump --schema-only --username=ps_admin ps_postgres_db" | gzip -9 > $(DB_BACKUP_DIR)/sliderule-postgres-backup-SCHEMA-ONLY-DEV-$(VERSION)-$(shell date +"%Y-%m-%d-%H-%M-%S").sql.gz
	docker compose -f docker-compose-prov-sys-db-only.yml down 

db-backup-DATA-ONLY: ## backup the db test containers db e.g. make db-backup VERSION=v2.0.4 NOTE:This backs up from DB ps_postgres_db to file sliderule-postgres-backup-v2.0.4.sql.gz
	docker compose -f docker-compose-prov-sys-db-only.yml up --detach
	docker compose -f docker-compose-prov-sys-db-only.yml exec -it ps-db /bin/bash -c "/usr/bin/pg_dump --data-only --username=ps_admin ps_postgres_db" | gzip -9 > $(DB_BACKUP_DIR)/sliderule-postgres-backup-DATA-ONLY-DEV-$(VERSION).sql.gz
	docker compose -f docker-compose-prov-sys-db-only.yml down 

db-backup-DATA-ONLY-timestamped: ## backup the db test containers db e.g. make db-backup VERSION=v2.0.4 NOTE:This backs up from DB ps_postgres_db to file sliderule-postgres-backup-v2.0.4.sql.gz
	docker compose -f docker-compose-prov-sys-db-only.yml up --detach
	docker compose -f docker-compose-prov-sys-db-only.yml exec -it ps-db /bin/bash -c "/usr/bin/pg_dump --data-only --username=ps_admin ps_postgres_db" | gzip -9 > $(DB_BACKUP_DIR)/sliderule-postgres-backup-DATA-ONLY-DEV-$(VERSION)-$(shell date +"%Y-%m-%d-%H-%M-%S").sql.gz
	docker compose -f docker-compose-prov-sys-db-only.yml down 

get-TASK-ARN:
	aws ecs list-tasks --cluster $(DOMAIN_ROOT)-ps-web-clstr	

ecs-ps-web-shell-testsliderule: ## login to the remote ps-web aws container on testsliderule.org using TASK_ARN for testsliderule-ps-web-srvc task
	aws ecs execute-command \
		--cluster testsliderule-ps-clstr \
		--task $(TASK_ARN_testsliderule_prov-sys) \
		--container ps-web \
		--interactive \
		--command "/bin/sh"

ecs-ps-web-shell-slideruleearth: ## login to the remote ps-web aws container on slideruleearth.io using TASK_ARN for slideruleearth-ps-srvc task
	aws ecs execute-command \
		--cluster slideruleearth-ps-clstr \
		--task $(TASK_ARN_slideruleearth_prov-sys) \
		--container ps-web \
		--interactive \
		--command "/bin/sh" 

ecs-ps-svr-shell-testsliderule: ## login to the remote ps-web aws container on testsliderule.org using TASK_ARN for testsliderule-ps-web-srvc task
	aws ecs execute-command \
		--cluster testsliderule-ps-clstr \
		--task $(TASK_ARN_testsliderule_prov-sys) \
		--container ps-server \
		--interactive \
		--command "/bin/sh" 

ecs-ps-svr-shell-slideruleearth: ## login to the remote ps-server aws container on slideruleearth.io using TASK_ARN for slideruleearth-ps-srvc task
	aws ecs execute-command \
		--cluster slideruleearth-ps-clstr \
		--task $(TASK_ARN_slideruleearth_prov-sys) \
		--container ps-server \
		--interactive \
		--command "/bin/sh" 

ecs-ps-redis-shell-testsliderule: ## login to the remote ps-web aws container on testsliderule.org using TASK_ARN for testsliderule-ps-web-srvc task
	aws ecs execute-command \
		--cluster testsliderule-ps-web-clstr \
		--task $(TASK_ARN_testsliderule_prov-sys) \
		--container redis \
		--interactive \
		--command "/bin/sh" 

ecs-ps-redis-shell-slideruleearth: ## login to the remote ps-web aws container on testsliderule.org using TASK_ARN for testsliderule-ps-web-srvc task
	aws ecs execute-command \
		--cluster slideruleearth-ps-web-clstr \
		--task $(TASK_ARN_slideruleearth_prov-sys) \
		--container redis \
		--interactive \
		--command "/bin/sh" 

# use the aws console and get rds_password from $(DOMAIN)/secrets
ecs-ps-web-pg-dump-db-schema-in-testsliderule: ## create a db-schema.sql file on the container
	aws ecs execute-command \
		--cluster testsliderule-ps-web-clstr \
		--task $(TASK_ARN_testsliderule_ps_web) \
		--container testsliderule-ps-web \
		--interactive \
		--command "/usr/bin/pg_dump --schema-only --username=ps_admin --host=$(TESTSLIDERULE_DB_HOST) provsys --file=db-schema.sql"

# TBD this creates a file with some extra lines in the beginning which are message from the session manager
# figure how to supress those and get a clean schema file
ecs-ps-web-get-db-schema-from-testsliderule: # cat the schema from testsliderule db
	make ecs-ps-web-pg-dump-db-schema-in-testsliderule && \
	aws ecs execute-command \
		--cluster testsliderule-ps-web-clstr \
		--task $(TASK_ARN_testsliderule_ps_web) \
		--container testsliderule-ps-web \
		--interactive \
		--command "cat db-schema.sql" > db.testsliderule-schema-$(shell date +"%Y-%m-%d-%H-%M-%S").sql

db-run: 
	docker compose -f docker-compose-prov-sys-db-only.yml up --detach

db-down:
	docker compose -f docker-compose-prov-sys-db-only.yml down 

db-drop: ##  (CAUTION!) drop the current test db in its docker volume e.g. make db-drop  NOTE:This drops the db named: ps_postgres_db 
	docker compose -f docker-compose-prov-sys-db-only.yml up --detach && \
	docker compose -f docker-compose-prov-sys-db-only.yml exec -it ps-db dropdb -i -e --username=ps_admin $(DB_NAME);
	docker compose -f docker-compose-prov-sys-db-only.yml down 

db-create-blank: #  create a blank test db in its docker volume e.g. make db-drop  NOTE:This drops the db named: ps_postgres_db 
	docker compose -f docker-compose-prov-sys-db-only.yml up --detach && \
	docker compose -f docker-compose-prov-sys-db-only.yml exec -it ps-db createdb -e --username=ps_admin ps_postgres_db; 
	docker compose -f docker-compose-prov-sys-db-only.yml down 

db-psql-shell: ##   NOTE:This runs psql in a shell with db running solo
	docker compose -f docker-compose-prov-sys-db-only.yml down 
	docker compose -f docker-compose-prov-sys-db-only.yml up --detach && \
 	docker compose -f docker-compose-prov-sys-db-only.yml exec -it ps-db psql -U ps_admin ps_postgres_db && \
	docker compose -f docker-compose-prov-sys-db-only.yml down 

db-restore-FULL:  ## restore the test db e.g. make db-restore-FULL VERSION=v2.0.4 NOTE:This restores to a BLANK! db named: ps_postgres_db from file /sliderule-postgres-backup-FULL-DEV-$(VERSION).sql.gz
	docker compose -f docker-compose-prov-sys-db-only.yml down 
	docker compose -f docker-compose-prov-sys-db-only.yml up --detach && \
	gunzip < $(DB_BACKUP_DIR)/sliderule-postgres-backup-FULL-DEV-$(VERSION).sql.gz | docker exec -i ps-db psql --username=ps_admin -d ps_postgres_db 
	docker compose -f docker-compose-prov-sys-db-only.yml down 

db-restore-DATA:  ## restore the test db SHEMA e.g. make db-restore-SCHEMA VERSION=v2.0.4 NOTE:This restores to a BLANK! db named: ps_postgres_db from file /sliderule-postgres-backup-SHEMA-ONLY-DEV-$(VERSION).sql.gz
	docker compose -f docker-compose-prov-sys-db-only.yml down 
	docker compose -f docker-compose-prov-sys-db-only.yml up --detach && \
	gunzip < $(DB_BACKUP_DIR)/sliderule-postgres-backup-DATA-ONLY-DEV-$(VERSION).sql.gz | docker exec -i ps-db psql --username=ps_admin -d ps_postgres_db 
	docker compose -f docker-compose-prov-sys-db-only.yml down 

db-restore-SCHEMA-FROM-DEV-file:  ## restore the test db SHEMA e.g. make db-restore-SCHEMA-DEV VERSION=v2.0.4 NOTE:This restores to a BLANK! db named: ps_postgres_db from file /sliderule-postgres-backup-SHEMA-ONLY-DEV-$(VERSION).sql.gz
	docker compose -f docker-compose-prov-sys-db-only.yml down 
	docker compose -f docker-compose-prov-sys-db-only.yml up --detach && \
	gunzip < $(DB_BACKUP_DIR)/sliderule-postgres-backup-SCHEMA-ONLY-DEV-$(VERSION).sql.gz | docker exec -i ps-db psql --username=ps_admin -d ps_postgres_db 
	docker compose -f docker-compose-prov-sys-db-only.yml down 

db-restore-DATA-FROM-DEV-file:  ## restore the test db DATA e.g. make db-restore-SCHEMA-DEV VERSION=v2.0.4 NOTE:This restores to a BLANK! db named: ps_postgres_db from file /sliderule-postgres-backup-SHEMA-ONLY-DEV-$(VERSION).sql.gz
	docker compose -f docker-compose-prov-sys-db-only.yml down 
	docker compose -f docker-compose-prov-sys-db-only.yml up --detach && \
	gunzip < $(DB_BACKUP_DIR)/sliderule-postgres-backup-DATA-ONLY-DEV-$(VERSION).sql.gz | docker exec -i ps-db psql --username=ps_admin -d ps_postgres_db 
	docker compose -f docker-compose-prov-sys-db-only.yml down 

#NOTE:This restores to a BLANK! db named: ps_postgres_db from file sliderule-postgres-backup-FROM-$(DOMAIN_ROOT)-SCHEMA-ONLY-$(DB_DATE).sql.gz
db-restore-SCHEMA-FROM-DOMAIN-file:  ## restore the test db SCHEMA e.g. make db-restore-SCHEMA-DOMAIN DOMAIN=slideruleearth DB_DATE=2023-02-06-10-03-59 
	docker compose -f docker-compose-prov-sys-db-only.yml down 
	docker compose -f docker-compose-prov-sys-db-only.yml up --detach && \
	gunzip < $(DB_BACKUP_DIR)/sliderule-postgres-backup-FROM-$(DOMAIN_ROOT)-SCHEMA-ONLY-$(DB_DATE).sql.gz | docker exec -i ps-db psql --username=ps_admin -d ps_postgres_db 
	docker compose -f docker-compose-prov-sys-db-only.yml down 

#NOTE:This restores to a BLANK! db named: ps_postgres_db from file sliderule-postgres-backup-FROM-$(DOMAIN_ROOT)-SCHEMA-ONLY-$(DB_DATE).sql.gz
db-restore-SCHEMA-FROM-sql-file:  ## restore the test db SCHEMA e.g. make db-restore-SCHEMA-DOMAIN DOMAIN=slideruleearth DB_DATE=2023-02-06-10-03-59 
	docker compose -f docker-compose-prov-sys-db-only.yml down 
	docker compose -f docker-compose-prov-sys-db-only.yml up --detach && \
	$(DB_BACKUP_DIR)/$(DB_FILE) | docker exec -i ps-db psql --username=ps_admin -d ps_postgres_db 
	docker compose -f docker-compose-prov-sys-db-only.yml down 

db-restore-FULL-from-sql-gz:  ## restore the test db e.g. make db-restore-FULL VERSION=v2.0.4 NOTE:This restores to a BLANK! db named: ps_postgres_db from file /sliderule-postgres-backup-FULL-DEV-$(VERSION).sql.gz
	docker compose -f docker-compose-prov-sys-db-only.yml down 
	docker compose -f docker-compose-prov-sys-db-only.yml up --detach && \
	gunzip < $(DB_BACKUP_DIR)/$(FILE) | docker exec -i ps-db psql --username=ps_admin -d ps_postgres_db 
	docker compose -f docker-compose-prov-sys-db-only.yml down 

db-destroy-create-restore-FULL: ## bring down any running; destroy existing db; create a blank one; FULL restore to a version
	make down && \
	make db-drop && \
	make db-create-blank && \
	make db-restore-FULL

db-destroy-create-restore-SCHEMA: ## bring down any running; destroy existing db; create a blank one; restore the SCHEMA to a version
	make down && \
	make db-drop && \
	make db-create-blank && \
	make db-restore-SCHEMA


db-restore-sql-file:  ## restore the test db e.g. make db-restore-file NOTE:This restores to a db named: ps_postgres_db from file $(DB_FILE)
	docker compose -f docker-compose-prov-sys-db-only.yml down 
	docker compose -f docker-compose-prov-sys-db-only.yml up --detach && \
	gunzip < $(DB_BACKUP_DIR)/$(DB_FILE) | docker exec -i ps-db psql --username=ps_admin -d ps_postgres_db 
	docker compose -f docker-compose-prov-sys-db-only.yml down 

db-clean:
	rm db_backups/sliderule-postgres-DEV-backup-run-locally-*

# These use the running container
db-psql-shell-running: ##   NOTE:This runs psql in a shell with the entire set of prov sys containers
	docker exec -it ps-db psql -U ps_admin -d ps_postgres_db 

db-backup-running: ## backup the currently running db in the run-all-locally test containers db e.g. make db-backup VERSION=v2.0.4 NOTE:This backs up from DB ps_postgres_db to file sliderule-postgres-backup-v2.0.4.sql.gz
	docker exec ps-db /bin/bash -c "/usr/bin/pg_dump -U ps_admin ps_postgres_db" | gzip -9 > $(DB_BACKUP_DIR)/sliderule-postgres-backup-test-$(VERSION).sql.gz

db-backup-timestamped-running: ## backup the currently running development db e.g. make db-backup VERSION=v2.0.4 NOTE:This backs up from DB ps_postgres_db to file sliderule-postgres-backup-v2.0.4.sql.gz
	docker exec ps-db /bin/bash -c "/usr/bin/pg_dump -U ps_admin ps_postgres_db" | gzip -9 > $(DB_BACKUP_DIR)/sliderule-postgres-backup-test-$(VERSION)-$(shell date +"%Y-%m-%d-%H-%M-%S").sql.gz

db-restore-version-running: db-drop-ps_postgres_db ## restore the test db e.g. make db-restore VERSION=v2.0.4 NOTE:This restores to a db named: ps_postgres_db from file sliderule-postgres-backup-v2.0.4.sql.gz
	gunzip < $(DB_BACKUP_DIR)/sliderule-postgres-DEV-backup-$(VERSION).sql.gz | docker exec -i test-ps-db psql -U ps_admin -d ps_postgres_db	

# db-restore-timestamped: db-drop-ps_postgres_db ## restore the test db e.g. make db-restore VERSION=run-locally TIMESTAMP=2023-02-02-15-00-21 NOTE:This restores to a db named: ps_postgres_db from file sliderule-postgres-backup-run-locally-2023-02-02-15-00-21.sql.gz
# 	gunzip < $(DB_BACKUP_DIR)/sliderule-postgres-DEV-backup-$(VERSION)-$(TIMESTAMP).sql.gz | docker exec -i test-ps-db psql -U ps_admin -d ps_postgres_db	

docker-prune: ## clean up stale dangling docker images
	docker system prune -a

.PHONY : run-unit-tests docker docker-no-versions clean-coverage

help: ## That's me!
	@printf "\033[37m%-30s\033[0m %s\n" "#-----------------------------------------------------------------------------------------"
	@printf "\033[37m%-30s\033[0m %s\n" "# Makefile Help       "
	@printf "\033[37m%-30s\033[0m %s\n" "#-----------------------------------------------------------------------------------------"
	@printf "\033[37m%-30s\033[0m %s\n" "#----target--------------------description------------------------------------------------"
	@grep -E '^[a-zA-Z_-].+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-30s\033[0m %s\n", $$1, $$2}'
	@echo SITE_ID:$(SITE_ID)
	@echo SITE_ID value:$($(SITE_ID))
	@echo DOMAIN:$(DOMAIN)
	@echo BLD_ENVVER:$(BLD_ENVVER)
