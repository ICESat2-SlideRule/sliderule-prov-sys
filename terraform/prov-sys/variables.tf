# core
variable "cost_grouping" {
  description = "the name tag to identify a grouping or subset of resources"
  type        = string
  default     = "prov-sys"
}

variable "region" {
  description = "The AWS region to create resources in."
  default     = "us-west-2"
}

# networking

variable "public_subnet_1_cidr" {
  description = "CIDR Block for Public Subnet 1"
  default     = "10.0.1.0/24"
}
variable "public_subnet_2_cidr" {
  description = "CIDR Block for Public Subnet 2"
  default     = "10.0.2.0/24"
}
variable "private_subnet_1_cidr" {
  description = "CIDR Block for Private Subnet 1"
  default     = "10.0.3.0/24"
}
variable "private_subnet_2_cidr" {
  description = "CIDR Block for Private Subnet 2"
  default     = "10.0.4.0/24"
}
variable "availability_zones" {
  description = "Availability zones"
  type        = list(string)
  default     = ["us-west-2a", "us-west-2c"]
}

# ps-web load balancer

variable "ps_web_health_check_path" {
  description = "Health check path for the default target group"
  default     = "/ping/"
}

# ps-server load balancer

variable "ps_server_host" {
  description = "url for ps_web gRpc client to connect to ps_server gRpc server"
  default     = "ps-server"
}

variable "ps_server_health_check_path" {
  description = "Health check path for the default target group"
  default     = "/"
  #default     = "/AWS.ALB/healthcheck"
}
# ecs
variable "docker_image_url_ps-web" {
  description = "django Docker image to run in the ECS cluster"
  default = "MUST_SUPPLY"
}
variable "docker_image_url_ps-nginx" {
  description = "nginx Docker image to run in the ECS cluster"
  default = "MUST_SUPPLY"
}
variable "docker_image_url_redis" {
  description = "redis Docker image to run in the ECS cluster"
  default     = "redis:alpine" #vanilla redis
}

variable "docker_image_url_ps-server" {
  description = "ps_server Docker image to run in the ECS cluster"
  default = "MUST_SUPPLY"
}

variable "ps_server_task_count" {
  description = "Number of ps server tasks to run"
  default     = 1
}
variable "ps_web_task_count" {
  description = "Number of web tasks to run"
  default     = 1
}
variable "django_allowed_hosts" {
  description = "Domain name for allowed hosts"
  default     = ".testsliderule.org .slideruleearth.io" # the leading dot acts as a wildcard
}
variable "django_csrf_trusted_origins"{
  description = "Cross Site Request Forgery trusted origins"
  default = "https://*.testsliderule.org https://*.slideruleearth.io"
}

variable "django_celery_url" {
  description = "Django celery message broker url"
  default = "redis://localhost:6379/0" 
}

# logs

variable "log_retention_in_days" {
  default = 30
}

# rds
variable "rds_id" {
  description = "RDS identifier"
  default = "prov-sys"
}

variable "rds_instance_class" {
  description = "RDS instance type"
  default     = "db.t4g.micro"
}

variable "rds_final_snapshot" {
  description = "The name to use for final rds snapshot before destroying"
}

variable "rds_restore_with_snapshot" {
  description = "The name of rds snapshot to use to restore the db upon creation"
}
# domain

# Fargate
variable "prov-sys-fargate-cpu" {
  description = "Fargate task CPU units to provision (1 vCPU = 1024 CPU units)"
  default     = 2048
}

variable "prov-sys-fargate-memory" {
  description = "Fargate task memory in MiB"
  default     = 4096
}

# containers
variable "django_app_port" {
    description = "port exposed by django app container"
    default     = 8000
}

variable "nginx_port" {
    description = "port exposed by nginx app container"
    default     = 80
}
variable "redis_port" {
    description = "port exposed by redis container"
    default     = 6379
}
variable "ps_server_container_port" {
    description = "port exposed by container"
    default     = 50051
}

variable "runtime_cpu_arch" {
  description = "The type of CPU to run container in"
  #default = "X86_64"
  default = "ARM64"
}

variable "domain" {
  description = "domain name of site to use with extension e.g. testsliderule.org"
  # Must provide on cmd line
}

variable "domain_root" {
  description = "domain name of site to use without extension e.g. testsliderule"
  # Must provide on cmd line
}

variable "ps_version" {
  description = "prov-sys sw version"
}

variable "ps_site_title" {
  description = "The Title displayed on the Web site e.g 'SlideRule Earth' or 'SlideRule Test"
}

variable "ps_bld_envver" {
  description = "The build and deploy version used when the terraform apply was issued for the ps system"
}

variable "site_id" {
  description = "Used to set the Django settings.SITE_ID"
}

# migrations

variable "create_new_db" {
  description = "Create the a new database"
  default     = false
}

variable "run_migrations" {
  description = "Run Django migrations"
  default     = false
}

variable "run_data_migrations" {
  description = "Run Django data migrations"
  default     = false
}