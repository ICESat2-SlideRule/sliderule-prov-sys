# Generated by Django 4.2.2 on 2023-06-27 12:48

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0019_alter_cluster_cnnro_id_alter_cluster_cqro_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='orgaccount',
            name='num_setup_cmd',
            field=models.BigIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='orgaccount',
            name='num_setup_cmd_successful',
            field=models.BigIntegerField(default=0),
        ),
    ]
