[
    {
        "name": "ps-server",
        "image": "${docker_image_url_ps-server}",
        "essential": true,
        "networkMode": "awsvpc",
        "portMappings": [
            {
                "containerPort": ${ps_server_port},
                "protocol": "tcp"
            }
        ],
        "command": [
            "python",
            "ps_server.py"
        ],
        "linuxParameters": {
            "initProcessEnabled": true
        },
        "environment": [
            {
                "name": "GRPC_POLL_STRATEGY",
                "value": "poll"
            },
            {
                "name": "CLUSTER_REPO",
                "value": "${cluster_repo}"
            },
            {
                "name":"DOMAIN",
                "value": "${domain}"
            }
        ],
        "logConfiguration": {
            "logDriver": "awslogs",
            "options": {
                "awslogs-create-group": "true",
                "awslogs-group": "/ecs/${domain_root}-prov-sys",
                "awslogs-region": "${region}",
                "awslogs-stream-prefix": "${domain_root}-ps-server"
            }
        }
    },
    {
        "name": "redis",
        "image": "${docker_image_url_redis}",
        "essential": true,
        "portMappings": [
            {
                "containerPort": ${redis_port},
                "protocol": "tcp"
            }
        ],
        "logConfiguration": {
            "logDriver": "awslogs",
            "options": {
                "awslogs-create-group": "true",
                "awslogs-group": "/ecs/${domain_root}-prov-sys",
                "awslogs-region": "${region}",
                "awslogs-stream-prefix": "${domain_root}-ps-redis"
            }
        }
    },
    {
        "name": "ps-web",
        "image": "${docker_image_url_ps-web}",
        "essential": true,
        "networkMode": "awsvpc",
        "portMappings": [
            {
                "containerPort": ${django_app_port},
                "protocol": "tcp"
            }
        ],
        "dependsOn": [
            {
            "containerName": "redis",
            "condition": "START"
            },
            {
            "containerName": "ps-server",
            "condition": "START"
            }
        ],
        "linuxParameters": {
            "initProcessEnabled": true
        },        
        "environment": [
            {
                "name": "RUN_MIGRATIONS",
                "value": "${run_migrations}"
            },
            {
                "name": "SQL_ENGINE",
                "value": "django.db.backends.postgresql"
            },
            {
                "name": "POSTGRES_DB",
                "value": "${rds_db_name}"
            },
            {
                "name": "POSTGRES_USER",
                "value": "${rds_username}"
            },
            {
                "name": "POSTGRES_PASSWORD",
                "value": "${rds_password}"
            },
            {
                "name": "POSTGRES_HOST",
                "value": "${rds_hostname}"
            },
            {
                "name": "POSTGRES_PORT",
                "value": "${rds_port}"
            },
            {
                "name": "LEGACY_POSTGRES_DB",
                "value": "${rds_legacy_db_name}"
            },
            {
                "name": "LEGACY_POSTGRES_USER",
                "value": "${rds_legacy_username}"
            },
            {
                "name": "LEGACY_POSTGRES_PASSWORD",
                "value": "${rds_legacy_password}"
            },
            {
                "name": "LEGACY_POSTGRES_HOST",
                "value": "${rds_legacy_hostname}"
            },
            {
                "name": "LEGACY_POSTGRES_PORT",
                "value": "${rds_legacy_port}"
            },
            {
                "name": "DJANGO_ALLOWED_HOSTS",
                "value": "${django_settings_allowed_hosts}"
            },
            {
                "name": "CSRF_TRUSTED_ORIGINS",
                "value": "${django_csrf_trusted_origins}"
            },
            {
                "name": "DJANGO_SECRET_KEY",
                "value": "${django_secret_key}"
            },
            {
                "name": "CELERY_BROKER_URL",
                "value": "${django_celery_url}"
            },
            {
                "name": "PS_SERVER_HOST",
                "value": "${ps_server_host}"
            },
            {
                "name": "PS_SERVER_PORT",
                "value": "${ps_server_port}"
            },
            {
                "name": "MFA_PLACEHOLDER",
                "value": "${mfa_placeholder}"
            },
            {
                "name": "GRPC_POLL_STRATEGY",
                "value": "poll"
            },
            {
                "name": "GRPC_DNS_RESOLVER",
                "value":  "native"
            },
            {
                "name": "JWT_SECRET_KEY",
                "value": "${jwt_secret_key}"
            },
            {
                "name": "DOMAIN",
                "value": "${domain}"
            },
            {
                "name": "PS_VERSION",
                "value": "${ps_version}"
            },
            {
                "name": "PS_SITE_TITLE",
                "value": "${ps_site_title}"
            },
            {
                "name": "PS_BLD_ENVVER",
                "value": "${ps_bld_envver}"
            },
            {
                "name": "SITE_ID",
                "value": "${site_id}"
            },
            {
                "name": "DJANGO_OIDC_RSA_PRIVATE_KEY",
                "value": "${oidc_rsa_private_key}"
            }
        ],
        "logConfiguration": {
            "logDriver": "awslogs",
            "options": {
                "awslogs-create-group": "true",
                "awslogs-group": "/ecs/${domain_root}-prov-sys",
                "awslogs-region": "${region}",
                "awslogs-stream-prefix": "${domain_root}-ps-web"
            }
        }
    },
    {
        "name": "ps-nginx",
        "image": "${docker_image_url_ps-nginx}",
        "essential": true,
        "networkMode": "awsvpc",
        "portMappings": [
            {
                "containerPort": ${nginx_port},
                "protocol": "tcp"
            }
        ],
        "dependsOn": [
            {
            "containerName": "ps-web",
            "condition": "START"
            }
        ],
        "logConfiguration": {
            "logDriver": "awslogs",
            "options": {
                "awslogs-create-group": "true",
                "awslogs-group": "/ecs/${domain_root}-prov-sys",
                "awslogs-region": "${region}",
                "awslogs-stream-prefix": "${domain_root}-ps-nginx"
            }
        }
    }
]