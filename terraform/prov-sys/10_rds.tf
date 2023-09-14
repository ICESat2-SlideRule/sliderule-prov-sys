resource "aws_db_subnet_group" "prov-sys" {
  name       = "${var.domain_root}-ps-db-subn-grp"
  subnet_ids = [aws_subnet.private-subnet-1.id, aws_subnet.private-subnet-2.id]
  tags = {
    Name = "${var.domain_root}-ps-db-subn-grp"
  }
}

resource "aws_db_parameter_group" "prov-sys" {
  name   = "${var.domain_root}-ps-db-parm-grp"
  family = "postgres13"

  parameter {
    name  = "log_connections"
    value = "1"
  }
  tags = {
    Name = "${var.domain_root}-ps-db-parm-grp"
  }
}
resource "aws_db_instance" "prov-sys" {
  identifier                = var.rds_id
  db_name                   = local.provsys_creds.rds_db_name
  username                  = local.provsys_creds.rds_username
  password                  = local.provsys_creds.rds_password
  port                      = "5432"
  engine                    = "postgres"
  instance_class            = var.rds_instance_class
  allocated_storage         = "20"
  storage_encrypted         = false
  vpc_security_group_ids    = [aws_security_group.rds.id]
  parameter_group_name      = aws_db_parameter_group.prov-sys.name
  db_subnet_group_name      = aws_db_subnet_group.prov-sys.name
  multi_az                  = true
  storage_type              = "gp2"
  publicly_accessible       = false
  backup_retention_period   = 7
  skip_final_snapshot       = false
  snapshot_identifier       = var.rds_restore_with_snapshot
  final_snapshot_identifier = var.rds_final_snapshot
  delete_automated_backups  = false

  tags = {
    Name = "${var.domain_root}-ps-db"
  }
}

resource "aws_db_instance" "prov-sys" {
  identifier                = var.rds_id
  db_name                   = local.provsys_creds.rds_db_name
  username                  = local.provsys_creds.rds_username
  password                  = local.provsys_creds.rds_password
  port                      = local.provsys_creds.rds_port
  engine                    = "postgres"
  instance_class            = var.rds_instance_class
  allocated_storage         = "20"
  storage_encrypted         = false
  vpc_security_group_ids    = [aws_security_group.rds.id]
  parameter_group_name      = aws_db_parameter_group.prov-sys.name
  db_subnet_group_name      = aws_db_subnet_group.prov-sys.name
  multi_az                  = true
  storage_type              = "gp2"
  publicly_accessible       = false
  backup_retention_period   = 7
  skip_final_snapshot       = false
  snapshot_identifier       = var.rds_restore_with_snapshot
  final_snapshot_identifier = var.rds_final_snapshot
  delete_automated_backups  = false

  tags = {
    Name = "${var.domain_root}-ps-db"
  }
}
