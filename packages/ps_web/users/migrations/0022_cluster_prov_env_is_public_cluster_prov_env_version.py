# Generated by Django 4.2.3 on 2023-07-05 16:58

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0021_alter_cluster_cur_max_node_cap_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='cluster',
            name='prov_env_is_public',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='cluster',
            name='prov_env_version',
            field=models.CharField(blank=True, max_length=16, null=True),
        ),
    ]
