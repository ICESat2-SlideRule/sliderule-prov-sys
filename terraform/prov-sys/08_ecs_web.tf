resource "aws_ecs_cluster" "prov-sys" {
  name = "${var.domain_root}-ps-clstr"
  tags = {
    Name = "${var.domain_root}-ps-clstr"
  }
}

resource "aws_ecs_cluster_capacity_providers" "prov-sys" {
  cluster_name = aws_ecs_cluster.prov-sys.name

  capacity_providers = ["FARGATE"]

  default_capacity_provider_strategy {
    base              = 1
    weight            = 100
    capacity_provider = "FARGATE"
  }
}
data "template_file" "prov-sys" {
  template = file("templates/prov-sys.json.tpl")

  vars = {
    docker_image_url_ps-web       = var.docker_image_url_ps-web
    docker_image_url_ps-nginx     = var.docker_image_url_ps-nginx
    docker_image_url_redis        = var.docker_image_url_redis
    docker_image_url_ps-server    = var.docker_image_url_ps-server
    cluster_repo                  = local.provsys_creds.cluster_repo
    region                        = var.region
    fargate_memory                = var.prov-sys-fargate-memory
    fargate_cpu                   = var.prov-sys-fargate-cpu
    django_app_port               = var.django_app_port
    nginx_port                    = var.nginx_port
    redis_port                    = var.redis_port

    rds_legacy_db_name            = local.provsys_creds.rds_db_name
    rds_legacy_username           = local.provsys_creds.rds_username
    rds_legacy_password           = local.provsys_creds.rds_password
    rds_legacy_port               = local.provsys_creds.rds_port
    rds_legacy_hostname           = aws_db_instance.prov-sys.address

    rds_db_name                   = local.provsys_creds.rds_db_name_v4
    rds_username                  = local.provsys_creds.rds_username_v4
    rds_password                  = local.provsys_creds.rds_password_v4
    rds_port                      = local.provsys_creds.rds_port_v4
    rds_hostname                  = aws_db_instance.prov-sys-v4.address

    create_new_db                 = var.create_new_db
    run_migrations                = var.run_migrations
    run_data_migrations           = var.run_data_migrations

    django_settings_allowed_hosts = var.django_allowed_hosts
    django_csrf_trusted_origins   = var.django_csrf_trusted_origins
    django_secret_key             = local.provsys_creds.django_secret_key
    django_celery_url             = var.django_celery_url
    ps_server_host                = var.ps_server_host
    ps_server_port                = var.ps_server_container_port
    mfa_placeholder               = local.provsys_creds.mfa_placeholder
    jwt_secret_key                = local.provsys_creds.jwt_secret_key
    domain                        = var.domain
    domain_root                   = var.domain_root
    ps_version                    = var.ps_version
    ps_site_title                 = var.ps_site_title
    ps_bld_envver                 = var.ps_bld_envver
    site_id                       = var.site_id
    oidc_rsa_private_key          = local.provsys_creds.oidc_rsa_key
  }
}

resource "aws_ecs_task_definition" "prov-sys" {
  family                    = "${var.domain_root}-prov-sys"
  requires_compatibilities  = ["FARGATE"]
  network_mode              = "awsvpc"
  cpu                       = var.prov-sys-fargate-cpu
  memory                    = var.prov-sys-fargate-memory
  execution_role_arn        = aws_iam_role.prov-sys-task-service-role.arn
  task_role_arn             = aws_iam_role.prov-sys-task-role.arn
  container_definitions     = data.template_file.prov-sys.rendered
  depends_on                = [aws_db_instance.prov-sys]
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture = var.runtime_cpu_arch
  }
  tags = {
    Name = "${var.domain_root}-ps-ecs-tsk"
  }
}

resource "aws_ecs_service" "prov-sys-ecs-service" {
  name            = "${var.domain_root}-ps-srvc"
  cluster         = aws_ecs_cluster.prov-sys.id
  task_definition = aws_ecs_task_definition.prov-sys.arn
  desired_count   = var.ps_web_task_count
  launch_type     = "FARGATE"
  depends_on      = [aws_alb_listener.ps-web-http-listener]
  enable_execute_command = true

  network_configuration {
    security_groups = [aws_security_group.ecs-ps-web.id]
    subnets         = [aws_subnet.private-subnet-1.id,aws_subnet.private-subnet-2.id]
  }

  load_balancer {
    target_group_arn = aws_alb_target_group.ps-web-target-group.arn
    container_name   = "ps-nginx"
    container_port   = 80
  }
  tags = {
    Name = "${var.domain_root}-ps-srvc"
  }
}
