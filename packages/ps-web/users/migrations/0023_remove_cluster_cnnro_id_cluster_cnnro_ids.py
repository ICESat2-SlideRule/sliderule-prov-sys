# Generated by Django 4.2.4 on 2023-08-10 18:04

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0022_cluster_prov_env_is_public_cluster_prov_env_version'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='cluster',
            name='cnnro_id',
        ),
        migrations.AddField(
            model_name='cluster',
            name='cnnro_ids',
            field=models.JSONField(blank=True, null=True),
        ),
    ]
